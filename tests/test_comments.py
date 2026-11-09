"""Tests for comment anchors, exact source selectors, and reattachment."""

import json
import secrets
from pathlib import Path

import pymupdf
import pytest

from tex_mcp_web.comments import (
    STORE_VERSION,
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
    area_digest,
    pdf_digest,
    swap_fragments,
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
    from tex_mcp_web.mcp_server import _agent_comment_to_dict, revision_of

    comment = store.add(text_anchor("digest", quote="selected text"), "tighten this")
    view = _agent_comment_to_dict(comment, store.path.parent)
    assert view == {
        "id": comment.id,
        "status": "open",
        "kind": "text_selection",
        "comment": "tighten this",
        "quote": "selected text",
        "page": 2,
        "rev": revision_of(comment.updated),
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

    view = _agent_comment_to_dict(comment, store.path.parent)
    # The same spelling an edits entry uses, not an object saying the same thing.
    assert view["source"] == "tex/intro.tex:8-10"
    assert "bbox" not in view


def test_agent_view_of_an_area_is_its_page_and_box_in_whole_points(store: CommentStore):
    from tex_mcp_web.mcp_server import _agent_comment_to_dict

    comment = store.add(AreaAnchor(2, (60.4, 70.6, 200.2, 99.5), "digest", "pixels"), "this panel")
    view = _agent_comment_to_dict(comment, store.path.parent)
    assert (view["kind"], view["page"], view["bbox"]) == ("area", 2, [60, 71, 200, 100])
    assert "pdf_digest" not in view and "region" not in view


def test_area_anchor_roundtrip():
    anchor = AreaAnchor(page=3, bbox=(10.5, 20.5, 100.0, 200.0), pdf_digest="abc", region="def")
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


def test_an_area_stays_while_its_pixels_do(store: CommentStore, tmp_path: Path):
    """Every compile writes new bytes, so an area is stale only when what it covers changed."""
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf, "first")
    box = (60, 60, 200, 100)
    comment = store.add(AreaAnchor(1, box, pdf_digest(pdf), area_digest(pdf, 1, box)), "area")

    def compile_to(text: str, y: float = 72) -> list[str]:
        make_pdf(tmp_path / "replacement.pdf", text, y)
        document = pymupdf.open(tmp_path / "replacement.pdf")
        document.set_metadata({"title": f"build {secrets.token_hex(4)}"})
        document.save(pdf)
        document.close()
        return store.refresh_anchors(tmp_path, pdf)

    assert compile_to("first") == []
    assert not store.get(comment.id).stale
    assert store.get(comment.id).anchor.pdf_digest == pdf_digest(pdf)
    assert compile_to("second") == [comment.id]
    assert store.get(comment.id).stale
    assert compile_to("first") == []
    assert not store.get(comment.id).stale


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


SENTENCE = (
    "We present \\emph{AlignSearch}, a framework that searches pre-route optimization "
    "algorithms and evaluates the resulting design states in terms of Routed Quality: "
    "timing and power after detailed routing."
)


def test_swap_fragments_rewrites_only_the_quoted_parts():
    """The agent quotes what changes; everything it did not quote is carried over, so a
    three-word change costs three words and not the whole sentence twice."""
    rewritten = swap_fragments(SENTENCE, [
        ("searches pre-route optimization algorithms",
         "searches the procedures of pre-route optimization algorithms rather than their parameters"),
        ("after detailed routing.",
         "after detailed routing; the name reflects that the search is aligned with routed results."),
    ])
    assert "rather than their parameters and evaluates" in rewritten
    assert rewritten.endswith("aligned with routed results.")
    assert "in terms of Routed Quality" in rewritten      # untouched text is kept verbatim


def test_swap_fragments_applies_back_to_front_so_earlier_edits_do_not_move_later_ones():
    assert swap_fragments("one two three", [("one", "a much longer first word"), ("three", "3")]) == (
        "a much longer first word two 3")


def test_swap_fragments_refuses_what_it_cannot_place_exactly():
    with pytest.raises(ValueError, match="at least one edit"):
        swap_fragments(SENTENCE, [])
    with pytest.raises(ValueError, match="which text to replace"):
        swap_fragments(SENTENCE, [("", "x")])
    with pytest.raises(ValueError, match="not found in the source"):
        swap_fragments(SENTENCE, [("post-route optimization", "x")])
    # An ambiguous quote comes back with the lines it was found on, so it can be
    # extended on purpose rather than by guessing.
    with pytest.raises(ValueError, match="occurs 2 times, on lines 1, 1"):
        swap_fragments(SENTENCE, [("and", "x")])
    with pytest.raises(ValueError, match="same text"):
        swap_fragments(SENTENCE, [("searches pre-route", "x"), ("pre-route optimization", "y")])
    with pytest.raises(ValueError, match="leave the text as it is"):
        swap_fragments(SENTENCE, [("Routed Quality", "Routed Quality")])


def test_a_version_6_store_loses_its_before_and_after_proposals(tmp_path: Path):
    """Through version 6 a proposal was one before/after pair with no file beside it, so
    it cannot be read as the pieces it changes. The threads stay; the proposals do not."""
    path = tmp_path / "comments.json"
    path.write_text(json.dumps({"version": 6, "comments": [
        {"id": "c-1", "anchor": {"kind": "source_range", "file": "a.tex",
                                 "line_start": 1, "line_end": 1},
         "status": "open", "suggestion": {"old": "was", "new": "becomes"},
         "suggestion_applied": False,
         "thread": [{"id": "e-1", "author": "human", "at": "2026-01-01T00:00:00+00:00",
                     "text": "tighten this"}],
         "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00"},
    ]}), encoding="utf-8")

    store = CommentStore(path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == STORE_VERSION
    kept = store.get("c-1")
    assert kept.suggestion is None
    assert [entry.text for entry in kept.thread] == ["tighten this"]


def test_a_version_5_store_is_raised_and_its_reference_threads_become_archived(tmp_path: Path):
    """The status was called "reference" through version 5. Opening such a store rewrites
    it once, so threads set aside before the rename stay listed under the new name."""
    path = tmp_path / "comments.json"
    path.write_text(json.dumps({"version": 5, "comments": [
        {"id": "c-1", "anchor": {"kind": "paper"}, "status": "reference",
         "thread": [{"id": "e-1", "author": "human", "at": "2026-01-01T00:00:00+00:00", "text": "set aside"}],
         "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00"},
        {"id": "c-2", "anchor": {"kind": "paper"}, "status": "open",
         "thread": [{"id": "e-2", "author": "human", "at": "2026-01-01T00:00:00+00:00", "text": "still open"}],
         "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00"},
    ]}), encoding="utf-8")

    store = CommentStore(path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == STORE_VERSION
    assert [c.id for c in store.list(status="archived")] == ["c-1"]
    assert [c.id for c in store.list(status="open")] == ["c-2"]
    assert store.get("c-1").thread[0].text == "set aside"   # the rewrite left the thread alone

    CommentStore(path)                                      # opening again changes nothing
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == STORE_VERSION


def test_a_version_7_store_keeps_its_drafts_ranges_found_again_by_their_words(tmp_path: Path):
    """Version 7 named the lines an Overleaf draft describes by fixed strings, and those
    lines may have moved since. Each range starts stale on them, shown with no line, until
    a refresh finds it by the words it held when drafted, and from then on edits carry it."""
    (tmp_path / "a.tex").write_text("New first line.\nOpening words.\nMiddle words.\n\nClosing words here.\n")
    path = tmp_path / "comments.json"
    path.write_text(json.dumps({"version": 7, "comments": [
        {"id": "c-1", "anchor": {"kind": "paper"}, "status": "open",
         "thread": [{"id": "e-1", "author": "human", "at": "2026-01-01T00:00:00+00:00", "text": "which lines?"}],
         "overleaf_draft": {"text": "These.", "edits": ["a.tex:1-2", "a.tex:3", "a.tex:4-4"],
                            "checks": [{"file": "a.tex", "text": "Opening words.\nMiddle words."}]},
         "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00"},
    ]}), encoding="utf-8")

    store = CommentStore(path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == STORE_VERSION
    ranges = store.get("c-1").overleaf_draft.ranges
    assert [(r.label, r.drafted, r.stale) for r in ranges] == [
        ("a.tex:1-2", "Opening words.\nMiddle words.", True), ("a.tex:3-3", "", True), ("a.tex:4-4", "", True)]
    assert store.get("c-1").overleaf_draft.checks == [{"file": "a.tex", "text": "Opening words.\nMiddle words."}]

    store.refresh_source_anchors(tmp_path)
    ranges = store.get("c-1").overleaf_draft.ranges
    assert [(r.label, r.stale) for r in ranges] == [("a.tex:2-3", False), ("a.tex:3-3", True), ("a.tex:4-4", True)]
    (tmp_path / "a.tex").write_text("\\top\nNew first line.\nOpening words.\nMiddle words.\n\nClosing words here.\n")
    store.refresh_source_anchors(tmp_path)
    assert store.get("c-1").overleaf_draft.ranges[0].label == "a.tex:3-4"


def test_an_archived_thread_stays_readable_and_comes_back(store: CommentStore):
    """Archived is a third status beside open and resolved, for a thread worth reading
    again: it takes replies without changing, and goes back to either with one flip."""
    comment = store.add(PaperAnchor(), "keep this reasoning")
    kept = store.archive(comment.id)
    assert kept.status == "archived" and kept.resolved is None
    assert len(kept.thread) == 1                      # a status flip adds no entry
    assert [c.id for c in store.list(status="archived")] == [comment.id]
    assert store.list(status="open") == [] and store.list(status="resolved") == []

    replied = store.reply(comment.id, "one more thought", author="agent")
    assert replied.status == "archived" and len(replied.thread) == 2
    assert store.resolve(comment.id, summary="").status == "resolved"
    assert store.archive(comment.id).resolved is None      # resolved -> archived
    assert store.reopen(comment.id).status == "open"
    assert len(store.get(comment.id).thread) == 2      # the flips left the thread alone
    assert "archived" not in json.dumps(CommentStore(store.path).get(comment.id).to_dict()["status"])


# --- reattach by surrounding context after the selected text itself was edited ---

_CTX_BEFORE = "The paragraph before the comment talks about something else entirely.\n"
_CTX_AFTER = "\nThe paragraph after the comment also stays exactly as it was written.\n"


def _source_comment(store, source, start, end):
    selector = capture_source_selector(source, start[0], end[0], column_start=start[1], column_end=end[1])
    return store.add(
        SourceRangeAnchor(file="paper.tex", line_start=start[0], line_end=end[0],
                          column_start=start[1], column_end=end[1]),
        "review",
        resolved_source=ResolvedSource("paper.tex", start[0], end[0], start[1], end[1]),
        source_selector=selector,
    )


def test_edited_selection_reattaches_between_unchanged_context(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text(_CTX_BEFORE + "This sentence shortens a cell." + _CTX_AFTER)
    comment = _source_comment(store, source, (2, 0), (2, 30))
    source.write_text(_CTX_BEFORE + "This sentence reduces the cell width,\nand it adds a line." + _CTX_AFTER)
    store.refresh_source_anchors(tmp_path)
    refreshed = store.get(comment.id)
    assert not refreshed.stale
    assert refreshed.reanchored
    assert refreshed.resolved_source == ResolvedSource("paper.tex", 2, 3, 0, 19)
    assert refreshed.source_selector.exact == "This sentence reduces the cell width,\nand it adds a line."
    # the new text is what the next edit is tracked from
    source.write_text("new first line\n" + source.read_text())
    store.refresh_source_anchors(tmp_path)
    assert store.get(comment.id).resolved_source == ResolvedSource("paper.tex", 3, 4, 0, 19)


def test_deleted_selection_reattaches_as_empty_range(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text(_CTX_BEFORE + "Delete me." + _CTX_AFTER)
    comment = _source_comment(store, source, (2, 0), (2, 10))
    source.write_text(_CTX_BEFORE + _CTX_AFTER)
    store.refresh_source_anchors(tmp_path)
    refreshed = store.get(comment.id)
    assert not refreshed.stale
    assert refreshed.reanchored
    assert refreshed.resolved_source == ResolvedSource("paper.tex", 2, 2, 0, 0)


def test_changed_context_still_goes_stale(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text(_CTX_BEFORE + "Edit me." + _CTX_AFTER)
    comment = _source_comment(store, source, (2, 0), (2, 8))
    source.write_text("Something totally different now.\nEdited.\nAnd a different ending too.\n")
    store.refresh_source_anchors(tmp_path)
    assert store.get(comment.id).stale


def test_ambiguous_context_still_goes_stale(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text(_CTX_BEFORE + "Edit me." + _CTX_AFTER)
    comment = _source_comment(store, source, (2, 0), (2, 8))
    block = _CTX_BEFORE + "Edited." + _CTX_AFTER
    source.write_text(block + block)
    store.refresh_source_anchors(tmp_path)
    assert store.get(comment.id).stale


def test_exact_match_does_not_mark_reanchored(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text(_CTX_BEFORE + "Keep me." + _CTX_AFTER)
    comment = _source_comment(store, source, (2, 0), (2, 8))
    source.write_text("inserted\n" + source.read_text())
    store.refresh_source_anchors(tmp_path)
    refreshed = store.get(comment.id)
    assert not refreshed.stale and not refreshed.reanchored
    assert refreshed.resolved_source == ResolvedSource("paper.tex", 3, 3, 0, 8)


def test_edited_line_selection_reattaches_between_context_lines(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text("alpha context line\nbeta context line\nold one\nold two\ngamma context line\ndelta context line\n")
    selector = capture_source_selector(source, 3, 4, context=2)
    comment = store.add(SourceRangeAnchor(file="paper.tex", line_start=3, line_end=4), "review",
                        resolved_source=ResolvedSource("paper.tex", 3, 4), source_selector=selector)
    source.write_text("alpha context line\nbeta context line\nnew one\nnew two\nnew three\ngamma context line\ndelta context line\n")
    store.refresh_source_anchors(tmp_path)
    refreshed = store.get(comment.id)
    assert not refreshed.stale and refreshed.reanchored
    assert refreshed.resolved_source == ResolvedSource("paper.tex", 3, 5)


def test_reanchored_flag_round_trips(store: CommentStore, tmp_path: Path):
    source = tmp_path / "paper.tex"
    source.write_text(_CTX_BEFORE + "Old words." + _CTX_AFTER)
    comment = _source_comment(store, source, (2, 0), (2, 10))
    source.write_text(_CTX_BEFORE + "New words." + _CTX_AFTER)
    store.refresh_source_anchors(tmp_path)
    assert CommentStore(store.path).get(comment.id).reanchored


# --- following the file's edits from the text the comment was last exact in, as Overleaf ---

def _followed(store, source, text, start, end):
    """A comment on characters of *text*, whose base text the store has recorded."""
    source.write_text(text)
    comment = _source_comment(store, source, start, end)
    store.refresh_source_anchors(source.parent)
    assert store.get(comment.id).base is not None
    return comment


def _after(store, source, text, comment):
    source.write_text(text)
    store.refresh_source_anchors(source.parent)
    moved = store.get(comment.id)
    return moved, moved.source_selector.exact


def test_a_comment_moves_with_text_inserted_before_it_even_where_its_words_repeat(store, tmp_path):
    source = tmp_path / "paper.tex"
    comment = _followed(store, source, "A cell. A cell.\n", (1, 8), (1, 14))
    moved, words = _after(store, source, "New. A cell. A cell.\n", comment)
    assert (moved.resolved_source, words, moved.stale, moved.reanchored) == (
        ResolvedSource("paper.tex", 1, 1, 13, 19), "A cell", False, False)


def test_words_written_over_or_inside_a_comment_take_it(store, tmp_path):
    source = tmp_path / "paper.tex"
    comment = _followed(store, source, "Keep this word here.\n", (1, 5), (1, 14))
    moved, words = _after(store, source, "Keep this other word here.\n", comment)
    assert words == "this other word" and moved.reanchored and not moved.stale
    moved, words = _after(store, source, "Keep these words here.\n", comment)
    assert words == "these words"
    # Text typed at either edge stays outside, as on Overleaf.
    moved, words = _after(store, source, "Keep (these words) here.\n", comment)
    assert words == "these words"


def test_a_comment_whose_words_are_all_deleted_stays_where_they_were(store, tmp_path):
    source = tmp_path / "paper.tex"
    text = "First sentence. Delete me. Last sentence.\n"
    comment = _followed(store, source, text, (1, 16), (1, 26))
    moved, words = _after(store, source, "First sentence. Last sentence.\n", comment)
    assert (moved.resolved_source, words, moved.stale) == (ResolvedSource("paper.tex", 1, 1, 16, 16), "", False)


def test_a_moved_paragraph_takes_its_comment_along(store, tmp_path):
    source = tmp_path / "paper.tex"
    one, two = "The first paragraph stays.\n", "The moved paragraph has the comment.\n"
    comment = _followed(store, source, one + "\n" + two, (3, 4), (3, 9))
    moved, words = _after(store, source, two + "\n" + one, comment)
    assert (moved.resolved_source, words) == (ResolvedSource("paper.tex", 1, 1, 4, 9), "moved")


def test_edits_made_while_no_server_ran_are_followed_from_the_kept_text(store, tmp_path):
    source = tmp_path / "paper.tex"
    block = _CTX_BEFORE + "Edit me." + _CTX_AFTER
    comment = _followed(store, source, block, (2, 0), (2, 8))
    # The same context twice: words alone cannot say which, the kept text can.
    source.write_text(block + _CTX_BEFORE + "Edited." + _CTX_AFTER)
    again = CommentStore(store.path)
    again.refresh_source_anchors(tmp_path)
    moved = again.get(comment.id)
    assert (moved.resolved_source, moved.stale) == (ResolvedSource("paper.tex", 2, 2, 0, 8), False)


def test_a_line_comment_follows_its_lines(store, tmp_path):
    source = tmp_path / "paper.tex"
    source.write_text("one\ntwo\nthree\nfour\n")
    comment = store.add(SourceRangeAnchor(file="paper.tex", line_start=2, line_end=3), "review",
                        resolved_source=ResolvedSource("paper.tex", 2, 3),
                        source_selector=capture_source_selector(source, 2, 3, context=2))
    store.refresh_source_anchors(tmp_path)
    source.write_text("zero\none\ntwo\nthree\nfour\n")
    store.refresh_source_anchors(tmp_path)
    assert store.get(comment.id).resolved_source == ResolvedSource("paper.tex", 3, 4)
    source.write_text("zero\none\nfour\n")
    store.refresh_source_anchors(tmp_path)
    gone = store.get(comment.id)
    assert (gone.resolved_source, gone.stale) == (ResolvedSource("paper.tex", 3, 3), False)


def test_a_comment_goes_stale_only_when_its_file_is_gone(store, tmp_path):
    source = tmp_path / "paper.tex"
    comment = _followed(store, source, "Some words.\n", (1, 5), (1, 10))
    source.unlink()
    store.refresh_source_anchors(tmp_path)
    assert store.get(comment.id).stale
    assert len(list((store.path.parent / "anchor-texts").glob("*.txt"))) == 1


def test_a_comment_placed_anew_follows_edits_from_the_text_it_was_placed_on(store, tmp_path):
    """A comment put on other words of a changed file, as a thread from Overleaf is placed,
    is moved by the next edit from the text it was placed on, not from the one before."""
    source = tmp_path / "paper.tex"
    comment = _followed(store, source, "AAAAA BBBBB CCCCC DDDDD.\n", (1, 6), (1, 11))
    placed = "CCCCC DDDDD AAAAA ZZZZZ.\n"
    source.write_text(placed)
    at = placed.index("ZZZZZ")
    store.place(comment.id, store.get(comment.id).anchor, ResolvedSource("paper.tex", 1, 1, at, at + 5),
                capture_source_selector(source, 1, 1, column_start=at, column_end=at + 5), placed)
    store.refresh_source_anchors(tmp_path)
    assert store.get(comment.id).source_selector.exact == "ZZZZZ"
    moved, words = _after(store, source, "More. " + placed, comment)
    assert (moved.resolved_source, words) == (ResolvedSource("paper.tex", 1, 1, at + 6, at + 11), "ZZZZZ")
