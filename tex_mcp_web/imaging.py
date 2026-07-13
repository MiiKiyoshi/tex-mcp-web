"""PDF page / region rendering via pymupdf.

Used by the ``/image`` HTTP endpoint and the ``image`` MCP tool
to give Claude a visual on the rendered output.  Rendering is the one
job the agent genuinely can't do for itself: PDF rendering nuances
(figure placement, equation typesetting, overfull-box wrap, table
spacing) only show up in the actual PDF.

pymupdf is an optional dependency (``pip install tex-mcp-web[image]``).
We surface a friendly message when it's missing rather than crashing.
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    import fitz  # pymupdf
except ImportError:
    fitz = None  # type: ignore[assignment]

from .synctex import SyncTeXData

logger = logging.getLogger(__name__)


class ImagingError(RuntimeError):
    """Raised when rendering can't proceed (missing dep, bad inputs, etc)."""


def _open_pdf(pdf_path: Path, page: int):
    """Open *pdf_path*, validate *page*, return ``(doc, page_obj)``.

    Caller owns ``doc.close()``.  Raises :class:`ImagingError` on missing
    pymupdf, missing file, or out-of-range page.
    """
    if fitz is None:
        raise ImagingError(
            "PDF imaging requires pymupdf. "
            "Install with: pip install 'tex-mcp-web[image]'"
        )
    if not pdf_path.exists():
        raise ImagingError(f"PDF not found: {pdf_path}")
    doc = fitz.open(pdf_path)
    n = len(doc)
    if page < 1 or page > n:
        doc.close()
        raise ImagingError(f"page {page} out of range (1..{n})")
    return doc, doc[page - 1]


# ---------------------------------------------------------------------------
# Page / region rendering
# ---------------------------------------------------------------------------


def render_page(pdf_path: Path, page: int, dpi: int = 150, gray: bool = False) -> bytes:
    """Render an entire page as PNG bytes.

    Args:
        pdf_path: Path to compiled PDF.
        page: 1-indexed page number.
        dpi: Render resolution.  150 is roughly retina-equivalent for
            on-screen display; 300 for high-detail extraction.
        gray: Render grayscale — halves the PNG size of a text page.

    Raises:
        ImagingError: pymupdf missing, or *page* out of range.
    """
    doc, page_obj = _open_pdf(pdf_path, page)
    try:
        cs = fitz.csGRAY if gray else fitz.csRGB
        return page_obj.get_pixmap(dpi=dpi, colorspace=cs).tobytes("png")
    finally:
        doc.close()


def render_region(
    pdf_path: Path,
    page: int,
    bbox: tuple[float, float, float, float],
    dpi: int = 150,
    margin: float = 6.0,
    gray: bool = False,
) -> bytes:
    """Render a rectangular region of a page as PNG bytes.

    *bbox* is in PDF points (1/72 inch), top-left origin (matching the
    coordinate system in ``PdfRegionAnchor`` and PDF.js viewports).
    A small *margin* is added around the crop so the rendered region
    has visible breathing room.
    """
    doc, page_obj = _open_pdf(pdf_path, page)
    try:
        x1, y1, x2, y2 = bbox
        # Clip to page bounds; pymupdf will raise on out-of-page rects.
        rect = fitz.Rect(
            max(0.0, x1 - margin),
            max(0.0, y1 - margin),
            min(page_obj.rect.width, x2 + margin),
            min(page_obj.rect.height, y2 + margin),
        )
        if rect.is_empty or rect.is_infinite:
            raise ImagingError(f"empty bbox after clip: {tuple(bbox)} on {page_obj.rect}")
        cs = fitz.csGRAY if gray else fitz.csRGB
        return page_obj.get_pixmap(dpi=dpi, clip=rect, colorspace=cs).tobytes("png")
    finally:
        doc.close()


def expand_region(
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
    min_width: float = 240.0,
    min_height: float = 70.0,
    margin: float = 12.0,
) -> tuple[float, float, float, float]:
    """Grow *bbox* to a readable context window, clamped to the page.

    Comment anchors are often a single word (~20x14pt); rendered as-is
    they carry no context for the agent to reason about. Expand around
    the center to at least ``min_width`` x ``min_height`` plus *margin*.
    """
    x1, y1, x2, y2 = bbox
    x1 -= margin
    y1 -= margin
    x2 += margin
    y2 += margin
    if x2 - x1 < min_width:
        pad = (min_width - (x2 - x1)) / 2
        x1 -= pad
        x2 += pad
    if y2 - y1 < min_height:
        pad = (min_height - (y2 - y1)) / 2
        y1 -= pad
        y2 += pad
    return (
        max(0.0, x1),
        max(0.0, y1),
        min(page_width, x2),
        min(page_height, y2),
    )


def page_size(pdf_path: Path, page: int) -> tuple[float, float]:
    """Return ``(width, height)`` of *page* in PDF points."""
    doc, page_obj = _open_pdf(pdf_path, page)
    try:
        return page_obj.rect.width, page_obj.rect.height
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# SyncTeX -> region resolution
# ---------------------------------------------------------------------------


def resolve_image_target(
    *,
    synctex: SyncTeXData | None,
    comment_lookup,
    page: int | None,
    bbox: tuple[float, float, float, float] | None,
    source: tuple[str, int, int] | None,
    comment_id: str | None,
    watch_dir: Path | None = None,
) -> tuple[int, tuple[float, float, float, float] | None]:
    """Map ``(page | source | comment_id)`` to ``(page, optional bbox)``.

    For ``comment_id``, dispatch goes through ``anchor.image_target``;
    each anchor type knows how to render itself.  ``page``/``source`` are
    direct constructions of an anchor + render.

    Raises :class:`ValueError` with a user-facing message on bad inputs.
    """
    from .comments import (
        PaperAnchor,
        ResolveContext,
        SourceRangeAnchor,
    )

    primary = sum(1 for v in (page, source, comment_id) if v not in (None, ""))
    if primary != 1:
        raise ValueError("specify exactly one of page, source, or comment")

    ctx = ResolveContext(
        watch_dir=watch_dir or Path("."),
        synctex=synctex,
    )

    if comment_id:
        c = comment_lookup(comment_id)
        if c is None:
            raise ValueError(f"no comment {comment_id}")
        if isinstance(c.anchor, PaperAnchor):
            raise ValueError("paper anchors have no PDF region")
        # Prefer the comment's pre-resolved source location.  The anchor
        # itself may not carry enough context here (section anchors
        # need the document structure, which the comment_id caller
        # doesn't have); the comment was resolved at creation time and
        # that resolution is what we want.
        target = c.anchor.image_target(ctx)
        if target is None and c.resolved_source is not None:
            if synctex is None:
                raise ValueError("no SyncTeX data; cannot resolve comment region")
            target = resolve_source_to_region(
                synctex,
                c.resolved_source.file,
                c.resolved_source.line_start,
                c.resolved_source.line_end,
            )
        if target is None:
            if synctex is None:
                raise ValueError("no SyncTeX data; cannot resolve comment region")
            raise ValueError("comment's anchor has no PDF coverage")
        return target

    if source is not None:
        # Synthesize a SourceRangeAnchor and dispatch through its method.
        anchor = SourceRangeAnchor(file=source[0], line_start=source[1], line_end=source[2])
        target = anchor.image_target(ctx)
        if target is None:
            if synctex is None:
                raise ValueError("no SyncTeX data")
            raise ValueError("no PDF region for this source range")
        return target

    # page mode (with optional bbox); no anchor dispatch needed.
    if page is None:
        raise ValueError("page must be an integer")
    return page, bbox


def page_text_hashes(pdf_path: Path) -> list[str]:
    """Compute one content hash per page in *pdf_path*.

    Used to detect which pages changed between compiles.  We hash the
    extracted text rather than rendered pixels: text-level diffs catch
    real edits, are cheap, and don't fluctuate with anti-aliasing
    noise.  Pages that differ only in figure layout but not text
    content won't show up; that's a known limitation.

    Returns a list of hex digests, one per page.  Empty list if
    pymupdf is missing or the file can't be opened.
    """
    if fitz is None or not pdf_path.exists():
        return []
    import hashlib
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []
    try:
        return [
            hashlib.sha1(doc[i].get_text("text").encode("utf-8")).hexdigest()
            for i in range(len(doc))
        ]
    finally:
        doc.close()


def diff_page_hashes(prev: list[str], curr: list[str]) -> list[int]:
    """Return 1-indexed page numbers that changed between *prev* and *curr*.

    Pages added or removed (length mismatch) are reported as changed
    starting from the first differing index.  An empty *prev* (first
    compile of the session) yields no changes; the agent doesn't need
    a "first compile" diff.
    """
    if not prev or not curr:
        return []
    n_common = min(len(prev), len(curr))
    changed = [i + 1 for i in range(n_common) if prev[i] != curr[i]]
    if len(curr) > len(prev):
        changed.extend(range(len(prev) + 1, len(curr) + 1))
    return changed


def resolve_source_to_region(
    synctex: SyncTeXData,
    file: str,
    line_start: int,
    line_end: int,
    pad: float = 4.0,
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Map a source line range to ``(page, bbox)`` using SyncTeX.

    Strategy:
      - Collect every SyncTeX position for ``(file, line)`` in the
        requested range.
      - Group by page; pick the page with the most matches.
      - Compute the bounding box of matched positions on that page,
        using the per-position width/height SyncTeX records.
      - Add *pad* on each side for breathing room.

    Returns None if nothing matched (e.g., the range falls inside a
    figure environment, which has no SyncTeX coverage).
    """
    by_page: dict[int, list] = {}
    for line in range(line_start, line_end + 1):
        for pos in synctex.source_to_pdf.get((file, line), []):
            by_page.setdefault(pos.page, []).append(pos)

    if not by_page:
        return None

    best_page = max(by_page, key=lambda k: len(by_page[k]))
    positions = by_page[best_page]

    x1 = min(p.x for p in positions)
    x2 = max(p.x + max(p.width, 0) for p in positions)
    # SyncTeX y is the baseline; height extends upward (toward smaller y
    # in top-left coord systems).  Using the recorded height covers the
    # ascender; we approximate descender with the same pad value.
    y1 = min(p.y - max(p.height, 0) for p in positions)
    y2 = max(p.y for p in positions)

    return best_page, (x1 - pad, y1 - pad, x2 + pad, y2 + pad)
