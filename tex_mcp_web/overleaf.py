"""Overleaf comments the server fetches itself, signed in as the reviewer.

Overleaf has no public route for comments. Its editor reads them from routes of its own:
``/project/<id>/threads`` for what was said, ``/project/<id>/ranges`` for the words each thread is on, and a document's
download for its name and text. They answer to the editor's session cookie, so the
server sends the reviewer's own. A reply goes by the editor's route too, with the token its
page carries.

That cookie signs in as the reviewer, so it is kept like a password: in the reviewer's
own config folder, readable by them alone, never in the paper's folder and never sent
back to the page. It goes only to the server the paper's config names, and over plain
HTTP only to this machine. ``TEX_MCP_WEB_OVERLEAF_COOKIE`` names one in place of the
file, for a machine set up by script.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp

from . import __version__
from . import overleaf_ot as ot

COOKIE_ENV = "TEX_MCP_WEB_OVERLEAF_COOKIE"
# overleaf.com's own, a self-hosted Overleaf's, and an older self-hosted ShareLaTeX's.
COOKIE_NAMES = ("overleaf_session2", "overleaf.sid", "sharelatex.sid")
_PROJECT = re.compile(r"^/project/([0-9a-f]{24})/?$")
_LOCAL = {"localhost", "127.0.0.1", "::1"}


class OverleafError(Exception):
    """Overleaf could not be read; the message says what to do."""


class SignedOut(OverleafError):
    """Overleaf turned the cookie away: it has expired or was never right."""


# ----- The cookie -----

def ledger_path(watch_dir: Path) -> Path:
    """What came in from Overleaf, by thread: the comment here and what it has been told."""
    return watch_dir / ".tex-mcp-web" / "overleaf-imported.json"


def ledger(watch_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        return json.loads(ledger_path(watch_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cookie_path() -> Path:
    """Where the reviewer's cookie is kept: their config folder, one for every paper."""
    from .config import user_config_dir

    return user_config_dir() / "overleaf-cookie"


def parse_cookie(pasted: str) -> tuple[str, str]:
    """(name, value) from a cookie as a browser shows it: the value alone, ``name=value``,
    or a whole Cookie header, from which the session cookie is picked."""
    text = pasted.strip()
    if not text:
        raise ValueError("the cookie is empty")
    if "=" not in text:
        if re.search(r"[\s;,]", text):
            raise ValueError("paste the value of the overleaf_session2 cookie")
        return COOKIE_NAMES[0], text
    pairs = {}
    for part in text.split(";"):
        name, _, value = part.strip().partition("=")
        if name and value:
            pairs[name.strip()] = value.strip()
    for name in COOKIE_NAMES:
        if pairs.get(name) and not re.search(r"[\s,]", pairs[name]):
            return name, pairs[name]
    raise ValueError("no overleaf_session2 cookie in what was pasted")


def save_cookie(pasted: str) -> None:
    """Keep the cookie in the reviewer's config folder, readable by them alone."""
    name, value = parse_cookie(pasted)
    path = cookie_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, f"{name}={value}\n".encode())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def forget_cookie() -> None:
    cookie_path().unlink(missing_ok=True)


def cookie_source() -> str | None:
    """Where the cookie comes from: "environment", "file", or None when there is none."""
    if os.environ.get(COOKIE_ENV, "").strip():
        return "environment"
    return "file" if cookie_path().is_file() else None


def read_cookie() -> tuple[str, str] | None:
    """(name, value) of the reviewer's cookie, the environment's before the file's."""
    try:
        pasted = os.environ.get(COOKIE_ENV, "").strip() or cookie_path().read_text(encoding="utf-8")
        return parse_cookie(pasted)
    except (OSError, ValueError):
        return None


# ----- The project -----

def project_of(link: str) -> tuple[str, str]:
    """(server, project id) from a project's link as the browser shows it. HTTPS, or
    plain HTTP to this machine: the cookie goes with every request."""
    parts = urlsplit(str(link).strip())
    match = _PROJECT.match(parts.path)
    if parts.scheme not in ("https", "http") or not parts.hostname or match is None:
        raise ValueError(f"overleaf must be a project's link such as https://www.overleaf.com/project/<id>, not {link!r}")
    if parts.scheme == "http" and parts.hostname not in _LOCAL:
        raise ValueError(f"overleaf must be an https:// link, not {link!r}")
    return f"{parts.scheme}://{parts.netloc}", match.group(1)


# ----- Reading Overleaf -----

def _disposition_name(header: str | None) -> str | None:
    """The file name a Content-Disposition gives, its exact UTF-8 form before the plain."""
    value = header or ""
    encoded = re.search(r"filename\*\s*=\s*([\w-]*)''([^;]+)", value, re.I)
    if encoded:
        return unquote(encoded.group(2), encoding=encoded.group(1) or "utf-8")
    quoted = re.search(r'filename\s*=\s*"((?:[^"\\]|\\.)*)"', value, re.I)
    if quoted:
        return re.sub(r"\\(.)", r"\1", quoted.group(1)) or None
    bare = re.search(r"filename\s*=\s*([^;\s]+)", value, re.I)
    return bare.group(1) if bare else None


def _user_name(user: Any) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("name") or " ".join(
        str(user[key]) for key in ("first_name", "last_name") if user.get(key)) or "").strip()


class _Reader:
    def __init__(self, session: aiohttp.ClientSession, server: str) -> None:
        self.session = session
        self.server = server

    async def get(self, path: str, *, json: bool = True, missing_ok: bool = False) -> Any:
        try:
            async with self.session.get(self.server + path, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308, 401, 403):
                    raise SignedOut("Overleaf did not accept the cookie; paste a new one")
                if response.status == 404:
                    if missing_ok:
                        return None
                    raise OverleafError("Overleaf has no such project for this account; check the overleaf: link")
                if response.status >= 400:
                    raise OverleafError(f"Overleaf answered {response.status}")
                if not json:
                    return await response.text(), _disposition_name(response.headers.get("Content-Disposition"))
                if "json" not in response.headers.get("Content-Type", ""):
                    raise SignedOut("Overleaf sent a page, not data; paste a new cookie")
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise OverleafError(f"could not reach Overleaf: {error or 'no answer'}") from error

    async def post(self, path: str, token: str, body: dict[str, Any]) -> None:
        headers = {"X-Csrf-Token": token, "Origin": self.server, "Content-Type": "application/json"}
        try:
            async with self.session.post(self.server + path, json=body, headers=headers,
                                         allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308, 401):
                    raise SignedOut("Overleaf did not accept the cookie; paste a new one")
                if response.status >= 300:
                    raise OverleafError(f"Overleaf refused it ({response.status}); nothing was changed there")
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise OverleafError(f"could not reach Overleaf, so it may or may not have arrived: "
                                f"{error or 'no answer'}") from error


def _place(names: dict[str, str | None], paths: list[str]) -> dict[str, str]:
    """Each document's path: the one path its file name ends, else the bare name."""
    placed = {}
    for doc, name in names.items():
        if not name:
            continue
        ending = [path for path in paths if path.rsplit("/", 1)[-1] == name]
        placed[doc] = ending[0] if len(ending) == 1 else name
    return placed


def _session(server: str, cookie: tuple[str, str], timeout: float) -> aiohttp.ClientSession:
    headers = {
        "Cookie": f"{cookie[0]}={cookie[1]}",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{server}/",
        "User-Agent": f"tex-mcp-web/{__version__} (Mozilla/5.0 compatible)",
    }
    return aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=timeout),
                                 cookie_jar=aiohttp.DummyCookieJar())


async def _write_token(read: _Reader, project: str) -> str:
    """The token Overleaf wants on a write, which it gives in the editor's page."""
    page, _ = await read.get(f"/project/{project}", json=False)
    for tag in re.findall(r"<meta\b[^>]*>", page):
        if re.search(r"""name=["']ol-csrfToken["']""", tag):
            found = re.search(r"""content=["']([^"']+)["']""", tag)
            if found:
                return unescape(found.group(1))
    raise OverleafError("Overleaf's page no longer gives a token to write with; nothing was changed there")


async def post_reply(link: str, cookie: tuple[str, str], thread: str, content: str, *, timeout: float = 30) -> None:
    """Add a reply to a thread on Overleaf, as the reviewer."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        read = _Reader(session, server)
        await read.post(f"/project/{project}/thread/{thread}/messages", await _write_token(read, project),
                        {"content": content})


async def open_thread(link: str, cookie: tuple[str, str], thread: str, content: str, *, timeout: float = 30) -> None:
    """Start a thread on Overleaf with its first message, as the reviewer, the way Overleaf's
    editor does before it puts the thread on words. A thread Overleaf already has, from a send
    that stopped before its words, is left as it is."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        read = _Reader(session, server)
        threads = await read.get(f"/project/{project}/threads")
        if isinstance(threads, dict) and thread in threads:
            return
        await read.post(f"/project/{project}/thread/{thread}/messages", await _write_token(read, project),
                        {"content": content})


async def download_file(link: str, cookie: tuple[str, str], file_id: str, *, timeout: float = 120) -> bytes:
    """An uploaded file's bytes, as Overleaf's editor downloads them."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        try:
            async with session.get(f"{server}/project/{project}/file/{file_id}", allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308, 401, 403):
                    raise SignedOut("Overleaf did not accept the cookie; paste a new one")
                if response.status >= 400:
                    raise OverleafError(f"Overleaf answered {response.status} for a file")
                return await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise OverleafError(f"could not reach Overleaf: {error or 'no answer'}") from error


async def upload_file(link: str, cookie: tuple[str, str], folder_id: str, name: str, data: bytes, *,
                      timeout: float = 120) -> dict[str, Any]:
    """Upload a file into a folder of the project, as the reviewer, the way Overleaf's editor
    uploads one: a file of that name there is replaced. Overleaf's answer, with the entity it
    made and the hash of its bytes."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        read = _Reader(session, server)
        token = await _write_token(read, project)
        form = aiohttp.FormData()
        form.add_field("relativePath", "null")
        form.add_field("name", name)
        form.add_field("type", "application/octet-stream")
        form.add_field("qqfile", data, filename=name, content_type="application/octet-stream")
        try:
            async with session.post(f"{server}/project/{project}/upload?folder_id={folder_id}", data=form,
                                    headers={"X-Csrf-Token": token, "Origin": server}, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308, 401):
                    raise SignedOut("Overleaf did not accept the cookie; paste a new one")
                answer = await response.json(content_type=None) if response.status < 300 else {}
                if response.status >= 300 or not answer.get("success"):
                    raise OverleafError(f"Overleaf refused the upload of {name} ({response.status}); nothing was changed there")
                return answer
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise OverleafError(f"could not reach Overleaf, so {name} may or may not have arrived: "
                                f"{error or 'no answer'}") from error


async def create_folder(link: str, cookie: tuple[str, str], parent_id: str, name: str, *,
                        timeout: float = 30) -> str:
    """Make a folder in the project, as Overleaf's editor does: its id."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        read = _Reader(session, server)
        token = await _write_token(read, project)
        headers = {"X-Csrf-Token": token, "Origin": server, "Content-Type": "application/json"}
        try:
            async with session.post(f"{server}/project/{project}/folder", json={"name": name, "parent_folder_id": parent_id},
                                    headers=headers, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308, 401):
                    raise SignedOut("Overleaf did not accept the cookie; paste a new one")
                answer = await response.json(content_type=None) if response.status < 300 else {}
                if response.status >= 300 or not answer.get("_id"):
                    raise OverleafError(f"Overleaf refused the folder {name} ({response.status}); nothing was changed there")
                return str(answer["_id"])
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise OverleafError(f"could not reach Overleaf, so the folder {name} may or may not exist: "
                                f"{error or 'no answer'}") from error


async def delete_entity(link: str, cookie: tuple[str, str], kind: str, entity_id: str, *, timeout: float = 30) -> None:
    """Delete a document, file or folder of the project, as Overleaf's editor does. Overleaf
    keeps it in the project's history."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        read = _Reader(session, server)
        token = await _write_token(read, project)
        try:
            async with session.delete(f"{server}/project/{project}/{kind}/{entity_id}",
                                      headers={"X-Csrf-Token": token, "Origin": server}, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308, 401):
                    raise SignedOut("Overleaf did not accept the cookie; paste a new one")
                if response.status >= 300:
                    raise OverleafError(f"Overleaf refused to delete it ({response.status}); nothing was changed there")
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise OverleafError(f"could not reach Overleaf, so it may or may not be deleted: {error or 'no answer'}") from error


def new_thread_id() -> str:
    """An id for a new thread, shaped as Overleaf's editor makes them: the time in seconds and
    random digits, 24 hexadecimal digits in all."""
    return f"{int(time.time()):08x}{secrets.token_hex(8)}"


async def set_resolved(link: str, cookie: tuple[str, str], threads: list[str], resolved: bool, *,
                       timeout: float = 30) -> AsyncIterator[str]:
    """Resolve threads on Overleaf, or reopen them, as the reviewer, one after another on one
    connection, giving each thread once Overleaf has taken it. Overleaf files the change
    under the file the thread is on, which its ranges say. The first thread Overleaf refuses
    raises, and it and the threads after it stay as they were there."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        read = _Reader(session, server)
        ranges = await read.get(f"/project/{project}/ranges")
        docs = ranges.get("docs", []) if isinstance(ranges, dict) else ranges or []
        doc_of: dict[str, str] = {}
        for doc in docs:
            for comment in ((doc.get("ranges") or {}).get("comments") or []) if isinstance(doc, dict) else []:
                doc_of.setdefault(str(((comment or {}).get("op") or {}).get("t")), str(doc.get("id") or doc.get("_id")))
        token = await _write_token(read, project)
        for thread in threads:
            if thread not in doc_of:
                raise OverleafError("Overleaf has this thread on no file, so nothing was changed there")
            await read.post(f"/project/{project}/doc/{doc_of[thread]}/thread/{thread}/{'resolve' if resolved else 'reopen'}",
                            token, {})
            yield thread


async def fetch_export(link: str, cookie: tuple[str, str], *, timeout: float = 30) -> dict[str, Any]:
    """The project's threads and the words each is on: ``threads`` by id, with their
    messages and whether each is resolved, and ``comments``, one per place a thread is on,
    with its file, line and words."""
    server, project = project_of(link)
    async with _session(server, cookie, timeout) as session:
        read = _Reader(session, server)
        raw_threads = await read.get(f"/project/{project}/threads")
        resolved = await read.get(f"/project/{project}/resolved-thread-ids", missing_ok=True)
        ranges = await read.get(f"/project/{project}/ranges")
        if not isinstance(raw_threads, dict):
            raise OverleafError("Overleaf's thread list has changed shape")
        resolved_ids = set(resolved.get("resolvedThreadIds", []) if isinstance(resolved, dict) else resolved or [])
        docs = ranges.get("docs", []) if isinstance(ranges, dict) else ranges or []
        commented = {}
        for doc in docs:
            comments = ((doc or {}).get("ranges") or {}).get("comments") or []
            if comments and (doc.get("id") or doc.get("_id")):
                commented[str(doc.get("id") or doc.get("_id"))] = comments
        texts, names = {}, {}
        for doc in commented:
            texts[doc], names[doc] = await read.get(f"/Project/{project}/doc/{doc}/download", json=False)
        entities = await read.get(f"/project/{project}/entities", missing_ok=True) if commented else None
    paths = [str(entity.get("path", "")).lstrip("/") for entity in (entities or {}).get("entities", [])
             if isinstance(entity, dict) and entity.get("type") == "doc"]
    placed = _place(names, paths)

    threads = {}
    for thread_id, thread in raw_threads.items():
        if not isinstance(thread, dict):
            continue
        threads[str(thread_id)] = {
            "resolved": bool(thread.get("resolved")) or thread_id in resolved_ids,
            "messages": [{"user": {"name": _user_name(message.get("user"))}, "content": str(message.get("content", "")),
                          **({"id": str(message["id"])} if message.get("id") else {})}
                         for message in thread.get("messages") or [] if isinstance(message, dict)],
        }
    entries = []
    for doc, comments in commented.items():
        text = texts[doc]
        # The file's text as Overleaf has it, by hash: a file here with the same hash holds the
        # same text, and there a comment's offset is exact.
        text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        for comment in comments:
            op = (comment or {}).get("op") or {}
            if not op.get("t"):
                continue
            # Overleaf counts positions as JavaScript does, in UTF-16 units.
            offset = ot.py_index(text, min(max(int(op.get("p") or 0), 0), ot.js_len(text)))
            before = text[:offset]
            entries.append({
                "thread_id": str(op["t"]),
                "pathname": "/" + placed.get(doc, f"unknown-{doc}.tex"),
                "line": before.count("\n") + 1,
                "col": len(before.rsplit("\n", 1)[-1]),
                "anchored_text": str(op.get("c") or ""),
                "offset": offset,
                "text_sha": text_sha,
            })
    return {"threads": threads, "comments": entries}
