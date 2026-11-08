"""Who is in the paper's Overleaf project, where their cursors are, and what they change,
read from the session Overleaf's own editor keeps open, signed in with the reviewer's cookie.

Overleaf's editor talks to its real-time service over socket.io 0.9: a handshake names the
project, the service answers with the project's files, and from then on it tells the editor
who moved where (clientTracking.*) and every edit applied to any file (otUpdateApplied).
This reads that and sends no edit to the text. It asks once who is connected, and says once where the
reviewer is, the top of the main file, which needs that file opened (joinDoc) first: an
editor that sees no one else sends its cursor every five minutes, and one that sees the
reviewer sends it twice a second. To the others it is one more tab of the reviewer's, idle.
The service also says when a comment changes, which is passed on for the server to read.
When the reviewer sends a new thread, the session puts it on its words with an edit that
changes no text, as the editor does for a comment. With a sync attached, the session also
carries the files' edits both ways (overleaf_sync); a file added, renamed or deleted on
Overleaf joins the project again, to read its files anew.

The protocol is the service's own (services/real-time in overleaf/overleaf), not a public
API; a change on Overleaf's side ends in an error here, never in a change there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

import aiohttp

from . import __version__
from . import overleaf_ot as ot
from .overleaf import project_of
from .overleaf_sync import _decode

logger = logging.getLogger(__name__)

_FRAME = re.compile(r"^(\d):(\d*)(\+?):([^:]*):?(.*)$", re.S)
_ASK_USERS = 1
_OPEN_MAIN = 2
# What the editor is told when comments change. edit-message is left out: a reworded message
# stays as it came in.
COMMENT_NEWS = ("new-comment", "new-comment-threads", "delete-message", "resolve-thread", "reopen-thread",
                "delete-thread")


def _paths(folder: dict, prefix: str = "") -> dict[str, str]:
    """{doc id: path} under one folder of the project's tree."""
    found = {}
    for doc in folder.get("docs") or []:
        if isinstance(doc, dict) and doc.get("_id"):
            found[str(doc["_id"])] = prefix + str(doc.get("name", ""))
    for child in folder.get("folders") or []:
        if isinstance(child, dict):
            found.update(_paths(child, f"{prefix}{child.get('name', '')}/"))
    return found


def _uploads(folder: dict, prefix: str = "") -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """The uploaded files under one folder of the project's tree, {path: {id, hash}}, and its
    folders, {path: id}, the folder itself as *prefix* ("" at the top)."""
    files: dict[str, dict[str, str]] = {}
    folders = {prefix.rstrip("/"): str(folder.get("_id", ""))}
    for ref in folder.get("fileRefs") or []:
        if isinstance(ref, dict) and ref.get("_id"):
            files[prefix + str(ref.get("name", ""))] = {"id": str(ref["_id"]), "hash": str(ref.get("hash", ""))}
    for child in folder.get("folders") or []:
        if isinstance(child, dict):
            more, inner = _uploads(child, f"{prefix}{child.get('name', '')}/")
            files.update(more)
            folders.update(inner)
    return files, folders


def _name(person: Any) -> str:
    if not isinstance(person, dict):
        return ""
    return str(person.get("name") or " ".join(
        str(person[key]) for key in ("first_name", "last_name") if person.get(key)) or "").strip()


class SessionRefused(Exception):
    """Overleaf refused the session cookie."""


# What Overleaf says when the project's files change: the session is begun again.
_STRUCTURE = ("reciveNewDoc", "reciveNewFile", "reciveNewFolder", "reciveEntityRename", "reciveEntityMove",
              "removeEntity")


class _Session:
    """Asking Overleaf over one open connection, and waiting for its answer."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse, public_id: str) -> None:
        self.ws = ws
        self.public_id = public_id
        self.waiting: dict[int, asyncio.Future] = {}
        self._next = 10

    async def emit(self, name: str, args: list) -> None:
        await self.ws.send_str("5:::" + json.dumps({"name": name, "args": args}))

    async def call(self, name: str, args: list, timeout: float = 30) -> list:
        self._next += 1
        number, answer = self._next, asyncio.get_running_loop().create_future()
        self.waiting[number] = answer
        try:
            await self.ws.send_str(f"5:{number}+::" + json.dumps({"name": name, "args": args}))
            return await asyncio.wait_for(answer, timeout)
        finally:
            self.waiting.pop(number, None)

    def answered(self, number: int, reply: list) -> bool:
        answer = self.waiting.get(number)
        if answer is None:
            return False
        if not answer.done():
            answer.set_result(reply)
        return True

    def closed(self) -> None:
        for answer in self.waiting.values():
            if not answer.done():
                answer.set_exception(ConnectionError("the connection to Overleaf closed"))


class LivePresence:
    """One connection to the project's editor session, kept up until stopped, which changes
    no text unless a sync is attached."""

    def __init__(self, link: str, cookie: Callable[[], tuple[str, str] | None], changed: Callable[[], Any], *,
                 comments: Callable[[str, list], Any] = lambda name, args: None, sync: Any = None,
                 retry_delays: tuple[float, ...] = (2, 5, 15, 30, 60)) -> None:
        self.server, self.project = project_of(link)
        self.link = link
        self._cookie = cookie
        self._changed = changed
        self._comments = comments
        self.sync = sync
        # The files brought together by hand (overleaf_sync.HandSync), when they do not follow live.
        self.files: Any = None
        self._session_api: _Session | None = None
        self.comments_trouble = ""
        self.doc_type = ""
        self._retry_delays = retry_delays
        self._task: asyncio.Task | None = None
        self.status = "off"
        self.message = ""
        self._me = ""
        self._main_doc = ""
        self._docs: dict[str, str] = {}
        self._uploads: dict[str, dict[str, str]] = {}
        self._folders: dict[str, str] = {}
        self._people: dict[str, str] = {}
        self._users: dict[str, dict] = {}
        self._changes: dict[str, dict] = {}

    # ----- What the page shows -----

    def snapshot(self) -> dict:
        users = [
            {"id": client, "user_id": user.get("user_id", ""), "name": user.get("name", ""),
             "file": self._docs.get(str(user.get("doc_id")), None), "row": user.get("row"),
             "column": user.get("column")}
            for client, user in self._users.items()
        ]
        changes = sorted(self._changes.values(), key=lambda change: change["at"], reverse=True)
        return {"status": self.status, "message": self.message, "project": self.link,
                "users": sorted(users, key=lambda user: user["name"]), "changes": changes,
                "comments_trouble": self.comments_trouble, "doc_type": self.doc_type,
                "files": (self.sync.snapshot() if self.sync is not None
                          else self.files.snapshot() if self.files is not None else []),
                "by_hand": self.files.state() if self.files is not None else None}

    def _set(self, status: str, message: str = "") -> None:
        self.status, self.message = status, message
        self._notify()

    def _notify(self) -> None:
        try:
            self._changed()
        except Exception:  # the page's trouble is not the connection's
            logger.exception("Overleaf presence listener failed")

    def _comment_news(self, name: str, args: list) -> None:
        try:
            self._comments(name, args)
        except Exception:  # the server's trouble is not the connection's
            logger.exception("Overleaf comment listener failed")

    # ----- Running -----

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._users.clear()
        self.status, self.message = "off", ""

    async def _run(self) -> None:
        attempt = 0
        while True:
            cookie = self._cookie()
            if cookie is None:
                self._set("signed_out", "No Overleaf cookie is saved.")
                return
            self._set("connecting")
            try:
                await self._session(cookie)
                attempt = 0
                message = "The connection to Overleaf closed."
            except SessionRefused as error:
                self._users.clear()
                self._set("signed_out", f"Overleaf did not accept the cookie ({error}); paste a new one.")
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:  # network, protocol: try again later
                logger.info("Overleaf presence: %s", error)
                message = f"Could not stay connected to Overleaf: {error}"
            self._users.clear()
            delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
            attempt += 1
            self._set("reconnecting", message)
            await asyncio.sleep(delay)

    async def _session(self, cookie: tuple[str, str]) -> None:
        query = f"projectId={self.project}&t={int(time.time() * 1000)}"
        headers = {"Origin": self.server, "User-Agent": f"tex-mcp-web/{__version__} (Mozilla/5.0 compatible)"}
        timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=None)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout,
                                         cookie_jar=aiohttp.DummyCookieJar()) as session:
            jar = {cookie[0]: cookie[1]}
            async with session.get(f"{self.server}/socket.io/1/?{query}", allow_redirects=False,
                                   headers={"Cookie": f"{cookie[0]}={cookie[1]}"}) as response:
                if response.status in (301, 302, 303, 307, 308, 401, 403):
                    raise SessionRefused(f"HTTP {response.status}")
                response.raise_for_status()
                handshake = await response.text()
                # The load balancer's own cookie keeps the socket on the server that knows the sid.
                for name, morsel in response.cookies.items():
                    if name != cookie[0]:
                        jar[name] = morsel.value
            parts = handshake.strip().split(":")
            if len(parts) < 4 or "websocket" not in parts[3].split(","):
                raise RuntimeError(f"unexpected handshake {handshake[:60]!r}")
            sid, heartbeat = parts[0], int(parts[1] or 60)
            socket_url = self.server.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
            cookies = "; ".join(f"{name}={value}" for name, value in jar.items())
            async with session.ws_connect(f"{socket_url}/socket.io/1/websocket/{sid}?{query}",
                                          headers={"Cookie": cookies}, autoping=True) as ws:
                try:
                    await self._listen(ws, heartbeat)
                finally:
                    api, self._session_api = self._session_api, None
                    if api is not None:
                        api.closed()
                        if self.sync is not None:
                            self.sync.session_closed()

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse, heartbeat: int) -> None:
        while True:
            message = await ws.receive(timeout=heartbeat * 2 + 10)
            if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                return
            if message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError(str(ws.exception()))
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            frame = _FRAME.match(message.data)
            if frame is None:
                continue
            kind, data = frame.group(1), frame.group(5)
            if kind == "2":
                await ws.send_str("2::")
            elif kind == "0":
                return
            elif kind == "7":
                raise RuntimeError(f"socket.io error {data}")
            elif kind == "5":
                payload = json.loads(data)
                await self._event(ws, str(payload.get("name")), payload.get("args") or [])
            elif kind == "6":
                acked = re.match(r"(\d+)\+?(.*)$", data, re.S)
                if acked and self._session_api is not None and self._session_api.answered(
                        int(acked.group(1)), json.loads(acked.group(2) or "[]")):
                    continue
                if acked and int(acked.group(1)) == _ASK_USERS and acked.group(2):
                    self._connected_users(json.loads(acked.group(2)))
                elif acked and int(acked.group(1)) == _OPEN_MAIN:
                    reply = json.loads(acked.group(2) or "[null]")
                    if reply and reply[0] is None:
                        self.doc_type = str(reply[5]) if len(reply) > 5 else ""
                        await ws.send_str("5:::" + json.dumps({"name": "clientTracking.updatePosition", "args": [
                            {"row": 0, "column": 0, "doc_id": self._main_doc}]}))
                    else:
                        logger.info("Overleaf presence: could not open the main file: %s", reply[0] if reply else "")

    async def _event(self, ws: aiohttp.ClientWebSocketResponse, name: str, args: list) -> None:
        first = args[0] if args else None
        if name == "connectionRejected":
            reason = str((first or {}).get("message", "")) if isinstance(first, dict) else str(first)
            if "session" in reason:
                raise SessionRefused(reason)
            raise RuntimeError(f"Overleaf refused the connection: {reason}")
        if name in ("forceDisconnect", "reconnectGracefully"):
            raise RuntimeError(f"Overleaf asked to reconnect ({name})")
        if name in _STRUCTURE:
            raise RuntimeError("the project's files changed on Overleaf; joining again to read them")
        if name == "joinProjectResponse" and isinstance(first, dict):
            project = first.get("project") or {}
            self._me = str(first.get("publicId", ""))
            self._docs = {}
            self._uploads, self._folders = {}, {}
            for root in project.get("rootFolder") or []:
                if isinstance(root, dict):
                    files, folders = _uploads(root)
                    self._uploads.update(files)
                    self._folders.update(folders)
            for root in project.get("rootFolder") or []:
                if isinstance(root, dict):
                    self._docs.update(_paths(root))
            people = [project.get("owner")] + list(project.get("members") or [])
            self._people = {str(person["_id"]): _name(person) for person in people
                            if isinstance(person, dict) and person.get("_id")}
            self._set("live")
            self._comment_news("joined", [])
            await ws.send_str(f"5:{_ASK_USERS}+::" + json.dumps(
                {"name": "clientTracking.getConnectedUsers", "args": []}))
            self._session_api = _Session(ws, self._me)
            if self.sync is not None:
                self.sync.session_opened(self._session_api, dict(self._docs))
            root = str(project.get("rootDoc_id") or "")
            self._main_doc = root if root in self._docs else next(iter(self._docs), "")
            if self._main_doc:
                # Read-only: the text that comes back is not used.
                await ws.send_str(f"5:{_OPEN_MAIN}+::" + json.dumps(
                    {"name": "joinDoc", "args": [self._main_doc, -1, {"encodeRanges": True, "supportsHistoryOT": True}]}))
        elif name in COMMENT_NEWS:
            self._comment_news(name, args)
        elif name == "clientTracking.clientUpdated" and isinstance(first, dict):
            if str(first.get("id")) != self._me:
                self._users[str(first.get("id"))] = {
                    "user_id": str(first.get("user_id", "")), "name": _name(first),
                    "doc_id": first.get("doc_id"), "row": first.get("row"), "column": first.get("column")}
                self._notify()
        elif name == "clientTracking.clientDisconnected":
            if self._users.pop(str(first), None) is not None:
                self._notify()
        elif name == "otUpdateError" and self.sync is not None:
            detail = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            self.sync.failed(str(detail.get("doc_id", "")), str(first))
        elif name == "otUpdateApplied" and isinstance(first, dict) and self.sync is not None and not first.get("op"):
            self.sync.remote(first)
        elif name == "otUpdateApplied" and isinstance(first, dict) and first.get("op"):
            # A collaborator's edit; one's own comes back without its op.
            if self.sync is not None:
                self.sync.remote(first)
            path = self._docs.get(str(first.get("doc")))
            if path is None:
                return
            meta = first.get("meta") or {}
            by = self._people.get(str(meta.get("user_id")), "") or next(
                (user["name"] for user in self._users.values() if user["user_id"] == str(meta.get("user_id"))), "someone")
            change = self._changes.setdefault(path, {"file": path, "by": by, "at": 0.0, "count": 0})
            change.update(by=by, at=time.time(), count=change["count"] + 1)
            self._notify()

    def editor(self) -> tuple[_Session | None, dict[str, str], str]:
        """The open session, the project's documents by path, and the main one, which the
        session keeps open. No session while the page is not in the project."""
        if self.status != "live" or self._session_api is None:
            return None, {}, ""
        return self._session_api, dict(self._docs), self._main_doc

    def tree(self) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """The project's uploaded files by path, each with its id and the hash of its bytes, and
        its folders by path, as the session last joined it. A file added, replaced, renamed or
        deleted on Overleaf joins the project again, so this is current while the page is in it."""
        return dict(self._uploads), dict(self._folders)

    async def comment(self, path: str, words: str, line: int, thread: str,
                      opened: Callable[[], Awaitable[None]]) -> str | None:
        """Put a new thread on *words* in the file at *path* on Overleaf, the occurrence nearest
        *line*, as Overleaf's editor does: once the words are found there, *opened* starts the
        thread with its message, and an edit that changes no text puts it on them. With the
        files following Overleaf live, the sync sends that edit in turn with its own. Says why
        not, or None."""
        if self.sync is not None:
            return await self.sync.comment(path, words, line, thread, opened)
        api = self._session_api
        if api is None or self.status != "live":
            return "The page is not connected to Overleaf right now. Send again in a moment."
        doc = next((doc for doc, name in self._docs.items() if name == path), None)
        if doc is None:
            return f"Overleaf's project has no file {path}."
        reply = await api.call("joinDoc", [doc, -1, {"encodeRanges": True, "supportsHistoryOT": True}])
        try:
            if not reply or reply[0] is not None:
                return f"Overleaf did not open {path}: {reply[0] if reply else 'no answer'}."
            if len(reply) > 5 and reply[5] != "sharejs-text-ot":
                return f"Overleaf keeps {path} in a form ({reply[5]}) this page puts no thread on."
            at = ot.nearest("\n".join(_decode(str(row)) for row in reply[1]), words, line)
            if at is None:
                return f"The lines this thread is on are not in {path} on Overleaf yet."
            await opened()
            answer = await api.call("applyOtUpdate", [doc, {"doc": doc, "op": [{"c": words, "p": at, "t": thread}],
                                                            "v": reply[2]}])
            if answer and answer[0] is not None:
                return f"Overleaf took the thread's message but did not put it on its words ({answer[0]}). Send again."
            return None
        finally:
            # The main file stays open, as the presence keeps it; any other is let go.
            if doc != self._main_doc:
                try:
                    await api.call("leaveDoc", [doc], timeout=5)
                except (ConnectionError, asyncio.TimeoutError):
                    pass

    def _connected_users(self, reply: list) -> None:
        users = reply[1] if len(reply) > 1 and isinstance(reply[1], list) else []
        self._users = {
            str(user["client_id"]): {
                "user_id": str(user.get("user_id", "")), "name": _name(user),
                "doc_id": (user.get("cursorData") or {}).get("doc_id"),
                "row": (user.get("cursorData") or {}).get("row"),
                "column": (user.get("cursorData") or {}).get("column")}
            for user in users if isinstance(user, dict) and user.get("client_id") and str(user["client_id"]) != self._me
        }
        self._notify()
