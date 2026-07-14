"""Smoke tests for the v0.4.0 server.

We don't run latexmk here; instead we instantiate the server, drive the
HTTP API directly, and check that comments + paper state plumbing works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tex_mcp_web.config import Config
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
    assert data["status"] == "resolved"
    assert data["thread"][-1]["edits"] == ["paper.tex:1-10"]


@pytest.mark.asyncio
async def test_dismiss_marks_dismissed(client):
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "x"})
    cid = (await resp.json())["id"]

    resp = await tc.post(f"/comments/{cid}/dismiss", json={"reason": "skip"})
    assert (await resp.json())["status"] == "dismissed"


@pytest.mark.asyncio
async def test_reopen_endpoint_is_gone(client):
    """v0.5.0 dropped the reopen verb."""
    tc, _ = client
    resp = await tc.post("/comments", json={"anchor": {"kind": "paper"}, "text": "x"})
    cid = (await resp.json())["id"]
    resp = await tc.post(f"/comments/{cid}/reopen", json={})
    assert resp.status == 404  # route doesn't exist


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
    doc.new_page().insert_text((72, 72), "Page 1")
    doc.new_page().insert_text((72, 72), "Page 2")
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
async def test_text_selection_is_verified_and_resolved_as_a_range(
    client_with_pdf, monkeypatch
):
    from tex_mcp_web.synctex import SourceRangePosition

    tc, server = client_with_pdf
    monkeypatch.setattr(
        "tex_mcp_web.server.selection_to_source",
        lambda **kwargs: SourceRangePosition("paper.tex", 5, 5),
    )
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {
                "kind": "text_selection",
                "quote": "Page 1",
                "segments": [{
                    "page": 1,
                    "bbox": [70, 60, 110, 80],
                    "rects": [[70, 60, 110, 80]],
                }],
                "start": {"page": 1, "index": 0},
                "end": {"page": 1, "index": 6},
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
    assert data["anchor"]["segments"][0]["rects"]


@pytest.mark.asyncio
async def test_text_selection_rejects_old_pdf_digest(client_with_pdf):
    tc, _ = client_with_pdf
    resp = await tc.post(
        "/comments",
        json={
            "anchor": {
                "kind": "text_selection",
                "quote": "Page 1",
                "segments": [{
                    "page": 1,
                    "bbox": [70, 60, 110, 80],
                    "rects": [[70, 60, 110, 80]],
                }],
                "start": {"page": 1, "index": 0},
                "end": {"page": 1, "index": 6},
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
