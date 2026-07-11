"""Tests for tex_mcp_web.imaging — page / region rendering, SyncTeX resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole module if pymupdf isn't installed.
fitz = pytest.importorskip("fitz")

from tex_mcp_web.imaging import (
    ImagingError,
    render_page,
    render_region,
    resolve_source_to_region,
)
from tex_mcp_web.synctex import PDFPosition, SyncTeXData


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    """A two-page PDF with a couple lines of text on each."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Page 1 line 1")
    p1.insert_text((72, 144), "Page 1 line 2")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Page 2 line 1")
    out = tmp_path / "tiny.pdf"
    doc.save(out)
    doc.close()
    return out


# ---------------------------------------------------------------------------
# render_page
# ---------------------------------------------------------------------------


def test_render_page_returns_png(tiny_pdf: Path):
    png = render_page(tiny_pdf, page=1, dpi=72)
    assert png.startswith(PNG_MAGIC)
    assert len(png) > 100


def test_render_page_second_page(tiny_pdf: Path):
    png = render_page(tiny_pdf, page=2, dpi=72)
    assert png.startswith(PNG_MAGIC)


def test_render_page_out_of_range(tiny_pdf: Path):
    with pytest.raises(ImagingError, match="out of range"):
        render_page(tiny_pdf, page=999)


def test_render_page_missing_pdf(tmp_path: Path):
    with pytest.raises(ImagingError, match="not found"):
        render_page(tmp_path / "nope.pdf", page=1)


def test_render_page_dpi_changes_size(tiny_pdf: Path):
    low = render_page(tiny_pdf, page=1, dpi=72)
    high = render_page(tiny_pdf, page=1, dpi=200)
    # Higher DPI should produce a larger PNG.
    assert len(high) > len(low)


# ---------------------------------------------------------------------------
# render_region
# ---------------------------------------------------------------------------


def test_render_region_basic(tiny_pdf: Path):
    png = render_region(tiny_pdf, page=1, bbox=(60, 60, 200, 100), dpi=72)
    assert png.startswith(PNG_MAGIC)


def test_render_region_clips_oversized_bbox(tiny_pdf: Path):
    """Bbox extending past the page should clip silently rather than crash."""
    png = render_region(tiny_pdf, page=1, bbox=(0, 0, 9999, 9999), dpi=72)
    assert png.startswith(PNG_MAGIC)


def test_render_region_empty_bbox_raises(tiny_pdf: Path):
    """Zero-size bbox with no margin is rejected (would render 0 pixels)."""
    with pytest.raises(ImagingError):
        render_region(tiny_pdf, page=1, bbox=(100, 100, 100, 100), dpi=72, margin=0)


def test_render_region_zero_size_with_margin_succeeds(tiny_pdf: Path):
    """A point-like bbox with the default margin renders the surrounding area."""
    png = render_region(tiny_pdf, page=1, bbox=(100, 100, 100, 100), dpi=72)
    assert png.startswith(PNG_MAGIC)


def test_render_region_out_of_range_page(tiny_pdf: Path):
    with pytest.raises(ImagingError, match="out of range"):
        render_region(tiny_pdf, page=99, bbox=(0, 0, 50, 50))


# ---------------------------------------------------------------------------
# resolve_source_to_region
# ---------------------------------------------------------------------------


def _synctex(map_data: dict) -> SyncTeXData:
    """Build a SyncTeXData with only source_to_pdf populated."""
    return SyncTeXData(
        pdf_to_source={},
        source_to_pdf=map_data,
        input_files={},
    )


def test_resolve_source_to_region_single_page():
    sx = _synctex({
        ("intro.tex", 10): [PDFPosition(page=1, x=72, y=100, width=400, height=10)],
        ("intro.tex", 11): [PDFPosition(page=1, x=72, y=120, width=380, height=10)],
        ("intro.tex", 12): [PDFPosition(page=1, x=72, y=140, width=400, height=10)],
    })
    result = resolve_source_to_region(sx, "intro.tex", 10, 12)
    assert result is not None
    page, (x1, y1, x2, y2) = result
    assert page == 1
    # x1 should be left edge minus pad
    assert x1 < 72
    # x2 should reach right edge of widest line plus pad
    assert x2 > 72 + 400
    # y1 covers ascender of first line; y2 reaches baseline of last line.
    assert y1 < 100
    assert y2 >= 140


def test_resolve_source_to_region_picks_majority_page():
    """When source spans pages, pick the one with the most matches."""
    sx = _synctex({
        ("paper.tex", 1): [PDFPosition(page=1, x=72, y=100, width=400, height=10)],
        ("paper.tex", 2): [PDFPosition(page=2, x=72, y=100, width=400, height=10)],
        ("paper.tex", 3): [PDFPosition(page=2, x=72, y=120, width=400, height=10)],
        ("paper.tex", 4): [PDFPosition(page=2, x=72, y=140, width=400, height=10)],
    })
    result = resolve_source_to_region(sx, "paper.tex", 1, 4)
    assert result is not None
    page, _ = result
    assert page == 2


def test_resolve_source_to_region_no_match():
    sx = _synctex({})
    assert resolve_source_to_region(sx, "missing.tex", 1, 5) is None


def test_resolve_source_to_region_partial_match_ok():
    """Lines outside the SyncTeX coverage are ignored, not failures."""
    sx = _synctex({
        ("intro.tex", 5): [PDFPosition(page=1, x=72, y=100, width=400, height=10)],
    })
    # Range 1..10 only has line 5 covered; should still return.
    result = resolve_source_to_region(sx, "intro.tex", 1, 10)
    assert result is not None
    assert result[0] == 1


# ---------------------------------------------------------------------------
# page_text_hashes / diff_page_hashes — change-detection signal for the
# agent's "did my edit do what I expected" loop.
# ---------------------------------------------------------------------------


def test_page_text_hashes_basic(tiny_pdf: Path):
    from tex_mcp_web.imaging import page_text_hashes
    hashes = page_text_hashes(tiny_pdf)
    assert len(hashes) == 2
    assert all(len(h) == 40 for h in hashes)
    assert hashes[0] != hashes[1]


def test_page_text_hashes_missing_pdf(tmp_path: Path):
    from tex_mcp_web.imaging import page_text_hashes
    assert page_text_hashes(tmp_path / "nope.pdf") == []


def test_diff_page_hashes_no_changes():
    from tex_mcp_web.imaging import diff_page_hashes
    hashes = ["a", "b", "c"]
    assert diff_page_hashes(hashes, hashes) == []


def test_diff_page_hashes_some_changes():
    from tex_mcp_web.imaging import diff_page_hashes
    assert diff_page_hashes(["a", "b", "c"], ["a", "X", "c"]) == [2]
    assert diff_page_hashes(["a", "b", "c"], ["a", "X", "Y"]) == [2, 3]


def test_diff_page_hashes_empty_prev_means_no_changes():
    """First compile has no prior; report no changes (we don't want the
    agent to think every page changed on session start)."""
    from tex_mcp_web.imaging import diff_page_hashes
    assert diff_page_hashes([], ["a", "b"]) == []


def test_diff_page_hashes_appended_pages():
    """A new page added at the end counts as changed."""
    from tex_mcp_web.imaging import diff_page_hashes
    assert diff_page_hashes(["a", "b"], ["a", "b", "c"]) == [3]


def test_page_text_hashes_changes_when_content_edits(tiny_pdf: Path, tmp_path: Path):
    """Editing one page's content changes only that page's hash."""
    import fitz
    from tex_mcp_web.imaging import page_text_hashes

    h1 = page_text_hashes(tiny_pdf)

    edited = tmp_path / "edited.pdf"
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Page 1 line 1")
    p1.insert_text((72, 144), "Page 1 line 2")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "DIFFERENT TEXT")
    doc.save(edited)
    doc.close()

    h2 = page_text_hashes(edited)
    assert h1[0] == h2[0]
    assert h1[1] != h2[1]
