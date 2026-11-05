"""The paper's files and its Overleaf project kept the same both ways, against a stand-in
for Overleaf's real-time service that moves, checks and orders edits as the service does."""

import asyncio
import json
import random
import re
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from tex_mcp_web import overleaf, overleaf_sync
from tex_mcp_web import overleaf_ot as ot
from tex_mcp_web.config import load_config
from tex_mcp_web.server import TexMcpWebServer

PROJECT_ID = "0123456789abcdef01234567"
MAIN = "\\documentclass{article}\n\\begin{document}\nThe placer is fast.\n\\input{tex/body}\n\\end{document}"
BODY = "Body text.\nMore body text here.\n가나다 😀 end."


def wire(text: str) -> str:
    """A line as joinDoc sends it: UTF-8 bytes one per character."""
    return text.encode("utf-8").decode("latin-1")


class FakeOverleaf:
    """The real-time service for one project of two documents."""

    def __init__(self) -> None:
        self.cookie = "s%3Agood"
        self.docs = {
            "d-main": {"name": "main.tex", "text": MAIN, "version": 40, "history": {}, "comments": [],
                       "kind": "sharejs-text-ot"},
            "d-body": {"name": "body.tex", "text": BODY, "version": 7, "history": {}, "comments": [],
                       "kind": "sharejs-text-ot"},
        }
        self.oldest = 0                  # edits before this version are no longer kept
        self.sockets: dict[str, web.WebSocketResponse] = {}
        self.sent: list[dict] = []       # every event a client sent
        self.applied: list[tuple] = []   # (doc, source, op) of every edit taken
        self.refuse_next = False
        self.drop_after_next = False
        self.connections = 0
        app = web.Application()
        app.router.add_get("/socket.io/1/", self._handshake)
        app.router.add_get("/socket.io/1/websocket/{sid}", self._websocket)
        for route in ("threads", "ranges", "entities"):
            app.router.add_get(f"/project/{PROJECT_ID}/{route}", self._empty)
        self.app = app

    async def _empty(self, request: web.Request) -> web.Response:
        return web.json_response({} if "threads" in request.path else [])

    async def _handshake(self, request: web.Request) -> web.Response:
        return web.Response(text="sid-1:60:60:websocket")

    def comment(self, doc_id: str, thread: str, words: str) -> None:
        doc = self.docs[doc_id]
        doc["comments"].append({"t": thread, "p": ot.js_len(doc["text"][:doc["text"].index(words)]),
                                "n": ot.js_len(words)})

    def project(self) -> dict:
        def docs(*ids):
            return [{"_id": key, "name": self.docs[key]["name"]} for key in ids]
        return {"_id": PROJECT_ID, "name": "Paper", "rootDoc_id": "d-main",
                "owner": {"_id": "u-me", "first_name": "Me"}, "members": [],
                "rootFolder": [{"_id": "f0", "name": "rootFolder", "docs": docs("d-main"), "fileRefs": [],
                                "folders": [{"_id": "f1", "name": "tex", "docs": docs("d-body"), "fileRefs": [],
                                             "folders": []}]}]}

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if f"overleaf_session2={self.cookie}" not in request.headers.get("Cookie", ""):
            await ws.send_str("5:::" + json.dumps({"name": "connectionRejected", "args": [{"message": "invalid session"}]}))
            await ws.close()
            return ws
        self.connections += 1
        me = f"me-{self.connections}"
        self.sockets[me] = ws
        await ws.send_str("1::")
        await self.emit(ws, "joinProjectResponse", {"publicId": me, "project": self.project(),
                                                    "permissionsLevel": "owner", "protocolVersion": 2})
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT or message.data == "2::":
                    continue
                frame = re.match(r"^5:(\d*)\+?:[^:]*:(.*)$", message.data, re.S)
                if frame is None:
                    continue
                number, payload = frame.group(1), json.loads(frame.group(2))
                self.sent.append(payload)
                reply = await self._answer(me, payload["name"], payload["args"])
                if number and reply is not None:
                    await ws.send_str(f"6:::{number}+" + json.dumps(reply))
        finally:
            self.sockets.pop(me, None)
        return ws

    async def emit(self, ws, name: str, *args) -> None:
        await ws.send_str("5:::" + json.dumps({"name": name, "args": list(args)}))

    async def _answer(self, me: str, name: str, args: list):
        if name == "clientTracking.getConnectedUsers":
            return [None, []]
        if name == "joinDoc":
            doc = self.docs[args[0]]
            since = args[1]
            if since >= 0 and since < self.oldest:
                return [{"message": "could not get ops"}]
            ops = [doc["history"][v] for v in range(since, doc["version"])] if since >= 0 else []
            ranges = {"comments": [{"id": f"c-{c['t']}", "op": {
                "t": c["t"], "p": c["p"], "c": wire(_units(doc["text"], c["p"], c["p"] + c["n"]))}}
                for c in doc["comments"]]}
            return [None, [wire(line) for line in doc["text"].split("\n")], doc["version"], ops, ranges,
                    doc["kind"], {}]
        if name == "applyOtUpdate":
            try:
                await self.apply(args[0], args[1], me)
            except ConnectionResetError:
                return None
            except Exception as error:
                return [{"message": str(error)}]
            return [None]
        return None

    async def apply(self, doc_id: str, update: dict, source: str) -> None:
        """Move an edit past those made since its version, check it and send it on."""
        doc = self.docs[doc_id]
        v = update["v"]
        if v > doc["version"]:
            raise ValueError("Op at future version")
        op = update["op"]
        for old in range(v, doc["version"]):
            if doc["history"][old]["meta"]["source"] in (update.get("dupIfSource") or []):
                raise ValueError("Op already submitted")
            op, _ = ot.transform_x(op, doc["history"][old]["op"])
        if self.refuse_next:
            self.refuse_next = False
            raise ValueError("Delete component does not match")
        text = ot.apply(doc["text"], op)
        if update.get("hash") and v == doc["version"] and update["hash"] != overleaf_sync.text_hash(text):
            raise ValueError("Invalid hash")
        doc["text"] = text
        doc["comments"] = ot.move_comments(doc["comments"], op)
        applied = {"doc": doc_id, "op": op, "v": doc["version"], "meta": {"source": source, "user_id": "u"}}
        doc["history"][doc["version"]] = applied
        doc["version"] += 1
        self.applied.append((doc_id, source, op))
        if self.drop_after_next and source in self.sockets:
            self.drop_after_next = False
            await self.sockets[source].close()
            raise ConnectionResetError()
        for who, ws in list(self.sockets.items()):
            echo = {"v": applied["v"], "doc": doc_id} if who == source else applied
            try:
                await self.emit(ws, "otUpdateApplied", echo)
            except ConnectionResetError:
                pass

    async def coauthor(self, doc_id: str, op: list, at: int | None = None) -> None:
        doc = self.docs[doc_id]
        await self.apply(doc_id, {"op": op, "v": doc["version"] if at is None else at}, "co-1")

    async def drop(self) -> None:
        for ws in list(self.sockets.values()):
            await ws.close()


def _units(text: str, start: int, end: int) -> str:
    return text[ot.py_index(text, start):ot.py_index(text, end)]


async def wait_for(check, timeout=8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


@pytest.fixture
def paper(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(overleaf_sync, "DISK_POLL", 0.05)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv(overleaf.COOKIE_ENV, raising=False)
    (tmp_path / "tex").mkdir()
    (tmp_path / "main.tex").write_text(MAIN, encoding="utf-8")
    (tmp_path / "tex" / "body.tex").write_text(BODY, encoding="utf-8")
    return tmp_path


class Review:
    """The review server for the paper, with a page open, and Overleaf's stand-in."""

    def __init__(self, folder: Path, fake: FakeOverleaf, link: str, sync: bool = True) -> None:
        self.folder, self.fake, self.link, self.sync = folder, fake, link, sync

    async def open(self) -> "Review":
        config = f"main: main.tex\noverleaf: {self.link}\n" + ("overleaf_sync: true\n" if self.sync else "")
        (self.folder / ".tex-mcp-web.yaml").write_text(config)
        self.server = TexMcpWebServer(load_config(self.folder / ".tex-mcp-web.yaml"))
        self.server.OVERLEAF_LIVE_GRACE = 0
        self.server.OVERLEAF_COMMENT_SETTLE = 0.01
        self.client = TestClient(TestServer(self.server.app))
        await self.client.start_server()
        overleaf.save_cookie(self.fake.cookie)
        self.page = await self.client.ws_connect("/ws")
        return self

    async def close(self) -> None:
        await self.page.close()
        await wait_for(lambda: self.server.overleaf_live is None)
        await self.client.close()

    def files(self) -> dict[str, str]:
        return {doc.path: doc.status for doc in self.server.overleaf_sync.docs.values()} if self.server.overleaf_sync else {}

    def read(self, path: str) -> str:
        return (self.folder / path).read_text(encoding="utf-8")

    def write(self, path: str, text: str) -> None:
        (self.folder / path).write_text(text, encoding="utf-8")

    async def settled(self) -> None:
        """Both sides hold the same text, and nothing is on its way."""
        def same():
            sync = self.server.overleaf_sync
            return sync is not None and all(
                doc.status == "synced" and doc.inflight is None and not doc.pending
                and self.fake.docs[doc.doc_id]["text"] == self.read(doc.path)
                for doc in sync.docs.values()) and len(sync.docs) == 2
        await wait_for(same)


@pytest.fixture
async def overleaf_site():
    fake = FakeOverleaf()
    site = TestServer(fake.app)
    await site.start_server()
    try:
        yield fake, f"http://127.0.0.1:{site.port}/project/{PROJECT_ID}"
    finally:
        await site.close()


@pytest.fixture
async def review(paper, overleaf_site):
    fake, link = overleaf_site
    opened = await Review(paper, fake, link).open()
    try:
        yield opened
    finally:
        await opened.close()


# ----- Both ways -----

async def test_nothing_is_written_either_side_unless_the_config_says_so(paper, overleaf_site) -> None:
    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        await wait_for(lambda: fake.sockets)
        await asyncio.sleep(0.3)
        await fake.coauthor("d-body", [{"p": 0, "i": "New. "}])
        opened.write("main.tex", MAIN + "\n% mine")
        await asyncio.sleep(0.5)
        assert opened.read("tex/body.tex") == BODY
        assert not [e for e in fake.sent if e["name"] == "applyOtUpdate"]
        assert opened.server.overleaf_sync is None
    finally:
        await opened.close()


async def test_edits_go_both_ways_as_small_edits(review) -> None:
    fake = review.fake
    await review.settled()
    assert review.files() == {"main.tex": "synced", "tex/body.tex": "synced"}

    await fake.coauthor("d-body", [{"p": 0, "i": "New. "}])
    await wait_for(lambda: review.read("tex/body.tex").startswith("New. Body"))

    review.write("main.tex", MAIN.replace("is fast", "is very fast"))
    await review.settled()
    sent = [op for doc, source, op in fake.applied if source != "co-1"]
    assert sent == [[{"p": ot.js_len(MAIN[:MAIN.index("fast")]), "i": "very "}]]


async def test_edits_made_at_once_on_both_sides_end_the_same_on_both(review) -> None:
    fake = review.fake
    await review.settled()
    version = fake.docs["d-main"]["version"]
    review.write("main.tex", MAIN.replace("The placer", "Our placer"))
    await fake.coauthor("d-main", [{"p": len(MAIN), "i": "\n% theirs"}], at=version)
    await review.settled()
    assert review.read("main.tex") == MAIN.replace("The placer", "Our placer") + "\n% theirs"


async def test_text_on_both_sides_with_edits_at_random_ends_the_same(review) -> None:
    """Each side inserts marks of its own while deleting other text, at random moments:
    in the end both hold the same text, with every mark in it exactly once."""
    fake, rng = review.fake, random.Random(11)

    def outside(text):
        """Places between marks, not inside one."""
        depth, spots = 0, []
        for index in range(len(text) + 1):
            if depth == 0:
                spots.append(index)
            if index < len(text):
                depth += {"[": 1, "]": -1}.get(text[index], 0)
        return spots

    await review.settled()
    marks = []
    for round_ in range(60):
        for doc_id, path in (("d-main", "main.tex"), ("d-body", "tex/body.tex")):
            if rng.random() < 0.5:
                text = review.read(path)
                at = rng.choice(outside(text))
                plain = re.match(r"[^\[\]]{0,4}", text[at:]).group(0)   # never into a mark
                mark = f"[m{round_}{doc_id[2]}]" if rng.random() < 0.7 else ""
                marks += [mark] if mark else []
                review.write(path, text[:at] + mark + text[at + len(plain if rng.random() < 0.5 else ""):])
            if rng.random() < 0.6:
                text = fake.docs[doc_id]["text"]
                at = rng.choice(outside(text))
                mark = f"[c{round_}{doc_id[2]}😀]"
                marks.append(mark)
                await fake.coauthor(doc_id, [{"p": ot.js_len(text[:at]), "i": mark}])
        await asyncio.sleep(rng.choice([0, 0, 0.01, 0.07]))
    await review.settled()
    both = review.read("main.tex") + review.read("tex/body.tex")
    assert sorted(re.findall(r"\[[^\]]*\]", both)) == sorted(marks)


# ----- Nothing lost -----

async def test_an_edit_that_deletes_an_open_comments_every_word_waits_for_the_reviewer(review) -> None:
    fake = review.fake
    fake.comment("d-main", "t1", "fast")
    await review.settled()
    review.write("main.tex", MAIN.replace("The placer is fast.", "The placer."))
    await wait_for(lambda: review.files()["main.tex"] == "held")
    await asyncio.sleep(0.2)
    assert fake.docs["d-main"]["text"] == MAIN
    doc = next(d for d in review.server.overleaf_sync.docs.values() if d.path == "main.tex")
    assert doc.threads == ["t1"]

    response = await review.client.post("/overleaf/sync", json={"path": "main.tex", "choice": "overleaf"})
    assert response.status == 200
    await review.settled()
    assert review.read("main.tex") == MAIN
    kept = list((review.folder / ".tex-mcp-web" / "overleaf-sync" / "kept").glob("main.tex.here.*"))
    assert kept and kept[0].read_text() == MAIN.replace("The placer is fast.", "The placer.")

    review.write("main.tex", MAIN.replace("The placer is fast.", "The placer."))
    await wait_for(lambda: review.files()["main.tex"] == "held")
    await review.client.post("/overleaf/sync", json={"path": "main.tex", "choice": "anyway"})
    await review.settled()
    assert fake.docs["d-main"]["text"] == MAIN.replace("The placer is fast.", "The placer.")


async def test_a_comment_on_words_kept_keeps_them_through_a_rewrite_around_it(review) -> None:
    fake = review.fake
    fake.comment("d-main", "t1", "fast")
    await review.settled()
    review.write("main.tex", MAIN.replace("The placer is fast.", "Our new placer is fast, and small."))
    await review.settled()
    comment = fake.docs["d-main"]["comments"][0]
    assert _units(fake.docs["d-main"]["text"], comment["p"], comment["p"] + comment["n"]) == "fast"


async def test_words_written_over_commented_words_take_the_comment_on_overleaf(review) -> None:
    fake = review.fake
    fake.comment("d-main", "t1", "fast")
    await review.settled()
    review.write("main.tex", MAIN.replace("is fast.", "is quick and small."))
    await review.settled()
    assert review.files()["main.tex"] == "synced"
    assert fake.docs["d-main"]["text"] == MAIN.replace("is fast.", "is quick and small.")
    comment = fake.docs["d-main"]["comments"][0]
    assert _units(fake.docs["d-main"]["text"], comment["p"], comment["p"] + comment["n"]) == "quick and small"


async def test_files_that_differ_at_the_start_wait_and_neither_is_overwritten(paper, overleaf_site) -> None:
    fake, link = overleaf_site
    (paper / "main.tex").write_text(MAIN + "\n% only here")
    fake.comment("d-main", "t1", "fast")
    opened = await Review(paper, fake, link).open()
    try:
        await wait_for(lambda: opened.files().get("main.tex") == "differs")
        await asyncio.sleep(0.3)
        assert opened.read("main.tex") == MAIN + "\n% only here" and fake.docs["d-main"]["text"] == MAIN
        live = await (await opened.client.get("/overleaf/live")).json()
        assert {"path": "main.tex", "status": "differs"}.items() <= live["files"][0].items()

        await opened.client.post("/overleaf/sync", json={"path": "main.tex", "choice": "here"})
        await opened.settled()
        assert fake.docs["d-main"]["text"] == MAIN + "\n% only here"
        assert fake.docs["d-main"]["comments"][0]["n"] == 4     # the comment kept its word
        assert list((paper / ".tex-mcp-web" / "overleaf-sync" / "kept").glob("main.tex.overleaf.*"))
    finally:
        await opened.close()


async def test_edits_made_on_both_sides_while_apart_come_together(paper, overleaf_site) -> None:
    fake, link = overleaf_site
    opened = await Review(paper, fake, link).open()
    await opened.settled()
    await opened.close()
    opened.write("main.tex", MAIN.replace("The placer", "Our placer"))
    await fake.coauthor("d-main", [{"p": len(MAIN), "i": "\n% theirs"}])
    await fake.coauthor("d-body", [{"p": 0, "i": "Theirs. "}])
    opened = await Review(paper, fake, link).open()
    try:
        await opened.settled()
        assert opened.read("main.tex") == MAIN.replace("The placer", "Our placer") + "\n% theirs"
        assert opened.read("tex/body.tex") == "Theirs. " + BODY
    finally:
        await opened.close()


async def test_when_overleaf_no_longer_has_the_edits_apart_ones_come_together_only_far_apart(
        paper, overleaf_site) -> None:
    fake, link = overleaf_site
    opened = await Review(paper, fake, link).open()
    await opened.settled()
    await opened.close()
    opened.write("main.tex", MAIN.replace("\\documentclass", "% mine\n\\documentclass"))
    await fake.coauthor("d-main", [{"p": len(MAIN), "i": "\n% theirs"}])
    opened.write("tex/body.tex", BODY.replace("More body", "Mine body"))
    await fake.coauthor("d-body", [{"p": BODY.index("More body"), "d": "More"}, {"p": BODY.index("More body"), "i": "Their"}])
    fake.oldest = 10 ** 6
    opened = await Review(paper, fake, link).open()
    try:
        await wait_for(lambda: opened.files() == {"main.tex": "synced", "tex/body.tex": "differs"})
        await wait_for(lambda: fake.docs["d-main"]["text"] == opened.read("main.tex"))
        assert opened.read("main.tex") == "% mine\n" + MAIN + "\n% theirs"
        assert opened.read("tex/body.tex") == BODY.replace("More body", "Mine body")
    finally:
        await opened.close()


async def test_a_connection_lost_after_overleaf_took_an_edit_does_not_send_it_twice(review) -> None:
    fake = review.fake
    await review.settled()
    fake.drop_after_next = True
    review.write("main.tex", MAIN + "\n% once")
    await wait_for(lambda: fake.connections >= 2)
    await review.settled()
    assert fake.docs["d-main"]["text"].count("% once") == 1
    assert review.read("main.tex") == MAIN + "\n% once"


async def test_an_edit_overleaf_turns_away_stops_that_file_only(review) -> None:
    fake = review.fake
    await review.settled()
    fake.refuse_next = True
    review.write("main.tex", MAIN + "\n% refused")
    await wait_for(lambda: review.files()["main.tex"] == "stopped")
    await fake.coauthor("d-body", [{"p": 0, "i": "Still. "}])
    await wait_for(lambda: review.read("tex/body.tex").startswith("Still. "))
    assert review.read("main.tex") == MAIN + "\n% refused" and fake.docs["d-main"]["text"] == MAIN
    await review.client.post("/overleaf/sync", json={"path": "main.tex", "choice": "here"})
    await review.settled()
    assert fake.docs["d-main"]["text"] == MAIN + "\n% refused"


async def test_a_save_from_the_page_over_an_edit_from_overleaf_takes_both(review) -> None:
    fake, client = review.fake, review.client
    await review.settled()
    opened = await (await client.get("/source", params={"path": "main.tex"})).json()
    await fake.coauthor("d-main", [{"p": 0, "i": "% theirs\n"}])
    await wait_for(lambda: review.read("main.tex").startswith("% theirs"))
    mine = opened["text"].replace("is fast", "is quick")
    response = await client.put("/source", params={"path": "main.tex"},
                                json={"text": mine, "revision": opened["revision"]})
    assert response.status == 200
    saved = await response.json()
    assert saved["text"] == "% theirs\n" + mine
    await review.settled()
    assert fake.docs["d-main"]["text"] == "% theirs\n" + mine


async def test_a_file_overleaf_keeps_in_its_newer_kind_is_left_alone(paper, overleaf_site) -> None:
    fake, link = overleaf_site
    fake.docs["d-body"]["kind"] = "history-ot"
    opened = await Review(paper, fake, link).open()
    try:
        await wait_for(lambda: opened.files().get("tex/body.tex") == "unsupported"
                       and opened.files().get("main.tex") == "synced")
        opened.write("tex/body.tex", BODY + "\nmine")
        await asyncio.sleep(0.3)
        assert fake.docs["d-body"]["text"] == BODY
    finally:
        await opened.close()


async def test_choosing_overleafs_text_for_a_file_that_differs_writes_it_and_keeps_this_one(
        paper, overleaf_site) -> None:
    fake, link = overleaf_site
    (paper / "main.tex").write_text(MAIN + "\n% only here")
    opened = await Review(paper, fake, link).open()
    try:
        await wait_for(lambda: opened.files().get("main.tex") == "differs")
        response = await opened.client.post("/overleaf/sync", json={"path": "main.tex", "choice": "overleaf"})
        assert response.status == 200, await response.text()
        await opened.settled()
        assert opened.read("main.tex") == MAIN and fake.docs["d-main"]["text"] == MAIN
        kept = list((paper / ".tex-mcp-web" / "overleaf-sync" / "kept").glob("main.tex.here.*"))
        assert kept and kept[0].read_text() == MAIN + "\n% only here"
    finally:
        await opened.close()
