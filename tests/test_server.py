"""Smoke tests for the web server.

We don't run latexmk here; instead we instantiate the server, drive the
HTTP API directly, and check that comments + paper state plumbing works.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from tex_mcp_web.config import Config
from tex_mcp_web.compiler import CompileResult
from tex_mcp_web.server import TexMcpWebServer


@pytest.fixture
def project(tmp_path: Path) -> Path:
    main = tmp_path / "paper.tex"
    main.write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\n"
        "\\label{sec:intro}\n"
        "Some prose with \\cite{ref1}.\n"
        "\\section{Methods}\n"
        "\\label{sec:methods}\n"
        "Some methods.\n"
        "\\end{document}\n"
    )
    (tmp_path / ".tex-mcp-web.yaml").write_text(
        "main: paper.tex\nauto_compile: false\n"
    )
    return tmp_path


@pytest.fixture
async def client(project: Path):
    cfg = Config(main="paper.tex", config_path=project / ".tex-mcp-web.yaml")
    server = TexMcpWebServer(cfg)
    # Trigger a structure parse without compiling
    from tex_mcp_web.structure import parse_structure
    server.structure = parse_structure(project)
    test_server = TestServer(server.app)
    test_client = TestClient(test_server)
    await test_client.start_server()
    try:
        yield test_client, server
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_paper_endpoint_returns_structure(client):
    tc, _ = client
    resp = await tc.get("/paper")
    assert resp.status == 200
    data = await resp.json()
    titles = {s["title"] for s in data["sections"]}
    assert {"Introduction", "Methods"} <= titles
    # v0.5.0: labels/citations/inputs are no longer exposed; the agent
    # greps for them. Only sections come back.
    assert "labels" not in data
    assert "citations" not in data
    assert "inputs" not in data
    # Sections carry the label they're attached to.
    methods = next(s for s in data["sections"] if s["title"] == "Methods")
    assert methods["label"] == "sec:methods"
    assert data["auto_compile"] is False


@pytest.mark.asyncio
async def test_auto_compile_mode_persists_and_broadcasts(client):
    tc, server = client
    ws = await tc.ws_connect("/ws")
    initial = await ws.receive_json()
    assert initial["auto_compile"] is False

    resp = await tc.put("/auto-compile", json={"enabled": True})

    assert resp.status == 200
    assert await resp.json() == {"auto_compile": True}
    assert server.config.auto_compile is True
    data = yaml.safe_load(server.config.config_path.read_text())
    assert data["auto_compile"] is True
    assert await ws.receive_json() == {"type": "auto_compile", "enabled": True}
    assert (await (await tc.get("/paper")).json())["auto_compile"] is True
    await ws.close()


@pytest.mark.asyncio
async def test_auto_compile_mode_rejects_non_boolean(client):
    tc, _ = client
    resp = await tc.put("/auto-compile", json={"enabled": "true"})
    assert resp.status == 400
    assert await resp.json() == {"error": "enabled must be true or false"}


@pytest.mark.asyncio
async def test_file_change_respects_auto_compile_mode(project):
    server = TexMcpWebServer(
        Config(main="paper.tex", config_path=project / ".tex-mcp-web.yaml")
    )
    server.do_compile = AsyncMock()

    await server.on_file_change(str(project / "paper.tex"))
    server.do_compile.assert_not_awaited()

    server.config.auto_compile = True
    await server.on_file_change(str(project / "paper.tex"))
    server.do_compile.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_compile_still_runs_when_auto_compile_is_off(client):
    tc, server = client
    result = CompileResult(success=False)
    server.do_compile = AsyncMock(return_value=result)

    resp = await tc.post("/compile")

    assert resp.status == 200
    server.do_compile.assert_awaited_once()


@pytest.mark.asyncio
async def test_root_exposes_auto_compile_control(client):
    tc, _ = client
    html = await (await tc.get("/")).text()
    assert 'id="auto-compile-btn"' in html
    assert "Auto: Off" in html
    assert "viewer.js?v=comment-submit-state" in html


@pytest.mark.asyncio
async def test_overlapping_compile_requests_share_one_build(project, monkeypatch):
    cfg = Config(main="paper.tex", config_path=project / ".tex-mcp-web.yaml")
    server = TexMcpWebServer(cfg)
    release = asyncio.Event()
    calls = 0

    async def fake_compile_tex(*args, **kwargs):
        nonlocal calls
        calls += 1
        await release.wait()
        return CompileResult(success=False)

    monkeypatch.setattr("tex_mcp_web.server.compile_tex", fake_compile_tex)
    first = asyncio.create_task(server.do_compile())
    await asyncio.sleep(0)
    second = asyncio.create_task(server.do_compile())
    await asyncio.sleep(0)
    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert calls == 1
    assert first_result is second_result


@pytest.mark.asyncio
async def test_create_comment_with_suggestion(client):
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {"kind": "paper"},
            "text": "rephrase",
            "suggestion": {"old": "the original phrasing", "new": "the new phrasing"},
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["suggestion"] == {"old": "the original phrasing", "new": "the new phrasing"}


@pytest.mark.asyncio
async def test_empty_suggestion_omitted(client):
    """Both old and new empty -> no suggestion stored (avoid empty pair)."""
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {"kind": "paper"},
            "text": "x",
            "suggestion": {"old": "", "new": ""},
        },
    )
    data = await resp.json()
    assert "suggestion" not in data


@pytest.mark.asyncio
async def test_create_paper_anchor_comment(client):
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={"anchor": {"kind": "paper"}, "text": "abstract is too long"},
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["status"] == "open"
    assert data["anchor"] == {"kind": "paper"}
    assert data["thread"][0]["author"] == "human"
    assert data["thread"][0]["text"] == "abstract is too long"
    cid = data["id"]

    resp = await tc.get("/comments")
    listed = await resp.json()
    assert len(listed["comments"]) == 1
    assert listed["comments"][0]["id"] == cid


@pytest.mark.asyncio
async def test_create_section_anchor_resolves_to_source(client):
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {"kind": "section", "title": "Methods"},
            "text": "expand the methods section",
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["resolved_source"]["file"] == "paper.tex"
    assert data["resolved_source"]["line_start"] == 6  # \section{Methods} line


@pytest.mark.asyncio
async def test_create_source_range_anchor_captures_exact_selector(client):
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {"kind": "source_range", "file": "paper.tex", "line_start": 5, "line_end": 5},
            "text": "rephrase this citation",
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["source_selector"]["exact"] == "Some prose with \\cite{ref1}."
    assert "ref1" not in data["source_selector"]["prefix"]
    assert "ref1" not in data["source_selector"]["suffix"]


@pytest.mark.asyncio
async def test_resolving_stamps_the_comment_and_reopening_clears_it(client):
    tc, _ = client
    first = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "a"})).json())["id"]
    second = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "b"})).json())["id"]
    assert "resolved" not in await (await tc.get(f"/comments/{first}")).json()
    await tc.post(f"/comments/{first}/resolve", json={})
    await tc.post(f"/comments/{second}/resolve", json={})
    stamps = {c["id"]: c["resolved"] for c in (await (await tc.get("/comments?status=resolved")).json())["comments"]}
    assert stamps[first] <= stamps[second]
    await tc.post(f"/comments/{second}/reopen", json={})
    assert "resolved" not in await (await tc.get(f"/comments/{second}")).json()


@pytest.mark.asyncio
async def test_reopen_comment(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "x"})
    cid = (await resp.json())["id"]
    await tc.post(f"/comments/{cid}/resolve", json={})
    resp = await tc.post(f"/comments/{cid}/reopen", json={})
    assert resp.status == 200
    assert await resp.json() == {"id": cid, "status": "open"}
    stored = await (await tc.get(f"/comments/{cid}")).json()
    assert stored["status"] == "open"
    assert len(stored["thread"]) == 1  # reopening adds no entry
    assert (await tc.post("/comments/c-00000000/reopen", json={})).status == 404


@pytest.mark.asyncio
async def test_resolve_comment(client):
    tc, _ = client
    # Create
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "x"})
    cid = (await resp.json())["id"]

    # Resolve
    resp = await tc.post(
        f"/comments/{cid}/resolve",
        json={"summary": "rewrote the abstract", "edits": ["paper.tex:1-10"]},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data == {"id": cid, "status": "resolved"}
    stored = await (await tc.get(f"/comments/{cid}")).json()
    assert stored["thread"][-1]["author"] == "human"
    assert stored["thread"][-1]["edits"] == ["paper.tex:1-10"]


@pytest.mark.asyncio
async def test_editing_a_thread_entry_rewrites_it_in_place(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "chek this"})
    created = await resp.json()
    cid = created["id"]
    await tc.post(f"/comments/{cid}/reply", json={"text": "mine too"})

    resp = await tc.post(f"/comments/{cid}/edit", json={"index": 0, "text": "check this"})
    assert resp.status == 200
    saved = await resp.json()
    assert [entry["text"] for entry in saved["thread"]] == ["check this", "mine too"]
    assert saved["thread"][0]["at"] == created["thread"][0]["at"]

    assert (await tc.post(f"/comments/{cid}/edit", json={"index": 9, "text": "x"})).status == 400
    assert (await tc.post(f"/comments/{cid}/edit", json={"index": 0, "text": " "})).status == 400
    assert (await tc.post(f"/comments/{cid}/edit", json={"text": "x"})).status == 400


@pytest.mark.asyncio
async def test_agent_entries_are_not_editable_from_the_browser(client):
    tc, server = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "look"})
    cid = (await resp.json())["id"]
    server.comments.reply(cid, text="agent answer", author="agent")

    resp = await tc.post(f"/comments/{cid}/edit", json={"index": 1, "text": "rewritten"})
    assert resp.status == 400
    stored = await (await tc.get(f"/comments/{cid}")).json()
    assert stored["thread"][1]["text"] == "agent answer"


@pytest.mark.asyncio
async def test_closing_without_a_message_leaves_the_thread_alone(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "x"})
    cid = (await resp.json())["id"]

    resp = await tc.post(f"/comments/{cid}/resolve", json={})
    assert resp.status == 200
    stored = await (await tc.get(f"/comments/{cid}")).json()
    assert stored["status"] == "resolved"
    assert len(stored["thread"]) == 1



@pytest.mark.asyncio
async def test_list_comments_filters(client):
    tc, _ = client
    a = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "open"})).json())["id"]
    b = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "to resolve"})).json())["id"]
    await tc.post(f"/comments/{b}/resolve", json={"summary": "done"})

    resp = await tc.get("/comments?status=open")
    open_ids = {c["id"] for c in (await resp.json())["comments"]}
    assert open_ids == {a}

    resp = await tc.get("/comments?status=resolved")
    resolved_ids = {c["id"] for c in (await resp.json())["comments"]}
    assert resolved_ids == {b}


@pytest.mark.asyncio
async def test_invalid_anchor_returns_400(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "bogus"}, "text": "x"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_missing_text_returns_400(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_get_unknown_comment_returns_404(client):
    tc, _ = client
    resp = await tc.get("/comments/c-doesntexist")
    assert resp.status == 404


# ---------------------------------------------------------------------------
# /goto disambiguates section vs label and returns matched-but-no-page
# when SyncTeX is unavailable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goto_section_match_without_synctex(client):
    """No PDF compiled yet, so synctex_data is None.  /goto should still
    resolve a section title to a source location and return 200 with
    page=null instead of 404."""
    tc, _ = client
    resp = await tc.post("/goto", json={"section": "Methods"})
    assert resp.status == 200
    data = await resp.json()
    assert data["page"] is None
    assert data["file"] == "paper.tex"
    assert data["line"] == 6  # \section{Methods}


@pytest.mark.asyncio
async def test_goto_label_distinct_from_section_title(client):
    """Passing label='sec:methods' must match by label, not by title."""
    tc, _ = client
    resp = await tc.post("/goto", json={"label": "sec:methods"})
    assert resp.status == 200
    data = await resp.json()
    assert data["file"] == "paper.tex"
    assert data["line"] == 6


@pytest.mark.asyncio
async def test_goto_unknown_section_returns_404(client):
    tc, _ = client
    resp = await tc.post("/goto", json={"section": "Nonexistent"})
    assert resp.status == 404


@pytest.mark.asyncio
async def test_goto_page_passthrough(client):
    tc, _ = client
    resp = await tc.post("/goto", json={"page": 3})
    assert resp.status == 200
    assert (await resp.json())["page"] == 3


@pytest.mark.asyncio
async def test_goto_source_line_returns_highlight_bbox(client):
    from tex_mcp_web.synctex import PDFPosition, SyncTeXData

    tc, server = client
    server.synctex_data = SyncTeXData(
        pdf_to_source={},
        source_to_pdf={
            ("paper.tex", 5): [
                PDFPosition(page=1, x=72.0, y=144.0, width=180.0, height=10.0)
            ]
        },
        input_files={},
    )
    resp = await tc.post(
        "/goto", json={"file": "paper.tex", "line": 5}
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["page"] == 1
    assert data["bbox"] == [72.0, 144.0, 252.0, 156.0]


# ---------------------------------------------------------------------------
# /image endpoint — page / bbox / source / comment modes.
# ---------------------------------------------------------------------------


pytest.importorskip("fitz")


@pytest.fixture
async def client_with_pdf(project: Path):
    """Like *client*, but also produces a real PDF + CompileResult so /image works."""
    from datetime import datetime, timezone

    import fitz
    from tex_mcp_web.compiler import CompileResult
    from tex_mcp_web.config import Config
    from tex_mcp_web.server import TexMcpWebServer
    from tex_mcp_web.structure import parse_structure
    from aiohttp.test_utils import TestClient, TestServer

    cfg = Config(main="paper.tex", config_path=project / ".tex-mcp-web.yaml")
    server = TexMcpWebServer(cfg)
    server.structure = parse_structure(project)

    # Build a tiny real PDF the server can render.
    pdf_path = project / "paper.pdf"
    doc = fitz.open()
    page_one = doc.new_page()
    page_one.insert_text((72, 72), "Page 1 cites [1]")
    page_two = doc.new_page()
    page_two.insert_text((72, 72), "Page 2")
    page_two.insert_text((72, 110), "[1] First reference entry")
    page_two.insert_text((72, 134), "[2] Second reference entry")
    page_one = doc[0]
    page_one.insert_link(
        {
            "kind": fitz.LINK_GOTO,
            "from": page_one.search_for("[1]")[0],
            "page": 1,
            "to": fitz.Point(72, 110),
        }
    )
    doc.save(pdf_path)
    doc.close()
    server.last_result = CompileResult(
        success=True,
        output_file=pdf_path,
        timestamp=datetime.now(timezone.utc),
    )
    from tex_mcp_web.comments import pdf_digest
    server.pdf_digest = pdf_digest(pdf_path)

    test_server = TestServer(server.app)
    test_client = TestClient(test_server)
    await test_client.start_server()
    try:
        yield test_client, server
    finally:
        await test_client.close()


@pytest.mark.asyncio
async def test_goto_exact_pdf_quote_returns_rectangles(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.post("/goto", json={"quote": "Page 1"})
    assert resp.status == 200
    data = await resp.json()
    assert data["page"] == 1
    assert data["quote"] == "Page 1"
    assert len(data["bbox"]) == 4
    assert data["rects"]


@pytest.mark.asyncio
async def test_goto_unmatched_section_falls_back_to_pdf_quote(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.post("/goto", json={"section": "Page 2"})
    assert resp.status == 200
    data = await resp.json()
    assert data["page"] == 2
    assert data["quote"] == "Page 2"


@pytest.mark.asyncio
async def test_reference_preview_returns_cited_entry_text(client_with_pdf):
    tc, server = client_with_pdf
    pdf_path = server.last_result.output_file
    doc = fitz.open(pdf_path)
    source_rect = doc[0].get_links()[0]["from"]
    doc.close()
    bbox = ",".join(str(value) for value in source_rect)

    resp = await tc.get(f"/reference-preview?page=1&bbox={bbox}")

    assert resp.status == 200
    assert resp.content_type == "application/json"
    result = await resp.json()
    assert "[1] First reference entry" in result["text"]
    assert "[2] Second reference entry" not in result["text"]


@pytest.mark.asyncio
async def test_mcp_contract_is_typed_and_nonduplicative(tmp_path: Path):
    pytest.importorskip("mcp")
    from tex_mcp_web.mcp_client import ProjectBinding
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(ProjectBinding(tmp_path))
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert set(tools) == {"paper", "list_comments", "read_comments", "compile", "comment", "image", "section", "goto", "wait_review"}
    assert "list_comments(unanswered=True)" in mcp.instructions
    assert "read_comments(comment_ids=[...])" in mcp.instructions
    assert "compile() once" in mcp.instructions
    compile_description = " ".join((tools["compile"].description or "").split())
    assert "paper().auto_compile" in compile_description
    assert "watcher owns compilation" in compile_description

    descriptions = "\n".join(tool.description or "" for tool in tools.values())
    assert "source-search key" not in descriptions
    assert "only for rendered evidence" not in descriptions
    assert "100-200" not in descriptions

    assert tools["paper"].inputSchema["properties"] == {}
    assert tools["list_comments"].inputSchema["properties"]["status"]["enum"] == [
        "open", "resolved", "all"
    ]
    assert tools["read_comments"].inputSchema["properties"]["comment_ids"]["minItems"] == 1

    comment_schema = tools["comment"].inputSchema
    assert comment_schema["properties"]["action"]["enum"] == [
        "add", "reply", "delete"
    ]
    anchor_schema = comment_schema["properties"]["anchor"]["anyOf"][0]
    assert set(anchor_schema["discriminator"]["mapping"]) == {
        "paper", "section", "source_range", "area"
    }
    assert "author" not in comment_schema["properties"]

    image_schema = tools["image"].inputSchema["properties"]
    assert image_schema["page"]["anyOf"][0]["minimum"] == 1
    assert image_schema["bbox"]["anyOf"][0]["minItems"] == 4
    assert image_schema["bbox"]["anyOf"][0]["maxItems"] == 4
    assert image_schema["margin"]["minimum"] == 0

    assert set(tools["goto"].inputSchema["properties"]) == {"target"}


def _free_port() -> int:
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture
def bound_project(project: Path, monkeypatch):
    """A project bound to an MCP process, served on a port of its own."""
    from tex_mcp_web.mcp_client import ProjectBinding

    (project / ".tex-mcp-web.yaml").write_text(
        f"main: paper.tex\nauto_compile: false\nport: {_free_port()}\n"
    )
    monkeypatch.chdir(project)
    binding = ProjectBinding(project)
    yield binding
    binding.stop()


@pytest.mark.asyncio
async def test_mcp_tool_call_serves_the_viewer(bound_project, project):
    pytest.importorskip("mcp")
    import aiohttp

    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)
    paper = json.loads((await mcp.call_tool("paper", {}))[0][0].text)

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{paper['review_url']}/paper") as response:
            assert response.status == 200
            served = await response.json()
    assert Path(served["watch_dir"]).resolve() == project.resolve()


@pytest.mark.asyncio
async def test_mcp_comment_discovery_reads_only_selected_history(bound_project, project, monkeypatch):
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, PaperAnchor, ResolvedSource, SectionAnchor
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)

    async def call(name, **arguments):
        return json.loads((await mcp.call_tool(name, arguments))[0][0].text)

    moment = "2026-01-01T00:00:00+00:00"
    monkeypatch.setattr("tex_mcp_web.comments._now", lambda: moment)
    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    first = store.add(SectionAnchor("Methods"), "Check this method",
                      resolved_source=ResolvedSource("paper.tex", 6, 8))
    second = store.add(PaperAnchor(), "Check this paper")
    agent_only = store.add(PaperAnchor(), "An agent note", author="agent")

    paper = await call("paper")
    assert "comments" not in paper
    assert paper["comment_counts"] == {"open": 3, "resolved": 0, "unanswered": 2}
    assert paper["sections"]
    requests = (await call("list_comments", unanswered=True))["comments"]
    assert requests == [
        {"id": first.id, "status": "open", "kind": "section", "request": "Check this method",
         "thread_entries": 1, "last_human_at": moment},
        {"id": second.id, "status": "open", "kind": "paper", "request": "Check this paper",
         "thread_entries": 1, "last_human_at": moment},
    ]
    assert (await call("list_comments", since=moment))["comments"] == []

    moment = "2026-01-01T00:00:01+00:00"
    history = "Detailed explanation. " * 1000
    store.reply(first.id, history, author="agent")
    assert [c["id"] for c in (await call("list_comments", unanswered=True))["comments"]] == [second.id]
    assert await call("paper") == {**paper, "comment_counts": {"open": 3, "resolved": 0, "unanswered": 1}}
    assert history not in json.dumps(await call("list_comments"))

    moment = "2026-01-01T00:00:02+00:00"
    store.reply(first.id, "Please check the boundary too", author="human")
    requests = (await call("list_comments", unanswered=True, since="2026-01-01T09:00:01+09:00"))["comments"]
    assert len(requests) == 1
    assert requests[0]["id"] == first.id
    assert requests[0]["request"] == "Please check the boundary too"
    assert requests[0]["last_human_at"] == moment
    assert requests[0]["thread_entries"] == 3
    assert (await call("list_comments", since="2026-01-01T00:00:02"))["comments"] == []

    details = (await call("read_comments", comment_ids=[first.id]))["comments"]
    assert len(details) == 1
    assert details[0]["source"] == {"file": "paper.tex", "line_start": 6, "line_end": 8}
    assert [entry["text"] for entry in details[0]["replies"]] == [history, "Please check the boundary too"]
    assert "replies" not in (await call("read_comments", comment_ids=[second.id]))["comments"][0]
    assert [c["id"] for c in (await call("read_comments", comment_ids=[second.id, first.id]))["comments"]] == [second.id, first.id]
    assert "unique" in (await call("read_comments", comment_ids=[first.id, first.id]))["error"]
    assert "not found" in (await call("read_comments", comment_ids=[first.id, "missing"]))["error"]

    store.resolve(second.id, "", author="human")
    assert [c["id"] for c in (await call("list_comments", status="resolved"))["comments"]] == [second.id]
    all_comments = (await call("list_comments", status="all"))["comments"]
    assert len(all_comments) == 3
    assert next(c for c in all_comments if c["id"] == agent_only.id)["last_human_at"] is None
    assert (await call("paper"))["comment_counts"] == {"open": 2, "resolved": 1, "unanswered": 1}


@pytest.mark.asyncio
async def test_mcp_comment_and_section_runtime_contract(bound_project, project):
    pytest.importorskip("mcp")
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)

    paper = await mcp.call_tool("paper", {})
    assert json.loads(paper[0][0].text)["auto_compile"] is False

    added = await mcp.call_tool(
        "comment",
        {
            "action": "add",
            "text": "review this",
            "anchor": {"kind": "paper"},
        },
    )
    comment = json.loads(added[0][0].text)
    stored = json.loads((project / ".tex-mcp-web" / "comments.json").read_text())
    assert stored["version"] == 5
    assert stored["comments"][0]["thread"][0]["author"] == "agent"
    assert comment["comment"] == "review this"

    # A suggested rewrite is two top-level strings, so the LaTeX in them is written as it
    # is rather than serialized by hand inside an object; one without the other is refused.
    suggested = await mcp.call_tool(
        "comment",
        {
            "action": "add",
            "text": "이 문장을 바꾸세요",
            "anchor": {"kind": "paper"},
            "suggestion_old": "the original phrasing",
            "suggestion_new": "the new phrasing: 새 문장",
        },
    )
    with_suggestion = json.loads(suggested[0][0].text)
    assert with_suggestion["suggestion"] == {"old": "the original phrasing", "new": "the new phrasing: 새 문장"}
    half = await mcp.call_tool(
        "comment",
        {"action": "add", "text": "x", "anchor": {"kind": "paper"}, "suggestion_new": "only new"},
    )
    assert "go together" in half[0][0].text
    await mcp.call_tool("comment", {"action": "delete", "id": with_suggestion["id"]})

    second_added = await mcp.call_tool(
        "comment",
        {
            "action": "add",
            "text": "review second",
            "anchor": {"kind": "paper"},
        },
    )
    second = json.loads(second_added[0][0].text)
    section = await mcp.call_tool(
        "section", {"name": "Methods", "include_image": True}
    )
    assert json.loads(section[0][0].text)["section"]["file"] == "paper.tex"
    assert json.loads(section[0][1].text) == {
        "error": "section image requested but no PDF exists"
    }


@pytest.mark.asyncio
async def test_image_full_page(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.get("/image?page=1&dpi=72")
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "image/png"
    body = await resp.read()
    assert body.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_image_bbox(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.get("/image?page=1&bbox=60,60,200,100&dpi=72")
    assert resp.status == 200
    body = await resp.read()
    assert body.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_image_margin_controls_bbox_context(client_with_pdf):
    tc, _ = client_with_pdf
    exact_resp = await tc.get("/image?page=1&bbox=60,60,200,100&dpi=72&margin=0")
    expanded_resp = await tc.get(
        "/image?page=1&bbox=60,60,200,100&dpi=72&margin=24"
    )
    assert exact_resp.status == 200
    assert expanded_resp.status == 200
    exact = fitz.Pixmap(await exact_resp.read())
    expanded = fitz.Pixmap(await expanded_resp.read())
    assert expanded.width > exact.width
    assert expanded.height > exact.height


@pytest.mark.asyncio
async def test_image_rejects_invalid_margin(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.get("/image?page=1&bbox=60,60,200,100&margin=nope")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_image_rejects_negative_margin(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.get("/image?page=1&bbox=60,60,200,100&margin=-1")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_image_requires_one_target(client_with_pdf):
    tc, _ = client_with_pdf
    # Neither page nor source nor comment.
    resp = await tc.get("/image")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_image_invalid_bbox(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.get("/image?page=1&bbox=garbage")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_image_comment_with_area_anchor(client_with_pdf):
    tc, server = client_with_pdf
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {
                "kind": "area",
                "page": 1,
                "bbox": [60, 60, 200, 100],
                "pdf_digest": server.pdf_digest,
            },
            "text": "look at this",
        },
    )
    cid = (await resp.json())["id"]
    resp = await tc.get(f"/image?comment={cid}&dpi=72")
    assert resp.status == 200
    body = await resp.read()
    assert body.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_text_selection_without_reverse_sync_remains_pdf_native(client_with_pdf):
    tc, server = client_with_pdf
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {
                "kind": "text_selection",
                "quote": "Page 1",
                "selection": {
                    "page": 1,
                    "bbox": [70, 60, 110, 80],
                    "rects": [[70, 60, 110, 80]],
                },
                "pdf_digest": server.pdf_digest,
            },
            "text": "review this text",
        },
    )
    assert resp.status == 201
    data = await resp.json()
    assert "resolved_source" not in data
    assert "source_selector" not in data
    assert data["anchor"]["selection"]["rects"]


@pytest.mark.asyncio
async def test_text_selection_canonicalizes_text_engine_difference(client_with_pdf):
    tc, server = client_with_pdf
    from tex_mcp_web.comments import locate_pdf_quote

    selection = locate_pdf_quote(server.last_result.output_file, "Page 1")
    assert selection is not None
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {
                "kind": "text_selection",
                "quote": "Page1",
                "selection": selection.to_dict(),
                "pdf_digest": server.pdf_digest,
            },
            "text": "review this text",
        },
    )

    assert resp.status == 201
    data = await resp.json()
    assert data["anchor"]["quote"] == "Page 1"


@pytest.mark.asyncio
async def test_text_selection_records_verified_source(
    client_with_pdf, monkeypatch
):
    tc, server = client_with_pdf
    monkeypatch.setattr(
        "tex_mcp_web.server.selection_to_source_range",
        lambda pdf, page, rects, watch_dir: ("paper.tex", 5, 5),
    )
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {
                "kind": "text_selection",
                "quote": "Page 1",
                "selection": {
                    "page": 1,
                    "bbox": [70, 60, 110, 80],
                    "rects": [[70, 60, 110, 80]],
                },
                "pdf_digest": server.pdf_digest,
            },
            "text": "review this text",
        },
    )

    assert resp.status == 201
    data = await resp.json()
    assert data["resolved_source"] == {
        "file": "paper.tex",
        "line_start": 5,
        "line_end": 5,
    }
    assert data["source_selector"]["exact"] == "Some prose with \\cite{ref1}."


@pytest.mark.asyncio
async def test_text_selection_rejects_old_pdf_digest(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {
                "kind": "text_selection",
                "quote": "Page 1",
                "selection": {
                    "page": 1,
                    "bbox": [70, 60, 110, 80],
                    "rects": [[70, 60, 110, 80]],
                },
                "pdf_digest": "old",
            },
            "text": "review this text",
        },
    )
    assert resp.status == 409


@pytest.mark.asyncio
async def test_image_comment_paper_anchor_rejects(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.post(
        "/comments",
        json={"anchor": {"kind": "paper"}, "text": "global"},
    )
    cid = (await resp.json())["id"]
    resp = await tc.get(f"/image?comment={cid}")
    assert resp.status == 400
    err = await resp.json()
    assert "paper" in err["error"].lower()


@pytest.mark.asyncio
async def test_image_unknown_comment(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.get("/image?comment=c-doesntexist")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_image_no_pdf_returns_404(client):
    """Without a successful compile (no last_result.output_file), /image is 404."""
    tc, _ = client
    resp = await tc.get("/image?page=1")
    assert resp.status == 404


def test_clamp_dpi_in_range():
    from tex_mcp_web.server import _clamp_dpi
    assert _clamp_dpi(150) == 150
    assert _clamp_dpi("96") == 96


def test_clamp_dpi_caps_extreme_values():
    """Without clamping, ?dpi=10000 would let any caller allocate
    multi-gigabyte pixmaps and OOM the daemon."""
    from tex_mcp_web.server import _clamp_dpi
    assert _clamp_dpi(10000) == 600
    assert _clamp_dpi(0) == 36
    assert _clamp_dpi("99999") == 600


@pytest.mark.asyncio
async def test_image_extreme_dpi_clamped(client_with_pdf):
    """A request with dpi=99999 must succeed (clamped down) rather than OOM."""
    tc, _ = client_with_pdf
    resp = await tc.get("/image?page=1&dpi=99999")
    assert resp.status == 200
    body = await resp.read()
    assert body.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# Pure helpers (factored out of the request handlers)
# ---------------------------------------------------------------------------


def test_parse_goto_target_recognizes_page_form():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("p3", default_file="paper.tex") == {"page": 3}


def test_parse_goto_target_recognizes_bare_line_with_default_file():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("42", default_file="paper.tex") == {
        "line": 42,
        "file": "paper.tex",
    }


def test_parse_goto_target_recognizes_file_line():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("intro.tex:7", default_file="paper.tex") == {
        "file": "intro.tex",
        "line": 7,
    }


def test_parse_goto_target_falls_back_to_section():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("Methods", default_file="paper.tex") == {
        "section": "Methods"
    }


def test_resolve_section_to_source_handles_eof(project):
    from tex_mcp_web.server import resolve_section_to_source
    from tex_mcp_web.structure import parse_structure

    structure = parse_structure(project)
    rs = resolve_section_to_source(
        structure, project, title="Methods", label="sec:methods"
    )
    assert rs is not None
    assert rs.file == "paper.tex"
    assert rs.line_start == 6  # \section{Methods}
    # End line should be the last line of the file (computed from EOF).
    total_lines = len((project / "paper.tex").read_text().splitlines())
    assert rs.line_end == total_lines


def test_resolve_section_to_source_returns_none_for_unknown(project):
    from tex_mcp_web.server import resolve_section_to_source
    from tex_mcp_web.structure import parse_structure

    structure = parse_structure(project)
    assert (
        resolve_section_to_source(structure, project, title="Nonexistent", label=None)
        is None
    )


# ---------------------------------------------------------------------------
# Call agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_button_wakes_a_parked_waiter_and_keeps_an_early_press(client) -> None:
    """The reviewer's press is an interrupt, not a message the agent must be listening for.
    The server keeps the press count and the consumption watermark, so a press made while
    nobody waits answers the next wait at once. The watermark moves only on the waiter's
    ack, so until the ack lands the press is offered again."""
    from tex_mcp_web.comments import PaperAnchor

    test_client, server = client

    # Presses with nobody parked are kept, and coalesce into the next wait.
    reply = await (await test_client.post("/review-request")).json()
    assert reply == {"calls": 1, "delivered": False}
    reply = await (await test_client.post("/review-request")).json()
    assert reply == {"calls": 2, "delivered": False}
    early = await test_client.get("/wait-review")
    assert early.status == 200
    assert early.headers["X-Press"] == "2"
    line = await early.text()
    assert line.startswith("[review] reviewer called (press #2)")
    assert "list_comments(unanswered=True)" in line
    # The line counts threads whose last word is the reviewer's: a count of open threads
    # sent an agent to read one it had already answered.
    assert "no unanswered comments" in line
    asked = server.comments.add(PaperAnchor(), "Is this right?")
    assert "1 unanswered comments" in server._review_line()
    server.comments.reply(asked.id, "Yes, checked.", author="agent")
    assert "no unanswered comments" in server._review_line()

    # Not acked yet (the line may never have reached the harness), so it is offered again.
    again = await test_client.get("/wait-review")
    assert again.status == 200
    assert again.headers["X-Press"] == "2"

    # Acked means consumed: the same waiter restarted parks rather than replaying.
    acked = await (await test_client.post("/wait-review/ack?upto=2")).json()
    assert acked == {"calls": 2, "consumed": 2}
    server.REVIEW_POLL_TIMEOUT = 0.05
    assert (await test_client.get("/wait-review")).status == 204
    server.REVIEW_POLL_TIMEOUT = TexMcpWebServer.REVIEW_POLL_TIMEOUT

    # A parked waiter is released by the press.
    parked = asyncio.ensure_future(test_client.get("/wait-review"))
    for _ in range(50):
        if server.review_waiters == 1:
            break
        await asyncio.sleep(0.02)
    assert server.review_waiters == 1
    reply = await (await test_client.post("/review-request")).json()
    assert reply == {"calls": 3, "delivered": True}
    released = await parked
    assert released.status == 200
    assert (await released.text()).startswith("[review] reviewer called (press #3)")
    assert server.review_waiters == 0

    # An ack from a script that outlived a server restart clamps to what exists and a
    # stale repeat never moves the watermark back.
    over = await (await test_client.post("/wait-review/ack?upto=9")).json()
    assert over == {"calls": 3, "consumed": 3}
    stale = await (await test_client.post("/wait-review/ack?upto=1")).json()
    assert stale == {"calls": 3, "consumed": 3}
    server.REVIEW_POLL_TIMEOUT = 0.05
    assert (await test_client.get("/wait-review")).status == 204


@pytest.mark.asyncio
async def test_presses_survive_a_server_restart(client) -> None:
    """The counters persist, so a press made before a restart is still on offer to the
    next server, and an ack it received is remembered too."""
    test_client, server = client
    await test_client.post("/review-request")
    reborn = TexMcpWebServer(server.config)
    assert (reborn.review_calls, reborn.review_consumed) == (1, 0)
    reborn.REVIEW_POLL_TIMEOUT = 0.05
    second_client = TestClient(TestServer(reborn.app))
    await second_client.start_server()
    try:
        offered = await second_client.get("/wait-review")
        assert offered.status == 200
        assert offered.headers["X-Press"] == "1"
        await second_client.post("/wait-review/ack?upto=1")
        third = TexMcpWebServer(server.config)
        assert (third.review_calls, third.review_consumed) == (1, 1)
    finally:
        await second_client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("codex", [False, True])
async def test_wait_review(bound_project, project, monkeypatch, codex):
    """The tool writes the waiter next to the comment store; run against the live review
    server, it prints one line for a press already waiting and acks it."""
    pytest.importorskip("mcp")
    import subprocess
    import time
    import os

    import aiohttp

    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)
    context = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=SimpleNamespace(name="claude-code"))))
    monkeypatch.setattr(mcp, "get_context", lambda: context)
    result = json.loads((await mcp.call_tool("wait_review", {}))[0][0].text)
    script = Path(result["script"])
    assert script == project / ".tex-mcp-web" / "wait-review.sh"
    assert script.stat().st_mode & 0o111
    assert "Monitor" in result["how"]
    assert "write_stdin" not in result["how"]
    for name in ("codex-mcp-client", "other-client"):
        context.session.client_params.clientInfo.name = name
        selected = json.loads((await mcp.call_tool("wait_review", {}))[0][0].text)
        assert "Monitor" not in selected["how"]
        assert ("codex queue" in selected["how"]) == (name == "codex-mcp-client")
    tool = next(t for t in await mcp.list_tools() if t.name == "wait_review")
    assert "ctx" not in tool.inputSchema["properties"]
    subprocess.run(["sh", "-n", str(script)], check=True)

    base = bound_project.base_url()
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{base}/review-request") as response:
            assert (await response.json()) == {"calls": 1, "delivered": False}

    args = []
    if codex:
        stub = project / "codex"
        stub.write_text("#!/bin/sh\n"
                        '[ "$1" = queue ] && [ "$2" = --thread ] && [ "$3" = test-thread ] && [ "$4" = --message ] || exit 2\n'
                        '[ -f "$0.ready" ] || { touch "$0.failed"; exit 1; }\n'
                        'printf "%s\\n" "$5" | tail -n +2\n')
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{project}:{os.environ['PATH']}")
        args = ["--codex", "test-thread"]
    waiter = subprocess.Popen(["sh", str(script), *args], stdout=subprocess.PIPE, text=True)
    try:
        if codex:
            deadline = time.monotonic() + 5
            while not (project / "codex.failed").exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert (project / "codex.failed").exists()
            state_path = project / ".tex-mcp-web" / "review-state.json"
            assert json.loads(state_path.read_text())["consumed"] == 0
            (project / "codex.ready").touch()
        line = waiter.stdout.readline()
        assert line.startswith("[review] reviewer called (press #1)")
        state_path = project / ".tex-mcp-web" / "review-state.json"
        for _ in range(100):
            if json.loads(state_path.read_text()) == {"calls": 1, "consumed": 1}:
                break
            time.sleep(0.05)
        assert json.loads(state_path.read_text()) == {"calls": 1, "consumed": 1}
    finally:
        waiter.kill()
        waiter.wait()
