"""Overleaf comments the server fetches itself, with the reviewer's own session cookie."""

import os
import stat
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tex_mcp_web import overleaf
from tex_mcp_web.config import Config, load_config
from tex_mcp_web.server import TexMcpWebServer

PROJECT_ID = "0123456789abcdef01234567"
PAPER = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section{Introduction}\n"
    "Some prose with an aside.\n"
    "\\input{tex/body}\n"
    "\\end{document}\n"
)
BODY = "Body text.\nMore body text here.\n"


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.delenv(overleaf.COOKIE_ENV, raising=False)
    return home


# ----- The cookie: the reviewer's own, kept where only they can read it -----

def test_the_cookie_is_kept_in_the_reviewers_config_folder_readable_by_them_alone(config_home: Path) -> None:
    assert overleaf.read_cookie() is None
    overleaf.save_cookie("overleaf_session2=s%3Aabc.def")
    path = config_home / "tex-mcp-web" / "overleaf-cookie"
    assert overleaf.cookie_path() == path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert overleaf.read_cookie() == ("overleaf_session2", "s%3Aabc.def")
    overleaf.forget_cookie()
    assert overleaf.read_cookie() is None and not path.exists()


def test_a_cookie_is_taken_as_pasted_from_a_browser(config_home: Path) -> None:
    assert overleaf.parse_cookie("  s%3Aabc.def \n") == ("overleaf_session2", "s%3Aabc.def")
    assert overleaf.parse_cookie("overleaf_session2=s%3Aabc") == ("overleaf_session2", "s%3Aabc")
    assert overleaf.parse_cookie("GCLB=x; overleaf_session2=s%3Aabc; other=y") == ("overleaf_session2", "s%3Aabc")
    assert overleaf.parse_cookie("overleaf.sid=s%3Aself") == ("overleaf.sid", "s%3Aself")
    for wrong in ("", "   ", "GCLB=x; other=y", "two words"):
        with pytest.raises(ValueError):
            overleaf.parse_cookie(wrong)


def test_the_environment_names_a_cookie_before_the_file(config_home: Path, monkeypatch) -> None:
    overleaf.save_cookie("s%3Afile")
    monkeypatch.setenv(overleaf.COOKIE_ENV, "s%3Aenv")
    assert overleaf.read_cookie() == ("overleaf_session2", "s%3Aenv")
    assert overleaf.cookie_source() == "environment"


def test_a_project_link_names_the_server_and_the_project() -> None:
    assert overleaf.project_of(f"https://www.overleaf.com/project/{PROJECT_ID}") == (
        "https://www.overleaf.com", PROJECT_ID)
    assert overleaf.project_of(f"https://www.overleaf.com/project/{PROJECT_ID}/") == (
        "https://www.overleaf.com", PROJECT_ID)
    assert overleaf.project_of(f"http://127.0.0.1:8080/project/{PROJECT_ID}") == (
        "http://127.0.0.1:8080", PROJECT_ID)
    # The cookie is a password: it goes over plain HTTP to this machine only.
    for wrong in (f"http://www.overleaf.com/project/{PROJECT_ID}", "https://www.overleaf.com/project/123",
                  "https://www.overleaf.com/", "not a link"):
        with pytest.raises(ValueError):
            overleaf.project_of(wrong)


def test_the_config_names_the_overleaf_project(tmp_path: Path) -> None:
    config = tmp_path / ".tex-mcp-web.yaml"
    config.write_text(f"main: paper.tex\noverleaf: https://www.overleaf.com/project/{PROJECT_ID}\n")
    assert load_config(config).overleaf == f"https://www.overleaf.com/project/{PROJECT_ID}"
    assert load_config(config).to_dict()["overleaf"] == f"https://www.overleaf.com/project/{PROJECT_ID}"
    config.write_text("main: paper.tex\noverleaf: https://www.overleaf.com/\n")
    with pytest.raises(ValueError):
        load_config(config)


# ----- A stand-in for Overleaf's own routes -----

class FakeOverleaf:
    """Overleaf's editor routes for one project, open to one session cookie."""

    def __init__(self) -> None:
        self.cookie = "s%3Agood"
        self.requests: list[str] = []
        self.threads = {
            "t1": {"messages": [
                {"user": {"first_name": "A.", "last_name": "Reviewer"}, "content": "Which prose?"},
                {"user": {"first_name": "Co", "last_name": "Author"}, "content": "The intro."}]},
            "t2": {"resolved": True, "messages": [{"user": {"first_name": "A."}, "content": "Done."}]},
            "t3": {"messages": [{"user": {"first_name": "B."}, "content": "More of it?"}]},
            "t4": {"messages": [{"user": {}, "content": "No longer here."}]},
        }
        paper_remote = PAPER.replace("an aside", "a longer aside")
        self.docs = {
            "d1": ("paper.tex", paper_remote, [
                {"id": "c1", "op": {"c": "prose", "p": paper_remote.index("prose"), "t": "t1"}},
                {"id": "c2", "op": {"c": "Some", "p": paper_remote.index("Some"), "t": "t2"}},
                {"id": "c4", "op": {"c": "a longer aside", "p": paper_remote.index("a longer"), "t": "t4"}},
            ]),
            "d2": ("body.tex", BODY, [
                {"id": "c3", "op": {"c": "body text", "p": BODY.index("body text"), "t": "t3"}},
            ]),
            "d3": ("unused.tex", "Nothing.\n", []),
        }
        app = web.Application(middlewares=[self._signed_in])
        app.router.add_get(f"/project/{PROJECT_ID}/threads", self._threads)
        app.router.add_get(f"/project/{PROJECT_ID}/ranges", self._ranges)
        app.router.add_get(f"/project/{PROJECT_ID}/entities", self._entities)
        app.router.add_get(f"/Project/{PROJECT_ID}/doc/{{doc}}/download", self._download)
        self.app = app

    @web.middleware
    async def _signed_in(self, request: web.Request, handler):
        self.requests.append(request.path)
        if request.cookies.get("overleaf_session2") != self.cookie:
            raise web.HTTPFound("/login")
        return await handler(request)

    async def _threads(self, request: web.Request) -> web.Response:
        return web.json_response(self.threads)

    async def _ranges(self, request: web.Request) -> web.Response:
        return web.json_response([{"id": doc, "ranges": {"comments": comments} if comments else {}}
                                  for doc, (_, _, comments) in self.docs.items()])

    async def _entities(self, request: web.Request) -> web.Response:
        return web.json_response({"project_id": PROJECT_ID, "entities": [
            {"path": "/paper.tex", "type": "doc"}, {"path": "/tex/body.tex", "type": "doc"},
            {"path": "/unused.tex", "type": "doc"}, {"path": "/fig/a.png", "type": "file"}]})

    async def _download(self, request: web.Request) -> web.Response:
        name, text, _ = self.docs[request.match_info["doc"]]
        return web.Response(text=text, content_type="text/plain",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})


@pytest.fixture
async def fake_overleaf():
    fake = FakeOverleaf()
    server = TestServer(fake.app)
    await server.start_server()
    try:
        yield fake, f"http://127.0.0.1:{server.port}/project/{PROJECT_ID}"
    finally:
        await server.close()


async def test_the_export_is_built_from_overleafs_own_routes(fake_overleaf) -> None:
    fake, link = fake_overleaf
    export = await overleaf.fetch_export(link, ("overleaf_session2", fake.cookie))
    assert export["threads"]["t1"]["messages"][0] == {"user": {"name": "A. Reviewer"}, "content": "Which prose?"}
    assert export["threads"]["t2"]["resolved"] is True
    by_thread = {entry["thread_id"]: entry for entry in export["comments"]}
    assert by_thread["t1"] == {"thread_id": "t1", "pathname": "/paper.tex", "line": 4, "col": 5,
                               "anchored_text": "prose"}
    # A file is named by its download and placed by the project's path list.
    assert by_thread["t3"]["pathname"] == "/tex/body.tex" and by_thread["t3"]["line"] == 2
    # A file with nothing on it is not downloaded.
    assert not any(path.endswith("/d3/download") for path in fake.requests)


async def test_a_cookie_overleaf_turns_away_says_to_paste_a_new_one(fake_overleaf) -> None:
    _, link = fake_overleaf
    with pytest.raises(overleaf.SignedOut):
        await overleaf.fetch_export(link, ("overleaf_session2", "s%3Aexpired"))


# ----- The review page -----

@pytest.fixture
async def review(tmp_path: Path, config_home: Path, fake_overleaf):
    fake, link = fake_overleaf
    project = tmp_path / "paper"
    (project / "tex").mkdir(parents=True)
    (project / "paper.tex").write_text(PAPER)
    (project / "tex" / "body.tex").write_text(BODY)
    (project / ".tex-mcp-web.yaml").write_text(f"main: paper.tex\noverleaf: {link}\n")
    server = TexMcpWebServer(load_config(project / ".tex-mcp-web.yaml"))
    client = TestClient(TestServer(server.app))
    await client.start_server()
    try:
        yield client, server, fake, project
    finally:
        await client.close()


async def test_the_page_brings_in_overleaf_comments_with_the_reviewers_cookie(review, config_home: Path) -> None:
    client, server, fake, _ = review
    status = await (await client.get("/overleaf")).json()
    assert status == {"project": fake_link(server), "cookie": None, "sync": False, "settable": True, "git": None, "waiting": 0, "imported": [], "unplaced": {}}

    response = await client.post("/overleaf/comments")
    assert response.status == 401
    assert "cookie" in (await response.json())["error"]

    assert (await client.post("/overleaf/cookie", json={"cookie": "no cookie here=1"})).status == 400
    response = await client.post("/overleaf/cookie", json={"cookie": f"overleaf_session2={fake.cookie}"})
    assert await response.json() == {"project": fake_link(server), "cookie": "file", "sync": False, "settable": True, "git": None, "waiting": 0, "imported": [], "unplaced": {}}
    # Saved, never sent back.
    assert fake.cookie not in await (await client.get("/overleaf")).text()

    counts = await (await client.post("/overleaf/comments")).json()
    assert counts == {"added": 3, "replies": 0, "resolved": 1, "unplaced": 1}
    comments = {c.thread[0].text.split("] ", 1)[1].split("\n")[0]: c for c in server.comments.list()}
    assert comments["Which prose?"].anchor.to_dict()["file"] == "paper.tex"
    assert comments["Which prose?"].anchor.to_dict()["line_start"] == 4
    assert [e.text for e in comments["Which prose?"].thread][1] == "[Overleaf · Co Author] The intro."
    assert comments["More of it?"].anchor.to_dict()["file"] == "tex/body.tex"
    assert comments["No longer here."].anchor.kind == "paper"

    fake.threads["t1"]["messages"].append({"user": {"first_name": "A.", "last_name": "Reviewer"}, "content": "Thanks."})
    counts = await (await client.post("/overleaf/comments")).json()
    assert counts == {"added": 0, "replies": 1, "resolved": 1, "unplaced": 0}

    fake.cookie = "s%3Arotated"
    response = await client.post("/overleaf/comments")
    assert response.status == 401 and "cookie" in (await response.json())["error"]

    assert (await (await client.delete("/overleaf/cookie")).json())["cookie"] is None
    assert overleaf.read_cookie() is None


async def test_without_a_project_in_the_config_the_page_says_where_to_name_it(tmp_path: Path, config_home: Path) -> None:
    (tmp_path / "paper.tex").write_text(PAPER)
    server = TexMcpWebServer(Config(main="paper.tex", config_path=tmp_path / ".tex-mcp-web.yaml"))
    client = TestClient(TestServer(server.app))
    await client.start_server()
    try:
        assert await (await client.get("/overleaf")).json() == {"project": None, "cookie": None, "sync": False, "settable": True, "git": None, "waiting": 0, "imported": [], "unplaced": {}}
        response = await client.post("/overleaf/comments")
        assert response.status == 409 and "overleaf:" in (await response.json())["error"]
    finally:
        await client.close()


def fake_link(server: TexMcpWebServer) -> str:
    return server.config.overleaf


def test_the_cookie_file_is_not_world_readable_even_with_a_loose_umask(config_home: Path) -> None:
    old = os.umask(0)
    try:
        overleaf.save_cookie("s%3Aloose")
    finally:
        os.umask(old)
    assert stat.S_IMODE(overleaf.cookie_path().stat().st_mode) == 0o600
