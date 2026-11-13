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
FIGURE = b"\x89PNG figure one"


def wire(text: str) -> str:
    """A line as joinDoc sends it: UTF-8 bytes one per character."""
    return text.encode("utf-8").decode("latin-1")


class FakeOverleaf:
    """The real-time service for one project of two documents."""

    def __init__(self) -> None:
        self.cookie = "s%3Agood"
        self.docs = {
            "d-main": {"name": "main.tex", "text": MAIN, "version": 40, "history": {}, "comments": [],
                       "kind": "sharejs-text-ot", "folder": ""},
            "d-body": {"name": "body.tex", "text": BODY, "version": 7, "history": {}, "comments": [],
                       "kind": "sharejs-text-ot", "folder": "tex"},
        }
        self.folders = {"": "f0", "tex": "f1", "fig": "f2"}
        self.deleted: list[tuple[str, str]] = []
        self.oldest = 0                  # edits before this version are no longer kept
        self.sockets: dict[str, web.WebSocketResponse] = {}
        self.sent: list[dict] = []       # every event a client sent
        self.applied: list[tuple] = []   # (doc, source, op) of every edit taken
        self.updates: list[dict] = []    # every edit a client sent, as sent
        # Whom Overleaf tracks the edits of, which the editor is told when it joins.
        self.track_state: dict | None = None
        self.refuse_next = False
        self.drop_after_next = False
        self.connections = 0
        self.threads: dict = {}          # what /threads gives: the messages of each thread
        # Uploaded files by path, each with its id and bytes, in the folder of that name.
        self.uploads = {"fig/plot.png": {"id": "u-1", "data": FIGURE}}
        app = web.Application()
        app.router.add_get("/socket.io/1/", self._handshake)
        app.router.add_get("/socket.io/1/websocket/{sid}", self._websocket)
        app.router.add_get(f"/project/{PROJECT_ID}/threads", self._threads)
        app.router.add_get(f"/project/{PROJECT_ID}/ranges", self._ranges)
        app.router.add_get(f"/project/{PROJECT_ID}/entities", self._entities)
        app.router.add_get(f"/Project/{PROJECT_ID}/doc/{{doc}}/download", self._download)
        app.router.add_get(f"/project/{PROJECT_ID}", self._editor_page)
        app.router.add_get(f"/project/{PROJECT_ID}/file/{{file}}", self._file)
        app.router.add_post(f"/project/{PROJECT_ID}/upload", self._upload)
        app.router.add_post(f"/project/{PROJECT_ID}/folder", self._folder)
        app.router.add_delete(f"/project/{PROJECT_ID}/{{kind}}/{{entity}}", self._delete)
        app.router.add_post(f"/project/{PROJECT_ID}/thread/{{thread}}/messages", self._post_message)
        self.app = app

    async def _threads(self, request: web.Request) -> web.Response:
        return web.json_response(self.threads)

    async def _editor_page(self, request: web.Request) -> web.Response:
        return web.Response(text='<html><head><meta name="ol-csrfToken" content="token-1"></head></html>',
                            content_type="text/html")

    async def _file(self, request: web.Request) -> web.Response:
        found = next((item for item in self.uploads.values() if item["id"] == request.match_info["file"]), None)
        if found is None:
            raise web.HTTPNotFound()
        return web.Response(body=found["data"], content_type="application/octet-stream")

    async def _upload(self, request: web.Request) -> web.Response:
        if request.headers.get("X-Csrf-Token") != "token-1":
            raise web.HTTPForbidden(text="invalid csrf token")
        folder = next(path for path, key in self.folders.items() if key == request.query["folder_id"])
        form = await request.post()
        path = f"{folder}/{form['name']}".lstrip("/")
        data = form["qqfile"].file.read()
        if path.endswith(".tex"):      # Overleaf makes an uploaded text file a document
            key = f"d-{len(self.docs) + 1}"
            self.docs[key] = {"name": form["name"], "text": data.decode("utf-8"), "version": 0, "history": {},
                              "comments": [], "kind": "sharejs-text-ot", "folder": folder}
            await self.broadcast("reciveNewDoc", self.folders[folder], {})
            return web.json_response({"success": True, "entity_id": key, "entity_type": "doc"})
        self.replace(path, data)
        return web.json_response({"success": True, "entity_id": self.uploads[path]["id"], "entity_type": "file",
                                  "hash": overleaf_sync.blob_sha(self.uploads[path]["data"])})

    async def _delete(self, request: web.Request) -> web.Response:
        if request.headers.get("X-Csrf-Token") != "token-1":
            raise web.HTTPForbidden(text="invalid csrf token")
        kind, entity = request.match_info["kind"], request.match_info["entity"]
        if kind == "doc":
            del self.docs[entity]
        else:
            del self.uploads[next(path for path, item in self.uploads.items() if item["id"] == entity)]
        self.deleted.append((kind, entity))
        await self.broadcast("removeEntity", entity, "editor")
        return web.Response(status=204)

    async def _folder(self, request: web.Request) -> web.Response:
        if request.headers.get("X-Csrf-Token") != "token-1":
            raise web.HTTPForbidden(text="invalid csrf token")
        body = await request.json()
        parent = next(path for path, key in self.folders.items() if key == body["parent_folder_id"])
        path = f"{parent}/{body['name']}".lstrip("/")
        self.folders[path] = f"f{len(self.folders)}"
        return web.json_response({"_id": self.folders[path], "name": body["name"], "docs": [], "fileRefs": [], "folders": []})

    def replace(self, path: str, data: bytes) -> None:
        """A file uploaded on Overleaf: a new id, as Overleaf gives a replaced file."""
        self.uploads[path] = {"id": f"u-{len(self.applied) + len(self.uploads) + 7}-{len(data)}", "data": data}

    async def _post_message(self, request: web.Request) -> web.Response:
        if request.headers.get("X-Csrf-Token") != "token-1":
            raise web.HTTPForbidden(text="invalid csrf token")
        body = await request.json()
        thread = self.threads.setdefault(request.match_info["thread"], {"messages": []})
        thread["messages"].append({"id": f"m-{len(thread['messages'])}", "user": {"first_name": "Me"},
                                   "content": body["content"]})
        return web.Response(status=204)

    async def _entities(self, request: web.Request) -> web.Response:
        return web.json_response({"entities": [{"path": "/main.tex", "type": "doc"},
                                               {"path": "/tex/body.tex", "type": "doc"}]})

    async def _ranges(self, request: web.Request) -> web.Response:
        """The words of each thread /threads gives, where each document has them now."""
        return web.json_response([{"id": key, "ranges": {"comments": [
            {"id": f"c-{c['t']}", "op": {"t": c["t"], "p": c["p"], "c": _units(doc["text"], c["p"], c["p"] + c["n"])}}
            for c in doc["comments"] if c["t"] in self.threads]}} for key, doc in self.docs.items()])

    async def _download(self, request: web.Request) -> web.Response:
        doc = self.docs[request.match_info["doc"]]
        return web.Response(text=doc["text"], content_type="text/plain",
                            headers={"Content-Disposition": f'attachment; filename="{doc["name"]}"'})

    async def broadcast(self, name: str, *args) -> None:
        for ws in list(self.sockets.values()):
            await self.emit(ws, name, *args)

    async def _handshake(self, request: web.Request) -> web.Response:
        return web.Response(text="sid-1:60:60:websocket")

    def comment(self, doc_id: str, thread: str, words: str) -> None:
        doc = self.docs[doc_id]
        doc["comments"].append({"t": thread, "p": ot.js_len(doc["text"][:doc["text"].index(words)]),
                                "n": ot.js_len(words)})

    def project(self) -> dict:
        def folder(path: str) -> dict:
            inner = [name for name in self.folders if name and name.rpartition("/")[0] == path]
            return {"_id": self.folders[path], "name": path.rpartition("/")[2] or "rootFolder",
                    "docs": [{"_id": key, "name": doc["name"]} for key, doc in self.docs.items() if doc["folder"] == path],
                    "fileRefs": [{"_id": item["id"], "name": name.rpartition("/")[2],
                                  "hash": overleaf_sync.blob_sha(item["data"])}
                                 for name, item in self.uploads.items() if name.rpartition("/")[0] == path],
                    "folders": [folder(name) for name in inner]}
        project = {"_id": PROJECT_ID, "name": "Paper", "rootDoc_id": "d-main",
                   "owner": {"_id": "u-me", "first_name": "Me"}, "members": [], "rootFolder": [folder("")]}
        if self.track_state is not None:
            project["trackChangesState"] = self.track_state
        return project

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
            return [None, [{"client_id": me, "user_id": "u-me", "first_name": "Me"}] if self.track_state is not None else []]
        if name == "joinDoc":
            doc = self.docs[args[0]]
            since = args[1]
            if since >= 0 and since < self.oldest:
                return [{"message": "could not get ops"}]
            ops = [doc["history"][v] for v in range(since, doc["version"])] if since >= 0 else []
            ranges = {"comments": [{"id": f"c-{c['t']}", "op": {
                "t": c["t"], "p": c["p"], "c": wire(_units(doc["text"], c["p"], c["p"] + c["n"]))}}
                for c in doc["comments"]]}
            if doc.get("changes"):
                ranges["changes"] = [{"id": change["id"], "op": {key: wire(value) if key in ("i", "d") else value
                                                                for key, value in change["op"].items()}}
                                     for change in doc["changes"]]
            return [None, [wire(line) for line in doc["text"].split("\n")], doc["version"], ops, ranges,
                    doc["kind"], {}]
        if name == "leaveDoc":
            return [None]
        if name == "applyOtUpdate":
            self.updates.append(args[1])
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
    (tmp_path / "fig").mkdir()
    (tmp_path / "fig" / "plot.png").write_bytes(FIGURE)
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
    # It came in by itself, so nothing waits to be brought in.
    assert review.server.overleaf_live.snapshot()["changes"] == []

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
        # Comments wait for every file to be in step, as they do for a git clone's commit.
        refused = await opened.client.post("/overleaf/comments")
        assert refused.status == 409 and "main.tex" in (await refused.json())["error"]

        await opened.client.post("/overleaf/sync", json={"path": "main.tex", "choice": "here"})
        await opened.settled()
        assert (await opened.client.post("/overleaf/comments")).status != 409
        assert fake.docs["d-main"]["text"] == MAIN + "\n% only here"
        assert fake.docs["d-main"]["comments"][0]["n"] == 4     # the comment kept its word
        assert list((paper / ".tex-mcp-web" / "overleaf-sync" / "kept").glob("main.tex.overleaf.*"))
    finally:
        await opened.close()


async def test_a_new_thread_comes_in_by_itself_once_every_file_is_in_step(paper, overleaf_site) -> None:
    """With the files kept the same both ways there is no Sync comments to press: a thread
    written on Overleaf is counted while a file waits for a choice, and comes in on its
    words, by itself, once every file is the same as on Overleaf."""
    fake, link = overleaf_site
    (paper / "main.tex").write_text(MAIN + "\n% only here")
    opened = await Review(paper, fake, link).open()
    try:
        await wait_for(lambda: opened.files().get("main.tex") == "differs")
        fake.threads["t1"] = {"messages": [{"id": "m1", "user": {"first_name": "Ann"}, "content": "Faster?"}]}
        fake.comment("d-main", "t1", "fast")
        await fake.broadcast("new-comment", "t1", {})
        await wait_for(lambda: opened.server._overleaf_waiting == 1)
        await asyncio.sleep(0.2)
        assert opened.server.comments.list() == []

        await opened.client.post("/overleaf/sync", json={"path": "main.tex", "choice": "here"})
        await opened.settled()
        await wait_for(lambda: len(opened.server.comments.list()) == 1)
        brought = opened.server.comments.list()[0]
        assert (brought.thread[0].name, brought.thread[0].text) == ("Ann", "Faster?")
        assert (brought.anchor.file, brought.anchor.line_start) == ("main.tex", MAIN[:MAIN.index("fast")].count("\n") + 1)
        assert opened.server._overleaf_waiting == 0

        # A thread written while every file is in step comes in at once.
        fake.threads["t2"] = {"messages": [{"id": "m2", "user": {"first_name": "Ann"}, "content": "And this?"}]}
        fake.comment("d-body", "t2", BODY.split()[0])
        await fake.broadcast("new-comment", "t2", {})
        await wait_for(lambda: len(opened.server.comments.list()) == 2)
        assert opened.server._overleaf_waiting == 0
    finally:
        await opened.close()


async def test_live_files_and_git_go_on_together_in_a_git_clone(paper, overleaf_site, tmp_path_factory,
                                                                 monkeypatch) -> None:
    """In a git clone with the files kept the same live, an edit on Overleaf comes into its
    file at once, and Overleaf's next commit is taken in by a read of its branch, with
    nothing left uncommitted. A figure committed here goes out by Push while Overleaf's text
    stays as Overleaf has it, though Overleaf moved on meanwhile."""
    import subprocess

    fake, link = overleaf_site
    for key, value in (("GIT_AUTHOR_NAME", "Someone"), ("GIT_COMMITTER_NAME", "Someone"),
                       ("GIT_AUTHOR_EMAIL", "someone@example.com"), ("GIT_COMMITTER_EMAIL", "someone@example.com")):
        monkeypatch.setenv(key, value)
    git = lambda folder, *args: subprocess.run(["git", "-C", str(folder), *args], check=True,
                                               capture_output=True, text=True).stdout
    bare = tmp_path_factory.mktemp("overleaf-git") / PROJECT_ID
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git(paper, "init", "-q", "-b", "main")
    git(paper, "add", "main.tex", "tex/body.tex")
    git(paper, "commit", "-q", "-m", "first")
    git(paper, "remote", "add", "overleaf", str(bare))
    git(paper, "push", "-q", "-u", "overleaf", "main")
    bridge = tmp_path_factory.mktemp("bridge") / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(bridge)], check=True, capture_output=True)

    def snapshot() -> None:
        """Overleaf's git server, read: a commit of the project as it is now."""
        git(bridge, "pull", "-q", "--ff-only", "origin", "main")
        for doc in fake.docs.values():
            (bridge / ("tex/" if doc["name"] == "body.tex" else "") / doc["name"]).write_text(doc["text"], encoding="utf-8")
        git(bridge, "commit", "-q", "-am", "Update on Overleaf.")
        git(bridge, "push", "-q", "origin", "HEAD:main")

    opened = await Review(paper, fake, link).open()
    try:
        await opened.settled()
        await fake.coauthor("d-body", [{"p": 0, "i": "New. "}])
        await wait_for(lambda: opened.read("tex/body.tex").startswith("New. "))
        snapshot()
        now = await (await opened.client.post("/overleaf/git/fetch")).json()
        assert (now["state"], now["uncommitted"]) == ("same", [])
        assert git(paper, "status", "--porcelain", "--untracked-files=no") == ""

        (paper / "fig.png").write_bytes(b"figure")
        git(paper, "add", "fig.png")
        git(paper, "commit", "-q", "-m", "figure")
        await fake.coauthor("d-main", [{"p": len(MAIN), "i": "\n% theirs"}])
        await wait_for(lambda: opened.read("main.tex").endswith("% theirs"))
        await opened.settled()
        snapshot()
        pushed = await opened.client.post("/overleaf/git/push")
        assert pushed.status == 200 and (await pushed.json())["state"] == "same"
        git(bridge, "pull", "-q", "--ff-only", "origin", "main")
        assert (bridge / "fig.png").read_bytes() == b"figure"
        assert (bridge / "main.tex").read_text(encoding="utf-8") == fake.docs["d-main"]["text"] == opened.read("main.tex")
        assert git(paper, "status", "--porcelain", "--untracked-files=no") == ""

        # While a file waits for a choice, Pull and Push wait too.
        fake.refuse_next = True
        opened.write("main.tex", opened.read("main.tex") + "\n% refused")
        await wait_for(lambda: opened.files()["main.tex"] == "stopped")
        refused = await opened.client.post("/overleaf/git/pull")
        assert refused.status == 409 and "main.tex" in (await refused.json())["error"]
    finally:
        await opened.close()


async def test_in_a_git_clone_an_edit_on_overleaf_waits_to_come_in_only_until_the_folder_holds_it(
        paper, overleaf_site, tmp_path_factory, monkeypatch) -> None:
    """The folder's own git push comes back from Overleaf as an edit there, which the folder
    already holds: once Overleaf's branch is read, nothing waits to be brought in. Someone
    else's edit waits until git pull brings it in."""
    import subprocess

    fake, link = overleaf_site
    for key, value in (("GIT_AUTHOR_NAME", "Someone"), ("GIT_COMMITTER_NAME", "Someone"),
                       ("GIT_AUTHOR_EMAIL", "someone@example.com"), ("GIT_COMMITTER_EMAIL", "someone@example.com")):
        monkeypatch.setenv(key, value)
    git = lambda folder, *args: subprocess.run(["git", "-C", str(folder), *args], check=True,
                                               capture_output=True, text=True).stdout
    bare = tmp_path_factory.mktemp("overleaf-git") / PROJECT_ID
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git(paper, "init", "-q", "-b", "main")
    git(paper, "add", "main.tex", "tex/body.tex")
    git(paper, "commit", "-q", "-m", "first")
    git(paper, "remote", "add", "overleaf", str(bare))
    git(paper, "push", "-q", "-u", "overleaf", "main")
    bridge = tmp_path_factory.mktemp("bridge") / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(bridge)], check=True, capture_output=True)

    def snapshot() -> None:
        """Overleaf's git server, read: a commit of the project as it is now."""
        git(bridge, "pull", "-q", "--ff-only", "origin", "main")
        for doc in fake.docs.values():
            (bridge / ("tex/" if doc["name"] == "body.tex" else "") / doc["name"]).write_text(doc["text"], encoding="utf-8")
        git(bridge, "commit", "-q", "-am", "Update on Overleaf.")
        git(bridge, "push", "-q", "origin", "HEAD:main")

    opened = await Review(paper, fake, link, sync=False).open()
    edited = lambda: [change["file"] for change in opened.server.overleaf_live.snapshot()["changes"]]
    try:
        await wait_for(lambda: opened.server.overleaf_live is not None
                       and opened.server.overleaf_live.snapshot()["status"] == "live")
        # git push from the folder, which Overleaf makes as an edit of its own.
        opened.write("tex/body.tex", "New. " + BODY)
        git(paper, "commit", "-q", "-am", "reworded")
        git(paper, "push", "-q", "overleaf", "main")
        await fake.coauthor("d-body", [{"p": 0, "i": "New. "}])
        await wait_for(lambda: edited() == ["tex/body.tex"])
        now = await (await opened.client.post("/overleaf/git/fetch")).json()
        assert now["state"] == "same" and edited() == []

        await fake.coauthor("d-main", [{"p": len(MAIN), "i": "\n% theirs"}])
        await wait_for(lambda: edited() == ["main.tex"])
        snapshot()
        now = await (await opened.client.post("/overleaf/git/fetch")).json()
        assert (now["state"], now["incoming"]) == ("behind", ["main.tex"]) and edited() == ["main.tex"]
        now = await (await opened.client.post("/overleaf/git/pull")).json()
        assert now["state"] == "same" and opened.read("main.tex").endswith("% theirs") and edited() == []
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


async def test_an_edit_that_reached_the_file_another_way_is_not_made_twice(paper, overleaf_site) -> None:
    """While no page was open, Overleaf's edit came into the file some other way, as a git
    pull brings it. The file already holds Overleaf's text: that is agreement, not an edit of
    its own to send back."""
    fake, link = overleaf_site
    opened = await Review(paper, fake, link).open()
    await opened.settled()
    await opened.close()
    await fake.coauthor("d-main", [{"p": MAIN.index("The placer"), "d": "The"}, {"p": MAIN.index("The placer"), "i": "Our"}])
    opened.write("main.tex", MAIN.replace("The placer", "Our placer"))
    opened = await Review(paper, fake, link).open()
    try:
        await opened.settled()
        assert opened.read("main.tex") == MAIN.replace("The placer", "Our placer") == fake.docs["d-main"]["text"]
        assert not [e for e in fake.sent if e["name"] == "applyOtUpdate"]
    finally:
        await opened.close()


async def test_edits_on_both_sides_to_one_place_while_apart_wait_for_a_choice(paper, overleaf_site) -> None:
    fake, link = overleaf_site
    opened = await Review(paper, fake, link).open()
    await opened.settled()
    await opened.close()
    await fake.coauthor("d-main", [{"p": MAIN.index("The placer"), "d": "The"}, {"p": MAIN.index("The placer"), "i": "Their"}])
    opened.write("main.tex", MAIN.replace("The placer", "Our placer"))
    opened = await Review(paper, fake, link).open()
    try:
        await wait_for(lambda: opened.files().get("main.tex") == "differs")
        assert opened.read("main.tex") == MAIN.replace("The placer", "Our placer")
        assert fake.docs["d-main"]["text"] == MAIN.replace("The placer", "Their placer")
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


async def test_live_edits_go_as_suggestions_while_overleaf_tracks_the_reviewers(paper, overleaf_site) -> None:
    """Overleaf's editor sends the reviewer's edits as suggestions while track changes is on
    for the reviewer's account, and so do the files kept the same live, as it is switched."""
    fake, link = overleaf_site
    fake.track_state = {"u-me": True, "u-co": False}
    review = await Review(paper, fake, link).open()
    try:
        await review.settled()
        review.write("main.tex", MAIN.replace("is fast", "is very fast"))
        await review.settled()
        assert re.fullmatch(r"[0-9a-f]{18}", fake.updates[-1]["meta"]["tc"])
        for ws in list(fake.sockets.values()):
            await fake.emit(ws, "toggle-track-changes", {"u-co": True})
        await wait_for(lambda: review.server.overleaf_live.tracking() is False)
        review.write("main.tex", MAIN.replace("is fast", "is fast now"))
        await review.settled()
        assert "meta" not in fake.updates[-1]
    finally:
        await review.close()


async def test_live_rejecting_suggested_edits_takes_their_words_back_on_both_sides(review) -> None:
    """A co-author changed "slow" to "fast" with track changes on: Overleaf's text holds "fast",
    and "slow" only as a suggested deletion. Rejecting both puts "slow" back, on Overleaf and in
    the file, by an edit marked as an undo, so Overleaf drops the suggestions."""
    fake = review.fake
    await review.settled()
    at = ot.js_len(MAIN[:MAIN.index("fast")])
    fake.docs["d-main"]["changes"] = [{"id": "s1", "op": {"p": at, "i": "fast"}},
                                      {"id": "s2", "op": {"p": at + 4, "d": "slow"}}]
    response = await review.client.post("/overleaf/changes/reject", json={"doc": "d-main", "ids": ["s1", "s2"]})
    assert response.status == 200, await response.text()
    await review.settled()
    assert review.read("main.tex") == MAIN.replace("fast", "slow")
    assert fake.updates[-1]["op"] == [{"p": at + 4, "i": "slow", "u": True}, {"p": at, "d": "fast", "u": True}]
    assert "meta" not in fake.updates[-1]
    # Suggestions Overleaf no longer has are not taken back twice.
    fake.docs["d-main"]["changes"] = []
    response = await review.client.post("/overleaf/changes/reject", json={"doc": "d-main", "ids": ["s1", "s2"]})
    assert response.status == 409 and "no longer has" in (await response.json())["error"]


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


# ----- Connecting from the page -----

async def test_the_page_connects_a_project_and_turns_each_switch_in_the_config(paper, overleaf_site) -> None:
    from tex_mcp_web.server import _overleaf_link

    fake, link = overleaf_site
    config = paper / ".tex-mcp-web.yaml"
    config.write_text("main: main.tex\n# the reviewer's note stays\n", encoding="utf-8")
    server = TexMcpWebServer(load_config(config))
    server.OVERLEAF_LIVE_GRACE = 0
    client = TestClient(TestServer(server.app))
    await client.start_server()
    overleaf.save_cookie(fake.cookie)
    page = await client.ws_connect("/ws")
    try:
        status = await (await client.get("/overleaf")).json()
        assert status["project"] is None and status["settable"] is True
        # Nothing may send before a project is named, and only Overleaf's own site is named.
        assert (await client.post("/overleaf/connect", json={"sync": True})).status == 400
        assert (await client.post("/overleaf/connect",
                                  json={"project": f"https://evil.example/project/{PROJECT_ID}"})).status == 400
        assert _overleaf_link(f"https://overleaf.com/project/{PROJECT_ID}?a=1") == \
            f"https://www.overleaf.com/project/{PROJECT_ID}"

        response = await client.post("/overleaf/connect", json={"project": link})
        assert response.status == 200
        assert (await response.json())["project"] == link
        assert config.read_text() == f"main: main.tex\n# the reviewer's note stays\noverleaf: {link}\n"
        await wait_for(lambda: fake.sockets)            # the open page now sits in the project
        assert server.overleaf_sync is None

        status = await (await client.post("/overleaf/connect", json={"sync": True})).json()
        assert status["sync"] is True and status["live"] is True and "overleaf_sync: true\n" in config.read_text()
        await wait_for(lambda: server.overleaf_sync is not None and len(server.overleaf_sync.docs) == 2)

        status = await (await client.post("/overleaf/connect", json={"project": None})).json()
        assert status["project"] is None and status["sync"] is False and status["live"] is False
        assert config.read_text() == "main: main.tex\n# the reviewer's note stays\n"
        await wait_for(lambda: server.overleaf_sync is None)
    finally:
        await page.close()
        await client.close()


# ----- New threads -----

@pytest.mark.parametrize("live", [False, True])
async def test_a_thread_drafted_for_overleaf_opens_there_on_its_lines_when_sent(paper, overleaf_site, live) -> None:
    """A thread the agent drafted is a comment with no entries. Sent, it opens on Overleaf as
    Overleaf's editor opens one: its first message, then an edit that changes no text and puts
    it on the lines it sits on here. The send is the reviewer's first entry, and a reading
    afterwards knows it as the one sent from here. By hand and live alike."""
    from tex_mcp_web.comments import DraftRange, OverleafDraft, ResolvedSource, SourceRangeAnchor, capture_source_selector

    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=live).open()
    try:
        if live:
            await opened.settled()
        else:
            await wait_for(lambda: opened.server.overleaf_live is not None and opened.server.overleaf_live.status == "live")
        store = opened.server.comments
        drafted = store.add_overleaf_thread(
            SourceRangeAnchor(file="tex/body.tex", line_start=2, line_end=2), OverleafDraft("Enough?"),
            ResolvedSource(file="tex/body.tex", line_start=2, line_end=2),
            capture_source_selector(paper / "tex" / "body.tex", 2, 2),
            {"tex/body.tex": (paper / "tex" / "body.tex").read_text()})
        assert drafted.thread == [] and fake.threads == {}
        response = await opened.client.post(f"/comments/{drafted.id}/reply",
                                            json={"text": "Is this enough?", "overleaf": True})
        assert response.status == 200, await response.text()
        [thread] = fake.threads
        assert [m["content"] for m in fake.threads[thread]["messages"]] == ["Is this enough?"]
        await wait_for(lambda: fake.docs["d-body"]["comments"])
        [placed] = fake.docs["d-body"]["comments"]
        assert placed["t"] == thread and _units(BODY, placed["p"], placed["p"] + placed["n"]) == "More body text here."
        assert fake.docs["d-body"]["text"] == BODY
        sent = store.get(drafted.id)
        assert [entry.text for entry in sent.thread] == ["Is this enough?"] and sent.overleaf_draft is None
        assert sent.thread[0].overleaf is not None
        if not live:
            assert (await opened.client.post("/overleaf/comments")).status == 200
            await asyncio.sleep(0.3)
            assert [entry.text for entry in store.get(drafted.id).thread] == ["Is this enough?"]
            assert len(store.list()) == 1
    finally:
        await opened.close()


async def test_what_was_said_about_a_drafted_thread_stays_here_when_it_opens_on_overleaf(paper, overleaf_site) -> None:
    """The reviewer and the agent talk a drafted thread over here. Sent, the thread opens on
    Overleaf with the draft alone, and the talk stays in it here, before the message sent."""
    from tex_mcp_web.comments import OverleafDraft, ResolvedSource, SourceRangeAnchor, capture_source_selector

    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        await wait_for(lambda: opened.server.overleaf_live is not None and opened.server.overleaf_live.status == "live")
        store = opened.server.comments
        drafted = store.add_overleaf_thread(
            SourceRangeAnchor(file="tex/body.tex", line_start=2, line_end=2), OverleafDraft("Enough?"),
            ResolvedSource(file="tex/body.tex", line_start=2, line_end=2),
            capture_source_selector(paper / "tex" / "body.tex", 2, 2),
            {"tex/body.tex": (paper / "tex" / "body.tex").read_text()})
        response = await opened.client.post(f"/comments/{drafted.id}/reply", json={"text": "Make it a question."})
        assert response.status == 200, await response.text()
        store.reply(drafted.id, "Drafted it as one.", author="agent")
        response = await opened.client.post(f"/comments/{drafted.id}/reply",
                                            json={"text": "Is this enough?", "overleaf": True})
        assert response.status == 200, await response.text()
        [thread] = fake.threads
        assert [m["content"] for m in fake.threads[thread]["messages"]] == ["Is this enough?"]
        sent = store.get(drafted.id)
        assert [(entry.text, entry.overleaf is not None) for entry in sent.thread] == [
            ("Make it a question.", False), ("Drafted it as one.", False), ("Is this enough?", True)]
        assert sent.overleaf_draft is None
    finally:
        await opened.close()


async def test_a_thread_the_reviewer_started_for_overleaf_opens_there_with_what_is_sent(paper, overleaf_site) -> None:
    """The reviewer starts a thread for Overleaf on source lines, telling the agent what to say.
    Sent, here with no draft from the agent, it opens on Overleaf with the message sent alone,
    and what the reviewer told the agent stays here."""
    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        await wait_for(lambda: opened.server.overleaf_live is not None and opened.server.overleaf_live.status == "live")
        response = await opened.client.post("/comments", json={
            "anchor": {"kind": "source_range", "file": "tex/body.tex", "line_start": 2, "line_end": 2,
                       "column_start": 0, "column_end": 4},
            "text": "ask if this is enough, politely", "overleaf": True})
        assert response.status == 201, await response.text()
        started = await response.json()
        response = await opened.client.post(f"/comments/{started['id']}/reply",
                                            json={"text": "Would this be enough?", "overleaf": True})
        assert response.status == 200, await response.text()
        [thread] = fake.threads
        assert [m["content"] for m in fake.threads[thread]["messages"]] == ["Would this be enough?"]
        await wait_for(lambda: fake.docs["d-body"]["comments"])
        sent = opened.server.comments.get(started["id"])
        assert [(entry.text, entry.overleaf is not None) for entry in sent.thread] == [
            ("ask if this is enough, politely", False), ("Would this be enough?", True)]
    finally:
        await opened.close()


async def test_a_drafted_thread_whose_lines_overleaf_lacks_is_not_opened_at_all(paper, overleaf_site) -> None:
    """The lines are looked for on Overleaf before anything is written there, so a thread
    whose words Overleaf lacks leaves no message behind, and keeps its draft here."""
    from tex_mcp_web.comments import DraftRange, OverleafDraft, ResolvedSource, SourceRangeAnchor, capture_source_selector

    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        await wait_for(lambda: opened.server.overleaf_live is not None and opened.server.overleaf_live.status == "live")
        opened.write("tex/body.tex", BODY + "\nWritten here only.")
        drafted = opened.server.comments.add_overleaf_thread(
            SourceRangeAnchor(file="tex/body.tex", line_start=4, line_end=4), OverleafDraft("New?"),
            ResolvedSource(file="tex/body.tex", line_start=4, line_end=4),
            capture_source_selector(paper / "tex" / "body.tex", 4, 4),
            {"tex/body.tex": (paper / "tex" / "body.tex").read_text()})
        response = await opened.client.post(f"/comments/{drafted.id}/reply", json={"text": "New?", "overleaf": True})
        assert response.status == 409
        assert "not in tex/body.tex on Overleaf yet" in (await response.json())["error"]
        assert fake.threads == {} and not fake.docs["d-body"]["comments"]
        kept = opened.server.comments.get(drafted.id)
        assert kept.thread == [] and kept.overleaf_draft.text == "New?"

        # One whose draft describes words the lines no longer hold, changed after it was
        # drafted, says so.
        stale = opened.server.comments.add_overleaf_thread(
            SourceRangeAnchor(file="tex/body.tex", line_start=1, line_end=1),
            OverleafDraft("Reworded.", [DraftRange("Words gone.", ResolvedSource(file="tex/body.tex", line_start=1, line_end=1),
                                                   capture_source_selector(paper / "tex" / "body.tex", 1, 1))]),
            ResolvedSource(file="tex/body.tex", line_start=1, line_end=1),
            capture_source_selector(paper / "tex" / "body.tex", 1, 1),
            {"tex/body.tex": (paper / "tex" / "body.tex").read_text()})
        response = await opened.client.post(f"/comments/{stale.id}/reply", json={"text": "Reworded.", "overleaf": True})
        assert response.status == 409 and (await response.json())["error"] == (
            "The lines of tex/body.tex this reply describes changed after it was drafted. Ask the agent to draft it again.")
        assert fake.threads == {}
    finally:
        await opened.close()


# ----- By hand, in a folder that is no git clone -----

async def hand_ready(opened: "Review") -> dict:
    """The page is in the project and has read where the files stand."""
    await wait_for(lambda: opened.server.overleaf_hand is not None and opened.server.overleaf_hand.read_once)
    return (await (await opened.client.post("/overleaf/files/read")).json())


async def test_by_hand_files_change_only_when_get_or_send_is_pressed(paper, overleaf_site) -> None:
    """Without git and without live, Get writes Overleaf's edits into the files and Send makes
    the files' edits there, each as a small edit, and nothing moves either way until pressed."""
    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        assert await hand_ready(opened) == {"read": True, "get": [], "send": [], "choose": [], "gone": [], "dropped": []}
        await fake.coauthor("d-body", [{"p": 0, "i": "New. "}])
        opened.write("main.tex", MAIN.replace("fast", "quick"))
        await asyncio.sleep(0.3)
        assert opened.read("tex/body.tex") == BODY and fake.docs["d-main"]["text"] == MAIN
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert (now["get"], now["send"]) == (["tex/body.tex"], ["main.tex"])
        edited = lambda: [change["file"] for change in opened.server.overleaf_live.snapshot()["changes"]]
        assert edited() == ["tex/body.tex"]

        now = await (await opened.client.post("/overleaf/files/get")).json()
        assert opened.read("tex/body.tex") == "New. " + BODY and (now["get"], now["send"]) == ([], ["main.tex"])
        assert edited() == []
        assert fake.docs["d-main"]["text"] == MAIN
        now = await (await opened.client.post("/overleaf/files/send")).json()
        assert fake.docs["d-main"]["text"] == MAIN.replace("fast", "quick") and now["send"] == []
        [sent] = [op for doc, _, op in fake.applied if doc == "d-main"]
        assert sum(len(step.get("i", "") + step.get("d", "")) for step in sent) == len("fast" + "quick")
    finally:
        await opened.close()


async def test_by_hand_get_brings_both_sides_together_and_a_clash_waits_for_a_choice(paper, overleaf_site) -> None:
    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        await hand_ready(opened)
        # Each side changed another place: Get writes both, and Send then sends this side's.
        await fake.coauthor("d-body", [{"p": 0, "i": "Theirs. "}])
        opened.write("tex/body.tex", BODY.replace("end.", "end. Mine."))
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["get"] == ["tex/body.tex"] and now["send"] == []
        now = await (await opened.client.post("/overleaf/files/get")).json()
        both = "Theirs. " + BODY.replace("end.", "end. Mine.")
        assert opened.read("tex/body.tex") == both and now["send"] == ["tex/body.tex"]
        await opened.client.post("/overleaf/files/send")
        assert fake.docs["d-body"]["text"] == both

        # Both changed one place: nothing is written, the file waits for a choice, and Send
        # leaves it.
        await fake.coauthor("d-body", [{"p": 0, "d": "Theirs."}, {"p": 0, "i": "Ours?"}])
        opened.write("tex/body.tex", both.replace("Theirs.", "Mine?"))
        now = await (await opened.client.post("/overleaf/files/get")).json()
        assert now["choose"] == ["tex/body.tex"] and opened.read("tex/body.tex") == both.replace("Theirs.", "Mine?")
        await opened.client.post("/overleaf/files/send")
        assert fake.docs["d-body"]["text"].startswith("Ours?")
        files = (await (await opened.client.get("/overleaf/live")).json())["files"]
        assert [(file["path"], file["status"]) for file in files] == [("tex/body.tex", "differs")]
        assert (await opened.client.post("/overleaf/sync", json={"path": "tex/body.tex", "choice": "here"})).status == 200
        now = await (await opened.client.post("/overleaf/files/send")).json()
        assert fake.docs["d-body"]["text"] == both.replace("Theirs.", "Mine?") and now == {
            "read": True, "get": [], "send": [], "choose": [], "gone": [], "dropped": []}
        assert list((paper / ".tex-mcp-web" / "overleaf-sync" / "kept").rglob("body.tex.overleaf.*"))
        # Comments come in only once nothing is left to Get or Send.
        opened.write("main.tex", MAIN + "% here")
        await opened.client.post("/overleaf/files/read")
        refused = await opened.client.post("/overleaf/comments")
        assert refused.status == 409 and "Press Send first" in (await refused.json())["error"]
    finally:
        await opened.close()


async def test_by_hand_files_pick_up_where_live_left_off(paper, overleaf_site) -> None:
    """Both ways of following keep one record per file, so a file followed live and then by
    hand, or the other way, is not read as differing."""
    fake, link = overleaf_site
    live = await Review(paper, fake, link).open()
    try:
        await live.settled()
        await fake.coauthor("d-body", [{"p": 0, "i": "Live. "}])
        await wait_for(lambda: live.read("tex/body.tex").startswith("Live. "))
    finally:
        await live.close()
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        assert await hand_ready(opened) == {"read": True, "get": [], "send": [], "choose": [], "gone": [], "dropped": []}
        await fake.coauthor("d-main", [{"p": 0, "i": "% hand\n"}])
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["get"] == ["main.tex"]
    finally:
        await opened.close()
    live = await Review(paper, fake, link).open()
    try:
        await live.settled()
        assert live.read("main.tex").startswith("% hand\n")
    finally:
        await live.close()


# ----- Uploaded files, without git -----

async def test_by_hand_get_and_send_move_uploaded_files_whole(paper, overleaf_site) -> None:
    """A figure replaced on Overleaf is counted by Get and brought in whole, one replaced here
    is counted by Send and uploaded, and one replaced on both sides waits for a choice. Overleaf
    names each by the hash of its bytes, so nothing is downloaded to compare."""
    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        assert await hand_ready(opened) == {"read": True, "get": [], "send": [], "choose": [], "gone": [], "dropped": []}
        fake.replace("fig/plot.png", b"\x89PNG figure two")
        await fake.broadcast("reciveNewFile", "f2", {})
        await wait_for(lambda: opened.server.overleaf_live.tree()[0]["fig/plot.png"]["hash"]
                       == overleaf_sync.blob_sha(b"\x89PNG figure two"), timeout=10)
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["get"] == ["fig/plot.png"]
        await opened.client.post("/overleaf/files/get")
        assert (paper / "fig" / "plot.png").read_bytes() == b"\x89PNG figure two"

        (paper / "fig" / "plot.png").write_bytes(b"\x89PNG figure three")
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["send"] == ["fig/plot.png"]
        now = await (await opened.client.post("/overleaf/files/send")).json()
        assert fake.uploads["fig/plot.png"]["data"] == b"\x89PNG figure three" and now["send"] == now["get"] == []

        fake.replace("fig/plot.png", b"\x89PNG theirs")
        await fake.broadcast("reciveNewFile", "f2", {})
        await wait_for(lambda: opened.server.overleaf_live.tree()[0]["fig/plot.png"]["hash"]
                       == overleaf_sync.blob_sha(b"\x89PNG theirs"), timeout=10)
        (paper / "fig" / "plot.png").write_bytes(b"\x89PNG mine")
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["choose"] == ["fig/plot.png"]
        assert (await opened.client.post("/overleaf/sync", json={"path": "fig/plot.png", "choice": "overleaf"})).status == 200
        assert (paper / "fig" / "plot.png").read_bytes() == b"\x89PNG theirs"
        assert list((paper / ".tex-mcp-web" / "overleaf-sync" / "kept").rglob("plot.png.here.*"))
    finally:
        await opened.close()


async def test_live_uploaded_files_follow_by_themselves(review) -> None:
    fake = review.fake
    await review.settled()
    fake.replace("fig/plot.png", b"\x89PNG from Overleaf")
    await fake.broadcast("reciveNewFile", "f2", {})
    await wait_for(lambda: (review.folder / "fig" / "plot.png").read_bytes() == b"\x89PNG from Overleaf", timeout=15)
    review.write("main.tex", review.read("main.tex"))
    (review.folder / "fig" / "plot.png").write_bytes(b"\x89PNG from here")
    await wait_for(lambda: fake.uploads["fig/plot.png"]["data"] == b"\x89PNG from here", timeout=15)


# ----- The agent -----

async def test_the_agent_reads_where_the_folder_stands_and_brings_overleafs_edits_in(paper, overleaf_site) -> None:
    """overleaf() names only what differs, read_comments(new=True) carries one line when
    Overleaf has edits this folder lacks, and the agent brings them in itself. Sending
    without git stays the reviewer's."""
    pytest.importorskip("mcp")
    from tex_mcp_web.mcp_server import create_server

    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        await hand_ready(opened)

        class Shared:
            config_path = paper / ".tex-mcp-web.yaml"
            watch_dir = paper

        class Binding:
            def require_shared(self):
                return Shared

            def base_url(self):
                return str(opened.client.make_url("")).rstrip("/")

        mcp = create_server(Binding())

        async def call(tool, **arguments):
            return json.loads((await mcp.call_tool(tool, arguments))[0].text)

        assert await call("overleaf") == {"by": "hand"}
        assert "overleaf" not in await call("read_comments", new=True)
        await fake.coauthor("d-body", [{"p": 0, "i": "New. "}])
        opened.write("main.tex", MAIN.replace("fast", "quick"))
        assert await call("overleaf") == {"by": "hand", "get": ["tex/body.tex"], "send": ["main.tex"]}
        told = await call("read_comments", new=True)
        assert told["overleaf"].startswith("Overleaf has edits to tex/body.tex this folder lacks.")
        assert await call("overleaf", action="get") == {"by": "hand", "send": ["main.tex"]}
        assert opened.read("tex/body.tex") == "New. " + BODY
        refused = await call("overleaf", action="push")
        assert "the reviewer sends from the page" in refused["error"]
        assert fake.docs["d-main"]["text"] == MAIN
    finally:
        await opened.close()


async def test_by_hand_send_adds_new_files_of_the_paper_and_leaves_others(paper, overleaf_site) -> None:
    """A new file here matching overleaf_add, or of a kind the project holds, is counted by Send
    and uploaded into its folder, made on Overleaf when missing. A text file becomes a
    document there. The paper's compiled PDF and files of other kinds stay here."""
    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    try:
        await hand_ready(opened)
        (paper / "tex" / "intro.tex").write_text("\\section{Intro}\nNew.\n", encoding="utf-8")
        (paper / "fig" / "more").mkdir()
        (paper / "fig" / "more" / "arch.png").write_bytes(b"\x89PNG new figure")
        (paper / "main.pdf").write_bytes(b"%PDF compiled")
        (paper / "main.aux").write_text("aux", encoding="utf-8")
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["send"] == ["fig/more/arch.png", "tex/intro.tex"]
        now = await (await opened.client.post("/overleaf/files/send")).json()
        assert fake.uploads["fig/more/arch.png"]["data"] == b"\x89PNG new figure"
        assert "fig/more" in fake.folders
        added = next(doc for doc in fake.docs.values() if doc["name"] == "intro.tex")
        assert (added["folder"], added["text"]) == ("tex", "\\section{Intro}\nNew.\n")
        assert now["send"] == []
        # The session joins again and finds both on Overleaf, the same as here.
        await wait_for(lambda: "tex/intro.tex" in opened.server.overleaf_live.editor()[1].values(), timeout=10)
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now == {"read": True, "get": [], "send": [], "choose": [], "gone": [], "dropped": []}
        assert not any("main.pdf" in path or "main.aux" in path for path in fake.uploads)
    finally:
        await opened.close()


async def test_by_hand_a_file_deleted_on_one_side_is_listed_and_deleted_only_when_chosen(paper, overleaf_site) -> None:
    """A file deleted here stays on Overleaf, and one deleted on Overleaf stays here: each is
    listed with a choice, to delete it on the other side too or to bring it back."""
    fake, link = overleaf_site
    opened = await Review(paper, fake, link, sync=False).open()
    choose = lambda path, choice: opened.client.post("/overleaf/sync", json={"path": path, "choice": choice})
    listed = lambda: opened.client.get("/overleaf/live")
    try:
        await hand_ready(opened)
        (paper / "tex" / "body.tex").unlink()
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert (now["gone"], now["get"], now["send"]) == (["tex/body.tex"], [], [])
        files = (await (await listed()).json())["files"]
        assert [(file["path"], file["status"]) for file in files] == [("tex/body.tex", "gone")]
        assert (await choose("tex/body.tex", "restore")).status == 200
        assert opened.read("tex/body.tex") == BODY
        (paper / "tex" / "body.tex").unlink()
        await opened.client.post("/overleaf/files/read")
        assert (await choose("tex/body.tex", "delete-there")).status == 200
        assert fake.deleted == [("doc", "d-body")] and "d-body" not in fake.docs

        # Deleted on Overleaf: listed until chosen, and nothing is deleted here meanwhile.
        del fake.uploads["fig/plot.png"]
        await fake.broadcast("removeEntity", "u-1", "editor")
        await wait_for(lambda: "fig/plot.png" not in opened.server.overleaf_live.tree()[0], timeout=10)
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["dropped"] == ["fig/plot.png"] and (paper / "fig" / "plot.png").exists()
        assert (await choose("fig/plot.png", "delete-here")).status == 200
        assert not (paper / "fig" / "plot.png").exists()
        assert list((paper / ".tex-mcp-web" / "overleaf-sync" / "kept").rglob("plot.png.here.*"))
        del fake.docs["d-main"]
        await fake.broadcast("removeEntity", "d-main", "editor")
        await wait_for(lambda: "main.tex" not in opened.server.overleaf_live.editor()[1].values(), timeout=10)
        now = await (await opened.client.post("/overleaf/files/read")).json()
        assert now["dropped"] == ["main.tex"]
        assert (await choose("main.tex", "restore")).status == 200
        assert any(doc["name"] == "main.tex" and doc["text"] == MAIN for doc in fake.docs.values())
    finally:
        await opened.close()


async def test_live_a_file_deleted_on_one_side_waits_for_a_choice(review) -> None:
    fake = review.fake
    await review.settled()
    (review.folder / "tex" / "body.tex").unlink()
    await wait_for(lambda: review.files().get("tex/body.tex") == "gone", timeout=10)
    assert "d-body" in fake.docs
    response = await review.client.post("/overleaf/sync", json={"path": "tex/body.tex", "choice": "restore"})
    assert response.status == 200 and review.read("tex/body.tex") == BODY
    await wait_for(lambda: review.files().get("tex/body.tex") == "synced", timeout=10)
    del fake.uploads["fig/plot.png"]
    await fake.broadcast("removeEntity", "u-1", "editor")
    await wait_for(lambda: any(file["path"] == "fig/plot.png" and file["status"] == "dropped"
                               for file in review.server.overleaf_sync.snapshot()), timeout=15)
    assert (review.folder / "fig" / "plot.png").exists()


async def test_live_a_new_file_of_the_paper_goes_up_by_itself(review) -> None:
    fake = review.fake
    await review.settled()
    (review.folder / "fig" / "new.png").write_bytes(b"\x89PNG a new figure")
    (review.folder / "main.log").write_text("log", encoding="utf-8")
    await wait_for(lambda: fake.uploads.get("fig/new.png", {}).get("data") == b"\x89PNG a new figure", timeout=15)
    await asyncio.sleep(0.3)
    assert "main.log" not in fake.uploads and len([path for path in fake.uploads if path == "fig/new.png"]) == 1
    # Replaced here at once, before the session joins again: it follows, not waiting for a choice.
    (review.folder / "fig" / "new.png").write_bytes(b"\x89PNG the new figure, redrawn")
    await wait_for(lambda: fake.uploads["fig/new.png"]["data"] == b"\x89PNG the new figure, redrawn", timeout=15)


async def test_live_a_file_changed_here_while_overleafs_comes_is_not_written_over(review, monkeypatch) -> None:
    fake = review.fake
    await review.settled()
    downloading = overleaf.download_file

    async def slowly(link, cookie, file_id, **kw):     # the file here changes while it comes
        (review.folder / "fig" / "plot.png").write_bytes(b"\x89PNG drawn here meanwhile")
        return await downloading(link, cookie, file_id, **kw)

    monkeypatch.setattr(overleaf, "download_file", slowly)
    fake.replace("fig/plot.png", b"\x89PNG from Overleaf")
    await fake.broadcast("reciveNewFile", "f2", {})
    await wait_for(lambda: any(file["path"] == "fig/plot.png" and file["status"] == "differs"
                               for file in review.server.overleaf_sync.snapshot()), timeout=15)
    assert (review.folder / "fig" / "plot.png").read_bytes() == b"\x89PNG drawn here meanwhile"
