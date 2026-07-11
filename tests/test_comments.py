"""Tests for tex_mcp_web.comments — anchors, threads, staleness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tex_mcp_web.comments import (
    Comment,
    CommentStore,
    PaperAnchor,
    PdfRegionAnchor,
    ResolveContext,
    ResolvedSource,
    SectionAnchor,
    SourceRangeAnchor,
    ThreadEntry,
    anchor_from_dict,
    capture_snippet,
    find_snippet,
)


# ---------------------------------------------------------------------------
# Anchor serialization
# ---------------------------------------------------------------------------


def test_pdf_region_anchor_roundtrip():
    a = PdfRegionAnchor(page=3, bbox=(10.5, 20.5, 100.0, 200.0))
    d = a.to_dict()
    assert d == {"kind": "pdf_region", "page": 3, "bbox": [10.5, 20.5, 100.0, 200.0]}
    restored = anchor_from_dict(d)
    assert restored == a


def test_section_anchor_roundtrip_with_label():
    a = SectionAnchor(title="Related Work", label="sec:related")
    d = a.to_dict()
    assert d == {"kind": "section", "title": "Related Work", "label": "sec:related"}
    restored = anchor_from_dict(d)
    assert restored == a


def test_section_anchor_roundtrip_without_label():
    a = SectionAnchor(title="Introduction")
    assert a.to_dict() == {"kind": "section", "title": "Introduction"}
    assert anchor_from_dict(a.to_dict()) == a


def test_source_range_anchor_roundtrip():
    a = SourceRangeAnchor(file="intro.tex", line_start=42, line_end=58)
    assert anchor_from_dict(a.to_dict()) == a


def test_paper_anchor_roundtrip():
    a = PaperAnchor()
    assert a.to_dict() == {"kind": "paper"}
    assert anchor_from_dict(a.to_dict()) == a


# ---------------------------------------------------------------------------
# Anchor.resolve_source / .image_target — methods that own the dispatch
# that used to live in three switch statements.
# ---------------------------------------------------------------------------


def test_paper_anchor_resolves_to_none(tmp_path: Path):
    ctx = ResolveContext(watch_dir=tmp_path)
    assert PaperAnchor().resolve_source(ctx) is None
    assert PaperAnchor().image_target(ctx) is None


def test_source_range_anchor_resolves_to_self(tmp_path: Path):
    ctx = ResolveContext(watch_dir=tmp_path)
    a = SourceRangeAnchor(file="intro.tex", line_start=10, line_end=20)
    rs = a.resolve_source(ctx)
    assert rs == ResolvedSource(file="intro.tex", line_start=10, line_end=20)


def test_pdf_region_image_target_returns_self(tmp_path: Path):
    ctx = ResolveContext(watch_dir=tmp_path)
    a = PdfRegionAnchor(page=3, bbox=(10.0, 20.0, 100.0, 50.0))
    assert a.image_target(ctx) == (3, (10.0, 20.0, 100.0, 50.0))


def test_section_anchor_resolves_via_structure(tmp_path: Path):
    """SectionAnchor.resolve_source uses ctx.structure."""
    from tex_mcp_web.structure import DocumentStructure, Section

    structure = DocumentStructure(
        sections=[
            Section(level="section", title="Methods", file="paper.tex", line=42),
            Section(level="section", title="Results", file="paper.tex", line=80),
        ]
    )
    (tmp_path / "paper.tex").write_text("\n" * 100)
    ctx = ResolveContext(watch_dir=tmp_path, structure=structure)
    rs = SectionAnchor(title="Methods").resolve_source(ctx)
    assert rs is not None
    assert rs.file == "paper.tex"
    assert rs.line_start == 42
    assert rs.line_end == 79  # one before the next section


def test_section_anchor_returns_none_without_structure(tmp_path: Path):
    ctx = ResolveContext(watch_dir=tmp_path, structure=None)
    assert SectionAnchor(title="Methods").resolve_source(ctx) is None


def test_pdf_region_anchor_returns_none_without_synctex(tmp_path: Path):
    ctx = ResolveContext(watch_dir=tmp_path, synctex=None)
    a = PdfRegionAnchor(page=1, bbox=(0, 0, 100, 100))
    assert a.resolve_source(ctx) is None


def test_anchor_from_dict_unknown_kind():
    with pytest.raises(ValueError, match="Unknown anchor kind"):
        anchor_from_dict({"kind": "invalid"})


# ---------------------------------------------------------------------------
# SuggestedEdit (the structured rewrite that rides alongside a comment)
# ---------------------------------------------------------------------------


def test_suggested_edit_roundtrip():
    from tex_mcp_web.comments import SuggestedEdit

    s = SuggestedEdit(old="The frumious bandersnatch", new="The slithy tove")
    assert s.to_dict() == {"old": "The frumious bandersnatch", "new": "The slithy tove"}
    assert SuggestedEdit.from_dict(s.to_dict()) == s


def test_comment_with_suggestion_persists(tmp_path: Path):
    from tex_mcp_web.comments import SuggestedEdit

    store = CommentStore(tmp_path / "comments.json")
    c = store.add(
        PaperAnchor(),
        "tighten this",
        suggestion=SuggestedEdit(old="historically, masked failure data", new="masked failure data"),
    )
    raw = json.loads((tmp_path / "comments.json").read_text())
    sugg = raw["comments"][0]["suggestion"]
    assert sugg == {"old": "historically, masked failure data", "new": "masked failure data"}

    # Round-trip through Comment.from_dict
    restored = Comment.from_dict(raw["comments"][0])
    assert restored.suggestion == c.suggestion


def test_comment_without_suggestion_omits_field(tmp_path: Path):
    """Backward-compat: a comment without a suggestion has no `suggestion` key."""
    store = CommentStore(tmp_path / "comments.json")
    store.add(PaperAnchor(), "no rewrite proposed")
    raw = json.loads((tmp_path / "comments.json").read_text())
    assert "suggestion" not in raw["comments"][0]


# ---------------------------------------------------------------------------
# Comment serialization
# ---------------------------------------------------------------------------


def test_comment_roundtrip(tmp_path: Path):
    store = CommentStore(tmp_path / "comments.json")
    c = store.add(
        SectionAnchor(title="Methods"),
        "expand the algorithm description",
    )
    raw = json.loads((tmp_path / "comments.json").read_text())
    assert raw["version"] == 1
    assert len(raw["comments"]) == 1
    restored = Comment.from_dict(raw["comments"][0])
    assert restored.id == c.id
    assert restored.text == "expand the algorithm description"
    assert isinstance(restored.anchor, SectionAnchor)
    assert restored.anchor.title == "Methods"


def test_thread_entry_with_edits():
    e = ThreadEntry(
        author="claude",
        at="2026-05-05T12:00:00",
        text="expanded the proof",
        edits=["intro.tex:42-58 → :42-78"],
    )
    d = e.to_dict()
    assert d["edits"] == ["intro.tex:42-58 → :42-78"]
    assert ThreadEntry.from_dict(d).edits == ["intro.tex:42-58 → :42-78"]


# ---------------------------------------------------------------------------
# CommentStore basic CRUD
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> CommentStore:
    return CommentStore(tmp_path / "comments.json")


def test_store_initially_empty(store: CommentStore):
    assert store.list() == []


def test_add_then_list(store: CommentStore):
    c = store.add(PaperAnchor(), "abstract is too long")
    assert c.id.startswith("c-")
    assert c.status == "open"
    assert c.text == "abstract is too long"
    assert len(store.list()) == 1
    assert store.list()[0].id == c.id


def test_get_existing(store: CommentStore):
    c = store.add(PaperAnchor(), "x")
    assert store.get(c.id) is not None
    assert store.get(c.id).id == c.id


def test_get_missing(store: CommentStore):
    assert store.get("c-deadbeef") is None


def test_reply_appends_thread_entry(store: CommentStore):
    c = store.add(PaperAnchor(), "first comment")
    updated = store.reply(c.id, "follow up", author="human")
    assert len(updated.thread) == 2
    assert updated.thread[1].author == "human"
    assert updated.thread[1].text == "follow up"
    # Updated timestamp should advance.
    assert updated.updated >= c.created


def test_resolve_marks_resolved_with_edits(store: CommentStore):
    c = store.add(PaperAnchor(), "needs more proof")
    resolved = store.resolve(
        c.id,
        "expanded proof in section 3",
        edits=["paper.tex:120-145 → :120-180"],
    )
    assert resolved.status == "resolved"
    assert resolved.thread[-1].author == "claude"
    assert resolved.thread[-1].edits == ["paper.tex:120-145 → :120-180"]


def test_dismiss_marks_dismissed(store: CommentStore):
    c = store.add(PaperAnchor(), "x")
    dismissed = store.dismiss(c.id, "no longer relevant")
    assert dismissed.status == "dismissed"
    assert dismissed.thread[-1].text == "no longer relevant"


def test_delete_removes_comment(store: CommentStore):
    c = store.add(PaperAnchor(), "x")
    assert store.delete(c.id) is True
    assert store.list() == []
    assert store.delete(c.id) is False


def test_update_unknown_id_raises(store: CommentStore):
    with pytest.raises(KeyError):
        store.reply("c-nonexistent", "x", author="human")


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_list_filter_by_status(store: CommentStore):
    a = store.add(PaperAnchor(), "open one")
    b = store.add(PaperAnchor(), "to resolve")
    store.resolve(b.id, "done")
    open_ids = {c.id for c in store.list(status="open")}
    resolved_ids = {c.id for c in store.list(status="resolved")}
    assert open_ids == {a.id}
    assert resolved_ids == {b.id}


# ---------------------------------------------------------------------------
# Snippet capture and find
# ---------------------------------------------------------------------------


def test_capture_snippet_basic(tmp_path: Path):
    f = tmp_path / "intro.tex"
    f.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
    snippet = capture_snippet(f, line_start=2, line_end=3, context=0)
    assert snippet == "line 2\nline 3"


def test_capture_snippet_with_context(tmp_path: Path):
    f = tmp_path / "intro.tex"
    f.write_text("a\nb\nc\nd\ne\n")
    snippet = capture_snippet(f, line_start=3, line_end=3, context=1)
    assert snippet == "b\nc\nd"


def test_capture_snippet_nonexistent_file(tmp_path: Path):
    snippet = capture_snippet(tmp_path / "missing.tex", 1, 1, context=0)
    assert snippet == ""


def test_find_snippet_exact(tmp_path: Path):
    f = tmp_path / "intro.tex"
    f.write_text("alpha\nbeta gamma\ndelta\nepsilon\n")
    located = find_snippet("beta gamma\ndelta", f)
    assert located == (2, 3)


def test_find_snippet_whitespace_normalized(tmp_path: Path):
    """Snippet matches even when whitespace differs in the source."""
    f = tmp_path / "intro.tex"
    f.write_text("alpha\nbeta    gamma\ndelta\nepsilon\n")  # extra spaces in src
    located = find_snippet("beta gamma\ndelta", f)
    assert located == (2, 3)


def test_find_snippet_after_insertion_above(tmp_path: Path):
    """Snippet still found after lines are inserted above it."""
    f = tmp_path / "intro.tex"
    f.write_text("inserted\nnew\nlines\nbeta gamma\ndelta\nepsilon\n")
    located = find_snippet("beta gamma\ndelta", f)
    assert located == (4, 5)


def test_find_snippet_missing(tmp_path: Path):
    f = tmp_path / "intro.tex"
    f.write_text("alpha\nbeta\n")
    assert find_snippet("nowhere to be found\nat all", f) is None


def test_find_snippet_near_line_picks_closest(tmp_path: Path):
    """When the same snippet appears twice, near_line breaks the tie."""
    f = tmp_path / "intro.tex"
    f.write_text(
        "preamble line\n"   # 1
        "\\item foo\n"       # 2  (first occurrence)
        "filler\n"           # 3
        "\\item foo\n"       # 4
        "filler\n"           # 5
        "\\item foo\n"       # 6  (third occurrence)
        "tail\n"             # 7
    )
    snippet = "\\item foo"
    assert find_snippet(snippet, f, near_line=1) == (2, 2)
    assert find_snippet(snippet, f, near_line=5) == (4, 4)
    assert find_snippet(snippet, f, near_line=999) == (6, 6)
    # Without near_line, returns the first occurrence
    assert find_snippet(snippet, f) == (2, 2)


def test_find_snippet_near_line_irrelevant_when_unique(tmp_path: Path):
    f = tmp_path / "intro.tex"
    f.write_text("only one\nbeta gamma\nonly one\n")
    assert find_snippet("beta gamma", f, near_line=999) == (2, 2)


# ---------------------------------------------------------------------------
# Staleness — section anchors
# ---------------------------------------------------------------------------


def test_section_anchor_staleness_resolved(store: CommentStore, tmp_path: Path):
    c = store.add(SectionAnchor(title="Methods"), "expand")

    # Resolver finds the section
    def resolver(title: str, label: str | None):
        if title == "Methods":
            return ResolvedSource(file="paper.tex", line_start=50, line_end=75)
        return None

    newly_stale = store.check_staleness(tmp_path, sections_resolver=resolver)
    assert newly_stale == []
    refreshed = store.get(c.id)
    assert refreshed.stale is False
    assert refreshed.resolved_source.line_start == 50
    assert refreshed.resolved_source.line_end == 75


def test_section_anchor_staleness_missing(store: CommentStore, tmp_path: Path):
    c = store.add(SectionAnchor(title="Old Section"), "x")

    def resolver(title: str, label: str | None):
        return None  # section no longer exists

    newly_stale = store.check_staleness(tmp_path, sections_resolver=resolver)
    assert newly_stale == [c.id]
    assert store.get(c.id).stale is True


# ---------------------------------------------------------------------------
# Staleness — source/PDF anchors via snippets
# ---------------------------------------------------------------------------


def test_source_range_staleness_unchanged_source(store: CommentStore, tmp_path: Path):
    f = tmp_path / "intro.tex"
    f.write_text("preface\nthe quick brown fox\njumps over\nthe lazy dog\n")
    snippet = "the quick brown fox\njumps over"
    c = store.add(
        SourceRangeAnchor(file="intro.tex", line_start=2, line_end=3),
        "rephrase",
        resolved_source=ResolvedSource(file="intro.tex", line_start=2, line_end=3),
        snippet=snippet,
    )
    newly_stale = store.check_staleness(tmp_path)
    assert newly_stale == []
    assert store.get(c.id).stale is False


def test_source_range_anchor_follows_content_after_insertion(
    store: CommentStore, tmp_path: Path
):
    """When Claude inserts lines above the anchor, the line range follows the snippet."""
    f = tmp_path / "intro.tex"
    f.write_text("preface\nthe quick brown fox\njumps over\nthe lazy dog\n")
    snippet = "the quick brown fox\njumps over"
    c = store.add(
        SourceRangeAnchor(file="intro.tex", line_start=2, line_end=3),
        "rephrase",
        resolved_source=ResolvedSource(file="intro.tex", line_start=2, line_end=3),
        snippet=snippet,
    )
    # Insert 3 lines above the anchor
    f.write_text("inserted\nmore\nstuff\npreface\nthe quick brown fox\njumps over\nthe lazy dog\n")
    store.check_staleness(tmp_path)
    refreshed = store.get(c.id)
    assert refreshed.stale is False
    assert refreshed.resolved_source.line_start == 5
    assert refreshed.resolved_source.line_end == 6


def test_source_range_goes_stale_when_content_deleted(
    store: CommentStore, tmp_path: Path
):
    f = tmp_path / "intro.tex"
    f.write_text("preface\nthe quick brown fox\njumps over\n")
    snippet = "the quick brown fox\njumps over"
    c = store.add(
        SourceRangeAnchor(file="intro.tex", line_start=2, line_end=3),
        "rephrase",
        resolved_source=ResolvedSource(file="intro.tex", line_start=2, line_end=3),
        snippet=snippet,
    )
    # Delete the anchored content
    f.write_text("preface\ncompletely different content\n")
    newly_stale = store.check_staleness(tmp_path)
    assert newly_stale == [c.id]
    assert store.get(c.id).stale is True


def test_paper_anchor_never_goes_stale(store: CommentStore, tmp_path: Path):
    c = store.add(PaperAnchor(), "abstract too long")
    newly_stale = store.check_staleness(tmp_path)
    assert newly_stale == []
    assert store.get(c.id).stale is False


def test_resolved_comments_skip_staleness_check(store: CommentStore, tmp_path: Path):
    """Resolved comments shouldn't be flipped to stale even if their anchor is gone."""
    c = store.add(SectionAnchor(title="X"), "x")
    store.resolve(c.id, "done")
    # Resolver returns None — would mark stale if comment were open
    store.check_staleness(tmp_path, sections_resolver=lambda t, l: None)
    assert store.get(c.id).stale is False


# ---------------------------------------------------------------------------
# Atomic write / re-read robustness
# ---------------------------------------------------------------------------


def test_store_persists_across_instances(tmp_path: Path):
    path = tmp_path / "comments.json"
    s1 = CommentStore(path)
    a = s1.add(PaperAnchor(), "x")
    s2 = CommentStore(path)
    listed = s2.list()
    assert len(listed) == 1
    assert listed[0].id == a.id


def test_store_recovers_from_corrupt_file(tmp_path: Path):
    path = tmp_path / "comments.json"
    path.write_text("{ this is not valid json")
    store = CommentStore(path)
    assert store.list() == []
    # New writes should work after a corrupt-file recovery
    c = store.add(PaperAnchor(), "fresh start")
    assert store.list() == [c]


def test_store_creates_sibling_lock_file(tmp_path: Path):
    path = tmp_path / "comments.json"
    store = CommentStore(path)
    store.add(PaperAnchor(), "x")
    assert (tmp_path / "comments.json.lock").exists()


def test_store_concurrent_writers_no_lost_updates(tmp_path: Path):
    """fcntl.flock serializes mutations from multiple processes.

    Spawn two processes that each insert N comments into the same store
    in a tight loop; afterward the file must contain 2N comments and
    valid JSON.  Without locking, the read-modify-write cycle of one
    process clobbers the other.
    """
    import multiprocessing as mp
    import sys
    if sys.platform == "win32":
        return  # no fcntl

    def _worker(path_str: str, n: int):
        from tex_mcp_web.comments import CommentStore, PaperAnchor
        store = CommentStore(Path(path_str))
        for i in range(n):
            store.add(PaperAnchor(), f"msg {i}")

    path = tmp_path / "comments.json"
    CommentStore(path)  # initialize
    n = 25
    p1 = mp.Process(target=_worker, args=(str(path), n))
    p2 = mp.Process(target=_worker, args=(str(path), n))
    p1.start(); p2.start()
    p1.join(timeout=15); p2.join(timeout=15)
    assert p1.exitcode == 0 and p2.exitcode == 0

    final = CommentStore(path)
    assert len(final.list()) == 2 * n
