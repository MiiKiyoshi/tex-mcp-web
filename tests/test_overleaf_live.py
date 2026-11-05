"""Who is in the Overleaf project, where their cursors are, and what they changed, read from
Overleaf's editor session, which is sent no edit: only who is there, and one cursor."""

import asyncio
import json
import re
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
    "_id": PROJECT_ID, "name": "Paper", "owner": ME, "rootDoc_id": "d-main", "members": [dict(CO, privileges="readAndWrite")],
    "rootFolder": [{"_id": "f0", "name": "rootFolder", "docs": [{"_id": "d-main", "name": "main.tex"}],
                    "fileRefs": [{"_id": "x1", "name": "a.png"}],
                    "folders": [{"_id": "f1", "name": "tex", "docs": [{"_id": "d-body", "name": "body.tex"}],
                                 "fileRefs": [], "folders": []}]}],
}


MAIN = "\\documentclass{article}\n\\begin{document}\nHi there.\n\\end{document}\n"


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
        self.threads: dict = {}
        self.ranges: list = []             # what /ranges gives, the words each thread is on
        self.ranges_read = 0
        self.ranges_after_first_read: list = []
        self.hold_next_reading: asyncio.Event | None = None
        self.reading_held = asyncio.Event()
        app = web.Application()
        app.router.add_get("/socket.io/1/", self._handshake)
        app.router.add_get("/socket.io/1/websocket/{sid}", self._websocket)
        app.router.add_get(f"/project/{PROJECT_ID}/threads", self._threads)
        app.router.add_get(f"/project/{PROJECT_ID}/ranges", self._ranges)
        app.router.add_get(f"/project/{PROJECT_ID}/entities", self._entities)
        app.router.add_get(f"/Project/{PROJECT_ID}/doc/d-main/download", self._download)
        app.router.add_get(f"/project/{PROJECT_ID}", self._editor_page)
        app.router.add_post(f"/project/{PROJECT_ID}/thread/{{thread}}/messages", self._post_message)
        app.router.add_post(f"/project/{PROJECT_ID}/doc/{{doc}}/thread/{{thread}}/{{what:resolve|reopen}}", self._close)
        self.written: list = []            # every write Overleaf took
        self.refuse_writes = False
        self.app = app

    async def _editor_page(self, request: web.Request) -> web.Response:
        self._refuse(request)
        return web.Response(text='<html><head><meta name="ol-csrfToken" content="token-1"></head></html>',
                            content_type="text/html")

    def _check_write(self, request: web.Request) -> None:
        self._refuse(request)
        if request.headers.get("X-Csrf-Token") != "token-1":
            raise web.HTTPForbidden(text="invalid csrf token")
        if self.refuse_writes:
            raise web.HTTPInternalServerError()

    async def _post_message(self, request: web.Request) -> web.Response:
        self._check_write(request)
        body = await request.json()
        self.written.append(("reply", request.match_info["thread"], body["content"]))
        thread = self.threads[request.match_info["thread"]]
        thread["messages"].append({"id": f"m-{len(self.written)}", "user": {"first_name": "Me"}, "content": body["content"]})
        return web.Response(status=204)

    async def _close(self, request: web.Request) -> web.Response:
        self._check_write(request)
        what, thread = request.match_info["what"], request.match_info["thread"]
        self.written.append((what, thread, request.match_info["doc"]))
        self.threads[thread]["resolved"] = what == "resolve"
        return web.Response(status=204)

    def _refuse(self, request: web.Request) -> None:
        if request.cookies.get("overleaf_session2") != self.cookie:
            raise web.HTTPFound("/login")

    async def _threads(self, request: web.Request) -> web.Response:
        self._refuse(request)
        return web.json_response(self.threads)

    async def _ranges(self, request: web.Request) -> web.Response:
        self._refuse(request)
        if self.hold_next_reading is not None:     # a reading caught halfway, once
            gate, self.hold_next_reading = self.hold_next_reading, None
            self.reading_held.set()
            await gate.wait()
        comments, self.ranges_read = list(self.ranges), self.ranges_read + 1
        self.ranges += self.ranges_after_first_read
        self.ranges_after_first_read = []
        return web.json_response([{"id": "d-main", "ranges": {"comments": comments}}])

    async def _entities(self, request: web.Request) -> web.Response:
        self._refuse(request)
        return web.json_response({"entities": [{"path": "/main.tex", "type": "doc"}]})

    async def _download(self, request: web.Request) -> web.Response:
        self._refuse(request)
        return web.Response(text=MAIN, content_type="text/plain",
                            headers={"Content-Disposition": 'attachment; filename="main.tex"'})

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
            elif message.data.startswith("5:2+::"):
                lines = ["\\documentclass{article}", "Hello."]
                await ws.send_str("6:::2+" + json.dumps([None, lines, 4, [], {}, "sharejs-text-ot", {}]))
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
        await wait_for(lambda: live.snapshot()["doc_type"])
        snapshot = live.snapshot()
        assert snapshot["status"] == "live" and snapshot["doc_type"] == "sharejs-text-ot"
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
    # No edit: a question about who is there, the main file opened so that one cursor at its
    # top says this reviewer is here, and heartbeats.
    events = [json.loads(re.match(r"^5:\d*\+?:[^:]*:(.*)$", frame, re.S).group(1))
              for frame in fake.sent if frame != "2::"]
    assert [e["name"] for e in events] == ["clientTracking.getConnectedUsers", "joinDoc", "clientTracking.updatePosition"]
    assert events[1]["args"][0] == "d-main" and events[1]["args"][2]["supportsHistoryOT"] is True
    assert events[2]["args"] == [{"row": 0, "column": 0, "doc_id": "d-main"}]
    assert fake.sent.index(next(f for f in fake.sent if "updatePosition" in f)) > fake.sent.index(
        next(f for f in fake.sent if "joinDoc" in f))
    assert live.snapshot()["status"] == "off"


async def test_news_of_comments_is_passed_on_with_the_session_opening_first(realtime) -> None:
    fake, link = realtime
    news = []
    live = LivePresence(link, lambda: ("overleaf_session2", fake.cookie), lambda: None,
                        comments=lambda name, args: news.append((name, args)))
    await live.start()
    try:
        await wait_for(lambda: news)
        assert news == [("joined", [])]     # what was said while no page was open
        for name in ("new-comment", "new-comment-threads", "delete-message", "resolve-thread", "reopen-thread",
                     "delete-thread"):
            await fake.push(event(name, "t1"))
        await fake.push(event("edit-message", "t1", "m1", "reworded"))  # not mirrored here
        await wait_for(lambda: len(news) == 7)
        assert [item[0] for item in news[1:]] == ["new-comment", "new-comment-threads", "delete-message",
                                                  "resolve-thread", "reopen-thread", "delete-thread"]
        assert news[-1][1] == ["t1"]
    finally:
        await live.stop()


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
    (tmp_path / "main.tex").write_text(MAIN)
    (tmp_path / ".tex-mcp-web.yaml").write_text(f"main: main.tex\noverleaf: {link}\n")
    server = TexMcpWebServer(load_config(tmp_path / ".tex-mcp-web.yaml"))
    server.OVERLEAF_LIVE_GRACE = 0
    server.OVERLEAF_COMMENT_SETTLE = 0.01
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


# ----- Comments coming in by themselves -----

def said(name, content):
    return {"user": {"first_name": name}, "content": content}


def on_the_words(thread, words="there"):
    return {"id": f"c-{thread}", "op": {"c": words, "p": MAIN.index(words), "t": thread}}


async def opened(client, fake):
    """A page open, with the cookie kept, so the session is up."""
    await client.post("/overleaf/cookie", json={"cookie": fake.cookie})
    page = await client.ws_connect("/ws")
    await page.receive_json(timeout=5)
    await wait_for(lambda: fake.sockets and fake.connected.is_set())
    return page


def texts(comment):
    return [entry.text for entry in comment.thread]


async def test_what_was_said_while_no_page_was_open_comes_in_on_opening(review) -> None:
    client, server, fake = review
    fake.threads["t1"] = {"messages": [said("Ann", "Earlier.")]}
    fake.ranges.append(on_the_words("t1"))
    page = await opened(client, fake)
    await wait_for(lambda: server.comments.list())
    assert texts(server.comments.list()[0]) == ["[Overleaf · Ann] Earlier."]
    await page.close()


async def test_a_comment_and_its_replies_come_in_by_themselves_and_close_and_open_with_overleaf(review) -> None:
    client, server, fake = review
    page = await opened(client, fake)
    await wait_for(lambda: fake.ranges_read)

    fake.threads["t1"] = {"messages": [said("Ann", "Which words?")]}
    fake.ranges.append(on_the_words("t1"))
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: server.comments.list())
    comment = server.comments.list()[0]
    assert texts(comment) == ["[Overleaf · Ann] Which words?"]
    assert comment.anchor.to_dict()["file"] == "main.tex" and comment.anchor.to_dict()["line_start"] == 3
    seen = []
    while not any(m.get("type") == "comment_added" for m in seen):
        seen.append(await page.receive_json(timeout=5))

    fake.threads["t1"]["messages"].append(said("Bo", "These."))
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: len(server.comments.get(comment.id).thread) == 2)
    assert texts(server.comments.get(comment.id))[1] == "[Overleaf · Bo] These."

    # A reply taken back, and then another: the new one still comes in.
    fake.threads["t1"]["messages"].pop()
    await fake.push(event("delete-message", "t1", "m2"))
    await wait_for(lambda: fake.ranges_read >= 6)
    fake.threads["t1"]["messages"].append(said("Bo", "Those."))
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: len(server.comments.get(comment.id).thread) == 3)
    assert texts(server.comments.get(comment.id))[2] == "[Overleaf · Bo] Those."

    fake.threads["t1"]["resolved"] = True
    await fake.push(event("resolve-thread", "t1", {}))
    await wait_for(lambda: server.comments.get(comment.id).status == "resolved")
    fake.threads["t1"]["resolved"] = False
    await fake.push(event("reopen-thread", "t1"))
    await wait_for(lambda: server.comments.get(comment.id).status == "open")
    await page.close()


async def test_what_the_reviewer_did_here_stands(review) -> None:
    client, server, fake = review
    fake.threads = {"t1": {"messages": [said("Ann", "One.")]}, "t2": {"messages": [said("Ann", "Two.")]}}
    fake.ranges += [on_the_words("t1"), on_the_words("t2", "Hi")]
    page = await opened(client, fake)
    await wait_for(lambda: len(server.comments.list()) == 2)
    one, two = sorted(server.comments.list(), key=lambda comment: texts(comment)[0])

    # Closed here, open on Overleaf: another reading does not open it again.
    server.comments.resolve(one.id, summary="", author="human")
    # Deleted here: it does not come back.
    server.comments.delete(two.id)
    read = fake.ranges_read
    await fake.push(event("new-comment", "t9", {}))
    await wait_for(lambda: fake.ranges_read >= read + 2)
    assert [comment.id for comment in server.comments.list()] == [one.id]
    assert server.comments.get(one.id).status == "resolved"
    await page.close()


async def test_a_thread_deleted_on_overleaf_is_closed_here_not_lost(review) -> None:
    client, server, fake = review
    fake.threads["t1"] = {"messages": [said("Ann", "Mistake.")]}
    fake.ranges.append(on_the_words("t1"))
    page = await opened(client, fake)
    await wait_for(lambda: server.comments.list())
    comment = server.comments.list()[0]

    fake.threads.clear()
    fake.ranges.clear()
    await fake.push(event("delete-thread", "t1"))
    await wait_for(lambda: server.comments.get(comment.id).status == "resolved")
    assert texts(server.comments.get(comment.id)) == ["[Overleaf · Ann] Mistake.", "[Overleaf] Deleted there."]
    await page.close()


async def test_a_comment_announced_before_its_words_are_stored_still_comes_in(review) -> None:
    client, server, fake = review
    page = await opened(client, fake)
    await wait_for(lambda: fake.ranges_read)
    fake.threads["t1"] = {"messages": [said("Ann", "Late words.")]}
    fake.ranges_after_first_read = [on_the_words("t1")]
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: server.comments.list())
    await page.close()


async def test_comments_that_cannot_be_read_say_so_on_the_page_and_recover(review) -> None:
    client, server, fake = review
    page = await opened(client, fake)
    await wait_for(lambda: fake.ranges_read)
    good, fake.cookie = fake.cookie, "s%3Arotated"
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: server.overleaf_live.snapshot()["comments_trouble"])
    assert "cookie" in server.overleaf_live.snapshot()["comments_trouble"]
    fake.cookie = good
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: not server.overleaf_live.snapshot()["comments_trouble"])
    await page.close()


async def test_two_readings_at_once_do_not_bring_a_comment_in_twice(review, monkeypatch) -> None:
    client, server, fake = review
    broadcast = server.broadcast

    async def slowly(message):     # a page on a slow link: readings overlap while it is told
        await asyncio.sleep(0.02)
        await broadcast(message)

    monkeypatch.setattr(server, "broadcast", slowly)
    fake.threads = {f"t{n}": {"messages": [said("Ann", f"Note {n}.")]} for n in range(6)}
    fake.ranges += [on_the_words(f"t{n}") for n in range(6)]
    await client.post("/overleaf/cookie", json={"cookie": fake.cookie})
    page = await client.ws_connect("/ws")
    await asyncio.gather(client.post("/overleaf/pull"), client.post("/overleaf/pull"), client.post("/overleaf/pull"))
    await wait_for(lambda: len(server.comments.list()) >= 6)
    await asyncio.sleep(0.2)
    assert len(server.comments.list()) == 6
    await page.close()


async def test_closing_the_session_while_comments_come_in_does_not_bring_them_in_twice(review, monkeypatch) -> None:
    client, server, fake = review
    fake.threads = {f"t{n}": {"messages": [said("Ann", f"Note {n}.")]} for n in range(3)}
    fake.ranges += [on_the_words(f"t{n}") for n in range(3)]
    await client.post("/overleaf/cookie", json={"cookie": fake.cookie})
    broadcast = server.broadcast

    async def cut_short(message):
        if message.get("type") == "comment_added":
            raise asyncio.CancelledError
        await broadcast(message)

    monkeypatch.setattr(server, "broadcast", cut_short)
    with pytest.raises(asyncio.CancelledError):
        await server._bring_in_overleaf(await overleaf.fetch_export(server.config.overleaf, overleaf.read_cookie()))
    monkeypatch.setattr(server, "broadcast", broadcast)
    await client.post("/overleaf/pull")
    assert len(server.comments.list()) == 3


# ----- What the reviewer sends to Overleaf -----

async def on_overleaf(client, server, fake, *, write=True):
    """A thread brought in from Overleaf, with writing to Overleaf on or off."""
    server.config.overleaf_write = write
    fake.threads["t1"] = {"messages": [{"id": "m-0", **said("Ann", "Which words?")}]}
    fake.ranges.append(on_the_words("t1"))
    page = await opened(client, fake)
    await wait_for(lambda: server.comments.list())
    return page, server.comments.list()[0]


async def test_nothing_goes_to_overleaf_unless_the_config_says_so(review, tmp_path) -> None:
    client, server, fake = review
    assert server.config.overleaf_write is False
    page, comment = await on_overleaf(client, server, fake, write=False)
    assert (await (await client.get("/overleaf")).json())["write"] is False
    for action, body in (("reply", {"text": "Mine.", "overleaf": True}), ("resolve", {"summary": "", "overleaf": True}),
                         ("reopen", {"overleaf": True})):
        response = await client.post(f"/comments/{comment.id}/{action}", json=body)
        assert response.status == 403 and "overleaf_write" in (await response.json())["error"]
    assert fake.written == []
    assert len(server.comments.get(comment.id).thread) == 1 and server.comments.get(comment.id).status == "open"
    await page.close()


async def test_the_config_turns_writing_on_and_says_so_when_it_is_not_true_or_false(tmp_path) -> None:
    config = tmp_path / ".tex-mcp-web.yaml"
    config.write_text(f"main: main.tex\noverleaf: https://www.overleaf.com/project/{PROJECT_ID}\noverleaf_write: true\n")
    assert load_config(config).overleaf_write is True and load_config(config).to_dict()["overleaf_write"] is True
    config.write_text("main: main.tex\noverleaf_write: yes please\n")
    with pytest.raises(ValueError):
        load_config(config)
    config.write_text("main: main.tex\noverleaf_write: true\n")     # to where?
    with pytest.raises(ValueError):
        load_config(config)


async def test_only_what_is_asked_to_goes_to_overleaf(review) -> None:
    """A reply on the page may be an instruction to the agent, so it stays here by default."""
    client, server, fake = review
    page, comment = await on_overleaf(client, server, fake)
    status = await (await client.get("/overleaf")).json()
    assert status["write"] is True and status["threads"] == [comment.id]
    await client.post(f"/comments/{comment.id}/reply", json={"text": "Claude, fix this."})
    server.comments.reply(comment.id, "Done.", "agent")
    await client.post(f"/comments/{comment.id}/resolve", json={"summary": ""})
    await client.post(f"/comments/{comment.id}/reopen", json={})
    assert fake.written == []
    await page.close()


async def test_a_reply_goes_to_overleaf_once_and_does_not_come_back_as_another(review) -> None:
    client, server, fake = review
    page, comment = await on_overleaf(client, server, fake)
    response = await client.post(f"/comments/{comment.id}/reply", json={"text": "  Mine.  ", "overleaf": True})
    assert response.status == 200
    assert fake.written == [("reply", "t1", "Mine.")]
    assert texts(server.comments.get(comment.id)) == ["[Overleaf · Ann] Which words?", "Mine."]

    read = fake.ranges_read
    await fake.push(event("new-comment", "t1", {}))      # Overleaf tells everyone, the sender too
    await wait_for(lambda: fake.ranges_read >= read + 2)
    assert len(server.comments.get(comment.id).thread) == 2

    # What others say afterwards still comes in.
    fake.threads["t1"]["messages"].append({"id": "m-next", **said("Bo", "Good.")})
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: len(server.comments.get(comment.id).thread) == 3)
    assert texts(server.comments.get(comment.id))[2] == "[Overleaf · Bo] Good."
    await page.close()


async def test_a_reply_someone_else_wrote_in_the_same_moment_is_not_lost(review) -> None:
    client, server, fake = review
    page, comment = await on_overleaf(client, server, fake)
    # Theirs reaches Overleaf first; the page has read neither when ours is sent.
    fake.threads["t1"]["messages"].append({"id": "m-theirs", **said("Bo", "Theirs.")})
    await client.post(f"/comments/{comment.id}/reply", json={"text": "Mine.", "overleaf": True})
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: len(server.comments.get(comment.id).thread) == 3)
    read = fake.ranges_read
    await wait_for(lambda: fake.ranges_read >= read + 1)
    assert sorted(texts(server.comments.get(comment.id))[1:]) == ["Mine.", "[Overleaf · Bo] Theirs."]
    await page.close()


async def test_closing_and_opening_goes_to_overleaf_on_the_threads_own_file_and_stays(review) -> None:
    client, server, fake = review
    page, comment = await on_overleaf(client, server, fake)
    await client.post(f"/comments/{comment.id}/resolve", json={"summary": "", "overleaf": True})
    assert fake.written == [("resolve", "t1", "d-main")]
    assert server.comments.get(comment.id).status == "resolved"
    read = fake.ranges_read
    await fake.push(event("resolve-thread", "t1", {}))
    await wait_for(lambda: fake.ranges_read >= read + 2)
    assert server.comments.get(comment.id).status == "resolved"

    await client.post(f"/comments/{comment.id}/reopen", json={"overleaf": True})
    assert fake.written[-1] == ("reopen", "t1", "d-main")
    read = fake.ranges_read
    await fake.push(event("reopen-thread", "t1"))
    await wait_for(lambda: fake.ranges_read >= read + 2)
    assert server.comments.get(comment.id).status == "open"
    await page.close()


async def test_a_reading_under_way_does_not_undo_a_close_sent_meanwhile(review, monkeypatch) -> None:
    client, server, fake = review
    page, comment = await on_overleaf(client, server, fake)
    reopened = []
    reopen = server.comments.reopen
    monkeypatch.setattr(server.comments, "reopen", lambda *a, **k: (reopened.append(a), reopen(*a, **k))[1])
    await wait_for(lambda: fake.ranges_read >= 2)
    await asyncio.sleep(0.05)

    # The reading has Overleaf's old threads in hand when the close goes out.
    release = fake.hold_next_reading = asyncio.Event()
    await fake.push(event("new-comment", "t1", {}))
    await asyncio.wait_for(fake.reading_held.wait(), 5)
    sending = asyncio.ensure_future(client.post(f"/comments/{comment.id}/resolve", json={"summary": "", "overleaf": True}))
    await asyncio.sleep(0.2)
    release.set()
    assert (await sending).status == 200
    read = fake.ranges_read
    await wait_for(lambda: fake.ranges_read >= read + 1)
    await asyncio.sleep(0.1)
    # Not even for a moment: a later reading would put it right, but there may be none.
    assert reopened == [] and server.comments.get(comment.id).status == "resolved"
    assert [item[0] for item in fake.written] == ["resolve"]
    await page.close()


async def test_what_overleaf_does_not_take_is_not_done_here_and_can_be_sent_again(review) -> None:
    client, server, fake = review
    page, comment = await on_overleaf(client, server, fake)
    fake.refuse_writes = True
    response = await client.post(f"/comments/{comment.id}/reply", json={"text": "Mine.", "overleaf": True})
    assert response.status == 502 and "error" in await response.json()
    response = await client.post(f"/comments/{comment.id}/resolve", json={"summary": "", "overleaf": True})
    assert response.status == 502
    assert len(server.comments.get(comment.id).thread) == 1 and server.comments.get(comment.id).status == "open"

    fake.refuse_writes = False
    good, fake.cookie = fake.cookie, "s%3Arotated"
    response = await client.post(f"/comments/{comment.id}/reply", json={"text": "Mine.", "overleaf": True})
    assert response.status == 401 and "cookie" in (await response.json())["error"]
    assert len(server.comments.get(comment.id).thread) == 1

    fake.cookie = good
    assert (await client.post(f"/comments/{comment.id}/reply", json={"text": "Mine.", "overleaf": True})).status == 200
    assert fake.written == [("reply", "t1", "Mine.")]
    assert len(server.comments.get(comment.id).thread) == 2
    await page.close()


async def test_a_comment_that_did_not_come_from_overleaf_cannot_be_sent_there(review) -> None:
    client, server, fake = review
    page, _ = await on_overleaf(client, server, fake)
    from tex_mcp_web.comments import PaperAnchor
    own = server.comments.add(anchor=PaperAnchor(), text="A note of mine.", author="human")
    response = await client.post(f"/comments/{own.id}/reply", json={"text": "More.", "overleaf": True})
    assert response.status == 409
    assert fake.written == [] and len(server.comments.get(own.id).thread) == 1
    await page.close()


async def test_a_ledger_from_before_messages_had_ids_adds_nothing_again(review) -> None:
    client, server, fake = review
    page, comment = await on_overleaf(client, server, fake)
    ledger = server.watch_dir / ".tex-mcp-web" / "overleaf-imported.json"
    await wait_for(lambda: ledger.exists())
    ledger.write_text(json.dumps({"t1": {"comment": comment.id, "messages": 1, "resolved": False}}))
    read = fake.ranges_read
    await fake.push(event("new-comment", "t1", {}))
    await wait_for(lambda: fake.ranges_read >= read + 2)
    assert len(server.comments.get(comment.id).thread) == 1
    await page.close()
