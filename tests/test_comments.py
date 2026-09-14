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
    canonicalize_pdf_selection,
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
    }
    assert "selection" not in view
    assert "pdf_digest" not in view
    assert "rects" not in view


def test_agent_comment_view_exposes_source_without_pdf_coordinates(store: CommentStore):
    from tex_mcp_web.mcp_server import _agent_comment_to_dict

    comment = store.add(
        text_anchor("digest", quote="selected text"),
        "tighten this",
        resolved_source=ResolvedSource("tex/intro.tex", 8, 10),
    )

    view = _agent_comment_to_dict(comment)
    assert view["source"] == {
        "file": "tex/intro.tex",
        "line_start": 8,
        "line_end": 10,
    }
    assert "bbox" not in view


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


def test_canonicalize_pdf_selection_uses_geometry_when_text_engines_disagree(
    tmp_path: Path,
):
    pdf = tmp_path / "formula.pdf"
    make_pdf(pdf, "S = 30 T + 50 P")
    left = locate_pdf_quote(pdf, "S = 30")
    right = locate_pdf_quote(pdf, "T + 50 P")
    assert left is not None
    assert right is not None
    hint = PageSelection(
        page=1,
        bbox=(left.bbox[0], left.bbox[1], right.bbox[2], right.bbox[3]),
        rects=[left.bbox, right.bbox],
    )

    canonical = canonicalize_pdf_selection(pdf, "S=30 T+50 P", hint)

    assert canonical is not None
    quote, selection = canonical
    assert quote == "S = 30 T + 50 P"
    assert selection.page == 1
    assert selection.rects


def test_canonicalize_pdf_selection_handles_separate_equation_number(
    tmp_path: Path,
):
    pdf = tmp_path / "numbered-formula.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "S=30 dT + 50 dP,")
    page.insert_text((300, 72), "(1)")
    document.save(pdf)
    document.close()

    formula = locate_pdf_quote(pdf, "S=30 dT + 50 dP,")
    number = locate_pdf_quote(pdf, "(1)")
    assert formula is not None
    assert number is not None
    hint = PageSelection(
        page=1,
        bbox=(formula.bbox[0], formula.bbox[1], number.bbox[2], number.bbox[3]),
        rects=[formula.bbox, number.bbox],
    )

    canonical = canonicalize_pdf_selection(
        pdf,
        "S = 30 dT + 50 dP, (1)",
        hint,
    )

    assert canonical is not None
    quote, selection = canonical
    assert quote == "S=30 dT + 50 dP,\n(1)"
    assert selection.page == 1
    assert selection.rects


def test_canonicalize_pdf_selection_trims_text_below_loose_browser_rect(
    tmp_path: Path,
):
    pdf = tmp_path / "loose-formula-selection.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "S=30 dT + 50 dP,")
    page.insert_text((300, 72), "(1)")
    page.insert_text((72, 84), "next line is outside the selection")
    document.save(pdf)
    document.close()

    formula = locate_pdf_quote(pdf, "S=30 dT + 50 dP,")
    number = locate_pdf_quote(pdf, "(1)")
    next_line = locate_pdf_quote(pdf, "next line is outside the selection")
    assert formula is not None
    assert number is not None
    assert next_line is not None
    loose_rect = (
        formula.bbox[0],
        formula.bbox[1],
        number.bbox[2],
        next_line.bbox[1] + 1,
    )
    hint = PageSelection(page=1, bbox=loose_rect, rects=[loose_rect])

    canonical = canonicalize_pdf_selection(
        pdf,
        "S = 30 dT + 50 dP, (1)",
        hint,
    )

    assert canonical is not None
    quote, _ = canonical
    assert quote == "S=30 dT + 50 dP,\n(1)"


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


def test_store_closes_without_a_thread_entry_when_no_message_is_given(
    store: CommentStore,
):
    comment = store.add(PaperAnchor(), "fix the wording")

    resolved = store.resolve(comment.id, summary="")
    assert resolved.status == "resolved"
    assert len(resolved.thread) == 1

    with_edits = store.resolve(comment.id, summary="", edits=["paper.tex:1"])
    assert with_edits.thread[-1].text == ""
    assert with_edits.thread[-1].edits == ["paper.tex:1"]

    with pytest.raises(ValueError, match="empty"):
        store.reply(comment.id, text="   ", author="agent")


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


def test_text_anchor_refresh_reuses_one_pdf_index(
    store: CommentStore, tmp_path: Path, monkeypatch
):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, "first rendered quote\nsecond rendered quote")
    for quote in ("first rendered quote", "second rendered quote"):
        selection = locate_pdf_quote(pdf, quote)
        assert selection is not None
        store.add(
            TextSelectionAnchor(
                quote=quote,
                selection=selection,
                pdf_digest=pdf_digest(pdf),
            ),
            "text",
        )

    original_open = pymupdf.open
    open_count = 0

    def counted_open(*args, **kwargs):
        nonlocal open_count
        open_count += 1
        return original_open(*args, **kwargs)

    monkeypatch.setattr("tex_mcp_web.comments.pymupdf.open", counted_open)
    store.refresh_anchors(tmp_path, pdf)

    assert open_count == 1


def test_text_anchor_captures_and_follows_resolved_source(
    store: CommentStore, tmp_path: Path
):
    source = tmp_path / "paper.tex"
    source.write_text("before\nselected source\nafter\n")
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, "rendered selection")
    selection = locate_pdf_quote(pdf, "rendered selection")
    comment = store.add(
        TextSelectionAnchor(
            quote="rendered selection",
            selection=selection,
            pdf_digest=pdf_digest(pdf),
        ),
        "text",
    )

    store.refresh_anchors(
        tmp_path,
        pdf,
        text_resolver=lambda _: ResolvedSource("paper.tex", 2, 2),
    )
    attached = store.get(comment.id)
    assert attached.resolved_source == ResolvedSource("paper.tex", 2, 2)
    assert attached.source_selector.exact == "selected source"

    source.write_text("new\nbefore\nselected source\nafter\n")
    store.refresh_anchors(
        tmp_path,
        pdf,
        text_resolver=lambda _: pytest.fail("selector should reattach first"),
    )
    assert store.get(comment.id).resolved_source == ResolvedSource("paper.tex", 3, 3)


@pytest.mark.parametrize("start,end,expected", [((1, 3), (1, 6), "b c"),
                                             ((1, 3), (2, 2), "b cd\nne"),
                                             ((1, 0), (2, 0), "a😀b cd\n"),
                                             ((2, 0), (3, 0), "next\n")])
def test_character_selector_utf16_and_newlines(tmp_path, start, end, expected):
    from tex_mcp_web.comments import capture_source_selector, find_source_characters, ResolvedSource
    path = tmp_path / "paper.tex"
    path.write_text("a😀b cd\nnext\n", encoding="utf-8")
    selector = capture_source_selector(path, start[0], end[0], column_start=start[1], column_end=end[1])
    assert selector.exact == expected
    assert find_source_characters(selector, path, path.name) == ResolvedSource(path.name, start[0], end[0], start[1], end[1])
    path.write_text("inserted\n" + path.read_text(), encoding="utf-8")
    found = find_source_characters(selector, path, path.name)
    assert (found.line_start, found.column_start, found.line_end, found.column_end) == (start[0] + 1, start[1], end[0] + 1, end[1])


def test_character_selector_does_not_guess_changed_or_ambiguous_text(tmp_path):
    from tex_mcp_web.comments import capture_source_selector, find_source_characters
    path = tmp_path / "paper.tex"
    path.write_text("before exact words after")
    selector = capture_source_selector(path, 1, 1, column_start=7, column_end=18)
    path.write_text("before exact  words after")
    assert find_source_characters(selector, path, path.name) is None
    path.write_text("before exact words after\nbefore exact words after")
    assert find_source_characters(selector, path, path.name) is None


def test_character_selector_rejects_half_surrogate(tmp_path):
    from tex_mcp_web.comments import capture_source_selector
    path = tmp_path / "paper.tex"
    path.write_text("a😀b")
    assert capture_source_selector(path, 1, 1, column_start=2, column_end=3) is None


def test_a_thread_kept_as_reference_stays_readable_and_comes_back(store: CommentStore):
    """Reference is a third status beside open and resolved, for a thread worth reading
    again: it takes replies without changing, and goes back to either with one flip."""
    comment = store.add(PaperAnchor(), "keep this reasoning")
    kept = store.keep_as_reference(comment.id)
    assert kept.status == "reference" and kept.resolved is None
    assert len(kept.thread) == 1                      # a status flip adds no entry
    assert [c.id for c in store.list(status="reference")] == [comment.id]
    assert store.list(status="open") == [] and store.list(status="resolved") == []

    replied = store.reply(comment.id, "one more thought", author="agent")
    assert replied.status == "reference" and len(replied.thread) == 2
    assert store.resolve(comment.id, summary="").status == "resolved"
    assert store.keep_as_reference(comment.id).resolved is None      # resolved -> reference
    assert store.reopen(comment.id).status == "open"
    assert len(store.get(comment.id).thread) == 2      # the flips left the thread alone
    assert "reference" not in json.dumps(CommentStore(store.path).get(comment.id).to_dict()["status"])
