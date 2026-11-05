"""Who is in the Overleaf project, where their cursors are, and what they changed, read from
Overleaf's editor session without sending it anything but a question about who is there."""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from tex_mcp_web import overleaf
from tex_mcp_web.config import load_config
from tex_mcp_web.overleaf_live import LivePresence
from tex_mcp_web.server import TexMcpWebServer

PROJECT_ID = "0123456789abcdef01234567"
ME = {"_id": "u-me", "first_name": "Me", "last_name": "Myself", "email": "me@example.org"}
CO = {"_id": "u-co", "first_name": "Co", "last_name": "Author", "email": "co@example.org"}
PROJECT = {
    "_id": PROJECT_ID, "name": "Paper", "owner": ME, "members": [dict(CO, privileges="readAndWrite")],
    "rootFolder": [{"_id": "f0", "name": "rootFolder", "docs": [{"_id": "d-main", "name": "main.tex"}],
                    "fileRefs": [{"_id": "x1", "name": "a.png"}],
                    "folders": [{"_id": "f1", "name": "tex", "docs": [{"_id": "d-body", "name": "body.tex"}],
                                 "fileRefs": [], "folders": []}]}],
}


def event(name, *args):
    return "5:::" + json.dumps({"name": name, "args": list(args)})


class FakeRealtime:
    """Overleaf's real-time service, as far as socket.io 0.9 and the editor's events go."""

    def __init__(self) -> None:
        self.cookie = "s%3Agood"
        self.sent: list[str] = []          # every frame the client sent
        self.handshakes: list[dict] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.connected = asyncio.Event()
        self.reject_with: str | None = None
        app = web.Application()
        app.router.add_get("/socket.io/1/", self._handshake)
        app.router.add_get("/socket.io/1/websocket/{sid}", self._websocket)
        self.app = app

    async def _handshake(self, request: web.Request) -> web.Response:
        self.handshakes.append({"cookie": request.headers.get("Cookie", ""), "origin": request.headers.get("Origin"),
                                "project": request.query.get("projectId")})
        response = web.Response(text="sid-1:60:60:websocket,xhr-polling")
        response.set_cookie("GCLB", "sticky")   # the load balancer's, wanted back on the socket
        return response

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        await ws.send_str("1::")
        cookie = request.headers.get("Cookie", "")
        if self.reject_with or f"overleaf_session2={self.cookie}" not in cookie or "GCLB=sticky" not in cookie:
            await ws.send_str(event("connectionRejected", {"message": self.reject_with or "invalid session"}))
            await ws.close()
            return ws
        await ws.send_str(event("joinProjectResponse", {"publicId": "me-1", "project": PROJECT,
                                                        "permissionsLevel": "owner", "protocolVersion": 2}))
        self.connected.set()
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            self.sent.append(message.data)
            if message.data.startswith("5:1+::"):
                users = [
                    {"client_id": "me-1", "user_id": "u-me", "first_name": "Me", "last_name": "Myself",
                     "email": "me@example.org", "cursorData": {"row": 0, "column": 0, "doc_id": "d-main"}},
                    {"client_id": "co-1", "user_id": "u-co", "first_name": "Co", "last_name": "Author",
                     "email": "co@example.org", "cursorData": {"row": 1, "column": 4, "doc_id": "d-body"}},
                ]
                await ws.send_str("6:::1+" + json.dumps([None, users]))
        return ws

    async def push(self, frame: str) -> None:
        await self.sockets[-1].send_str(frame)


@pytest.fixture
async def realtime():
    fake = FakeRealtime()
    server = TestServer(fake.app)
    await server.start_server()
    try:
        yield fake, f"http://127.0.0.1:{server.port}/project/{PROJECT_ID}"
    finally:
        await server.close()


async def wait_for(check, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


async def test_the_project_shows_who_is_there_where_they_are_and_what_they_changed(realtime) -> None:
    fake, link = realtime
    changes = []
    live = LivePresence(link, lambda: ("overleaf_session2", fake.cookie), lambda: changes.append(1))
    await live.start()
    try:
        await wait_for(lambda: live.snapshot()["users"])
        snapshot = live.snapshot()
        assert snapshot["status"] == "live"
        assert fake.handshakes[0] == {"cookie": "overleaf_session2=s%3Agood", "origin": f"{link.split('/project')[0]}",
                                      "project": PROJECT_ID}
        # Others only, by name and file; no e-mail addresses.
        assert snapshot["users"] == [{"id": "co-1", "user_id": "u-co", "name": "Co Author",
                                      "file": "tex/body.tex", "row": 1, "column": 4}]

        await fake.push(event("clientTracking.clientUpdated", {"id": "co-1", "user_id": "u-co", "name": "Co Author",
                                                               "email": "co@example.org", "doc_id": "d-main",
                                                               "row": 3, "column": 0}))
        await fake.push(event("clientTracking.clientUpdated", {"id": "me-1", "user_id": "u-me", "name": "Me Myself",
                                                               "doc_id": "d-main", "row": 9, "column": 9}))
        await wait_for(lambda: live.snapshot()["users"][0]["file"] == "main.tex")
        assert live.snapshot()["users"][0]["row"] == 3 and len(live.snapshot()["users"]) == 1

        await fake.push(event("otUpdateApplied", {"doc": "d-body", "op": [{"p": 3, "i": "x"}], "v": 7,
                                                  "meta": {"source": "co-1", "user_id": "u-co", "ts": 1}}))
        await fake.push(event("otUpdateApplied", {"doc": "d-body", "op": [{"p": 4, "i": "y"}], "v": 8,
                                                  "meta": {"source": "co-1", "user_id": "u-co", "ts": 2}}))
        await fake.push(event("otUpdateApplied", {"doc": "d-main", "v": 9}))  # an echo of one's own: no op
        await wait_for(lambda: live.snapshot()["changes"])
        await wait_for(lambda: live.snapshot()["changes"][0]["count"] == 2)
        change = live.snapshot()["changes"][0]
        assert (change["file"], change["by"], change["count"]) == ("tex/body.tex", "Co Author", 2)

        await fake.push("2::")
        await wait_for(lambda: "2::" in fake.sent)
        await fake.push(event("clientTracking.clientDisconnected", "co-1"))
        await wait_for(lambda: not live.snapshot()["users"])
        assert changes
    finally:
        await live.stop()
    # Read-only: a question about who is there, and heartbeats; no edit, no cursor.
    assert all(frame == "2::" or json.loads(frame.split("::", 1)[1])["name"] == "clientTracking.getConnectedUsers"
               for frame in fake.sent), fake.sent
    assert live.snapshot()["status"] == "off"


async def test_a_session_overleaf_turns_away_stops_and_says_so(realtime) -> None:
    fake, link = realtime
    live = LivePresence(link, lambda: ("overleaf_session2", "s%3Aexpired"), lambda: None)
    await live.start()
    try:
        await wait_for(lambda: live.snapshot()["status"] == "signed_out")
        await asyncio.sleep(0.3)
        assert len(fake.handshakes) == 1   # not tried again with the same cookie
    finally:
        await live.stop()


async def test_a_dropped_connection_comes_back(realtime) -> None:
    fake, link = realtime
    live = LivePresence(link, lambda: ("overleaf_session2", fake.cookie), lambda: None, retry_delays=(0.05,))
    await live.start()
    try:
        await wait_for(lambda: live.snapshot()["status"] == "live")
        await fake.sockets[-1].close()
        await wait_for(lambda: len(fake.handshakes) == 2)
        await wait_for(lambda: live.snapshot()["status"] == "live")
    finally:
        await live.stop()


# ----- The review page -----

@pytest.fixture
async def review(tmp_path: Path, monkeypatch, realtime):
    fake, link = realtime
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv(overleaf.COOKIE_ENV, raising=False)
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nHi.\n\\end{document}\n")
    (tmp_path / ".tex-mcp-web.yaml").write_text(f"main: main.tex\noverleaf: {link}\n")
    server = TexMcpWebServer(load_config(tmp_path / ".tex-mcp-web.yaml"))
    server.OVERLEAF_LIVE_GRACE = 0
    client = TestClient(TestServer(server.app))
    await client.start_server()
    try:
        yield client, server, fake
    finally:
        await client.close()


async def test_an_open_page_is_live_on_overleaf_and_a_closed_one_is_not(review) -> None:
    client, server, fake = review
    # No cookie yet: an open page does not connect.
    page = await client.ws_connect("/ws")
    await page.receive_json(timeout=5)
    assert (await (await client.get("/overleaf/live")).json())["status"] == "off"
    await page.close()

    await client.post("/overleaf/cookie", json={"cookie": fake.cookie})
    page = await client.ws_connect("/ws")
    seen = []
    while not any(m.get("type") == "overleaf_live" and m["users"] for m in seen):
        seen.append(await page.receive_json(timeout=5))
    live = [m for m in seen if m.get("type") == "overleaf_live"][-1]
    assert live["users"][0]["name"] == "Co Author" and live["status"] == "live"
    assert (await (await client.get("/overleaf/live")).json())["users"][0]["file"] == "tex/body.tex"

    await page.close()
    await wait_for(lambda: server.overleaf_live is None)
    assert (await (await client.get("/overleaf/live")).json())["status"] == "off"
