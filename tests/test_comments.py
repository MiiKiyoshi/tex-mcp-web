"""Tests for comment anchors, exact source selectors, and reattachment."""

import json
from pathlib import Path

import pymupdf
import pytest

from tex_mcp_web.comments import (
    AreaAnchor,
    CommentStore,
    PageSelection,
    PaperAnchor,
    ResolvedSource,
    SectionAnchor,
    SourceRangeAnchor,
    SourceSelector,
    TextSelectionAnchor,
    anchor_from_dict,
    capture_source_selector,
    find_source_selector,
    locate_pdf_quote,
    pdf_digest,
)


def make_pdf(path: Path, text: str, y: float = 72) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(72, y, 400, y + 120), text, fontsize=11)
    document.save(path)
    document.close()


def text_anchor(digest: str, quote: str = "selected text") -> TextSelectionAnchor:
    return TextSelectionAnchor(
        quote=quote,
        selection=PageSelection(
            page=2,
            bbox=(70.0, 100.0, 250.0, 130.0),
            rects=[(70.0, 100.0, 250.0, 112.0), (70.0, 118.0, 150.0, 130.0)],
        ),
        pdf_digest=digest,
    )


def test_text_selection_anchor_roundtrip():
    anchor = text_anchor("abc")
    assert anchor_from_dict(anchor.to_dict()) == anchor


def test_agent_comment_view_hides_storage_only_anchor_data(store: CommentStore):
    from tex_mcp_web.mcp_server import _agent_comment_to_dict

    comment = store.add(text_anchor("digest", quote="selected text"), "tighten this")
    view = _agent_comment_to_dict(comment)
    assert view == {
        "id": comment.id,
        "status": "open",
        "kind": "text_selection",
        "comment": "tighten this",
        "quote": "selected text",
        "page": 2,
        "bbox": [70.0, 100.0, 250.0, 130.0],
    }
    assert "selection" not in view
    assert "pdf_digest" not in view
    assert "rects" not in view


def test_area_anchor_roundtrip():
    anchor = AreaAnchor(page=3, bbox=(10.5, 20.5, 100.0, 200.0), pdf_digest="abc")
    assert anchor_from_dict(anchor.to_dict()) == anchor


@pytest.mark.parametrize(
    "anchor",
    [
        SectionAnchor(title="Methods", label="sec:methods"),
        SourceRangeAnchor(file="intro.tex", line_start=3, line_end=5),
        PaperAnchor(),
    ],
)
def test_non_pdf_anchor_roundtrip(anchor):
    assert anchor_from_dict(anchor.to_dict()) == anchor


def test_unknown_anchor_fails():
    with pytest.raises(ValueError, match="Unknown anchor kind"):
        anchor_from_dict({"kind": "removed-anchor"})


def test_capture_source_selector_keeps_context_outside_selection(tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text("before 1\nbefore 2\nselected 1\nselected 2\nafter 1\nafter 2\n")
    selector = capture_source_selector(source, 3, 4, context=2)
    assert selector == SourceSelector(
        exact="selected 1\nselected 2",
        prefix="before 1\nbefore 2",
        suffix="after 1\nafter 2",
    )


def test_capture_source_selector_rejects_invalid_range(tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text("one\n")
    assert capture_source_selector(source, 0, 1) is None
    assert capture_source_selector(source, 1, 2) is None


def test_source_selector_follows_insertion_without_widening(tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text("before\nselected 1\nselected 2\nafter\n")
    selector = capture_source_selector(source, 2, 3, context=1)
    source.write_text("new 1\nnew 2\nbefore\nselected 1\nselected 2\nafter\n")
    assert find_source_selector(selector, source) == (4, 5)


def test_source_selector_uses_context_to_disambiguate(tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text("first\nsame\nafter first\nsecond\nsame\nafter second\n")
    selector = SourceSelector(exact="same", prefix="second", suffix="after second")
    assert find_source_selector(selector, source) == (5, 5)


def test_source_selector_rejects_ambiguous_duplicate(tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text("same\nother\nsame\n")
    selector = SourceSelector(exact="same", prefix="", suffix="")
    assert find_source_selector(selector, source) is None


def test_locate_pdf_quote_returns_per_line_rectangles(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    quote = "A measurement-integrity design keeps official results separate from self reports."
    make_pdf(pdf, quote)
    selection = locate_pdf_quote(pdf, quote)
    assert selection is not None
    assert selection.page == 1
    assert selection.rects
    assert selection.bbox[0] >= 70


def test_locate_pdf_quote_reconstructs_line_end_hyphen(tmp_path: Path):
    pdf = tmp_path / "hyphenated.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "where interconnect estimates become ac-", fontsize=11)
    page.insert_text((72, 88), "curate enough to act on", fontsize=11)
    document.save(pdf)
    document.close()

    selection = locate_pdf_quote(
        pdf, "where interconnect estimates become accurate enough to act on"
    )
    assert selection is not None
    assert len(selection.rects) == 2


def test_locate_pdf_quote_rejects_duplicate_without_hint(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, "duplicate phrase\n\nduplicate phrase")
    assert locate_pdf_quote(pdf, "duplicate phrase") is None


@pytest.fixture
def store(tmp_path: Path) -> CommentStore:
    return CommentStore(tmp_path / "comments.json")


def test_store_crud(store: CommentStore):
    comment = store.add(PaperAnchor(), "review this")
    assert store.get(comment.id).text == "review this"
    replied = store.reply(comment.id, "reply", author="human")
    assert replied.thread[-1].text == "reply"
    resolved = store.resolve(comment.id, "done", edits=["paper.tex:1"])
    assert resolved.status == "resolved"
    assert resolved.thread[-1].author == "agent"
    assert resolved.thread[-1].edits == ["paper.tex:1"]
    assert store.delete(comment.id)
    assert store.get(comment.id) is None


def test_store_rejects_old_schema(tmp_path: Path):
    path = tmp_path / "comments.json"
    path.write_text(json.dumps({"version": 2, "comments": []}))
    store = CommentStore(path)
    with pytest.raises(ValueError, match="unsupported comment store version"):
        store.list()


def test_source_range_refresh_follows_exact_lines(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text("before\nselected one\nselected two\nafter\n")
    selector = capture_source_selector(source, 2, 3, context=1)
    comment = store.add(
        SourceRangeAnchor(file="paper.tex", line_start=2, line_end=3),
        "review",
        resolved_source=ResolvedSource("paper.tex", 2, 3),
        source_selector=selector,
    )
    source.write_text("new\nbefore\nselected one\nselected two\nafter\n")
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, "unrelated")
    store.refresh_anchors(tmp_path, pdf)
    refreshed = store.get(comment.id)
    assert refreshed.resolved_source == ResolvedSource("paper.tex", 3, 4)
    assert not refreshed.stale


def test_area_anchor_becomes_stale_after_pdf_changes(store: CommentStore, tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, "first")
    comment = store.add(AreaAnchor(1, (10, 10, 30, 30), pdf_digest(pdf)), "area")
    make_pdf(tmp_path / "replacement.pdf", "second")
    (tmp_path / "replacement.pdf").replace(pdf)
    newly_stale = store.refresh_anchors(tmp_path, pdf)
    assert comment.id in newly_stale
    assert store.get(comment.id).stale


def test_text_anchor_refreshes_pdf_rectangles(store: CommentStore, tmp_path: Path):
    quote = "exact rendered quote"
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, quote, y=72)
    old_selection = locate_pdf_quote(pdf, quote)
    comment = store.add(
        TextSelectionAnchor(
            quote=quote,
            selection=old_selection,
            pdf_digest=pdf_digest(pdf),
        ),
        "text",
    )
    replacement = tmp_path / "replacement.pdf"
    make_pdf(replacement, quote, y=220)
    replacement.replace(pdf)
    store.refresh_anchors(tmp_path, pdf)
    refreshed = store.get(comment.id)
    assert not refreshed.stale
    assert refreshed.anchor.pdf_digest == pdf_digest(pdf)
    assert refreshed.anchor.selection.bbox[1] > old_selection.bbox[1]
