"""Who is in the paper's Overleaf project, where their cursors are, and what they change,
read from the session Overleaf's own editor keeps open, signed in with the reviewer's cookie.

Overleaf's editor talks to its real-time service over socket.io 0.9: a handshake names the
project, the service answers with the project's files, and from then on it tells the editor
who moved where (clientTracking.*) and every edit applied to any file (otUpdateApplied).
This reads that and sends nothing but heartbeats and one question, who is connected. It
sends no edit and no cursor, so to the others it is one more tab of the reviewer's, idle.

The protocol is the service's own (services/real-time in overleaf/overleaf), not a public
API; a change on Overleaf's side ends in an error here, never in a change there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable

import aiohttp

from . import __version__
from .overleaf import project_of

logger = logging.getLogger(__name__)

_FRAME = re.compile(r"^(\d):(\d*)(\+?):([^:]*):?(.*)$", re.S)
_ASK_USERS = 1


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


def _name(person: Any) -> str:
    if not isinstance(person, dict):
        return ""
    return str(person.get("name") or " ".join(
        str(person[key]) for key in ("first_name", "last_name") if person.get(key)) or "").strip()


class SessionRefused(Exception):
    """Overleaf refused the session cookie."""


class LivePresence:
    """One read-only connection to the project's editor session, kept up until stopped."""

    def __init__(self, link: str, cookie: Callable[[], tuple[str, str] | None], changed: Callable[[], Any], *,
                 retry_delays: tuple[float, ...] = (2, 5, 15, 30, 60)) -> None:
        self.server, self.project = project_of(link)
        self.link = link
        self._cookie = cookie
        self._changed = changed
        self._retry_delays = retry_delays
        self._task: asyncio.Task | None = None
        self.status = "off"
        self.message = ""
        self._me = ""
        self._docs: dict[str, str] = {}
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
                "users": sorted(users, key=lambda user: user["name"]), "changes": changes}

    def _set(self, status: str, message: str = "") -> None:
        self.status, self.message = status, message
        self._notify()

    def _notify(self) -> None:
        try:
            self._changed()
        except Exception:  # the page's trouble is not the connection's
            logger.exception("Overleaf presence listener failed")

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
                await self._listen(ws, heartbeat)

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
                if acked and int(acked.group(1)) == _ASK_USERS and acked.group(2):
                    self._connected_users(json.loads(acked.group(2)))

    async def _event(self, ws: aiohttp.ClientWebSocketResponse, name: str, args: list) -> None:
        first = args[0] if args else None
        if name == "connectionRejected":
            reason = str((first or {}).get("message", "")) if isinstance(first, dict) else str(first)
            if "session" in reason:
                raise SessionRefused(reason)
            raise RuntimeError(f"Overleaf refused the connection: {reason}")
        if name in ("forceDisconnect", "reconnectGracefully"):
            raise RuntimeError(f"Overleaf asked to reconnect ({name})")
        if name == "joinProjectResponse" and isinstance(first, dict):
            project = first.get("project") or {}
            self._me = str(first.get("publicId", ""))
            self._docs = {}
            for root in project.get("rootFolder") or []:
                if isinstance(root, dict):
                    self._docs.update(_paths(root))
            people = [project.get("owner")] + list(project.get("members") or [])
            self._people = {str(person["_id"]): _name(person) for person in people
                            if isinstance(person, dict) and person.get("_id")}
            self._set("live")
            await ws.send_str(f"5:{_ASK_USERS}+::" + json.dumps(
                {"name": "clientTracking.getConnectedUsers", "args": []}))
        elif name == "clientTracking.clientUpdated" and isinstance(first, dict):
            if str(first.get("id")) != self._me:
                self._users[str(first.get("id"))] = {
                    "user_id": str(first.get("user_id", "")), "name": _name(first),
                    "doc_id": first.get("doc_id"), "row": first.get("row"), "column": first.get("column")}
                self._notify()
        elif name == "clientTracking.clientDisconnected":
            if self._users.pop(str(first), None) is not None:
                self._notify()
        elif name == "otUpdateApplied" and isinstance(first, dict) and first.get("op"):
            # A collaborator's edit; one's own comes back without its op.
            path = self._docs.get(str(first.get("doc")))
            if path is None:
                return
            meta = first.get("meta") or {}
            by = self._people.get(str(meta.get("user_id")), "") or next(
                (user["name"] for user in self._users.values() if user["user_id"] == str(meta.get("user_id"))), "someone")
            change = self._changes.setdefault(path, {"file": path, "by": by, "at": 0.0, "count": 0})
            change.update(by=by, at=time.time(), count=change["count"] + 1)
            self._notify()

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
