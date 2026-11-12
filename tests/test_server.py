"""Smoke tests for the web server.

We don't run latexmk here; instead we instantiate the server, drive the
HTTP API directly, and check that comments + paper state plumbing works.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fitz
import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from tex_mcp_web.comments import (
    STORE_VERSION,
    ResolvedSource,
    SourceRangeAnchor,
    SourceSelector,
    SuggestedEdit,
)
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
        "main: paper.tex\n"
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


@pytest.mark.asyncio
async def test_file_change_broadcasts_without_compiling(project):
    server = TexMcpWebServer(
        Config(main="paper.tex", config_path=project / ".tex-mcp-web.yaml")
    )
    server.do_compile = AsyncMock()
    server.broadcast = AsyncMock()

    await server.on_file_change(str(project / "paper.tex"))
    server.do_compile.assert_not_awaited()
    change = server.broadcast.await_args.args[0]
    assert change["type"] == "source_changed"
    assert change["path"] == "paper.tex"
    assert len(change["revision"]) == 64


@pytest.mark.asyncio
async def test_config_edit_applies_without_restart(project):
    from tex_mcp_web.watcher import Watcher

    config_path = project / ".tex-mcp-web.yaml"
    server = TexMcpWebServer(Config(main="paper.tex", config_path=config_path))
    server.watcher = Watcher(project, ["*.tex"], [], AsyncMock(), roots=[])
    server.do_compile = AsyncMock()
    server.broadcast = AsyncMock()

    config_path.write_text("main: paper.tex\nwatch:\n- '*.tex'\n- 'notes/*.md'\n")
    await server.on_file_change(str(config_path))

    assert server.config.watch == ["*.tex", "notes/*.md"]
    assert server.watcher.watch_patterns == ["*.tex", "notes/*.md"]
    assert server.broadcast.await_args.args[0] == {"type": "config_reloaded"}
    server.do_compile.assert_not_awaited()


@pytest.mark.asyncio
async def test_config_edit_to_main_compiles_the_new_paper(project):
    config_path = project / ".tex-mcp-web.yaml"
    server = TexMcpWebServer(Config(main="paper.tex", config_path=config_path))
    server.do_compile = AsyncMock()
    server.broadcast = AsyncMock()

    (project / "other.tex").write_text("\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    config_path.write_text("main: other.tex\n")
    await server.on_file_change(str(config_path))

    assert server.main_file == project / "other.tex"
    server.do_compile.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["main: paper.tex\nport: 9000\n", "main: paper.tex\ndir: sub\n", "main: [\n"])
async def test_config_edit_the_server_cannot_take_is_reported(project, text):
    (project / "sub").mkdir()
    config_path = project / ".tex-mcp-web.yaml"
    server = TexMcpWebServer(Config(main="paper.tex", config_path=config_path))
    server.do_compile = AsyncMock()
    server.broadcast = AsyncMock()

    config_path.write_text(text)
    await server.on_file_change(str(config_path))

    assert server.broadcast.await_args.args[0]["type"] == "config_error"
    assert (server.config.port, server.config.dir) == (8765, None)
    server.do_compile.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_compile_runs(client):
    tc, server = client
    result = CompileResult(success=False)
    server.do_compile = AsyncMock(return_value=result)

    resp = await tc.post("/compile")

    assert resp.status == 200
    server.do_compile.assert_awaited_once()


@pytest.mark.asyncio
async def test_root_serves_the_viewer(client):
    tc, server = client
    html = await (await tc.get("/")).text()
    assert [f'data-view="{view}"' in html for view in ("pdf", "source", "split")] == [True] * 3
    # The page names its files under the tag of their newest change, so a browser that
    # cached the old ones fetches the new ones, and the files under any tag are the same.
    tag = server.static_tag()
    assert f'"/static/{tag}/style.css"' in html and f'"/static/{tag}/viewer.js"' in html
    assert f'"/static/{tag}/ace/ace.js?v=1.44.0"' in html
    tagged = await tc.get(f"/static/{tag}/style.css")
    assert tagged.status == 200 and tagged.headers["Cache-Control"] == "no-cache"
    assert await tagged.text() == await (await tc.get("/static/style.css")).text()
    assert (await tc.get(f"/static/{tag}/../server.py")).status == 404


@pytest.mark.asyncio
async def test_source_api_lists_and_reads_watched_files(client, project):
    tc, server = client
    (project / "references.bib").write_text("@book{x}\n", encoding="utf-8")
    (project / "notes.tmp").write_text("not watched\n", encoding="utf-8")
    (project / "ignored.tex").write_text("private draft\n", encoding="utf-8")
    server.config.ignore = ["ignored.tex"]

    listed = await (await tc.get("/sources")).json()
    assert listed == {"main_file": "paper.tex", "files": ["paper.tex", "references.bib"]}

    response = await tc.get("/source", params={"path": "paper.tex"})
    assert response.status == 200
    source = await response.json()
    assert source["path"] == "paper.tex"
    assert source["text"] == (project / "paper.tex").read_text(encoding="utf-8")
    assert len(source["revision"]) == 64
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_source_save_requires_current_revision(client, project):
    tc, _ = client
    path = project / "paper.tex"
    path.chmod(0o640)
    opened = await (await tc.get("/source", params={"path": "paper.tex"})).json()
    edited = opened["text"].replace("Some methods.", "Revised methods.")

    response = await tc.put(
        "/source",
        params={"path": "paper.tex"},
        json={"text": edited, "revision": opened["revision"]},
    )
    assert response.status == 200
    saved = await response.json()
    assert path.read_text(encoding="utf-8") == edited
    assert saved["revision"] != opened["revision"]
    assert path.stat().st_mode & 0o777 == 0o640

    path.write_text("external edit\n", encoding="utf-8")
    stale = await tc.put(
        "/source",
        params={"path": "paper.tex"},
        json={"text": "browser overwrite\n", "revision": saved["revision"]},
    )
    assert stale.status == 409
    assert (await stale.json())["error"] == "source changed after it was opened"
    assert path.read_text(encoding="utf-8") == "external edit\n"


@pytest.mark.asyncio
async def test_source_api_rejects_outside_ignored_and_non_utf8_files(client, project):
    tc, server = client
    outside = project.parent / "outside.tex"
    outside.write_text("outside\n", encoding="utf-8")
    (project / "outside-link.tex").symlink_to(outside)
    (project / "ignored.tex").write_text("ignored\n", encoding="utf-8")
    (project / "binary.tex").write_bytes(b"\xff")
    server.config.ignore = ["ignored.tex"]

    assert (await tc.get("/source", params={"path": "../outside.tex"})).status == 400
    assert (await tc.get("/source", params={"path": "outside-link.tex"})).status == 403
    assert (await tc.get("/source", params={"path": "ignored.tex"})).status == 403
    assert (await tc.get("/source", params={"path": "missing.tex"})).status == 404
    assert (await tc.get("/source", params={"path": "binary.tex"})).status == 415


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
            "anchor": {"kind": "source_range", "file": "paper.tex",
                       "line_start": 5, "line_end": 5},
            "text": "rephrase",
            "suggestion": {"old": "Some prose", "new": "Clear prose"},
        },
    )
    assert resp.status == 201
    data = await resp.json()
    # The page offers one piece of the file the comment sits in.
    assert data["suggestion"] == {
        "file": "paper.tex",
        "changes": [{"old": "Some prose", "new": "Clear prose"}],
    }


@pytest.mark.asyncio
async def test_a_suggestion_needs_a_comment_whose_place_in_the_source_is_known(client):
    """A proposal is applied to a file, so a whole-paper note cannot carry one."""
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={"anchor": {"kind": "paper"}, "text": "rephrase",
              "suggestion": {"old": "a", "new": "b"}},
    )
    assert resp.status == 400
    assert "place in the source is known" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_a_reviewer_replacement_on_a_section_goes_to_its_source(client):
    """A section, like PDF text, points into the source through what it resolves to, so
    the replacement a reviewer types beside it lands in that file, once it is found there."""
    tc, _ = client
    resp = await tc.post(
        "/comments",
        json={"anchor": {"kind": "section", "title": "Methods"}, "text": "clearer",
              "suggestion": {"old": "Some methods.", "new": "Two careful methods."}},
    )
    assert resp.status == 201
    assert (await resp.json())["suggestion"] == {
        "file": "paper.tex",
        "changes": [{"old": "Some methods.", "new": "Two careful methods."}],
    }
    resp = await tc.post(
        "/comments",
        json={"anchor": {"kind": "section", "title": "Methods"}, "text": "clearer",
              "suggestion": {"old": "Words the PDF shows but the source spells otherwise", "new": "x"}},
    )
    assert resp.status == 400
    assert "not found in the source" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_a_section_comment_keeps_to_the_heading_it_was_made_on(client_with_pdf, project):
    """Headings can share a title. The Sections tab names the heading pressed, the
    comment keeps to it through lines added above, and the title alone is refused."""
    from tex_mcp_web.server import resolve_section_to_source
    from tex_mcp_web.structure import parse_structure

    tc, server = client_with_pdf
    body = ("\\section{Methods}\n\\subsection{Setup}\nFirst.\n"
            "\\section{Experiments}\n\\subsection{Setup}\nSecond.\n")
    (project / "paper.tex").write_text(body)
    server.structure = parse_structure(project)

    def where(comment):
        return {key: comment["resolved_source"][key] for key in ("file", "line_start", "line_end")}

    resp = await tc.post("/comments", json={"anchor": {"kind": "section", "title": "Setup", "label": None},
                                            "text": "which setup", "heading": {"file": "paper.tex", "line": 5}})
    assert resp.status == 201
    made = await resp.json()
    assert where(made) == {"file": "paper.tex", "line_start": 5, "line_end": 6}

    (project / "paper.tex").write_text("% one\n% two\n" + body)
    server.structure = parse_structure(project)
    server.comments.refresh_anchors(
        project, server.last_result.output_file,
        sections_resolver=lambda title, label, near: resolve_section_to_source(
            server.structure, project, title, label, near))
    assert where((await (await tc.get(f"/comments/{made['id']}")).json())) == {
        "file": "paper.tex", "line_start": 7, "line_end": 8}

    resp = await tc.post("/comments", json={"anchor": {"kind": "section", "title": "Setup"}, "text": "which"})
    assert resp.status == 409
    assert "2 headings are titled 'Setup'" in (await resp.json())["error"]
    resp = await tc.post("/goto", json={"section": "Setup"})
    assert resp.status == 409


@pytest.mark.asyncio
async def test_the_pdf_downloads_named_as_overleaf_names_it(client_with_pdf, project):
    """Download PDF names the file after the Overleaf project, every character but a
    letter or a digit turned to _, and keeps the PDF's own name without a project."""
    from types import SimpleNamespace

    tc, server = client_with_pdf
    resp = await tc.get("/pdf/download")
    assert resp.status == 200
    assert resp.headers["Content-Disposition"] == "attachment; filename=\"paper.pdf\"; filename*=UTF-8''paper.pdf"
    assert await resp.read() == (project / "paper.pdf").read_bytes()

    server.overleaf_live = SimpleNamespace(project_name="논문 v2: Final")
    try:
        resp = await tc.get("/pdf/download")
    finally:
        server.overleaf_live = None
    assert resp.headers["Content-Disposition"] == (
        "attachment; filename=\"___v2__Final.pdf\"; "
        "filename*=UTF-8''%EB%85%BC%EB%AC%B8_v2__Final.pdf")


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
@pytest.mark.parametrize("suggestion", [{"old": "x"}, {"old": "x", "new": None}])
async def test_incomplete_suggestion_rejected(client, suggestion):
    tc, _ = client
    response = await tc.post(
        "/comments",
        json={"anchor": {"kind": "paper"}, "text": "x", "suggestion": suggestion},
    )
    assert response.status == 400
    assert "suggestion old and new must be strings" in (await response.json())["error"]


async def _source_suggestion(
    tc,
    *,
    old: str = "Some prose with \\cite{ref1}.",
    new: str = "Clear prose with \\cite{ref1}.",
    anchor: dict | None = None,
) -> dict:
    response = await tc.post(
        "/comments",
        json={
            "anchor": anchor or {
                "kind": "source_range",
                "file": "paper.tex",
                "line_start": 5,
                "line_end": 5,
            },
            "text": "Use the suggested wording.",
            "suggestion": {"old": old, "new": new},
        },
    )
    assert response.status == 201
    return await response.json()


@pytest.mark.asyncio
async def test_apply_source_suggestion_replaces_only_anchor_and_keeps_comment_open(
    client, project
):
    tc, server = client
    path = project / "paper.tex"
    path.chmod(0o640)
    before = path.read_text(encoding="utf-8")
    comment = await _source_suggestion(tc)
    server.do_compile = AsyncMock()

    response = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion",
        json={"updated": comment["updated"]},
    )

    assert response.status == 200
    applied = await response.json()
    assert path.read_text(encoding="utf-8") == before.replace(
        "Some prose with \\cite{ref1}.", "Clear prose with \\cite{ref1}.", 1
    )
    assert path.stat().st_mode & 0o777 == 0o640
    assert applied["status"] == "open"
    # Applying takes the proposal off the thread: it is in the file now, and the entry
    # it leaves says where.
    assert "suggestion" not in applied
    assert applied["thread"][-1]["author"] == "human"
    assert applied["thread"][-1]["edits"] == ["paper.tex:5-5"]
    assert applied["resolved_source"] == {
        "file": "paper.tex",
        "line_start": 5,
        "line_end": 5,
        "column_start": 0,
        "column_end": 29,
    }
    server.comments.refresh_source_anchors(project)
    stored = server.comments.get(comment["id"])
    assert stored is not None
    assert stored.status == "open"
    assert not stored.stale
    server.do_compile.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_source_suggestion_rejects_stale_thread(client, project):
    tc, server = client
    path = project / "paper.tex"
    before = path.read_text(encoding="utf-8")
    comment = await _source_suggestion(tc)
    server.comments.reply(comment["id"], "The thread moved on.", author="human")

    response = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion",
        json={"updated": comment["updated"]},
    )

    assert response.status == 409
    assert "stale thread" in (await response.json())["error"]
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_apply_source_suggestion_rechecks_changed_source(client, project):
    tc, _ = client
    path = project / "paper.tex"
    comment = await _source_suggestion(tc)
    changed = path.read_text(encoding="utf-8").replace("Some prose", "Other prose", 1)
    path.write_text(changed, encoding="utf-8")

    response = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion",
        json={"updated": comment["updated"]},
    )

    assert response.status == 409
    # What guards the apply is the text the proposal names, not a flag about the anchor.
    assert "not found in the source" in (await response.json())["error"]
    assert path.read_text(encoding="utf-8") == changed


@pytest.mark.asyncio
async def test_apply_refuses_a_comment_with_nothing_to_apply(client, project):
    tc, _ = client
    path = project / "paper.tex"
    before = path.read_text(encoding="utf-8")
    plain = await (await tc.post("/comments", json={
        "anchor": {"kind": "source_range", "file": "paper.tex",
                   "line_start": 5, "line_end": 5},
        "text": "just a remark"})).json()

    response = await tc.post(
        f"/comments/{plain['id']}/apply-suggestion",
        json={"updated": plain["updated"]},
    )

    assert response.status == 409
    assert "carries no suggestion" in (await response.json())["error"]
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_apply_source_suggestion_refuses_an_ambiguous_piece(client, project):
    """A piece that appears twice is refused with the lines it was found on, so the quote
    can be extended on purpose."""
    tc, _ = client
    path = project / "paper.tex"
    path.write_text(path.read_text(encoding="utf-8") + "Some prose with \\cite{ref1}.\n",
                    encoding="utf-8")
    comment = await _source_suggestion(tc, old="Some prose with \\cite{ref1}.",
                                       new="Replacement.")
    before = path.read_text(encoding="utf-8")

    response = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion",
        json={"updated": comment["updated"]},
    )

    assert response.status == 409
    error = (await response.json())["error"]
    assert "occurs 2 times, on lines 5, 10" in error
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_a_proposal_may_reach_past_what_the_reviewer_underlined(client, project):
    """A reviewer underlines a phrase to point at something, and what they ask for is
    regularly wider than the phrase. The proposal stays on their thread and edits what it
    has to; the thread is the only place it can live."""
    tc, server = client
    path = project / "paper.tex"
    pointed = await (await tc.post("/comments", json={
        "anchor": {"kind": "source_range", "file": "paper.tex", "line_start": 3,
                   "line_end": 3, "column_start": 9, "column_end": 21},
        "text": "split this section up"})).json()
    assert pointed["resolved_source"]["column_end"] - pointed["resolved_source"]["column_start"] == 12

    from tex_mcp_web.server import derive_suggestion
    proposed = server.comments.suggest(
        pointed["id"], pointed["updated"], "rewrote the section and the prose under it",
        lambda current: derive_suggestion(project, current.resolved_source, [
            ("\\section{Introduction}", "\\section{Overview}"),
            ("Some prose with", "Rewritten prose with"),   # outside the underline
        ]),
    )
    assert proposed.suggestion.file == "paper.tex" and len(proposed.suggestion.changes) == 2

    response = await tc.post(
        f"/comments/{pointed['id']}/apply-suggestion", json={"updated": proposed.updated})
    assert response.status == 200
    rewritten = path.read_text(encoding="utf-8")
    assert "\\section{Overview}" in rewritten and "Rewritten prose with" in rewritten
    # The reviewer's own comment is still attached to what they underlined.
    kept = server.comments.get(pointed["id"])
    assert not kept.stale and kept.suggestion is None
    assert kept.source_selector.exact == "\\section{Overview}"


@pytest.mark.asyncio
async def test_the_page_refuses_an_empty_replacement_but_an_agent_may_remove_text(client, project):
    """Clearing the replacement box is a slip; deleting a sentence on purpose is what a
    reviewer sometimes asks for, and the agent says so in the reply beside it."""
    tc, server = client
    refused = await tc.post("/comments", json={
        "anchor": {"kind": "source_range", "file": "paper.tex", "line_start": 5, "line_end": 5},
        "text": "drop it", "suggestion": {"old": "Some prose", "new": ""}})
    assert refused.status == 400
    assert "replacement must not be empty" in (await refused.json())["error"]

    from tex_mcp_web.server import derive_suggestion
    asked = await (await tc.post("/comments", json={
        "anchor": {"kind": "source_range", "file": "paper.tex", "line_start": 5, "line_end": 5},
        "text": "이 인용은 빼주세요"})).json()
    proposed = server.comments.suggest(
        asked["id"], asked["updated"], "인용을 지웠습니다",
        lambda current: derive_suggestion(project, current.resolved_source, [(" with \\cite{ref1}", "")]))
    response = await tc.post(f"/comments/{asked['id']}/apply-suggestion",
                             json={"updated": proposed.updated})
    assert response.status == 200
    assert "Some prose." in (project / "paper.tex").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_two_pieces_touching_one_anchor_carry_it_across_together(client, project):
    """One piece swallows what the reviewer underlined and another sits inside it. The
    anchor has to come out pointing at the text that replaced both."""
    tc, server = client
    from tex_mcp_web.server import derive_suggestion

    pointed = await (await tc.post("/comments", json={
        "anchor": {"kind": "source_range", "file": "paper.tex", "line_start": 5,
                   "line_end": 5, "column_start": 5, "column_end": 10},
        "text": "이 부분"})).json()
    assert pointed["source_selector"]["exact"] == "prose"

    proposed = server.comments.suggest(
        pointed["id"], pointed["updated"], "두 군데를 한 번에",
        lambda current: derive_suggestion(project, current.resolved_source, [
            ("Some prose with", "Clearer prose citing"),   # swallows the underline
            ("\\section{Methods}", "\\section{Approach}"),  # further down the file
        ]))
    assert len(proposed.suggestion.changes) == 2

    response = await tc.post(f"/comments/{pointed['id']}/apply-suggestion",
                             json={"updated": proposed.updated})
    assert response.status == 200
    kept = server.comments.get(pointed["id"])
    assert not kept.stale
    assert kept.source_selector.exact == "Clearer prose citing"


@pytest.mark.asyncio
async def test_a_thread_whose_anchor_came_loose_can_still_be_proposed_on(client, project):
    """A comment whose words are not found goes stale, which is when a proposal is most
    needed, so a loose anchor cannot bar one: the pieces are found in the file, not in the
    range the comment was written against."""
    import shutil

    tc, server = client
    from tex_mcp_web.server import derive_suggestion

    pointed = await (await tc.post("/comments", json={
        "anchor": {"kind": "source_range", "file": "paper.tex", "line_start": 5,
                   "line_end": 5, "column_start": 0, "column_end": 10},
        "text": "이 표현 손봐줘"})).json()
    # Without the text it was made in, the comment is looked for by its words.
    shutil.rmtree(project / ".tex-mcp-web" / "anchor-texts")
    (project / "paper.tex").write_text(
        (project / "paper.tex").read_text(encoding="utf-8").replace("Some prose with", "Rewritten using"),
        encoding="utf-8")
    server.comments.refresh_source_anchors(project)
    loose = server.comments.get(pointed["id"])
    # The words after the selection changed too, so no unchanged text around it
    # can reattach it.
    assert loose.stale

    proposed = server.comments.suggest(
        loose.id, loose.updated, "현재 문구 기준으로 다시 제안합니다",
        lambda current: derive_suggestion(project, current.resolved_source, [("Rewritten", "Clearer")]))
    assert proposed.suggestion.changes == [("Rewritten", "Clearer")]

    response = await tc.post(f"/comments/{loose.id}/apply-suggestion",
                             json={"updated": proposed.updated})
    assert response.status == 200
    assert "Clearer using \\cite{ref1}." in (project / "paper.tex").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_suggestion_needs_a_thread_the_reviewer_wrote_in(client, project):
    """The agent opening its own card and proposing on it is what put proposals beside
    the reviewer's threads instead of in them."""
    tc, server = client
    from tex_mcp_web.comments import SourceRangeAnchor
    from tex_mcp_web.server import derive_suggestion

    own = server.comments.add(
        SourceRangeAnchor(file="paper.tex", line_start=5, line_end=5),
        "I noticed this myself", author="agent",
        resolved_source=ResolvedSource(file="paper.tex", line_start=5, line_end=5))

    with pytest.raises(ValueError, match="the reviewer has written in"):
        server.comments.suggest(
            own.id, own.updated, "here is a fix",
            lambda current: derive_suggestion(project, current.resolved_source,
                                              [("Some prose", "Clear prose")]))

    server.comments.reply(own.id, "그래 고쳐줘", author="human")
    reread = server.comments.get(own.id)
    proposed = server.comments.suggest(
        reread.id, reread.updated, "here is a fix",
        lambda current: derive_suggestion(project, current.resolved_source, [("Some prose", "Clear prose")]))
    assert proposed.suggestion.changes == [("Some prose", "Clear prose")]


@pytest.mark.asyncio
@pytest.mark.parametrize("outside_path", ["../outside.tex", "outside-link.tex"])
async def test_apply_source_suggestion_rejects_source_outside_project(
    client, project, outside_path
):
    tc, server = client
    outside = project.parent / "outside.tex"
    outside.write_text("outside text\n", encoding="utf-8")
    if outside_path == "outside-link.tex":
        (project / outside_path).symlink_to(outside)
    comment = server.comments.add(
        SourceRangeAnchor(file=outside_path, line_start=1, line_end=1),
        "Do not escape the project.",
        resolved_source=ResolvedSource(file=outside_path, line_start=1, line_end=1),
        source_selector=SourceSelector(exact="outside text", prefix="", suffix=""),
        suggestion=SuggestedEdit(file=outside_path,
                                 changes=[("outside text", "changed outside text")]),
    )

    response = await tc.post(
        f"/comments/{comment.id}/apply-suggestion",
        json={"updated": comment.updated},
    )

    assert response.status in {400, 403}
    assert outside.read_text(encoding="utf-8") == "outside text\n"


@pytest.mark.asyncio
async def test_apply_source_suggestion_is_single_use_under_concurrency(client, project):
    tc, _ = client
    path = project / "paper.tex"
    comment = await _source_suggestion(tc)

    first, second = await asyncio.gather(
        tc.post(
            f"/comments/{comment['id']}/apply-suggestion",
            json={"updated": comment["updated"]},
        ),
        tc.post(
            f"/comments/{comment['id']}/apply-suggestion",
            json={"updated": comment["updated"]},
        ),
    )
    responses = [(first.status, await first.json()), (second.status, await second.json())]

    assert sorted(status for status, _ in responses) == [200, 409]
    rejected = next(body for status, body in responses if status == 409)
    # The thread moved when the winner wrote its entry, so the loser is refused by the
    # stamp it quoted rather than by a flag.
    assert "stale thread" in rejected["error"]
    assert path.read_text(encoding="utf-8").count("Clear prose with \\cite{ref1}.") == 1
    stored = await (await tc.get(f"/comments/{comment['id']}")).json()
    assert stored["status"] == "open"
    assert len(stored["thread"]) == 2

    duplicate = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion",
        json={"updated": stored["updated"]},
    )
    assert duplicate.status == 409
    assert "carries no suggestion" in (await duplicate.json())["error"]


@pytest.mark.asyncio
async def test_apply_source_suggestion_preserves_unicode_multiline_range(client, project):
    tc, _ = client
    path = project / "paper.tex"
    text = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "앞 문장.\n"
        "첫 줄 😀 α\n"
        "둘째 줄 β\n"
        "뒤 문장.\n"
        "\\end{document}\n"
    )
    path.write_text(text, encoding="utf-8")
    old = "첫 줄 😀 α\n둘째 줄 β"
    new = "새 첫 줄 😀 γ\n새 둘째 줄 δ"
    comment = await _source_suggestion(
        tc,
        old=old,
        new=new,
        anchor={
            "kind": "source_range",
            "file": "paper.tex",
            "line_start": 4,
            "line_end": 5,
            "column_start": 0,
            "column_end": 6,
        },
    )

    response = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion",
        json={"updated": comment["updated"]},
    )

    assert response.status == 200
    applied = await response.json()
    assert path.read_text(encoding="utf-8") == text.replace(old, new, 1)
    assert applied["status"] == "open"
    assert applied["resolved_source"] == {
        "file": "paper.tex",
        "line_start": 4,
        "line_end": 5,
        "column_start": 0,
        "column_end": 8,
    }


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
async def test_picked_comments_change_status_in_one_call(client):
    tc, _ = client
    ids = [(await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": t})).json())["id"]
           for t in ("a", "b", "c")]
    for status in ("resolved", "archived", "open"):
        resp = await tc.post("/comments/status", json={"ids": ids[:2], "status": status})
        assert resp.status == 200 and await resp.json() == {"ids": ids[:2], "status": status}
        stored = {c["id"]: c for c in (await (await tc.get("/comments")).json())["comments"]}
        assert [stored[cid]["status"] for cid in ids] == [status, status, "open"]
        assert [len(stored[cid]["thread"]) for cid in ids] == [1, 1, 1]  # a status change adds no entry
    resp = await tc.post("/comments/status", json={"ids": [ids[0], "c-00000000"], "status": "resolved"})
    assert resp.status == 404 and (await (await tc.get(f"/comments/{ids[0]}")).json())["status"] == "open"
    assert (await tc.post("/comments/status", json={"ids": ids, "status": "done"})).status == 400


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

    assert set(tools) == {
        "guide", "read_comments", "write_comments", "compile", "image", "listen", "setup_info", "overleaf",
    }
    # A structured answer repeats the content as JSON beside it, an image as base64 text,
    # and wraps a text answer in {"result": ...}, which the plugin's hook cannot read.
    assert all(tool.outputSchema is None for tool in tools.values())
    assert "call guide() and read every file in its read list in full" in mcp.instructions
    assert "read_comments(new=True)" in mcp.instructions
    assert "read_comments(ids=[...])" in mcp.instructions
    assert "compile() once" in mcp.instructions
    assert "Within the user's editing scope" in mcp.instructions
    assert "Respect read-only or discussion-only requests" in mcp.instructions
    assert "with a reply in its thread, with or without an edit, on a thread from Overleaf too" in mcp.instructions
    assert "its dir can name the paper's folder anywhere" in mcp.instructions
    assert "write in chat only what no thread carries" in mcp.instructions
    assert "not the replies or the edits they report" in mcp.instructions
    for needed in ("asks to listen", "follow how", "Do not poll", "stay queued", "init.md"):
        assert needed in mcp.instructions, needed
    assert "state()" not in mcp.instructions

    descriptions = "\n".join(tool.description or "" for tool in tools.values())
    assert "source-search key" not in descriptions
    assert "only for rendered evidence" not in descriptions
    assert "100-200" not in descriptions

    # Source is read with the agent's own file tools, so no tool ships the paper's text.
    assert "section" not in tools
    assert all(mcp.instructions not in (tool.description or "") for tool in tools.values())
    read_schema = tools["read_comments"].inputSchema["properties"]
    assert read_schema["status"]["enum"] == ["open", "resolved", "archived", "all"]
    assert set(read_schema) == {"ids", "new", "status", "unanswered", "since", "limit", "save"}

    comment_schema = tools["write_comments"].inputSchema
    assert comment_schema["properties"]["action"]["enum"] == [
        "add", "reply", "suggest", "withdraw", "overleaf", "edit", "delete"
    ]
    # A suggestion names the text it replaces by quoting it, so nothing in its schema
    # asks the agent for a line or a column.
    fragment = comment_schema["$defs"]["FragmentInput"]["properties"]
    assert set(fragment) == {"old", "new"} and fragment["old"]["minLength"] == 1
    anchor_schema = comment_schema["properties"]["anchor"]["anyOf"][0]
    # No area: an agent has no way to arrive at PDF coordinates that mean anything.
    assert set(anchor_schema["discriminator"]["mapping"]) == {
        "paper", "section", "source_range"
    }
    assert "author" not in comment_schema["properties"]

    image_schema = tools["image"].inputSchema["properties"]
    assert image_schema["page"]["anyOf"][0]["minimum"] == 1
    assert image_schema["bbox"]["anyOf"][0]["minItems"] == 4
    assert image_schema["bbox"]["anyOf"][0]["maxItems"] == 4
    assert image_schema["margin"]["minimum"] == 0

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
        f"main: paper.tex\nport: {_free_port()}\n"
    )
    monkeypatch.chdir(project)
    binding = ProjectBinding(project)
    yield binding
    binding.stop()


_TINY_PAPER = "\\documentclass{article}\n\\begin{document}x\\end{document}\n"


def test_tools_keep_the_bound_project_when_a_closer_config_appears(tmp_path, monkeypatch):
    from tex_mcp_web.mcp_client import ProjectBinding
    from tex_mcp_web.mcp_server import _load_project

    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / "paper.tex").write_text(_TINY_PAPER)
    (parent / ".tex-mcp-web.yaml").write_text(f"main: paper.tex\nport: {_free_port()}\n")
    monkeypatch.chdir(child)
    binding = ProjectBinding(child)
    try:
        bound = binding.require_shared()
        (child / ".tex-mcp-web.yaml").write_text(f"main: other.tex\nport: {_free_port()}\n")
        # Comments, image and the compile log come from here, so they must stay with the
        # project the review page serves, not the config that appeared later.
        cfg, watch_dir, store = _load_project(binding)
        assert cfg.config_path.resolve() == bound.config_path
        assert watch_dir == bound.watch_dir == parent.resolve()
        assert store.path == parent.resolve() / ".tex-mcp-web" / "comments.json"
    finally:
        binding.stop()


@pytest.mark.asyncio
async def test_setup_info_reports_paths_without_connecting(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import threading

    from tex_mcp_web.mcp_client import ProjectBinding
    from tex_mcp_web.mcp_server import create_server

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home"))
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / "paper.tex").write_text(_TINY_PAPER)
    config = parent / ".tex-mcp-web.yaml"
    config.write_text(f"main: paper.tex\nport: {_free_port()}\n")
    binding = ProjectBinding(child)
    mcp = create_server(binding)

    async def setup_info():
        return json.loads((await mcp.call_tool("setup_info", {}))[0].text)

    files = sorted(tmp_path.rglob("*"))
    try:
        told = await setup_info()
        assert told.pop("guidelines")["entries"] is None
        assert told == {
            "startup_dir": str(child.resolve()),
            "discovered_config_path": str(config.resolve()),
            "bound_config_path": None,
        }
        # A report only: no server, no binding, no file.
        assert binding.bound_config_path() is None
        assert not any(thread.name == "tex-mcp-web" for thread in threading.enumerate())
        assert sorted(tmp_path.rglob("*")) == files
        binding.require_shared()
        assert (await setup_info())["bound_config_path"] == str(config.resolve())
    finally:
        binding.stop()


def test_a_server_started_for_another_config_file_is_not_shared(tmp_path):
    from tex_mcp_web.mcp_client import ProjectBinding, ProjectSetupError

    root = tmp_path / "root"
    paper = root / "paper"
    paper.mkdir(parents=True)
    (paper / "paper.tex").write_text(_TINY_PAPER)
    port = _free_port()
    # Two config files that serve the same folder on the same port.
    (root / ".tex-mcp-web.yaml").write_text(f"main: paper.tex\ndir: paper\nport: {port}\n")
    (paper / ".tex-mcp-web.yaml").write_text(f"main: paper.tex\nport: {port}\n")
    first, second = ProjectBinding(root), ProjectBinding(paper)
    try:
        first.require_shared()
        with pytest.raises(ProjectSetupError, match="serves"):
            second.require_shared()
    finally:
        first.stop()
        second.stop()


@pytest.mark.asyncio
async def test_binding_serves_the_viewer(bound_project, project):
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{bound_project.base_url()}/paper") as response:
            assert response.status == 200
            served = await response.json()
    assert Path(served["watch_dir"]).resolve() == project.resolve()


@pytest.mark.asyncio
async def test_mcp_drafts_an_overleaf_reply_only_on_a_thread_from_overleaf(bound_project, project):
    """The agent drafts the reply the reviewer may send to Overleaf. A thread holds one draft,
    which a second replaces; nothing is said in the thread until the reviewer sends it. The
    draft keeps what the ranges it names held, words Overleaf must have before it goes."""
    pytest.importorskip("mcp")
    from tex_mcp_web import overleaf
    from tex_mcp_web.comments import CommentStore, PaperAnchor
    from tex_mcp_web.mcp_server import create_server, revision_of

    mcp = create_server(bound_project)

    async def call(tool_name, **arguments):
        return json.loads((await mcp.call_tool(tool_name, arguments))[0].text)

    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    brought = store.add(PaperAnchor(), "Which words?", name="Ann")
    own = store.add(PaperAnchor(), "A note of mine.")
    overleaf.ledger_path(project).write_text(json.dumps({"t1": {"comment": brought.id, "messages": 1}}))

    refused = await call("write_comments", action="overleaf", id=own.id, text="Fixed.", rev=revision_of(own.updated))
    assert "did not come from Overleaf" in refused["error"]
    receipt = await call("write_comments", action="overleaf", id=brought.id, text="Fixed it.",
                         edits=["paper.tex:5-5"], rev=revision_of(brought.updated))
    draft = store.get(brought.id).overleaf_draft
    assert (draft.text, [r.label for r in draft.ranges], draft.checks) == (
        "Fixed it.", ["paper.tex:5-5"], [{"file": "paper.tex", "text": "Some prose with \\cite{ref1}."}])
    assert len(store.get(brought.id).thread) == 1
    told = (await call("read_comments", ids=[brought.id]))["comments"][0]
    assert told["on_overleaf"] is True and told["overleaf_draft"] == "Fixed it."
    assert "on_overleaf" not in (await call("read_comments", ids=[own.id]))["comments"][0]

    receipt = await call("write_comments", action="overleaf", id=brought.id, text="Fixed it better.", rev=receipt["rev"])
    assert store.get(brought.id).overleaf_draft.text == "Fixed it better."
    outside = await call("write_comments", action="overleaf", id=brought.id, text="Again.",
                         edits=["paper.tex:99-99"], rev=receipt["rev"])
    assert "outside paper.tex" in outside["error"]

    # Words on Overleaf that are not in the file here yet: the thread says where they are,
    # and a suggestion on it says why it has to wait.
    overleaf.ledger_path(project).write_text(json.dumps({"t1": {
        "comment": brought.id, "messages": 1,
        "place": {"pathname": "/paper.tex", "line": 3, "anchored_text": "words written on Overleaf"}}}))
    told = (await call("read_comments", ids=[brought.id]))["comments"][0]
    assert told["overleaf_place"] == "paper.tex:3, not found here"
    refused = await call("write_comments", action="suggest", id=brought.id, text="Shorter.",
                         changes=[{"old": "Some methods.", "new": "Methods."}], rev=receipt["rev"])
    assert "on Overleaf at paper.tex:3" in refused["error"]

    # What the reviewer asks after the draft is answered with a reply, which stays here and
    # leaves the draft for the people on Overleaf.
    store.reply(brought.id, text="Explain it to me, not in the draft.", author="human")
    asked = next(c for c in (await call("read_comments", new=True))["comments"] if c["id"] == brought.id)
    assert asked["on_overleaf"] is True
    assert [e["text"] for e in asked["entries"]] == ["Explain it to me, not in the draft."]
    await call("write_comments", action="reply", id=brought.id, text="It names the cited words.")
    store.reply(own.id, text="And this?", author="human")
    assert [c["id"] for c in (await call("read_comments", new=True))["comments"]] == [own.id]
    assert store.get(brought.id).overleaf_draft.text == "Fixed it better."


@pytest.mark.asyncio
async def test_guide_lists_the_papers_guideline_base_first(bound_project, project, tmp_path, monkeypatch):
    """guide() names the guideline the config chooses and every one it extends, base first."""
    pytest.importorskip("mcp")
    from tex_mcp_web.mcp_server import create_server

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home"))
    base = tmp_path / "home" / "tex-mcp-web" / "guidelines" / "plain-writing"
    base.mkdir(parents=True)
    (base / "GUIDELINE.md").write_text("# Plain writing\n")
    mine = base.parent / "my-field"
    mine.mkdir()
    (mine / "GUIDELINE.md").write_text("---\nextends: plain-writing\n---\n# My field\n")
    mcp = create_server(bound_project)

    async def guide():
        return json.loads((await mcp.call_tool("guide", {}))[0].text)

    config = project / ".tex-mcp-web.yaml"
    assert (await guide())["read"] == []
    config.write_text(config.read_text() + "guideline: my-field\n")
    read = (await guide())["read"]
    assert [Path(path).parent.name for path in read] == ["plain-writing", "my-field"]
    config.write_text(config.read_text().replace("my-field", "nowhere"))
    assert "guideline not found" in (await guide())["error"]


@pytest.mark.asyncio
async def test_mcp_drafts_a_new_overleaf_thread_on_source_lines(bound_project, project):
    """With an anchor instead of an id, the agent drafts a new thread for Overleaf: a comment
    with no entries holding the draft, on whole source lines with words on them. It redrafts
    it by id, and nothing is said in it until the reviewer sends it."""
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)

    async def call(tool_name, **arguments):
        return json.loads((await mcp.call_tool(tool_name, arguments))[0].text)

    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    lines = {"kind": "source_range", "file": "paper.tex", "start": 5, "end": 5}
    receipt = await call("write_comments", action="overleaf", text="Feedback: which prose? Answer: this.",
                         anchor=lines)
    drafted = store.get(receipt["id"])
    assert drafted.thread == [] and drafted.overleaf_draft.text == "Feedback: which prose? Answer: this."
    assert (drafted.resolved_source.file, drafted.resolved_source.line_start) == ("paper.tex", 5)
    told = (await call("read_comments", ids=[drafted.id]))["comments"][0]
    assert "comment" not in told and told["overleaf_draft"] == "Feedback: which prose? Answer: this."
    assert "on_overleaf" not in told
    assert any(item["id"] == drafted.id for item in (await call("read_comments"))["comments"])
    receipt = await call("write_comments", action="overleaf", id=drafted.id, text="Answer: this, shorter.",
                         rev=receipt["rev"])
    assert store.get(drafted.id).overleaf_draft.text == "Answer: this, shorter."
    assert store.get(drafted.id).thread == []

    blank = await call("write_comments", action="overleaf", text="On nothing.",
                       anchor={"kind": "source_range", "file": "paper.tex", "start": 10, "end": 10})
    assert "blank" in blank["error"]
    paper_wide = await call("write_comments", action="overleaf", text="Everywhere.", anchor={"kind": "paper"})
    assert "source_range" in paper_wide["error"]
    both = await call("write_comments", action="overleaf", id=drafted.id, text="Both.", anchor=lines)
    assert "no id or rev" in both["error"]
    # The agent may take back its own draft: nobody has said anything in it.
    assert (await call("write_comments", action="delete", id=drafted.id))["ok"] is True


@pytest.mark.asyncio
async def test_a_reply_sent_to_overleaf_waits_for_the_people_there_not_the_agent(bound_project, project):
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, PaperAnchor
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)

    async def call(tool_name, **arguments):
        return json.loads((await mcp.call_tool(tool_name, arguments))[0].text)

    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    thread = store.add(PaperAnchor(), "Say hello properly.", name="A. Reviewer")
    store.sent_to_overleaf(thread.id, "Thanks, fixed!", "")
    # The reply answers what the thread said before it, so neither lists the thread.
    assert (await call("read_comments", unanswered=True))["comments"] == []
    assert (await call("read_comments", new=True))["comments"] == []


@pytest.mark.asyncio
async def test_mcp_comment_discovery_reads_only_selected_history(bound_project, project, monkeypatch):
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, PaperAnchor, ResolvedSource, SectionAnchor
    from tex_mcp_web.mcp_server import create_server, revision_of, short_time

    mcp = create_server(bound_project)

    async def call(tool_name, **arguments):
        return json.loads((await mcp.call_tool(tool_name, arguments))[0].text)

    moment = "2026-01-01T00:00:00+00:00"
    monkeypatch.setattr("tex_mcp_web.comments._now", lambda: moment)
    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    first = store.add(SectionAnchor("Methods"), "Check this method",
                      resolved_source=ResolvedSource("paper.tex", 6, 8))
    second = store.add(PaperAnchor(), "Check this paper")
    agent_only = store.add(PaperAnchor(), "An agent note", author="agent")

    requests = (await call("read_comments", unanswered=True))["comments"]
    assert requests == [
        {"id": first.id, "rev": revision_of(first.updated), "status": "open",
         "kind": "section", "request": "Check this method",
         "thread_entries": 1, "last_human_at": short_time(moment)},
        {"id": second.id, "rev": revision_of(second.updated), "status": "open",
         "kind": "paper", "request": "Check this paper",
         "thread_entries": 1, "last_human_at": short_time(moment)},
    ]
    # since reads back the form the server printed, and nothing is newer than now.
    assert (await call("read_comments", since=short_time(moment)))["comments"] == []
    assert "reads as" in (await call("read_comments", since=moment))["error"]

    moment = "2026-01-01T00:00:01+00:00"
    history = "Detailed explanation. " * 1000
    store.reply(first.id, history, author="agent")
    assert [c["id"] for c in (await call("read_comments", unanswered=True))["comments"]] == [second.id]
    assert history not in json.dumps(await call("read_comments"))

    moment = "2026-01-01T00:00:02+00:00"
    store.reply(first.id, "Please check the boundary too", author="human")
    requests = (await call("read_comments", unanswered=True,
                            since=short_time("2026-01-01T00:00:01+00:00")))["comments"]
    assert len(requests) == 1
    assert requests[0]["id"] == first.id
    assert requests[0]["request"] == "Please check the boundary too"
    assert requests[0]["last_human_at"] == short_time(moment)
    assert requests[0]["thread_entries"] == 3
    # A time names a minute, so asking again from the minute after it is empty. Within the
    # same minute a thread comes back rather than being missed.
    assert (await call("read_comments", since="01-01 09:01"))["comments"] == []

    details = (await call("read_comments", ids=[first.id]))["comments"]
    assert len(details) == 1
    assert details[0]["source"] == "paper.tex:6-8"
    assert [entry["text"] for entry in details[0]["replies"]] == [history, "Please check the boundary too"]
    assert "replies" not in (await call("read_comments", ids=[second.id]))["comments"][0]
    assert [c["id"] for c in (await call("read_comments", ids=[second.id, first.id]))["comments"]] == [second.id, first.id]
    assert "unique" in (await call("read_comments", ids=[first.id, first.id]))["error"]
    assert "not found" in (await call("read_comments", ids=[first.id, "missing"]))["error"]

    receipt = await call("write_comments", action="reply", id=first.id, text="Fixed the boundary", edits=["paper.tex:6-8"])
    assert receipt == {"id": first.id, "status": "open", "rev": revision_of(moment)}
    saved = (await call("read_comments", ids=[first.id]))["comments"][0]
    assert saved["replies"][-1]["text"] == "Fixed the boundary"
    assert saved["replies"][-1]["edits"] == ["paper.tex:6-8"]

    store.resolve(second.id, "", author="human")
    assert [c["id"] for c in (await call("read_comments", status="resolved"))["comments"]] == [second.id]
    all_comments = (await call("read_comments", status="all"))["comments"]
    assert len(all_comments) == 3
    assert next(c for c in all_comments if c["id"] == agent_only.id)["last_human_at"] is None


@pytest.mark.asyncio
async def test_a_suggestion_answers_a_comment_made_off_the_source(bound_project, project):
    """A reviewer points at the PDF or a section, not at source lines. The proposal is
    quoted from the file that place resolves to, so it can answer them there; only a
    whole-paper note, which names no file, cannot carry one."""
    pytest.importorskip("mcp")
    import aiohttp

    from tex_mcp_web.comments import CommentStore
    from tex_mcp_web.mcp_server import create_server, revision_of

    mcp = create_server(bound_project)
    store = CommentStore(project / ".tex-mcp-web" / "comments.json")

    async def opened(anchor):
        # Placed the way the page places it, so a section resolves to its source lines.
        added = await mcp.call_tool("write_comments", {"action": "add", "text": "Look here.", "anchor": anchor})
        comment_id = json.loads(added[0].text)["id"]
        return store.reply(comment_id, "Please fix this.", author="human")

    section = await opened({"kind": "section", "title": "Methods"})
    note = await opened({"kind": "paper"})

    async def suggest(comment, old, new):
        result = await mcp.call_tool("write_comments", {
            "action": "suggest", "id": comment.id, "rev": revision_of(comment.updated),
            "text": "A proposal.", "changes": [{"old": old, "new": new}]})
        return json.loads(result[0].text)

    assert "place in the source is known" in (await suggest(note, "Some", "Any"))["error"]
    assert "error" not in await suggest(section, "Some methods.", "Two careful methods.")

    proposed = store.get(section.id)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{bound_project.base_url()}/comments/{section.id}/apply-suggestion",
            json={"updated": proposed.updated},
        ) as response:
            assert response.status == 200
    assert "Two careful methods." in (project / "paper.tex").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_agent_names_a_heading_others_share_the_title_of_by_its_label(bound_project, project):
    """A title several headings carry does not say which one the agent means: the
    comment is refused with the headings, and their label tells them apart."""
    pytest.importorskip("mcp")
    from tex_mcp_web.mcp_server import create_server

    (project / "paper.tex").write_text(
        "\\section{Methods}\n\\subsection{Setup}\nFirst.\n"
        "\\section{Experiments}\n\\subsection{Setup}\\label{sec:exp-setup}\nSecond.\n")
    mcp = create_server(bound_project)

    async def add(anchor):
        result = await mcp.call_tool("write_comments", {"action": "add", "text": "Look here.", "anchor": anchor})
        return json.loads(result[0].text)

    refused = await add({"kind": "section", "title": "Setup"})
    assert refused["error"] == ("2 headings are titled 'Setup': 1.1 at paper.tex:2, 2.1 at paper.tex:5 "
                                "(label sec:exp-setup). Give the label, or anchor on the heading's lines "
                                "with source_range.")
    assert "id" in await add({"kind": "section", "title": "Setup", "label": "sec:exp-setup"})


@pytest.mark.asyncio
async def test_mcp_comment_and_section_runtime_contract(bound_project, project):
    pytest.importorskip("mcp")
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)

    added = await mcp.call_tool(
        "write_comments",
        {
            "action": "add",
            "text": "review this",
            "anchor": {"kind": "paper"},
        },
    )
    comment = json.loads(added[0].text)
    stored = json.loads((project / ".tex-mcp-web" / "comments.json").read_text())
    assert stored["version"] == STORE_VERSION
    assert stored["comments"][0]["thread"][0]["author"] == "agent"
    assert set(comment) == {"id", "status", "rev"}
    assert stored["comments"][0]["thread"][0]["text"] == "review this"

    # A rewrite is proposed on one comment's thread, quoted from the file the comment sits
    # in. The agent quotes the text it replaces the way an editing tool takes it, so it
    # never counts columns, and it never sends back the text it is not changing.
    async def call_comment(**payload):
        return json.loads((await mcp.call_tool("write_comments", payload))[0].text)

    async def reread(comment_id):
        result = await mcp.call_tool("read_comments", {"ids": [comment_id]})
        return json.loads(result[0].text)["comments"][0]

    anchored = await call_comment(
        action="add", text="이 문장을 바꾸세요",
        anchor={"kind": "source_range", "file": "paper.tex", "start": 5, "end": 5},
    )
    # A proposal belongs to a thread the reviewer wrote in, so one of their words comes
    # first; without it suggest is refused.
    refused = await call_comment(
        action="suggest", id=anchored["id"], rev=anchored["rev"], text="x",
        changes=[{"old": "Some prose", "new": "Clear prose"}])
    assert "the reviewer has written in" in refused["error"]
    from tex_mcp_web.comments import CommentStore

    CommentStore(project / ".tex-mcp-web" / "comments.json").reply(
        anchored["id"], "그래 고쳐줘", author="human")

    read_back = await reread(anchored["id"])
    # The anchored text comes back, so fragments are quoted from what the file holds now.
    assert read_back["quote"] == "Some prose with \\cite{ref1}."
    assert "suggestion" not in read_back

    proposed = await call_comment(
        action="suggest", id=anchored["id"], rev=read_back["rev"],
        text="인용 앞을 다듬었습니다",
        changes=[{"old": "Some prose", "new": "Some tighter prose"}],
    )
    assert set(proposed) == {"id", "status", "rev"}
    detail = await reread(anchored["id"])
    # Only the piece that changes; the rest of the line is not part of the proposal.
    assert detail["suggestion"] == [{"old": "Some prose", "new": "Some tighter prose"}]
    assert [entry["text"] for entry in detail["replies"]][-1] == "인용 앞을 다듬었습니다"

    # A second reading replaces the suggestion in place and leaves the conversation whole.
    await call_comment(
        action="suggest", id=anchored["id"], rev=detail["rev"], text="2판입니다",
        changes=[{"old": "Some prose", "new": "Different prose"}],
    )
    detail = await reread(anchored["id"])
    assert detail["suggestion"] == [{"old": "Some prose", "new": "Different prose"}]
    assert [entry["text"] for entry in detail["replies"]][-2:] == ["인용 앞을 다듬었습니다", "2판입니다"]

    missing = await call_comment(
        action="suggest", id=anchored["id"], rev=detail["rev"], text="x",
        changes=[{"old": "prose that is not there", "new": "y"}],
    )
    assert "not found in the source" in missing["error"]
    stale = await call_comment(
        action="suggest", id=anchored["id"], rev="deadbeef", text="x",
        changes=[{"old": "Some prose", "new": "y"}],
    )
    assert "stale" in stale["error"]

    # Withdrawing takes the proposal back without touching a single entry.
    await call_comment(
        action="withdraw", id=anchored["id"], rev=detail["rev"],
        text="이 제안은 접겠습니다",
    )
    detail = await reread(anchored["id"])
    assert "suggestion" not in detail
    # the reviewer's word, two proposals and the withdrawal, all still standing
    assert len(detail["replies"]) == 4
    await mcp.call_tool("write_comments", {"action": "delete", "id": anchored["id"]})

    second_added = await mcp.call_tool(
        "write_comments",
        {
            "action": "add",
            "text": "review second",
            "anchor": {"kind": "paper"},
        },
    )
    assert json.loads(second_added[0].text)["status"] == "open"


@pytest.mark.asyncio
async def test_an_added_thread_opens_with_its_proposal(client, bound_project, project):
    """When the reviewer asks for proposals left as comments, each thread the agent opens
    carries its proposal, and the reviewer applies it from there."""
    pytest.importorskip("mcp")
    from tex_mcp_web.mcp_server import create_server

    tc, server = client
    mcp = create_server(bound_project)

    async def call_comment(**payload):
        return json.loads((await mcp.call_tool("write_comments", payload))[0].text)

    def stored():
        return json.loads((project / ".tex-mcp-web" / "comments.json").read_text())["comments"]

    line = {"kind": "source_range", "file": "paper.tex", "start": 5, "end": 5}
    added = await call_comment(action="add", text="Plainer here.", anchor=line,
                               changes=[{"old": "Some prose", "new": "Plain prose"}])
    assert set(added) == {"id", "status", "rev"}
    read = json.loads((await mcp.call_tool("read_comments", {"ids": [added["id"]]}))[0].text)
    assert read["comments"][0]["suggestion"] == [{"old": "Some prose", "new": "Plain prose"}]
    assert read["comments"][0]["comment"] == "Plainer here."

    server.do_compile = AsyncMock()
    response = await tc.post(f"/comments/{added['id']}/apply-suggestion",
                             json={"updated": server.comments.get(added["id"]).updated})
    assert response.status == 200
    assert "Plain prose with \\cite{ref1}." in (project / "paper.tex").read_text(encoding="utf-8")

    # A proposal that cannot be written into the file opens no thread.
    paper = await call_comment(action="add", text="Everywhere.", anchor={"kind": "paper"},
                               changes=[{"old": "Plain prose", "new": "Prose"}])
    assert "place in the source is known" in paper["error"]
    missing = await call_comment(action="add", text="Here.", anchor=line,
                                 changes=[{"old": "prose that is not there", "new": "Prose"}])
    assert "not found in the source" in missing["error"]
    assert len(stored()) == 1

    # A field an action does not read is refused, not dropped.
    stray = await call_comment(action="reply", id=added["id"], text="Also here.",
                               changes=[{"old": "Plain prose", "new": "Prose"}])
    assert stray["error"] == "reply does not take changes"
    assert len(stored()[0]["thread"]) == 2



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
async def test_pdf_to_source_refuses_a_malformed_selection_and_a_pdf_without_synctex(client_with_pdf):
    """A PDF built without -synctex=1 records no source places, and the answer says so
    rather than that this one selection has none."""
    tc, server = client_with_pdf
    rects = [[60, 60, 200, 100]]
    bare = await tc.post("/synctex/pdf-to-source", json={"page": 1, "rects": rects, "pdf_digest": server.pdf_digest})
    assert bare.status == 409 and "built without SyncTeX" in (await bare.json())["error"]
    words = await tc.post("/synctex/source-to-pdf-words",
                          json={"file": "paper.tex", "line_start": 1, "line_end": 1, "text": "word"})
    assert words.status == 409 and "Press Compile" in (await words.json())["error"]
    for body in ({"page": 1, "rects": [], "pdf_digest": server.pdf_digest}, {"rects": rects, "pdf_digest": server.pdf_digest}):
        assert (await tc.post("/synctex/pdf-to-source", json=body)).status == 400


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
async def test_a_selection_saved_while_the_pdf_is_rebuilt_is_refused_as_changed(client_with_pdf):
    """A compile writes the PDF in place while the page still shows the one before it,
    whose digest the server keeps until the compile ends. A selection saved meanwhile is
    refused as made on a PDF that changed, not checked against a file half written."""
    import fitz

    tc, server = client_with_pdf
    selection = {"page": 1, "bbox": [70, 60, 110, 80], "rects": [[70, 60, 110, 80]]}
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 300), "Rebuilt")
        pdf.save(server.last_result.output_file)
    server.compiling = True
    for anchor in ({"kind": "text_selection", "quote": "Page 1", "selection": selection},
                   {"kind": "area", "page": 1, "bbox": [60, 60, 200, 100]}):
        resp = await tc.post("/comments", json={
            "anchor": {**anchor, "pdf_digest": server.pdf_digest}, "text": "review this"})
        assert resp.status == 409
        assert "being rebuilt" in (await resp.json())["error"]
    assert server.comments.list() == []


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
async def test_a_press_counts_the_threads_new_gives_by_the_rule_unanswered_lists_by(bound_project, project) -> None:
    """Of eight open threads the reviewer wrote in, one waits for the agent: a suggestion
    applied asks nothing, and a draft for Overleaf answers what its thread said before it.
    The line a press prints, read_comments(new=True) and read_comments(unanswered=True)
    name that one alike. A word written after a draft makes its thread wait again. The
    press and new=True leave out what new=True has carried before, which unanswered=True
    still lists while it has no answer."""
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, OverleafDraft, PaperAnchor
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)

    async def ids(**arguments) -> list[str]:
        return [c["id"] for c in json.loads((await mcp.call_tool("read_comments", arguments))[0].text)["comments"]]

    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    server = TexMcpWebServer(Config(main="paper.tex", config_path=project / ".tex-mcp-web.yaml"))
    for n in range(4):
        asked = store.add(PaperAnchor(), f"Tighten sentence {n}.")
        proposed = store.suggest(asked.id, asked.updated, "Shorter.",
                                 lambda current: SuggestedEdit("paper.tex", [("Some prose", "Prose")]))
        applied = store.apply_suggestion(asked.id, proposed.updated, lambda current: ["paper.tex:5-5"])
        assert applied.thread[-1].applied and not applied.thread[-1].asks_agent
    drafted = []
    for n in range(3):
        brought = store.add(PaperAnchor(), f"Question {n} from a coauthor.", name="Ann")
        drafted.append(store.draft_overleaf(brought.id, brought.updated, OverleafDraft(f"Answer {n}."), {}))
    request = store.add(PaperAnchor(), "Add a citation here.")

    assert "1 new comments" in server._review_line()
    assert await ids(unanswered=True) == [request.id]
    assert await ids(new=True) == [request.id]

    store.reply(drafted[0].id, "And the figure?", author="human")
    assert "1 new comments" in server._review_line()
    assert await ids(new=True) == [drafted[0].id]
    assert await ids(unanswered=True) == [drafted[0].id, request.id]
    assert "no new comments" in server._review_line()


@pytest.mark.asyncio
async def test_a_suggestion_applied_on_a_thread_from_overleaf_waits_while_it_has_no_draft(bound_project,
                                                                                         project) -> None:
    """Applying a suggestion asks nothing of the agent on a thread of the page's own. On a
    thread from Overleaf with no draft it waits, for the agent to draft the reply the change
    calls for, and a draft, there before or written after, answers it."""
    pytest.importorskip("mcp")
    from tex_mcp_web import overleaf
    from tex_mcp_web.comments import CommentStore, OverleafDraft, PaperAnchor
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)

    async def read(**arguments) -> dict[str, list[str]]:
        told = json.loads((await mcp.call_tool("read_comments", arguments))[0].text)["comments"]
        return {c["id"]: [e["text"] for e in c.get("entries", [])] for c in told}

    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    server = TexMcpWebServer(Config(main="paper.tex", config_path=project / ".tex-mcp-web.yaml"))
    here = store.add(PaperAnchor(), "Tighten it.")
    brought = store.add(PaperAnchor(), "Could this be shorter?", name="Ann")
    drafted = store.add(PaperAnchor(), "And this one?", name="Ann")
    overleaf.ledger_path(project).write_text(json.dumps({"t1": {"comment": brought.id, "messages": 1},
                                                         "t2": {"comment": drafted.id, "messages": 1}}))
    for asked in (here, brought, drafted):
        proposed = store.suggest(asked.id, asked.updated, "Shorter.",
                                 lambda current: SuggestedEdit("paper.tex", [("Some prose", "Prose")]))
        if asked is drafted:
            proposed = store.draft_overleaf(drafted.id, proposed.updated, OverleafDraft("Shortened it."), {})
        store.apply_suggestion(asked.id, proposed.updated, lambda current: ["paper.tex:5-5"])

    assert "1 new comments" in server._review_line()
    assert list(await read(unanswered=True)) == [brought.id]
    assert await read(new=True) == {brought.id: ["Applied suggestion."]}
    store.draft_overleaf(brought.id, store.get(brought.id).updated, OverleafDraft("Shortened it."), {})
    assert await read(unanswered=True) == {}


@pytest.mark.asyncio
async def test_the_page_answers_only_its_own_pages_at_localhost(client) -> None:
    """Another site open in the browser cannot act through the page: what its page sends
    carries its origin and is turned away, and so is a request addressed by another name,
    as from a site that points its name at this machine. The page's own requests, at any
    of this machine's names, and a tool's, with no origin, are answered."""
    test_client, server = client
    here = f"{test_client.server.host}:{test_client.server.port}"
    assert (await test_client.post("/review-request", headers={"Origin": "https://example.com"})).status == 403
    assert (await test_client.post("/review-request", headers={"Origin": "null"})).status == 403
    assert (await test_client.get("/paper", headers={"Host": f"example.com:{test_client.server.port}"})).status == 403
    assert server.review_calls == 0
    assert (await test_client.post("/review-request", headers={"Origin": f"http://{here}"})).status == 200
    assert (await test_client.get("/paper", headers={"Host": f"localhost:{test_client.server.port}",
                                                      "Origin": f"http://localhost:{test_client.server.port}"})).status == 200
    assert (await test_client.get("/paper")).status == 200
    assert server.review_calls == 1


@pytest.mark.asyncio
async def test_call_button_wakes_a_parked_waiter_and_keeps_an_early_press(client) -> None:
    """The reviewer's press is an interrupt, not a message the agent must be listening for.
    The server keeps the press count and the consumption watermark, so a press made while
    nobody waits answers the next wait at once. The watermark moves only on the waiter's
    ack, so until the ack lands the press is offered again."""
    from tex_mcp_web.comments import PaperAnchor

    test_client, server = client
    wait = f"/wait-review?listener=one&pid={os.getpid()}"

    # Presses with nobody parked are kept, and coalesce into the next wait.
    reply = await (await test_client.post("/review-request")).json()
    assert reply == {"calls": 1, "delivered": False}
    reply = await (await test_client.post("/review-request")).json()
    assert reply == {"calls": 2, "delivered": False}
    early = await test_client.get(wait)
    assert early.status == 200
    assert early.headers["X-Press"] == "2"
    line = await early.text()
    assert line.startswith("[review] reviewer called (press #2)")
    assert "read_comments(new=True)" in line
    # The line counts threads with words for the agent it has not answered: a count of open
    # threads sent an agent to read one it had already answered.
    assert "no new comments" in line
    asked = server.comments.add(PaperAnchor(), "Is this right?")
    assert "1 new comments" in server._review_line()
    server.comments.reply(asked.id, "Yes, checked.", author="agent")
    assert "no new comments" in server._review_line()
    # A reply sent to Overleaf is for the people there, so its thread waits for them.
    server.comments.reply(asked.id, "Sure?", author="human")
    assert "1 new comments" in server._review_line()
    server.comments.sent_to_overleaf(asked.id, "Thanks, fixed!", "")
    assert "no new comments" in server._review_line()

    # Not acked yet (the line may never have reached the harness), so it is offered again.
    again = await test_client.get(wait)
    assert again.status == 200
    assert again.headers["X-Press"] == "2"

    # Acked means consumed: the same waiter restarted parks rather than replaying.
    acked = await (await test_client.post("/wait-review/ack?upto=2")).json()
    assert acked == {"calls": 2, "consumed": 2}
    server.REVIEW_POLL_TIMEOUT = 0.05
    assert (await test_client.get(wait)).status == 204
    server.REVIEW_POLL_TIMEOUT = TexMcpWebServer.REVIEW_POLL_TIMEOUT

    # A parked waiter is released by the press.
    parked = asyncio.ensure_future(test_client.get(wait))
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
    assert (await test_client.get(wait)).status == 204


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
        offered = await second_client.get(f"/wait-review?listener=one&pid={os.getpid()}")
        assert offered.status == 200
        assert offered.headers["X-Press"] == "1"
        await second_client.post("/wait-review/ack?upto=1")
        third = TexMcpWebServer(server.config)
        assert (third.review_calls, third.review_consumed) == (1, 1)
    finally:
        await second_client.close()


@pytest.mark.asyncio
async def test_presses_go_to_one_listener(client) -> None:
    """Two sessions answering one press would edit the same threads, so a press goes to
    one waiter: the one asked to listen last, or else the one that listened first. A
    waiter whose place is taken hears so once, with no press to ack."""
    test_client, server = client
    pid = os.getpid()

    # A waiter with no listener named before it is the one.
    parked = asyncio.ensure_future(test_client.get(f"/wait-review?listener=one&pid={pid}"))
    for _ in range(50):
        if server.review_waiters == 1:
            break
        await asyncio.sleep(0.02)
    assert server.review_listener == "one"
    # A session starting up leaves the presses to a waiter that runs.
    refused = await test_client.post("/wait-review/listen?listener=two&if_free=1")
    assert await refused.json() == {"listening": False}
    # A session asked to listen takes them, and the parked waiter hears it at once.
    taken = await test_client.post("/wait-review/listen?listener=two")
    assert await taken.json() == {"listening": True}
    moved = await parked
    assert moved.headers["X-Moved"] == "1" and "X-Press" not in moved.headers
    assert (await moved.text()).startswith("[moved] another session took over listening")

    await test_client.post("/review-request")
    offered = await test_client.get(f"/wait-review?listener=two&pid={pid}")
    assert offered.headers["X-Press"] == "1"
    assert (await test_client.get(f"/wait-review?listener=one&pid={pid}")).headers["X-Moved"] == "1"
    await test_client.post("/wait-review/ack?upto=1")

    # A listener whose waiter has ended leaves the presses to the next session that starts.
    ended = await asyncio.create_subprocess_exec("true")
    await ended.wait()
    server.REVIEW_POLL_TIMEOUT = 0.05
    assert (await test_client.get(f"/wait-review?listener=two&pid={ended.pid}")).status == 204
    started = await test_client.post("/wait-review/listen?listener=three&if_free=1")
    assert await started.json() == {"listening": True}
    # One that was just named has a while to start its waiter before it counts as ended.
    early = await test_client.post("/wait-review/listen?listener=four&if_free=1")
    assert await early.json() == {"listening": False}
    server.LISTENER_START = 0
    late = await test_client.post("/wait-review/listen?listener=four&if_free=1")
    assert await late.json() == {"listening": True}


@pytest.mark.asyncio
async def test_a_waiter_its_own_session_replaced_hears_that_this_session_listens_on(client, monkeypatch) -> None:
    """tex-mcp started again in a session listens with a new waiter. The waiter the session
    ran before hears that its own session took the presses, and one in another session
    hears that another did."""
    from tex_mcp_web import plugin_monitor

    test_client, server = client
    pid = os.getpid()
    monkeypatch.setattr(plugin_monitor, "session_of", lambda waiter: {pid: 111}.get(waiter))
    await test_client.post("/wait-review/listen?listener=one&session=111")
    parked = asyncio.ensure_future(test_client.get(f"/wait-review?listener=one&pid={pid}"))
    for _ in range(50):
        if server.review_waiters == 1:
            break
        await asyncio.sleep(0.02)
    await test_client.post("/wait-review/listen?listener=two&session=111")
    moved = await parked
    assert moved.headers["X-Moved"] == "1"
    assert await moved.text() == "[moved] this session listens for Call agent with a newer waiter, so this one ends"
    await test_client.post("/wait-review/listen?listener=three&session=222")
    taken = await test_client.get(f"/wait-review?listener=two&pid={pid}")
    assert (await taken.text()).startswith("[moved] another session took over listening")


@pytest.mark.asyncio
@pytest.mark.parametrize("codex", [False, True])
async def test_listen(bound_project, project, monkeypatch, codex):
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
    result = json.loads((await mcp.call_tool("listen", {}))[0].text)
    script, listener = shlex.split(result["command"])
    script = Path(script)
    assert script == project / ".tex-mcp-web" / "wait-review.sh"
    assert script.stat().st_mode & 0o111
    # Claude Code's Monitor takes command, description and timeout_ms (at most 30 minutes).
    assert "Monitor" in result["how"] and "persistent" not in result["how"]
    assert 'description="' in result["how"] and "timeout_ms=1800000" in result["how"]
    assert "write_stdin" not in result["how"]
    # An expiry is not restarted: an agent cannot tell whether the user is still there.
    assert "When it expires, leave it stopped" in result["how"]
    for name in ("codex-mcp-client", "other-client"):
        context.session.client_params.clientInfo.name = name
        selected = json.loads((await mcp.call_tool("listen", {}))[0].text)
        assert "Monitor" not in selected["how"]
        assert "the user asks" in selected["how"]
        assert ("codex queue" in selected["how"]) == (name == "codex-mcp-client")
        assert ('sandbox_permissions="require_escalated"' in selected["how"]) == (name == "codex-mcp-client")
    tool = next(t for t in await mcp.list_tools() if t.name == "listen")
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
    # Each listen() call names a new listener, and the last one is the one presses go to.
    listener = shlex.split(selected["command"])[1]
    waiter = subprocess.Popen(["sh", str(script), listener, *args], stdout=subprocess.PIPE, text=True)
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


@pytest.mark.asyncio
async def test_listen_hands_the_waiter_to_the_plugin_monitor(bound_project, project, monkeypatch, tmp_path):
    """In a session the plugin's monitor serves, listen() hands it the waiter and tells the
    agent not to start one, and the monitor passes a press on as the waiter prints it."""
    pytest.importorskip("mcp")
    import os
    import shlex

    import aiohttp

    from tex_mcp_web import plugin_monitor
    from tex_mcp_web.mcp_server import create_server

    cache = tmp_path / "cache"
    monkeypatch.setattr(plugin_monitor, "DIRECTORY", cache / "tex-mcp-web" / "monitor")
    session = await asyncio.create_subprocess_exec("sleep", "60")
    monkeypatch.setattr(plugin_monitor, "claude_pid", lambda: session.pid)
    monitor = await asyncio.create_subprocess_exec(
        "sh", str(Path(__file__).parents[1] / "plugin" / "monitors" / "review.sh"), str(session.pid),
        stdout=asyncio.subprocess.PIPE, env={**os.environ, "XDG_CACHE_HOME": str(cache)})
    try:
        record = plugin_monitor.DIRECTORY / f"{session.pid}.monitor"
        for _ in range(50):
            if record.exists():
                break
            await asyncio.sleep(0.1)
        assert int(record.read_text()) == monitor.pid
        mcp = create_server(bound_project)
        context = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
            clientInfo=SimpleNamespace(name="claude-code"))))
        monkeypatch.setattr(mcp, "get_context", lambda: context)
        result = json.loads((await mcp.call_tool("listen", {}))[0].text)
        assert "plugin's monitor now runs the command" in result["how"]
        assert "Monitor(" not in result["how"] and "On [review]" in result["how"]
        handed = (plugin_monitor.DIRECTORY / f"{session.pid}.command").read_text().splitlines()
        assert handed[1] == result["command"]
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{bound_project.base_url()}/review-request") as response:
                assert (await response.json())["calls"] == 1
        line = await asyncio.wait_for(monitor.stdout.readline(), 15)
        assert line.decode().startswith("[review] reviewer called (press #1)")
        # The session ending ends the monitor, its waiter, and its record.
        session.kill()
        await asyncio.wait_for(monitor.wait(), 10)
        assert not record.exists()
    finally:
        for process in (session, monitor):
            if process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.asyncio
async def test_a_session_with_the_plugin_listens_from_its_start(bound_project, project, monkeypatch, tmp_path):
    """Where the plugin's monitor runs for the session, the server starts listening with
    no listen() call, unless another session listens to the paper already."""
    pytest.importorskip("mcp")
    import aiohttp

    from tex_mcp_web import plugin_monitor
    from tex_mcp_web.mcp_server import _listen_from_start, create_server

    cache = tmp_path / "cache"
    monkeypatch.setattr(plugin_monitor, "DIRECTORY", cache / "tex-mcp-web" / "monitor")
    session = await asyncio.create_subprocess_exec("sleep", "60")
    monkeypatch.setattr(plugin_monitor, "claude_pid", lambda: session.pid)
    monitor = await asyncio.create_subprocess_exec(
        "sh", str(Path(__file__).parents[1] / "plugin" / "monitors" / "review.sh"), str(session.pid),
        stdout=asyncio.subprocess.PIPE, env={**os.environ, "XDG_CACHE_HOME": str(cache)})
    try:
        shared = bound_project.require_shared()
        base = bound_project.base_url()
        handed = plugin_monitor.DIRECTORY / f"{session.pid}.command"
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{base}/wait-review/listen?listener=other") as response:
                assert (await response.json())["listening"]
            await asyncio.to_thread(_listen_from_start, shared)
            assert not handed.exists()
            # The other session never started its waiter: this one listens in its place.
            shared.server.LISTENER_START = 0
            await asyncio.to_thread(_listen_from_start, shared)
            script, _ = shlex.split(handed.read_text().splitlines()[1])
            assert script == str(project / ".tex-mcp-web" / "wait-review.sh")
            async with client.post(f"{base}/review-request") as response:
                assert (await response.json())["calls"] == 1
        line = await asyncio.wait_for(monitor.stdout.readline(), 15)
        assert line.decode().startswith("[review] reviewer called (press #1)")
        # Asked to listen again, the session restarts its waiter, which hears of no takeover.
        mcp = create_server(bound_project)
        context = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
            clientInfo=SimpleNamespace(name="claude-code"))))
        monkeypatch.setattr(mcp, "get_context", lambda: context)
        told = json.loads((await mcp.call_tool("listen", {}))[0].text)
        assert "plugin's monitor now runs the command" in told["how"]
        async with aiohttp.ClientSession() as client:
            async with client.post(f"{base}/review-request") as response:
                assert (await response.json())["calls"] == 2
        line = await asyncio.wait_for(monitor.stdout.readline(), 15)
        assert line.decode().startswith("[review] reviewer called (press #2)")
        session.kill()
        await asyncio.wait_for(monitor.wait(), 10)
    finally:
        for process in (session, monitor):
            if process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.asyncio
async def test_a_session_that_starts_the_server_again_listens_again(bound_project, project, monkeypatch, tmp_path):
    """A session that came to a paper another session served and listened to leaves it
    alone. When that session dies with the server and its waiter, the next tool call here
    starts the server again and listens from the start again, so a press reaches a waiter."""
    pytest.importorskip("mcp")
    import signal
    import sys

    from tex_mcp_web import plugin_monitor
    from tex_mcp_web.mcp_client import ProjectBinding
    from tex_mcp_web.mcp_server import _listen_in_background

    cache = tmp_path / "cache"
    monkeypatch.setattr(plugin_monitor, "DIRECTORY", cache / "tex-mcp-web" / "monitor")
    session = await asyncio.create_subprocess_exec("sleep", "60")
    monkeypatch.setattr(plugin_monitor, "claude_pid", lambda: session.pid)
    monitor = await asyncio.create_subprocess_exec(
        "sh", str(Path(__file__).parents[1] / "plugin" / "monitors" / "review.sh"), str(session.pid),
        stdout=asyncio.subprocess.PIPE, env={**os.environ, "XDG_CACHE_HOME": str(cache)})
    # The other session serves the paper and keeps a waiter parked on it.
    other = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        "import os, sys, urllib.request\n"
        "from pathlib import Path\n"
        "from tex_mcp_web.config import load_config\n"
        "from tex_mcp_web.mcp_client import SharedProjectServer\n"
        "shared = SharedProjectServer(load_config(Path(sys.argv[1])))\n"
        "shared.ensure()\n"
        "base = f'http://127.0.0.1:{shared.port}'\n"
        "urllib.request.urlopen(urllib.request.Request(f'{base}/wait-review/listen?listener=other', method='POST'))\n"
        "print('serving', flush=True)\n"
        "while True:\n"
        "    urllib.request.urlopen(f'{base}/wait-review?listener=other&pid={os.getpid()}', timeout=120).read()\n",
        str(project / ".tex-mcp-web.yaml"),
        stdout=asyncio.subprocess.PIPE, env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])})
    binding = ProjectBinding(project, on_serve=_listen_in_background)
    try:
        assert await asyncio.wait_for(other.stdout.readline(), 30) == b"serving\n"
        record = plugin_monitor.DIRECTORY / f"{session.pid}.monitor"
        for _ in range(50):
            if record.exists():
                break
            await asyncio.sleep(0.1)
        handed = plugin_monitor.DIRECTORY / f"{session.pid}.command"
        shared = await asyncio.to_thread(binding.connect)
        assert shared.server is None
        await asyncio.sleep(2)
        assert not handed.exists()
        # The other session ends outright, and its server and waiter with it.
        other.send_signal(signal.SIGKILL)
        await other.wait()
        await asyncio.to_thread(binding.connect)
        assert shared.server is not None
        for _ in range(150):
            if shared.server.review_waiters == 1:
                break
            await asyncio.sleep(0.1)
        assert shared.server.review_waiters == 1
    finally:
        await asyncio.to_thread(binding.stop)
        # TERM lets the monitor stop the waiter it runs.
        if monitor.returncode is None:
            monitor.terminate()
            await monitor.wait()
        for process in (other, session):
            if process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.asyncio
async def test_a_monitor_killed_outright_leaves_nothing_a_later_session_takes(monkeypatch, tmp_path):
    """A monitor killed outright leaves its record and the last command. A pid in that record
    now held by another process is no monitor, and the next monitor for a session with the
    same pid drops the command before it records itself."""
    from tex_mcp_web import plugin_monitor

    cache = tmp_path / "cache"
    monkeypatch.setattr(plugin_monitor, "DIRECTORY", cache / "tex-mcp-web" / "monitor")
    plugin_monitor.DIRECTORY.mkdir(parents=True)
    session = await asyncio.create_subprocess_exec("sleep", "60")
    other = await asyncio.create_subprocess_exec("sleep", "60")
    monkeypatch.setattr(plugin_monitor, "claude_pid", lambda: session.pid)
    record = plugin_monitor.DIRECTORY / f"{session.pid}.monitor"
    handed = plugin_monitor.DIRECTORY / f"{session.pid}.command"
    record.write_text(f"{other.pid}\n")
    handed.write_text("1\necho left over\n")
    monitor = None
    try:
        assert plugin_monitor.monitored_session() is None
        monitor = await asyncio.create_subprocess_exec(
            "sh", str(Path(__file__).parents[1] / "plugin" / "monitors" / "review.sh"), str(session.pid),
            stdout=asyncio.subprocess.PIPE, env={**os.environ, "XDG_CACHE_HOME": str(cache)})
        for _ in range(50):
            if record.read_text().strip() == str(monitor.pid):
                break
            await asyncio.sleep(0.1)
        assert plugin_monitor.monitored_session() == session.pid
        assert not handed.exists()
    finally:
        for process in (monitor, other, session):
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.asyncio
async def test_exact_source_selection_revision_and_save(client, project):
    tc, server = client
    source = await (await tc.get("/source?path=paper.tex")).json()
    anchor = {"kind": "source_range", "file": "paper.tex", "line_start": 5,
              "line_end": 5, "column_start": 5, "column_end": 10}
    response = await tc.post("/comments", json={"anchor": anchor, "text": "clarify",
                                             "source_revision": source["revision"]})
    assert response.status == 201
    comment = await response.json()
    assert comment["source_selector"]["exact"] == "prose"
    assert comment["resolved_source"]["column_start"] == 5
    # The same source text moves down and right with automatic compilation off.
    changed = source["text"].replace("Some prose", "\nSome extra prose")
    response = await tc.put("/source?path=paper.tex", json={"text": changed, "revision": source["revision"]})
    assert response.status == 200
    saved = server.comments.get(comment["id"])
    assert not saved.stale
    assert (saved.resolved_source.line_start, saved.resolved_source.column_start,
            saved.resolved_source.column_end) == (6, 11, 16)
    response = await tc.post("/comments", json={"anchor": anchor, "text": "old selection",
                                             "source_revision": source["revision"]})
    assert response.status == 409
    # File watcher updates source anchors without a PDF or a compile. Words written over the
    # commented words take the comment, as on Overleaf, and it says its words were edited.
    (project / "paper.tex").write_text(changed.replace("prose", "replacement"))
    await server.on_file_change(str(project / "paper.tex"))
    moved = server.comments.get(comment["id"])
    assert not moved.stale and moved.reanchored and moved.source_selector.exact == "replacement"


@pytest.mark.asyncio
@pytest.mark.parametrize("columns", [{"column_start": 1}, {"column_start": -1, "column_end": 2},
                                    {"column_start": 3, "column_end": 2},
                                    {"column_start": 0, "column_end": 1000},
                                    {"column_start": 1.5, "column_end": 2}])
async def test_invalid_source_columns_rejected(client, columns):
    tc, server = client
    response = await tc.post("/comments", json={"anchor": {"kind": "source_range", "file": "paper.tex",
                            "line_start": 5, "line_end": 5, **columns}, "text": "invalid"})
    assert response.status == 400
    assert server.comments.list() == []


@pytest.mark.asyncio
async def test_stdio_source_and_reply_use_explicit_detail_reads(project):
    import os
    import socket
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (project / ".tex-mcp-web.yaml").write_text(f"main: paper.tex\nport: {port}\n")
    params = StdioServerParameters(command=sys.executable,
        args=["-c", "from tex_mcp_web.cli import main_mcp; raise SystemExit(main_mcp())"],
        cwd=project, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                          "XDG_CACHE_HOME": str(project / "cache")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = (await session.list_tools()).tools
            assert all(initialized.instructions not in (tool.description or "") for tool in tools)
            # An agent anchors by whole lines; nothing in the surface asks it for a column.
            assert "column_start" not in json.dumps(
                next(t for t in tools if t.name == "write_comments").inputSchema)
            async def call(tool, arguments):
                result = await session.call_tool(tool, arguments)
                assert not result.isError
                return json.loads(result.content[0].text)
            receipt = await call("write_comments", {"action": "add", "text": "Clarify this word",
                "anchor": {"kind": "source_range", "file": "paper.tex",
                           "start": 5, "end": 5}})
            assert set(receipt) == {"id", "status", "rev"}
            reply = await call("write_comments", {"action": "reply", "id": receipt["id"],
                "text": "A detailed explanation. " * 50, "edits": ["paper.tex:5"]})
            assert set(reply) == {"id", "status", "rev"}
            detail = (await call("read_comments", {"ids": [receipt["id"]]}))["comments"][0]
            assert detail["quote"] == "Some prose with \\cite{ref1}."
            assert detail["source"] == "paper.tex:5-5"
            assert detail["replies"][-1]["text"] == "A detailed explanation. " * 50
            # A listing and a full read now come from the one reading tool.
            listing = await call("read_comments", {})
            assert [c["id"] for c in listing["comments"]] == [receipt["id"]]


@pytest.mark.asyncio
async def test_a_discovered_paper_gets_its_review_page_without_a_tool_call(project):
    """Started where a config is found, the MCP server opens the review page on its own,
    beside the handshake, with no tool call."""
    import os
    import socket
    import sys
    import time
    import urllib.request
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (project / ".tex-mcp-web.yaml").write_text(f"main: paper.tex\nport: {port}\n")
    params = StdioServerParameters(command=sys.executable,
        args=["-c", "from tex_mcp_web.cli import main_mcp; raise SystemExit(main_mcp())"],
        cwd=project, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                          "XDG_CACHE_HOME": str(project / "cache")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                        assert response.status == 200
                        break
                except OSError:
                    assert time.monotonic() < deadline, "the review page never answered"
                    await asyncio.sleep(0.2)
            info = json.loads((await session.call_tool("setup_info", {})).content[0].text)
            assert info["bound_config_path"] == str((project / ".tex-mcp-web.yaml").resolve())


@pytest.mark.asyncio
async def test_an_archived_thread_is_listed_apart(client):
    tc, server = client
    kept = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "keep"})).json())["id"]
    working = (await (await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "work"})).json())["id"]
    resp = await tc.post(f"/comments/{kept}/archive", json={})
    assert resp.status == 200 and await resp.json() == {"id": kept, "status": "archived"}
    stored = await (await tc.get(f"/comments/{kept}")).json()
    assert stored["status"] == "archived" and "resolved" not in stored and len(stored["thread"]) == 1
    listed = (await (await tc.get("/comments?status=archived")).json())["comments"]
    assert [c["id"] for c in listed] == [kept]
    listed = (await (await tc.get("/comments?status=open")).json())["comments"]
    assert [c["id"] for c in listed] == [working]
    assert server._comment_summary() == {"open": 1, "resolved": 0, "archived": 1, "stale": 0}
    await tc.post(f"/comments/{kept}/reply", json={"text": "still true"})
    assert (await (await tc.get(f"/comments/{kept}")).json())["status"] == "archived"
    resp = await tc.post(f"/comments/{kept}/reopen", json={})
    assert await resp.json() == {"id": kept, "status": "open"}
    assert (await tc.post("/comments/c-00000000/archive", json={})).status == 404


@pytest.mark.asyncio
async def test_mcp_edit_rewrites_the_agents_own_entry_and_refuses_the_rest(bound_project, project):
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, PaperAnchor
    from tex_mcp_web.mcp_server import create_server, revision_of, short_time

    mcp = create_server(bound_project)

    async def call(tool_name, **arguments):
        return json.loads((await mcp.call_tool(tool_name, arguments))[0].text)

    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    comment = store.add(PaperAnchor(), "Check this")
    replied = await call("write_comments", action="reply", id=comment.id, text="first answer")
    thread = store.get(comment.id).thread
    human, agent = thread
    read = (await call("read_comments", ids=[comment.id]))["comments"][0]
    assert read["replies"][0]["id"] == agent.id and "updated_at" not in read["replies"][0]
    assert read["rev"] == replied["rev"]
    other = store.add(PaperAnchor(), "Another")

    assert "written by human" in (await call("write_comments", action="edit", id=comment.id, entry=human.id, text="x", rev=read["rev"]))["error"]
    assert "stale" in (await call("write_comments", action="edit", id=comment.id, entry=agent.id, text="x", rev="old"))["error"]
    assert "not found" in (await call("write_comments", action="edit", id=comment.id, entry="e-deadbeef", text="x", rev=read["rev"]))["error"]
    assert "not found" in (await call("write_comments", action="edit", id=other.id, entry=agent.id, text="x", rev=revision_of(store.get(other.id).updated)))["error"]
    assert "requires id" in (await call("write_comments", action="edit", entry=agent.id, text="x", rev=read["rev"]))["error"]

    changed = await call("write_comments", action="edit", id=comment.id, entry=agent.id, text="better answer", rev=read["rev"])
    assert changed["id"] == comment.id and changed["rev"] != replied["rev"]
    entry = store.get(comment.id).thread[1]
    assert entry.text == "better answer" and entry.at == agent.at and entry.updated_at is not None
    read = (await call("read_comments", ids=[comment.id]))["comments"][0]
    assert read["replies"][0]["updated_at"] == short_time(entry.updated_at)


@pytest.mark.asyncio
async def test_new_gives_what_the_reviewer_wrote_since_it_last_answered(
    bound_project, project, monkeypatch
):
    """Answering a call took a listing, then a full read of each thread for its rev, then
    the write. new does it in one: the reviewer's words since last time, thread by thread,
    each with the rev a write quotes back, and the moment it read from so a lost turn can
    be taken again."""
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, PaperAnchor
    from tex_mcp_web.mcp_server import create_server, revision_of

    mcp = create_server(bound_project)
    store = CommentStore(project / ".tex-mcp-web" / "comments.json")

    async def call(**arguments):
        return json.loads((await mcp.call_tool("read_comments", arguments))[0].text)

    def at(stamp):
        monkeypatch.setattr("tex_mcp_web.comments._now", lambda: stamp)

    at("2026-01-01T00:00:00+00:00")
    asked = store.add(PaperAnchor(), "first request")
    store.add(PaperAnchor(), "a note of my own", author="agent")

    first = await call(new=True)
    assert [c["id"] for c in first["comments"]] == [asked.id]
    told = first["comments"][0]
    assert told["rev"] == revision_of(store.get(asked.id).updated)
    assert [e["text"] for e in told["entries"]] == ["first request"]
    assert "from" not in first and first["cursor"] == "01-01 09:00"

    # Nothing new, and the agent's own reply is not news to it.
    store.reply(asked.id, "on it", author="agent")
    assert (await call(new=True))["comments"] == []

    at("2026-01-01T00:05:00+00:00")
    store.reply(asked.id, "and one more thing", author="human")
    second = await call(new=True)
    assert second["from"] == "01-01 09:00" and second["cursor"] == "01-01 09:05"
    # Only what was said since; the request and the agent's reply are already in mind.
    assert [e["text"] for e in second["comments"][0]["entries"]] == ["and one more thing"]

    # A turn that went wrong is taken again from the moment it was told about.
    again = await call(since=second["from"])
    assert [c["id"] for c in again["comments"]] == [asked.id]

    # The whole conversation is still one call away, for when it is no longer in mind.
    whole = await call(ids=[asked.id])
    assert [e["text"] for e in whole["comments"][0]["replies"]] == ["on it", "and one more thing"]

    assert "new answers on its own" in (await call(new=True, ids=[asked.id]))["error"]


@pytest.mark.asyncio
async def test_a_first_new_carries_what_stands_unanswered_not_the_history(
    bound_project, project, monkeypatch
):
    """Asked with no cursor, new took every human word ever written: on this paper that
    was 209 threads where one request was waiting. It reports what is unanswered, and a
    closed thread is not where a request arrives."""
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, PaperAnchor
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)
    store = CommentStore(project / ".tex-mcp-web" / "comments.json")

    async def call(**arguments):
        return json.loads((await mcp.call_tool("read_comments", arguments))[0].text)

    monkeypatch.setattr("tex_mcp_web.comments._now", lambda: "2026-01-01T00:00:00+00:00")
    settled = store.add(PaperAnchor(), "long ago")
    store.reply(settled.id, "answered long ago", author="agent")
    closed = store.add(PaperAnchor(), "closed but spoken in")
    store.resolve(closed.id, "", author="human")
    waiting = store.add(PaperAnchor(), "still waiting")

    first = await call(new=True)
    assert [c["id"] for c in first["comments"]] == [waiting.id]

    # Reopening is how a closed thread becomes one again.
    store.reopen(closed.id)
    monkeypatch.setattr("tex_mcp_web.comments._now", lambda: "2026-01-01T00:10:00+00:00")
    store.reply(closed.id, "one more thing after all", author="human")
    assert [c["id"] for c in (await call(new=True))["comments"]] == [closed.id]


@pytest.mark.asyncio
async def test_mcp_delete_refuses_a_thread_the_reviewer_has_written_in(bound_project, project):
    """Deleting used to be the only way to take a wrong proposal down, and it carried the
    reviewer's replies away with it. Withdrawing covers that case now, so delete stops at
    their words and an agent can still clean up a thread it alone wrote."""
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(bound_project)
    store = CommentStore(project / ".tex-mcp-web" / "comments.json")

    async def call_comment(**payload):
        return json.loads((await mcp.call_tool("write_comments", payload))[0].text)

    own = await call_comment(action="add", text="my own note", anchor={"kind": "paper"})
    assert await call_comment(action="delete", id=own["id"]) == {"deleted": own["id"], "ok": True}

    shared = await call_comment(action="add", text="a proposal", anchor={"kind": "paper"})
    store.reply(shared["id"], "is one of them enough?", author="human")
    refused = await call_comment(action="delete", id=shared["id"])
    assert "reviewer has written" in refused["error"]
    kept = store.get(shared["id"])
    assert kept is not None and [e.text for e in kept.thread] == ["a proposal", "is one of them enough?"]


@pytest.mark.asyncio
async def test_a_replaced_suggestion_can_be_applied_again(client, project):
    """A thread carries one live proposal. Reading it again arms Apply once more and
    leaves every entry under it standing, which is what deleting the thread used to cost."""
    tc, server = client
    from tex_mcp_web.server import derive_suggestion

    path = project / "paper.tex"
    comment = await _source_suggestion(tc)
    applied = await (await tc.post(
        f"/comments/{comment['id']}/apply-suggestion", json={"updated": comment["updated"]}
    )).json()
    assert "suggestion" not in applied
    spent = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion", json={"updated": applied["updated"]}
    )
    assert spent.status == 409 and "carries no suggestion" in (await spent.json())["error"]

    second = server.comments.suggest(
        comment["id"], applied["updated"], "tighter still",
        lambda current: derive_suggestion(project, current.resolved_source, [("Clear prose", "Plain prose")]),
    )
    assert second.suggestion.changes == [("Clear prose", "Plain prose")]
    response = await tc.post(
        f"/comments/{comment['id']}/apply-suggestion", json={"updated": second.updated}
    )
    assert response.status == 200
    assert "Plain prose with \\cite{ref1}." in path.read_text(encoding="utf-8")
    assert [entry.text for entry in server.comments.get(comment["id"]).thread] == [
        "Use the suggested wording.", "Applied suggestion.", "tighter still", "Applied suggestion.",
    ]


@pytest.mark.asyncio
async def test_list_comments_shows_an_opening_and_counts_the_rest(bound_project, project):
    """A listing is for picking which threads to open. A paper accumulates hundreds of
    them, so it shows an opening of each request and says how many it left out rather
    than returning every word of every thread."""
    pytest.importorskip("mcp")
    from tex_mcp_web.comments import CommentStore, PaperAnchor
    from tex_mcp_web.mcp_server import REQUEST_PREVIEW, create_server

    mcp = create_server(bound_project)
    store = CommentStore(project / ".tex-mcp-web" / "comments.json")
    long_request = "x" * (REQUEST_PREVIEW + 50)
    store.add(PaperAnchor(), long_request)
    for index in range(4):
        store.add(PaperAnchor(), f"short {index}")

    async def listed(**payload):
        return json.loads((await mcp.call_tool("read_comments", payload))[0].text)

    everything = await listed()
    assert len(everything["comments"]) == 5 and "older" not in everything
    assert everything["comments"][0]["request"] == "x" * REQUEST_PREVIEW + "…"

    # The cap keeps the newest threads, which are the ones just written, and still reads
    # in the order the paper does.
    capped = await listed(limit=2)
    assert [c["request"] for c in capped["comments"]] == ["short 2", "short 3"]
    assert capped["older"] == 3
    # The listing carries what a write has to quote back, so answering a thread does not
    # cost a read of it first. A thread grows; the listing row does not.
    row = everything["comments"][-1]
    assert set(row) == {"id", "rev", "status", "kind", "request", "thread_entries",
                        "last_human_at"}
    replied = json.loads((await mcp.call_tool("write_comments", {
        "action": "reply", "id": row["id"], "text": "answered from the listing alone"}))[0].text)
    assert replied["id"] == row["id"]
    store.reply(row["id"], "and the reviewer moved it on", author="human")
    stale = json.loads((await mcp.call_tool("write_comments", {
        "action": "edit", "id": row["id"], "entry": "e-nope", "text": "x",
        "rev": row["rev"]}))[0].text)
    assert "stale" in stale["error"]


def test_unknown_arguments_are_refused_and_schemas_are_compact(tmp_path: Path) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    from tex_mcp_web.mcp_client import ProjectBinding
    from tex_mcp_web.mcp_server import create_server

    mcp = create_server(ProjectBinding(tmp_path))
    with pytest.raises(ToolError) as error:
        asyncio.run(mcp.call_tool("read_comments", {"no_such": 1}))
    assert str(error.value).startswith("read_comments does not take no_such. It takes: ")
    tools = asyncio.run(mcp.list_tools())
    # FastMCP adds a title to every schema and a null default to every optional field.
    schemas = json.dumps([tool.inputSchema for tool in tools])
    assert '"title": "' not in schemas and '"default": null' not in schemas
    # A field that is itself named title stays.
    write = next(tool for tool in tools if tool.name == "write_comments").inputSchema
    assert any("title" in model["properties"] for model in write["$defs"].values())


# --- Translation panel ---

from tex_mcp_web.translate import split_paragraphs  # noqa: E402


def _translation_project(project, server, runner):
    from tex_mcp_web.synctex import SourcePosition, SyncTeXData
    from tex_mcp_web.translate import Translator, TranslationStore

    (project / "body.tex").write_text(
        "% a plan kept in a comment\nFirst paragraph of the body text.\n\nSecond paragraph of the body text.\n\n"
        "Third paragraph on another page.\n")
    (project / "abs.tex").write_text("Abstract words are here.\n")
    server.synctex_data = SyncTeXData(
        pdf_to_source={1: [(10.0, SourcePosition("abs.tex", 1)), (80.0, SourcePosition("body.tex", 2)),
                           (100.0, SourcePosition("body.tex", 4))],
                       2: [(90.0, SourcePosition("body.tex", 6))]},
        source_to_pdf={}, input_files={1: "abs.tex", 2: "body.tex"})
    server.config.translation_language = "Korean"
    server.translator = Translator(TranslationStore(project / ".tex-mcp-web" / "translations-korean.json"),
                                   [], project, runner=runner, language="Korean")
    server.translator.start = lambda paragraphs: server.translator.translate(paragraphs) or True


def _echo_runner(calls):
    def runner(prompt, cwd):
        blocks = json.loads(prompt.split("Paragraphs (JSON):\n", 1)[1])
        calls.append(list(blocks.values()))
        return json.dumps({"t": {pid: "T " + text.split()[0] for pid, text in blocks.items()}})
    return runner


@pytest.mark.asyncio
async def test_translations_cover_a_whole_file_once(client, project):
    tc, server = client
    calls = []
    _translation_project(project, server, _echo_runner(calls))

    first = await (await tc.get("/translations?file=body.tex&page=2")).json()
    assert first["status"] == "pending" and first["file"] == "body.tex"
    assert first["language"] == "Korean"
    assert [(p["line_start"], p["text"], p["on_page"]) for p in first["paragraphs"]] == [
        (2, None, False), (4, None, False), (6, None, True)]
    assert calls == [["First paragraph of the body text.", "Second paragraph of the body text.",
                      "Third paragraph on another page."]]

    again = await (await tc.get("/translations?file=body.tex")).json()
    assert again["status"] == "ready"
    assert [p["text"] for p in again["paragraphs"]] == ["T First", "T Second", "T Third"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_translations_by_page_follow_the_file_most_on_that_page(client, project):
    tc, server = client
    _translation_project(project, server, _echo_runner([]))
    data = await (await tc.get("/translations?page=1")).json()
    assert data["file"] == "body.tex"
    assert [p["on_page"] for p in data["paragraphs"]] == [True, True, False]


@pytest.mark.asyncio
async def test_translations_reject_bad_requests(client, project):
    tc, server = client
    _translation_project(project, server, _echo_runner([]))
    assert (await tc.get("/translations")).status == 400
    assert (await tc.get("/translations?page=x")).status == 400
    assert (await tc.get("/translations?file=../secret.tex")).status == 400
    assert (await tc.get("/translations?file=missing.tex")).status == 404
    assert (await tc.get("/translations?page=9")).status == 404
    server.synctex_data = None
    assert (await tc.get("/translations?page=1")).status == 404


@pytest.mark.asyncio
async def test_translations_need_a_configured_language(client, project):
    tc, server = client
    assert server.config.translation_language is None
    assert (await (await tc.get("/paper")).json())["translation_language"] is None
    response = await tc.get("/translations?file=paper.tex")
    assert response.status == 404
    assert "translation" in (await response.json())["error"]


@pytest.mark.asyncio
async def test_each_language_keeps_its_own_translations(client, project):
    tc, server = client
    calls = []
    _translation_project(project, server, _echo_runner(calls))
    await tc.get("/translations?file=body.tex")
    assert (project / ".tex-mcp-web" / "translations-korean.json").exists()

    server.config.translation_language = "Japanese"
    assert (await (await tc.get("/paper")).json())["translation_language"] == "Japanese"
    translator = server._translator("Japanese")
    assert translator.language == "Japanese"
    assert translator.store.path.name == "translations-japanese.json"
    assert translator.missing(split_paragraphs((project / "body.tex").read_text(), "body.tex"))
    assert server._translator("Japanese") is translator



async def test_overleaf_threads_become_comments_on_their_words(client) -> None:
    """Each open Overleaf thread comes in as a comment on the words it was written about,
    the occurrence nearest the line Overleaf gives, with its replies after it and every
    entry marked with its Overleaf author. A
    resolved thread stays out, words not in the source put the comment on the paper, and
    the same threads again add only what is new."""
    _, server = client

    def message(name, content):
        return {"user": {"name": name}, "content": content, "timestamp": "2026-11-04T00:00:00Z"}

    export = {
        "threads": {
            "t1": {"resolved": False, "messages": [message("A. Reviewer", "Which prose?"),
                                                   message("Co Author", "The intro.")]},
            "t2": {"resolved": True, "messages": [message("A. Reviewer", "Done.")]},
            "t3": {"resolved": False, "messages": [message("A. Reviewer", "Gone from here.")]},
        },
        "comments": [
            {"thread_id": "t1", "pathname": "/paper.tex", "line": 8, "col": 5, "anchored_text": "prose"},
            {"thread_id": "t2", "pathname": "/paper.tex", "line": 5, "col": 0, "anchored_text": "Some"},
            {"thread_id": "t3", "pathname": "/paper.tex", "line": 2, "col": 0, "anchored_text": "not in the paper"},
        ],
    }
    reply = await server._bring_in_overleaf(export, new=True)
    assert reply == {"added": 2, "replies": 0, "resolved": 1, "unplaced": 1}
    comments = {c.thread[0].text.split("\n")[0]: c for c in server.comments.list()}
    placed = comments["Which prose?"]
    assert placed.anchor.to_dict() == {"kind": "source_range", "file": "paper.tex", "line_start": 5,
                                       "line_end": 5, "column_start": 5, "column_end": 10}
    assert [(e.name, e.text) for e in placed.thread] == [("A. Reviewer", "Which prose?"), ("Co Author", "The intro.")]
    unplaced = comments["Gone from here."]
    assert unplaced.anchor.kind == "paper" and unplaced.thread[0].text == "Gone from here."
    assert (await server._overleaf_status())["unplaced"] == {unplaced.id: "paper.tex:2"}

    export["threads"]["t1"]["messages"].append(message("A. Reviewer", "Thanks."))
    reply = await server._bring_in_overleaf(export, new=True)
    assert reply == {"added": 0, "replies": 1, "resolved": 1, "unplaced": 0}
    assert (server.comments.get(placed.id).thread[-1].name, server.comments.get(placed.id).thread[-1].text) == (
        "A. Reviewer", "Thanks.")
    assert len(server.comments.list()) == 2

    # The words reach the file, by a pull or by hand, and the comment moves onto them.
    paper = server.watch_dir / "paper.tex"
    paper.write_text(paper.read_text().replace("Some methods.", "Some methods, not in the paper before."))
    await server.on_file_change(str(paper))
    moved = server.comments.get(unplaced.id)
    assert moved.anchor.kind == "source_range" and moved.anchor.file == "paper.tex"
    assert (await server._overleaf_status())["unplaced"] == {}


async def test_overleaf_threads_are_kept_as_overleaf_has_them(client) -> None:
    """A thread here follows its thread on Overleaf. Where the file holds Overleaf's text,
    by hash, the comment sits exactly where Overleaf has it and moves when Overleaf moves
    it; where the file differs, this file's own edits keep it on its words. A message
    reworded or deleted there is reworded or deleted here, and a thread deleted there is
    deleted here. A reading that finds no threads at all deletes nothing."""
    import hashlib

    _, server = client
    paper = server.watch_dir / "paper.tex"
    text = paper.read_text()
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    def message(key, name, content):
        return {"id": key, "user": {"name": name}, "content": content}

    export = {
        "threads": {"t1": {"resolved": False, "messages": [message("m1", "A. Reviewer", "Which prose?"),
                                                           message("m2", "Co Author", "The intro.")]},
                    "t2": {"resolved": False, "messages": [message("m3", "A. Reviewer", "And this?")]}},
        "comments": [
            # "Some" is in the file twice; the offset says which, and the hash says it holds here.
            {"thread_id": "t1", "pathname": "/paper.tex", "line": 1, "anchored_text": "Some",
             "offset": text.index("Some methods"), "text_sha": sha},
            {"thread_id": "t2", "pathname": "/paper.tex", "line": 8, "anchored_text": "methods"},
        ],
    }
    await server._bring_in_overleaf(export, new=True)
    one = next(c for c in server.comments.list() if c.thread[0].text.endswith("Which prose?"))
    assert (one.anchor.line_start, one.anchor.column_start) == (8, 0)

    # Moved on Overleaf, in a file holding its text: it moves exactly.
    export["comments"][0].update(anchored_text="Introduction", offset=text.index("Introduction"), line=3)
    await server._bring_in_overleaf(export, new=False)
    moved = server.comments.get(one.id).anchor
    assert (moved.line_start, moved.column_start, moved.column_end) == (3, 9, 21)
    # The file edited here: Overleaf's text is no longer this one, and it stays on its words.
    paper.write_text("% a line here\n" + text)
    export["comments"][0].update(anchored_text="prose", offset=text.index("prose"))
    await server._bring_in_overleaf(export, new=False)
    assert server.comments.get(one.id).anchor.to_dict() == moved.to_dict()
    paper.write_text(text)

    # A reply reworded and one deleted on Overleaf.
    export["threads"]["t1"]["messages"][1]["content"] = "The introduction."
    await server._bring_in_overleaf(export, new=False)
    assert [(e.name, e.text) for e in server.comments.get(one.id).thread] == [
        ("A. Reviewer", "Which prose?"), ("Co Author", "The introduction.")]
    server.comments.reply(one.id, "Claude, look.", "human")
    del export["threads"]["t1"]["messages"][1]
    await server._bring_in_overleaf(export, new=False)
    assert [(e.name, e.text) for e in server.comments.get(one.id).thread] == [
        ("A. Reviewer", "Which prose?"), (None, "Claude, look.")]

    # Resolved there, resolved here; open there again, open here.
    export["threads"]["t1"]["resolved"] = True
    await server._bring_in_overleaf(export, new=False)
    assert server.comments.get(one.id).status == "resolved"
    export["threads"]["t1"]["resolved"] = False
    await server._bring_in_overleaf(export, new=False)
    assert server.comments.get(one.id).status == "open"

    # A reading that finds no threads at all deletes nothing; one without this thread does.
    await server._bring_in_overleaf({"threads": {}, "comments": []}, new=False)
    assert server.comments.get(one.id) is not None
    del export["threads"]["t1"]
    export["comments"].pop(0)
    await server._bring_in_overleaf(export, new=False)
    assert server.comments.get(one.id) is None and len(server.comments.list()) == 1


@pytest.mark.asyncio
async def test_a_message_from_overleaf_goes_by_its_writers_name_and_is_answered_not_edited(client) -> None:
    """A message brought in from Overleaf carries the name Overleaf gives its writer apart
    from its words, also one brought in when the name began the words, and the page does
    not edit what Overleaf keeps as written there."""
    from tex_mcp_web import overleaf
    from tex_mcp_web.comments import PaperAnchor

    http, server = client
    message = lambda key, content: {"id": key, "user": {"name": "A. Reviewer"}, "content": content}
    older = server.comments.add(PaperAnchor(), "[Overleaf · A. Reviewer] Which prose?")
    oldest = server.comments.add(PaperAnchor(), "[Overleaf · A. Reviewer] And this?")
    overleaf.ledger_path(server.watch_dir).write_text(json.dumps({
        "t1": {"comment": older.id, "entries": {"m1": older.thread[0].id}},
        "t2": {"comment": oldest.id, "messages": 1}}))
    export = {"threads": {"t1": {"messages": [message("m1", "Which prose?")]},
                          "t2": {"messages": [message("m2", "And this?")]}},
              "comments": [{"thread_id": "t1", "pathname": "/paper.tex", "line": 1, "anchored_text": "Some"},
                           {"thread_id": "t2", "pathname": "/paper.tex", "line": 1, "anchored_text": "Some"}]}
    await server._bring_in_overleaf(export, new=False)
    for comment, words in ((older, "Which prose?"), (oldest, "And this?")):
        assert [(e.name, e.text) for e in server.comments.get(comment.id).thread] == [("A. Reviewer", words)]

    refused = await http.post(f"/comments/{older.id}/edit", json={"index": 0, "text": "Mine now."})
    assert refused.status == 400
    assert (await refused.json())["error"] == "thread entry 0 is on Overleaf, which keeps it as written there: reply instead"
    assert server.comments.get(older.id).thread[0].text == "Which prose?"


@pytest.mark.asyncio
async def test_overleafs_threads_are_listed_in_the_order_the_paper_reads_them(client) -> None:
    """Threads that came in from Overleaf all at once, in Overleaf's order, are listed by
    where the paper reads them, a file at the \\input that brings it in, and those on no
    line of the paper last. The comments written here keep the order they were written in."""
    from tex_mcp_web import overleaf
    from tex_mcp_web.comments import PaperAnchor

    http, server = client
    (server.watch_dir / "tex").mkdir()
    (server.watch_dir / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nOpening words.\n\\input{tex/a}\n"
        "Between.  \\input{tex/b}\nClosing words.\n\\end{document}\n")
    (server.watch_dir / "tex" / "a.tex").write_text("First of a.\nSecond.\nThird.\n")
    (server.watch_dir / "tex" / "b.tex").write_text("First of b.\nSecond.\n")
    (server.watch_dir / "notes.tex").write_text("Not in the build.\n")

    def on(file: str, line: int, column: int = 0):
        return {"anchor": SourceRangeAnchor(file=file, line_start=line, line_end=line, column_start=column,
                                            column_end=column + 1),
                "resolved_source": ResolvedSource(file=file, line_start=line, line_end=line,
                                                  column_start=column, column_end=column + 1)}

    mine = server.comments.add(PaperAnchor(), "Written here first.")
    brought = {words: server.comments.add(text=words, **where) for words, where in (
        ("b 2", on("tex/b.tex", 2)), ("main 6", on("paper.tex", 6)), ("a 3", on("tex/a.tex", 3)),
        ("notes 1", on("notes.tex", 1)), ("a 1 at 4", on("tex/a.tex", 1, 4)), ("main 5", on("paper.tex", 5)),
        ("main 3", on("paper.tex", 3)), ("a 1", on("tex/a.tex", 1)))}
    brought["paper"] = server.comments.add(PaperAnchor(), "paper")
    overleaf.ledger_path(server.watch_dir).write_text(json.dumps(
        {f"t{index}": {"comment": comment.id} for index, comment in enumerate(brought.values())}))
    mine_later = server.comments.add(PaperAnchor(), "Written here after.")

    listed = [c["thread"][0]["text"] for c in (await (await http.get("/comments")).json())["comments"]]
    assert listed == [mine.text, mine_later.text, "main 3", "a 1", "a 1 at 4", "a 3", "main 5", "b 2",
                      "main 6", "notes 1", "paper"]


# --- Completion lists for the source editor ---

@pytest.mark.asyncio
async def test_completions_list_what_the_paper_defines(client, project):
    tc, server = client
    (project / "sec").mkdir()
    (project / "sec" / "a.tex").write_text(
        "\\label{fig:a} % \\label{fig:hidden}\n\\newcommand{\\cpp}[1]{#1 CPP}\n"
        "\\newenvironment{sketch}{}{}\n")
    (project / "refs.bib").write_text(
        "@IEEEtranBSTCTL{BSTcontrol, CTLuse_forced_etal = \"yes\"}\n"
        "@article{kim23, title = {Fusion Cells}, author = {Kim, A. and Lee, B.}, year = 2023}\n")
    (project / "fig").mkdir()
    (project / "fig" / "flow.pdf").write_bytes(b"%PDF")
    (project / "fig" / "notes.txt").write_text("not a picture")
    (project / ".tex-mcp-web").mkdir(exist_ok=True)
    (project / ".tex-mcp-web" / "cached.png").write_bytes(b"x")
    server.config.ignore = ["*.pre-*"]
    (project / "fig" / "flow.pdf.pre-old").write_bytes(b"%PDF")

    data = await (await tc.get("/completions")).json()

    assert data["labels"] == [
        {"name": "sec:intro", "file": "paper.tex", "line": 4},
        {"name": "sec:methods", "file": "paper.tex", "line": 7},
        {"name": "fig:a", "file": "sec/a.tex", "line": 1},
    ]
    assert data["citations"] == [{"key": "kim23", "title": "Fusion Cells", "author": "Kim et al.",
                                  "year": "2023", "file": "refs.bib", "line": 2}]
    assert data["commands"] == [{"name": "cpp", "args": 1}]
    assert data["environments"] == ["sketch"]
    assert data["tex_files"] == ["paper.tex", "sec/a.tex"]
    assert data["bib_files"] == ["refs.bib"]
    assert data["graphics"] == ["fig/flow.pdf"]

