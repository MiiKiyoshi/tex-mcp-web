"""Overleaf comments the server fetches itself, signed in as the reviewer.

Overleaf has no public route for comments. Its editor reads them from routes of its own,
the same ones Overleaf Comments Export reads: ``/project/<id>/threads`` for what was
said, ``/project/<id>/ranges`` for the words each thread is on, and a document's
download for its name and text. They answer to the editor's session cookie, so the
server sends the reviewer's own.

That cookie signs in as the reviewer, so it is kept like a password: in the reviewer's
own config folder, readable by them alone, never in the paper's folder and never sent
back to the page. It goes only to the server the paper's config names, and over plain
HTTP only to this machine. ``TEX_MCP_WEB_OVERLEAF_COOKIE`` names one in place of the
file, for a machine set up by script.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp

from . import __version__

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

def cookie_path() -> Path:
    """Where the reviewer's cookie is kept: their config folder, one for every paper."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tex-mcp-web" / "overleaf-cookie"


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


def _place(names: dict[str, str | None], paths: list[str]) -> dict[str, str]:
    """Each document's path: the one path its file name ends, else the bare name."""
    placed = {}
    for doc, name in names.items():
        if not name:
            continue
        ending = [path for path in paths if path.rsplit("/", 1)[-1] == name]
        placed[doc] = ending[0] if len(ending) == 1 else name
    return placed


async def fetch_export(link: str, cookie: tuple[str, str], *, timeout: float = 30) -> dict[str, Any]:
    """The project's threads and the words each is on, in the shape of Overleaf Comments
    Export's comments.json (schema 1), which the import already reads."""
    server, project = project_of(link)
    headers = {
        "Cookie": f"{cookie[0]}={cookie[1]}",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{server}/",
        "User-Agent": f"tex-mcp-web/{__version__} (Mozilla/5.0 compatible)",
    }
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=timeout),
                                     cookie_jar=aiohttp.DummyCookieJar()) as session:
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
            "messages": [{"user": {"name": _user_name(message.get("user"))}, "content": str(message.get("content", ""))}
                         for message in thread.get("messages") or [] if isinstance(message, dict)],
        }
    entries = []
    for doc, comments in commented.items():
        text = texts[doc]
        for comment in comments:
            op = (comment or {}).get("op") or {}
            if not op.get("t"):
                continue
            offset = min(max(int(op.get("p") or 0), 0), len(text))
            before = text[:offset]
            entries.append({
                "thread_id": str(op["t"]),
                "pathname": "/" + placed.get(doc, f"unknown-{doc}.tex"),
                "line": before.count("\n") + 1,
                "col": len(before.rsplit("\n", 1)[-1]),
                "anchored_text": str(op.get("c") or ""),
            })
    return {"schema_version": "1.0", "source": "tex-mcp-web", "threads": threads, "comments": entries}
