"""Comment thread storage for paper review.

The human is the reviewer and the coding agent is the author. This module
provides the data model, JSON-backed storage, and anchor-durability logic.

Anchor types
------------
- ``text_selection``: exact rendered text and per-line PDF rectangles
- ``area``: a visual PDF rectangle tied to one compiled PDF
- ``section``: a logical section by title and/or label
- ``source_range``: an explicit file + line range
- ``paper``: a global comment, no anchor

Threads
-------
Each comment carries an ordered list of :class:`ThreadEntry` so the human
and coding agent can converse about a region (request → action → follow-up).

Staleness
---------
Source selectors keep exact selected lines separate from their prefix and
suffix.  Reattachment never widens the selected range to include context.
When the selected source text itself was edited, the unchanged prefix and
suffix around it, each found exactly once, reattach the comment to the edited
text (``reanchored``); edited or ambiguous context leaves it stale.
Text selections also carry the exact rendered quote; after a compile the
quote is found again in the new PDF and its rectangles are regenerated.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import logging
import math
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal

import pymupdf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anchor types
# ---------------------------------------------------------------------------


AnchorKind = Literal["text_selection", "area", "section", "source_range", "paper"]

# bbox in PDF points: (x1, y1, x2, y2) with top-left origin.
BBox = tuple[float, float, float, float]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_entry_id() -> str:
    return f"e-{secrets.token_hex(4)}"


def _new_id() -> str:
    """Short, URL-safe comment id (~6 hex chars, prefixed)."""
    return "c-" + secrets.token_hex(4)


@dataclass
class ResolveContext:
    """Inputs anchors need to resolve themselves.

    Anchors are dumb data; resolution requires either the parsed
    document structure (for section anchors) or SyncTeX data (for PDF
    regions, and for converting source ranges to image regions).
    Callers assemble whichever pieces are available; anchors gracefully
    return None when missing.
    """

    watch_dir: Path
    structure: Any | None = None  # forward-ref to DocumentStructure
    synctex: Any | None = None    # forward-ref to SyncTeXData


def _source_anchor_to_image_target(anchor, ctx: ResolveContext) -> tuple[int, BBox] | None:
    """Helper: source/section anchors produce image targets via SyncTeX."""
    rs = anchor.resolve_source(ctx)
    if rs is None or ctx.synctex is None:
        return None
    from . import imaging  # lazy: imaging is an optional dep
    return imaging.resolve_source_to_region(
        ctx.synctex, rs.file, rs.line_start, rs.line_end
    )


@dataclass
class PageSelection:
    page: int
    bbox: BBox
    rects: list[BBox]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "bbox": list(self.bbox),
            "rects": [list(rect) for rect in self.rects],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageSelection":
        bbox = data["bbox"]
        return cls(
            page=int(data["page"]),
            bbox=tuple(float(value) for value in bbox),
            rects=[tuple(float(value) for value in rect) for rect in data["rects"]],
        )


@dataclass
class TextSelectionAnchor:
    quote: str
    selection: PageSelection
    pdf_digest: str
    kind: Literal["text_selection"] = "text_selection"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "quote": self.quote,
            "selection": self.selection.to_dict(),
            "pdf_digest": self.pdf_digest,
        }

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        return None

    def image_target(self, ctx: ResolveContext) -> tuple[int, BBox] | None:
        return self.selection.page, self.selection.bbox


@dataclass
class AreaAnchor:
    """A box drawn on a page, for what has no text to select, such as part of a figure.
    ``region`` is :func:`area_digest` of the box when it was drawn."""

    page: int
    bbox: BBox
    pdf_digest: str
    region: str
    kind: Literal["area"] = "area"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "page": self.page,
            "bbox": list(self.bbox),
            "pdf_digest": self.pdf_digest,
            "region": self.region,
        }

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        return None

    def image_target(self, ctx: ResolveContext) -> tuple[int, BBox] | None:
        return self.page, self.bbox


@dataclass
class SectionAnchor:
    title: str
    label: str | None = None
    kind: Literal["section"] = "section"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "title": self.title}
        if self.label is not None:
            d["label"] = self.label
        return d

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        if ctx.structure is None:
            return None
        from .structure import find_section
        match = find_section(ctx.structure, title=self.title, label=self.label)
        if match is None:
            return None
        file, line_start, line_end = match
        if line_end < 0:
            try:
                line_end = len(
                    (ctx.watch_dir / file).read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except OSError:
                line_end = line_start
        return ResolvedSource(file=file, line_start=line_start, line_end=line_end)

    def image_target(self, ctx: ResolveContext) -> tuple[int, BBox] | None:
        return _source_anchor_to_image_target(self, ctx)


@dataclass
class SourceRangeAnchor:
    file: str
    line_start: int
    line_end: int
    column_start: int | None = None
    column_end: int | None = None
    kind: Literal["source_range"] = "source_range"

    def to_dict(self) -> dict[str, Any]:
        data = {
            "kind": self.kind,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }
        if self.column_start is not None:
            data.update(column_start=self.column_start, column_end=self.column_end)
        return data

    def __post_init__(self) -> None:
        if (self.column_start is None) != (self.column_end is None):
            raise ValueError("Both source columns are required")
        if self.column_start is not None:
            if type(self.column_start) is not int or type(self.column_end) is not int:
                raise ValueError("Source columns must be integers")
            if self.column_start < 0 or self.column_end < 0:
                raise ValueError("Source columns must be nonnegative")
            if self.line_end < self.line_start or (self.line_start == self.line_end and self.column_end <= self.column_start):
                raise ValueError("Source selection must be nonempty and ordered")

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        # Already a literal source range; the file existence check is
        # left to staleness, not creation.
        return ResolvedSource(
            file=self.file, line_start=self.line_start, line_end=self.line_end,
            column_start=self.column_start, column_end=self.column_end
        )

    def image_target(self, ctx: ResolveContext) -> tuple[int, BBox] | None:
        return _source_anchor_to_image_target(self, ctx)


@dataclass
class PaperAnchor:
    kind: Literal["paper"] = "paper"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind}

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        return None

    def image_target(self, ctx: ResolveContext) -> tuple[int, BBox] | None:
        return None


Anchor = TextSelectionAnchor | AreaAnchor | SectionAnchor | SourceRangeAnchor | PaperAnchor


def anchor_from_dict(d: dict[str, Any]) -> Anchor:
    """Reconstruct an Anchor from its dict form."""
    kind = d["kind"]
    if kind == "text_selection":
        return TextSelectionAnchor(
            quote=str(d["quote"]),
            selection=PageSelection.from_dict(d["selection"]),
            pdf_digest=str(d["pdf_digest"]),
        )
    if kind == "area":
        bbox = d["bbox"]
        return AreaAnchor(
            page=int(d["page"]),
            bbox=tuple(float(value) for value in bbox),
            pdf_digest=str(d["pdf_digest"]),
            region=str(d["region"]),
        )
    if kind == "section":
        return SectionAnchor(
            title=str(d["title"]),
            label=d["label"] if "label" in d else None,
        )
    if kind == "source_range":
        return SourceRangeAnchor(
            file=str(d["file"]),
            line_start=int(d["line_start"]),
            line_end=int(d["line_end"]),
            column_start=d.get("column_start"), column_end=d.get("column_end"),
        )
    if kind == "paper":
        return PaperAnchor()
    raise ValueError(f"Unknown anchor kind: {kind!r}")


# ---------------------------------------------------------------------------
# Resolved source location
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSource:
    """The source location an anchor currently points at.

    Section anchors use document structure; source-range anchors use their
    explicit file and lines. Text selections retain their PDF anchor while a
    separately verified reverse-SyncTeX location follows the source.
    """

    file: str
    line_start: int
    line_end: int
    column_start: int | None = None
    column_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResolvedSource":
        return cls(
            file=str(d["file"]),
            line_start=int(d["line_start"]),
            line_end=int(d["line_end"]),
            column_start=d.get("column_start"), column_end=d.get("column_end"),
        )


@dataclass
class SourceSelector:
    """Exact source range with context kept outside the selected text."""

    exact: str
    prefix: str
    suffix: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceSelector":
        return cls(
            exact=str(data["exact"]),
            prefix=str(data["prefix"]),
            suffix=str(data["suffix"]),
        )


# ---------------------------------------------------------------------------
# Thread entries and comments
# ---------------------------------------------------------------------------


Author = Literal["human", "agent"]
# A thread is open, resolved, or archived: a thread worth reading again after
# the work it asked for is done, or instead of it, listed on its own. A dismissed state
# once closed a thread without acting on it; a comment not worth acting on is resolved
# or deleted like any other.
Status = Literal["open", "resolved", "archived"]


@dataclass
class SuggestedEdit:
    """The rewrite a thread currently proposes: the pieces of one file it changes.

    Each ``old`` is a verbatim piece of *file* that occurs there exactly once, and
    ``new`` is what it becomes.  Nothing that stays the same is carried, so a proposal
    that touches three words costs three words rather than the paragraph twice.

    A thread holds at most one of these. Proposing again replaces it, so reading a
    thread never costs the proposals it used to carry, and applying it clears it.
    """

    file: str
    changes: list[tuple[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file,
                "changes": [{"old": old, "new": new} for old, new in self.changes]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SuggestedEdit":
        return cls(file=str(d["file"]),
                   changes=[(str(c["old"]), str(c["new"])) for c in d["changes"]])


def locate_fragments(text: str, edits: list[tuple[str, str]]) -> list[tuple[int, int, str]]:
    """Find each ``(old, new)`` fragment in *text*, all or none, ordered by position.

    The agent names what to change by quoting it, the way an editing tool does, so it
    never has to count characters to reach a column. A fragment that is missing, that
    occurs more than once, or that overlaps another one is refused with what the caller
    needs to fix it in one retry: for an ambiguous one that is the lines it was found
    on, so the quote can be extended on purpose rather than by guessing.
    """
    if not edits:
        raise ValueError("a suggestion needs at least one edit")
    spans: list[tuple[int, int, str]] = []
    for old, new in edits:
        if not old:
            raise ValueError("an edit must say which text to replace")
        found = _all_occurrences(text, old)
        if not found:
            raise ValueError(f"not found in the source: {old!r}")
        if len(found) > 1:
            lines = ", ".join(str(text.count("\n", 0, at) + 1) for at in found)
            raise ValueError(
                f"{old!r} occurs {len(found)} times, on lines {lines}; "
                "quote more of the surrounding text"
            )
        spans.append((found[0], found[0] + len(old), new))
    spans.sort()
    for (_, end, _), (start, _, _) in zip(spans, spans[1:]):
        if start < end:
            raise ValueError("two edits cover the same text")
    return spans


def swap_fragments(text: str, edits: list[tuple[str, str]]) -> str:
    """Rewrite *text* by replacing each quoted fragment, all or none."""
    spans = locate_fragments(text, edits)
    updated = text
    # Back to front, so an earlier swap cannot move a later one's offsets.
    for start, end, new in reversed(spans):
        updated = updated[:start] + new + updated[end:]
    if updated == text:
        raise ValueError("the edits leave the text as it is")
    return updated


@dataclass
class ThreadEntry:
    author: Author
    at: str
    text: str
    edits: list[str] = field(default_factory=list)
    # A stable id, so an entry can be named after the thread grows; updated_at is set when
    # its text was rewritten after it was written.
    id: str = field(default_factory=_new_entry_id)
    updated_at: str | None = None
    # Set when the entry was sent to the Overleaf thread: the commit Overleaf's branch was
    # at, or "" for a folder that does not follow Overleaf by git.
    overleaf: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "author": self.author, "at": self.at, "text": self.text}
        if self.edits:
            d["edits"] = list(self.edits)
        if self.updated_at is not None:
            d["updated_at"] = self.updated_at
        if self.overleaf is not None:
            d["overleaf"] = self.overleaf
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ThreadEntry":
        return cls(
            author=d["author"],  # type: ignore[arg-type]
            at=str(d["at"]),
            text=str(d["text"]),
            edits=list(d["edits"]) if "edits" in d else [],
            id=str(d["id"]) if "id" in d else _new_entry_id(),
            updated_at=str(d["updated_at"]) if "updated_at" in d else None,
            overleaf=str(d["overleaf"]) if "overleaf" in d else None,
        )


@dataclass
class OverleafDraft:
    """What the agent drafted for Overleaf, which the reviewer edits and sends: a reply to the
    thread a comment came from, or, on a comment with no entries yet, the first message of a
    new thread on its lines. *edits* names the source ranges it describes, and *checks* holds
    what each range read when it was drafted: words that must be on Overleaf before it goes.
    *thread* is the id a new thread was given on Overleaf when it was first sent, so a send
    that stopped halfway finishes that thread instead of opening another."""
    text: str
    edits: list[str] = field(default_factory=list)
    checks: list[dict[str, str]] = field(default_factory=list)
    thread: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"text": self.text, "edits": list(self.edits), "checks": [dict(check) for check in self.checks]}
        if self.thread is not None:
            data["thread"] = self.thread
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OverleafDraft":
        return cls(text=str(d["text"]), edits=list(d["edits"]),
                   checks=[{"file": str(check["file"]), "text": str(check["text"])} for check in d["checks"]],
                   thread=str(d["thread"]) if "thread" in d else None)


@dataclass
class Comment:
    id: str
    anchor: Anchor
    thread: list[ThreadEntry] = field(default_factory=list)
    status: Status = "open"
    resolved_source: ResolvedSource | None = None
    source_selector: SourceSelector | None = None
    suggestion: SuggestedEdit | None = None
    overleaf_draft: OverleafDraft | None = None
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    # When the comment was last closed; None while it is open.
    resolved: str | None = None
    stale: bool = False
    # The selected source text was edited and the comment was reattached by the
    # unchanged text around it, so it now points at the edited text.
    reanchored: bool = False
    # The hash of the file's text in which resolved_source is exact, kept under
    # anchor-texts beside the store: the next edits to the file move the comment from it.
    base: str | None = None

    @property
    def text(self) -> str:
        """The original comment text (first thread entry)."""
        return self.thread[0].text if self.thread else ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "anchor": self.anchor.to_dict(),
            "thread": [e.to_dict() for e in self.thread],
            "status": self.status,
            "created": self.created,
            "updated": self.updated,
        }
        if self.resolved is not None:
            d["resolved"] = self.resolved
        if self.resolved_source is not None:
            d["resolved_source"] = self.resolved_source.to_dict()
        if self.source_selector is not None:
            d["source_selector"] = self.source_selector.to_dict()
        if self.suggestion is not None:
            d["suggestion"] = self.suggestion.to_dict()
        if self.overleaf_draft is not None:
            d["overleaf_draft"] = self.overleaf_draft.to_dict()
        if self.stale:
            d["stale"] = True
        if self.reanchored:
            d["reanchored"] = True
        if self.base is not None:
            d["base"] = self.base
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Comment":
        return cls(
            id=str(d["id"]),
            anchor=anchor_from_dict(d["anchor"]),
            thread=[ThreadEntry.from_dict(e) for e in d["thread"]],
            status=d["status"],  # type: ignore[arg-type]
            resolved_source=(
                ResolvedSource.from_dict(d["resolved_source"])
                if "resolved_source" in d
                else None
            ),
            source_selector=(
                SourceSelector.from_dict(d["source_selector"])
                if "source_selector" in d
                else None
            ),
            suggestion=(
                SuggestedEdit.from_dict(d["suggestion"])
                if "suggestion" in d
                else None
            ),
            overleaf_draft=(
                OverleafDraft.from_dict(d["overleaf_draft"])
                if "overleaf_draft" in d
                else None
            ),
            created=str(d["created"]),
            updated=str(d["updated"]),
            resolved=str(d["resolved"]) if "resolved" in d else None,
            stale=bool(d["stale"]) if "stale" in d else False,
            reanchored=bool(d.get("reanchored", False)),
            base=str(d["base"]) if "base" in d else None,
        )


# ---------------------------------------------------------------------------
# Source selector capture and reattachment
# ---------------------------------------------------------------------------


def capture_source_selector(
    file: Path, line_start: int, line_end: int, context: int = 2,
    *, column_start: int | None = None, column_end: int | None = None,
) -> SourceSelector | None:
    """Capture selected source separately from its surrounding context."""
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n") if column_start is not None else text.splitlines()
    except OSError:
        return None
    if line_start < 1 or line_end < line_start or line_end > len(lines):
        return None
    if column_start is not None:
        try:
            start = source_offset(text, line_start, column_start)
            end = source_offset(text, line_end, column_end)
        except (ValueError, UnicodeError):
            return None
        if end <= start:
            return None
        return SourceSelector(text[start:end], text[max(0, start - 80):start], text[end:end + 80])
    selected_start = line_start - 1
    return SourceSelector(
        exact="\n".join(lines[selected_start:line_end]),
        prefix="\n".join(lines[max(0, selected_start - context):selected_start]),
        suffix="\n".join(lines[line_end:min(len(lines), line_end + context)]),
    )


def source_offset(text: str, line: int, column: int) -> int:
    """Convert a 1-based line and 0-based UTF-16 editor column to a text offset."""
    lines = text.split("\n")
    if line < 1 or line > len(lines) or column < 0:
        raise ValueError("Source position is out of bounds")
    encoded = lines[line - 1].encode("utf-16-le")
    if column * 2 > len(encoded):
        raise ValueError("Source column is out of bounds")
    prefix = encoded[:column * 2].decode("utf-16-le")
    return sum(len(value) + 1 for value in lines[:line - 1]) + len(prefix)


def find_source_characters(selector: SourceSelector, file: Path, name: str) -> ResolvedSource | None:
    """Relocate exact characters; ambiguous or changed text stays stale."""
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    candidates = []
    position = 0
    while selector.exact:
        start = text.find(selector.exact, position)
        if start < 0:
            break
        candidates.append(start)
        position = start + 1
    if len(candidates) > 1:
        candidates = [start for start in candidates
                      if text[:start].endswith(selector.prefix)
                      and text[start + len(selector.exact):].startswith(selector.suffix)]
    if len(candidates) != 1:
        return None
    start = candidates[0]
    end = start + len(selector.exact)
    def coordinate(offset):
        before = text[:offset]
        return before.count("\n") + 1, len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2
    ls, cs = coordinate(start)
    le, ce = coordinate(end)
    return ResolvedSource(name, ls, le, cs, ce)


_CONTEXT_WINDOWS = (80, 40, 20)
_CONTEXT_MIN_CHARS = 12


def _unique_find(text: str, needle: str) -> int | None:
    first = text.find(needle)
    if first < 0 or text.find(needle, first + 1) >= 0:
        return None
    return first


def _context_span(text: str, selector: SourceSelector) -> tuple[int, int] | None:
    """Locate the edited selection as the text between its unchanged context.

    The prefix and the suffix are each looked up on their own, shortened from
    the side away from the selection when the full context was edited too. Both
    must occur exactly once, in order, and close together; otherwise the
    selection stays unresolved rather than being attached to a guess.
    """
    limit = 3 * len(selector.exact) + 400
    for prefix_size in _CONTEXT_WINDOWS:
        prefix = selector.prefix[-prefix_size:]
        if len(prefix.strip()) < _CONTEXT_MIN_CHARS:
            continue
        start = _unique_find(text, prefix)
        if start is None:
            continue
        start += len(prefix)
        for suffix_size in _CONTEXT_WINDOWS:
            suffix = selector.suffix[:suffix_size]
            if len(suffix.strip()) < _CONTEXT_MIN_CHARS:
                continue
            end = _unique_find(text, suffix)
            if end is None or end < start or end - start > limit:
                continue
            return start, end
    return None


def _coordinate(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    return before.count("\n") + 1, len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2


def reattach_source_characters(
    selector: SourceSelector, file: Path, name: str
) -> tuple[ResolvedSource, SourceSelector] | None:
    """Reattach an edited character selection by its surrounding context.

    Returns the new range (empty when the selection was deleted) and a selector
    holding the edited text, so the next edit is tracked from what is there now.
    """
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    span = _context_span(text, selector)
    if span is None:
        return None
    start, end = span
    ls, cs = _coordinate(text, start)
    le, ce = _coordinate(text, end)
    return (ResolvedSource(name, ls, le, cs, ce),
            SourceSelector(text[start:end], text[max(0, start - 80):start], text[end:end + 80]))


def reattach_source_lines(
    selector: SourceSelector, file: Path, name: str
) -> tuple[ResolvedSource, SourceSelector] | None:
    """Reattach an edited line selection between its unchanged context lines."""
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not selector.prefix.strip() or not selector.suffix.strip():
        return None
    lines = text.splitlines()
    before = selector.prefix.splitlines()
    after = selector.suffix.splitlines()
    starts = [i for i in range(len(lines) - len(before) + 1) if lines[i:i + len(before)] == before]
    ends = [i for i in range(len(lines) - len(after) + 1) if lines[i:i + len(after)] == after]
    if len(starts) != 1 or len(ends) != 1:
        return None
    first, last = starts[0] + len(before), ends[0] - 1   # 0-based selected rows
    if last < first or last - first > 3 * (selector.exact.count("\n") + 1) + 20:
        return None
    return (ResolvedSource(name, first + 1, last + 1),
            SourceSelector("\n".join(lines[first:last + 1]), selector.prefix, selector.suffix))


def _span(text: str, source: ResolvedSource) -> tuple[int, int]:
    """The Python offsets a resolved source range covers: its characters, or for whole lines
    their text without the last newline. Empty when its words were deleted."""
    if source.column_start is not None:
        return (source_offset(text, source.line_start, source.column_start),
                source_offset(text, source.line_end, source.column_end))
    lines = text.split("\n")
    if not 1 <= source.line_start <= source.line_end <= len(lines):
        raise ValueError("Source range is out of bounds")
    start = source_offset(text, source.line_start, 0)
    return start, start + len("\n".join(lines[source.line_start - 1:source.line_end]))


def follow_edits(base: str, now: str, comments: list["Comment"]) -> dict[str, tuple[ResolvedSource, SourceSelector, bool]]:
    """Where comments exact in *base* sit in *now*, as Overleaf moves a comment through an
    edit: the edit is the smallest one between the two texts, text inserted at or before a
    comment's start moves it, inside it widens it, words written over its words take it, and
    a delete takes away what it removes of it, all of it leaving an empty range where the
    words were. When what is left there shares little with the comment's words and those
    occur once elsewhere in *now*, as a moved paragraph's do, the comment goes with them. By
    comment id: the range in *now*, its selector, and whether its words changed."""
    from . import overleaf_ot as ot

    ranges, spans = [], {}
    for comment in comments:
        start, end = _span(base, comment.resolved_source)
        spans[comment.id] = (start, end)
        ranges.append({"t": comment.id, "p": ot.js_len(base[:start]), "n": ot.js_len(base[start:end])})
    moved = {r["t"]: r for r in ot.move_comments(ranges, ot.diff(base, now, ranges))}
    placed = {}
    for comment in comments:
        start, end = spans[comment.id]
        words = base[start:end]
        r = moved[comment.id]
        new_start = ot.py_index(now, r["p"])
        new_end = ot.py_index(now, r["p"] + r["n"])
        # A smallest edit cannot tell a moved paragraph from a rewritten one: the words left
        # where the comment was may be another paragraph's. When they share little with the
        # comment's own words and those occur once elsewhere, the comment goes with them.
        taken = now[new_start:new_end]
        if (taken != words and words.strip() and now.count(words) == 1
                and difflib.SequenceMatcher(None, words, taken).ratio() < 0.5):
            new_start = now.index(words)
            new_end = new_start + len(words)
        source = comment.resolved_source
        if source.column_start is not None:
            ls, cs = _coordinate(now, new_start)
            le, ce = _coordinate(now, new_end)
            resolved = ResolvedSource(source.file, ls, le, cs, ce)
            selector = SourceSelector(now[new_start:new_end], now[max(0, new_start - 80):new_start],
                                      now[new_end:new_end + 80])
        else:
            ls = now.count("\n", 0, new_start) + 1
            le = max(ls, now.count("\n", 0, max(new_start, new_end - 1 if new_end > new_start else new_end)) + 1)
            lines = now.split("\n")
            resolved = ResolvedSource(source.file, ls, le)
            selector = SourceSelector("\n".join(lines[ls - 1:le]), "\n".join(lines[max(0, ls - 3):ls - 1]),
                                      "\n".join(lines[le:le + 2]))
        placed[comment.id] = (resolved, selector, now[new_start:new_end] != words)
    return placed


def moved_line(base: str, now: str, line: int) -> int:
    """The line of *now* that *line* of *base* starts, carried through the smallest edit
    between them as :func:`follow_edits` carries a comment."""
    from . import overleaf_ot as ot

    start = ot.js_len(base[:source_offset(base, line, 0)])
    (moved,) = ot.move_comments([{"t": "line", "p": start, "n": 0}], ot.diff(base, now))
    return now.count("\n", 0, ot.py_index(now, moved["p"])) + 1


def _strip_for_match(s: str) -> str:
    """Normalize whitespace for source and rendered-text matching."""
    return re.sub(r"\s+", " ", s).strip()


def find_source_selector(
    selector: SourceSelector, file: Path
) -> tuple[int, int] | None:
    """Locate one unambiguous source selector without widening its range."""
    if not selector.exact.strip():
        return None
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    candidates: list[tuple[int, int]] = []

    # Exact occurrences (overlapping search)
    pos = 0
    while True:
        idx = text.find(selector.exact, pos)
        if idx < 0:
            break
        before = text[:idx]
        start_line = before.count("\n") + 1
        end_line = start_line + selector.exact.count("\n")
        candidates.append((start_line, end_line))
        pos = idx + 1

    if not candidates:
        # Whitespace-normalized fallback: line-by-line sliding window
        src_lines = text.splitlines()
        snip_lines = selector.exact.splitlines()
        if not snip_lines:
            return None
        target_joined = " ".join(t for t in (_strip_for_match(line) for line in snip_lines) if t)
        if not target_joined:
            return None
        n = len(snip_lines)
        for i in range(len(src_lines) - n + 1):
            joined = " ".join(
                w for w in (_strip_for_match(line) for line in src_lines[i : i + n]) if w
            )
            if joined == target_joined:
                candidates.append((i + 1, i + n))

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None

    source_lines = text.splitlines()
    prefix_lines = selector.prefix.splitlines()
    suffix_lines = selector.suffix.splitlines()
    contextual: list[tuple[int, int]] = []
    for start_line, end_line in candidates:
        before = source_lines[max(0, start_line - 1 - len(prefix_lines)):start_line - 1]
        after = source_lines[end_line:end_line + len(suffix_lines)]
        prefix_matches = _strip_for_match("\n".join(before)) == _strip_for_match(selector.prefix)
        suffix_matches = _strip_for_match("\n".join(after)) == _strip_for_match(selector.suffix)
        if prefix_matches and suffix_matches:
            contextual.append((start_line, end_line))
    return contextual[0] if len(contextual) == 1 else None


def pdf_digest(pdf_path: Path) -> str:
    """Return the identity of the compiled PDF bytes."""
    with pdf_path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def area_digest(pdf_path: Path, page: int, bbox: BBox) -> str:
    """What a box on a page shows: the digest of its pixels in grey at 72 dpi.

    Every compile writes new PDF bytes, so the bytes cannot say whether a box still
    covers what it was drawn on. Its pixels can.
    """
    with pymupdf.open(pdf_path) as pdf:
        if not 1 <= page <= pdf.page_count:
            raise ValueError(f"page {page} is not in the PDF")
        sheet = pdf[page - 1]
        rect = pymupdf.Rect(*bbox) & sheet.rect
        if rect.is_empty:
            raise ValueError("the area lies outside its page")
        pixels = sheet.get_pixmap(clip=rect, dpi=72, colorspace=pymupdf.csGRAY)
        return hashlib.sha256(pixels.samples).hexdigest()


def _normalize_pdf_text(text: str) -> tuple[str, list[int]]:
    """Normalize PDF whitespace and retain raw indexes across line-end hyphens."""
    characters: list[str] = []
    raw_indexes: list[int] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "-" and characters and characters[-1].isalnum():
            next_index = index + 1
            saw_line_break = False
            while next_index < len(text) and text[next_index].isspace():
                if text[next_index] in "\r\n":
                    saw_line_break = True
                next_index += 1
            if saw_line_break and next_index < len(text) and text[next_index].isalnum():
                index = next_index
                continue
        if character.isspace():
            if characters and characters[-1] != " ":
                characters.append(" ")
                raw_indexes.append(index)
            index += 1
            continue
        characters.append(character)
        raw_indexes.append(index)
        index += 1
    if characters and characters[-1] == " ":
        characters.pop()
        raw_indexes.pop()
    return "".join(characters), raw_indexes


def _all_occurrences(text: str, needle: str) -> list[int]:
    positions: list[int] = []
    start = 0
    while True:
        position = text.find(needle, start)
        if position < 0:
            return positions
        positions.append(position)
        start = position + 1


def _compact_pdf_text(text: str) -> tuple[str, list[int]]:
    normalized, raw_indexes = _normalize_pdf_text(text)
    characters: list[str] = []
    compact_raw_indexes: list[int] = []
    for index, character in enumerate(normalized):
        if character == " ":
            continue
        characters.append(character)
        compact_raw_indexes.append(raw_indexes[index])
    return "".join(characters), compact_raw_indexes


class PdfTextIndex:
    def __init__(self, pdf_path: Path):
        self.document = pymupdf.open(pdf_path)
        self.pages: list[tuple[str, str, list[int]]] = []
        try:
            for page in self.document:
                raw_text = page.get_text("text")
                normalized_text, raw_indexes = _normalize_pdf_text(raw_text)
                self.pages.append((raw_text, normalized_text, raw_indexes))
        except Exception:
            self.document.close()
            raise

    def close(self) -> None:
        self.document.close()

    def locate(
        self,
        quote: str,
        hint: PageSelection | None = None,
    ) -> PageSelection | None:
        normalized_quote, _ = _normalize_pdf_text(quote)
        if not normalized_quote:
            return None

        candidates: list[tuple[int, str]] = []
        for page_index, (raw_page, normalized_page, raw_indexes) in enumerate(
            self.pages
        ):
            for start in _all_occurrences(normalized_page, normalized_quote):
                raw_start = raw_indexes[start]
                raw_end = raw_indexes[start + len(normalized_quote) - 1] + 1
                candidates.append((page_index, raw_page[raw_start:raw_end]))

        if len(candidates) == 1:
            matched_page, raw_quote = candidates[0]
            matches = self.document[matched_page].search_for(raw_quote)
        else:
            if hint is None:
                return None
            if hint.page < 1 or hint.page > len(self.document):
                return None
            raw_quotes = {
                raw_quote for page_index, raw_quote in candidates
                if page_index == hint.page - 1
            }
            page = self.document[hint.page - 1]
            search_results = [
                rect for raw_quote in raw_quotes for rect in page.search_for(raw_quote)
            ]
            x1, y1, x2, y2 = hint.bbox
            matches = [
                rect
                for rect in search_results
                if rect.x1 >= x1 and rect.x0 <= x2 and rect.y1 >= y1 and rect.y0 <= y2
            ]
            if not matches:
                return None
            matched_page = hint.page - 1

        if not matches:
            return None
        rects = [
            (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
            for rect in matches
        ]
        bbox = (
            min(rect[0] for rect in rects),
            min(rect[1] for rect in rects),
            max(rect[2] for rect in rects),
            max(rect[3] for rect in rects),
        )
        return PageSelection(page=matched_page + 1, bbox=bbox, rects=rects)


def locate_pdf_quote(
    pdf_path: Path,
    quote: str,
    hint: PageSelection | None = None,
) -> PageSelection | None:
    """Find one exact rendered quote and return its per-line rectangles."""
    index = PdfTextIndex(pdf_path)
    try:
        return index.locate(quote, hint=hint)
    finally:
        index.close()


def canonicalize_pdf_selection(
    pdf_path: Path,
    quote: str,
    hint: PageSelection,
) -> tuple[str, PageSelection] | None:
    """Verify a browser selection and return text PyMuPDF can find again."""
    located = locate_pdf_quote(pdf_path, quote, hint=hint)
    if located is not None:
        return quote, located

    document = pymupdf.open(pdf_path)
    try:
        if hint.page < 1 or hint.page > len(document) or not hint.rects:
            return None
        page = document[hint.page - 1]
        page_rect = page.rect
        selected_rects: list[pymupdf.Rect] = []
        for values in hint.rects:
            if not all(math.isfinite(value) for value in values):
                return None
            rect = pymupdf.Rect(values)
            if rect.is_empty or not page_rect.contains(rect):
                return None
            selected_rects.append(rect)

        line_rects: list[pymupdf.Rect] = []
        for rect in sorted(selected_rects, key=lambda item: (item.y0, item.x0)):
            for index, line_rect in enumerate(line_rects):
                if rect.y0 < line_rect.y1 and rect.y1 > line_rect.y0:
                    line_rects[index] = line_rect | rect
                    break
            else:
                line_rects.append(rect)

        fragments: list[str] = []
        for rect in sorted(line_rects, key=lambda item: (item.y0, item.x0)):
            fragment = page.get_textbox(rect).strip()
            if not fragment:
                return None
            fragments.append(fragment)
    finally:
        document.close()

    selected_text = "\n".join(fragments)
    compact_selected, raw_indexes = _compact_pdf_text(selected_text)
    compact_quote, _ = _compact_pdf_text(quote)
    positions = _all_occurrences(compact_selected, compact_quote)
    if len(positions) != 1:
        return None
    start = positions[0]
    raw_start = raw_indexes[start]
    raw_end = raw_indexes[start + len(compact_quote) - 1] + 1
    canonical_quote = selected_text[raw_start:raw_end].strip()
    located = locate_pdf_quote(pdf_path, canonical_quote, hint=hint)
    if located is None:
        return None
    return canonical_quote, located


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


STORE_VERSION = 7


class CommentStore:
    """JSON-backed comment storage.

    Comments live in ``<watch_dir>/.tex-mcp-web/comments.json``.  The file
    is small (one paper, typically <1k comments), so we read/write the
    whole file on each operation.

    Concurrency is real: the daemon, the MCP server, and the CLI may all
    mutate the store at the same time.  Each read-modify-write cycle is
    serialized with an exclusive ``fcntl.flock`` on a sibling ``.lock``
    file (POSIX only — on Windows the lock is a no-op and the last-writer-
    wins risk is documented).  The actual data write is atomic via temp
    file + ``os.replace``.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        if not self.path.exists():
            self._write({"version": STORE_VERSION, "comments": []})
        else:
            self._upgrade()
            self._assign_entry_ids()

    def _upgrade(self) -> None:
        """Raise a store written by an earlier version to STORE_VERSION, once, under the
        lock. Version 5 called the archived status "reference"; version 6 held a
        suggestion as one before/after pair, which version 7 replaced with the pieces it
        changes. A pair cannot be read as pieces without the file it belongs to, and no
        such proposal was ever applied, so version 6's are dropped rather than carried."""
        with self._locked():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data["version"] not in (5, 6):
                    return
                for comment in data["comments"]:
                    if data["version"] == 5 and comment["status"] == "reference":
                        comment["status"] = "archived"
                    comment.pop("suggestion", None)
                    comment.pop("suggestion_applied", None)
            except (OSError, ValueError, KeyError, TypeError):
                return  # an unreadable store fails on its first operation, as before
            data["version"] = STORE_VERSION
            self._write(data)

    def _assign_entry_ids(self) -> None:
        """Give entries written before they carried ids their ids, once, under the lock:
        an id made on every load would name nothing across loads."""
        with self._locked():
            try:
                data = self._read()
            except (OSError, ValueError, KeyError, TypeError):
                return  # an unreadable store fails on its first operation, as before
            if all("id" in entry for comment in data["comments"] for entry in comment["thread"]):
                return
            self._save([Comment.from_dict(value) for value in data["comments"]])

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize read-modify-write across processes.

        Held only for the duration of one mutation; readers do not lock
        because the atomic rename guarantees they see a complete file.
        """
        try:
            import fcntl
        except ImportError:
            # Windows: no flock; accept last-writer-wins.  Most papers
            # have one writer at a time anyway.
            yield
            return
        # 'a' so concurrent processes share the same descriptor target
        # without truncating each other.
        with open(self._lock_path, "a") as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data["version"] != STORE_VERSION:
            raise ValueError(
                f"unsupported comment store version {data['version']}; expected {STORE_VERSION}"
            )
        return data

    def _write(self, data: dict[str, Any]) -> None:
        # Atomic write: temp file + rename
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".comments-",
            suffix=".tmp",
            delete=False,
        ) as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            tmp = Path(f.name)
        os.replace(tmp, self.path)

    def _all(self) -> list[Comment]:
        data = self._read()
        return [Comment.from_dict(c) for c in data["comments"]]

    def _save(self, comments: Iterable[Comment]) -> None:
        self._write({
            "version": STORE_VERSION,
            "comments": [c.to_dict() for c in comments],
        })

    # ----- public API -----

    def list(
        self,
        status: Status | None = None,
        include_stale: bool = True,
    ) -> list[Comment]:
        comments = self._all()
        if status is not None:
            comments = [c for c in comments if c.status == status]
        if not include_stale:
            comments = [c for c in comments if not c.stale]
        return comments

    def get(self, comment_id: str) -> Comment | None:
        for c in self._all():
            if c.id == comment_id:
                return c
        return None

    def add(
        self,
        anchor: Anchor,
        text: str,
        author: Author = "human",
        resolved_source: ResolvedSource | None = None,
        source_selector: SourceSelector | None = None,
        suggestion: SuggestedEdit | None = None,
        base_text: str | None = None,
    ) -> Comment:
        """*base_text* is the file's text *resolved_source* was found in, which later edits
        carry the comment from."""
        now = _now()
        comment = Comment(
            id=_new_id(),
            anchor=anchor,
            thread=[ThreadEntry(author=author, at=now, text=text)],
            status="open",
            resolved_source=resolved_source,
            source_selector=source_selector,
            suggestion=suggestion,
            created=now,
            updated=now,
        )
        with self._locked():
            if base_text is not None:
                comment.base = self._keep_text(base_text)
            all_comments = self._all()
            all_comments.append(comment)
            self._save(all_comments)
        return comment

    def add_overleaf_thread(
        self,
        anchor: SourceRangeAnchor,
        draft: "OverleafDraft",
        resolved_source: ResolvedSource,
        source_selector: SourceSelector,
    ) -> Comment:
        """A new thread drafted for Overleaf on source lines: a comment with no entries, since
        nothing has been said until the reviewer sends the draft."""
        if not draft.text.strip():
            raise ValueError("a draft must not be empty")
        now = _now()
        comment = Comment(
            id=_new_id(),
            anchor=anchor,
            thread=[],
            status="open",
            resolved_source=resolved_source,
            source_selector=source_selector,
            overleaf_draft=OverleafDraft(draft.text.strip(), draft.edits, draft.checks),
            created=now,
            updated=now,
        )
        with self._locked():
            all_comments = self._all()
            all_comments.append(comment)
            self._save(all_comments)
        return comment

    def name_overleaf_thread(self, comment_id: str, thread: str) -> None:
        """Keep the id a new thread is given on Overleaf with its draft, before it is sent."""
        with self._locked():
            comments = self._all()
            for comment in comments:
                if comment.id == comment_id and comment.overleaf_draft is not None:
                    comment.overleaf_draft.thread = thread
                    self._save(comments)
                    return
        raise KeyError(f"comment {comment_id!r} has no draft")

    def _append_entry(
        self,
        comment_id: str,
        author: Author,
        text: str,
        *,
        edits: list[str] | None = None,
        new_status: Status | None = None,
    ) -> Comment:
        """Locate a comment, append a thread entry, optionally update status.

        Closing a comment may carry no message: replies already hold the
        content, so an empty text flips the status without a thread entry.
        """
        with self._locked():
            comments = self._all()
            for i, c in enumerate(comments):
                if c.id == comment_id:
                    now = _now()
                    if text.strip() or edits:
                        c.thread.append(
                            ThreadEntry(author=author, at=now, text=text, edits=list(edits or []))
                        )
                    if new_status is not None:
                        c.status = new_status
                        c.resolved = now if new_status == "resolved" else None
                    c.updated = now
                    comments[i] = c
                    self._save(comments)
                    return c
        raise KeyError(f"comment {comment_id!r} not found")

    def export_comments(self, comment_ids: list[str]) -> dict:
        from .comment_drafts import export

        with self._locked():
            by_id = {comment.id: comment for comment in self._all()}
            return export(self.path.parent / "drafts", [by_id[key].to_dict() for key in comment_ids])

    def reply_file(self, path: str, edits: list[str] | None = None) -> list[Comment]:
        from .comment_drafts import load

        replies, entry_edits, expected = load(self.path.parent / "drafts", path)
        return self.apply_batch(replies, entry_edits, expected, edits)

    def edit_agent_entries(self, entry_edits: list[tuple[str, str, str]],
                           expected_updated: dict[str, str]) -> list[Comment]:
        """Rewrite agent entries named by (comment id, entry id), all or none.
        expected_updated maps each touched comment to the updated stamp the caller read;
        a thread that moved on since refuses the whole batch."""
        return self.apply_batch({}, entry_edits, expected_updated, None)

    def apply_batch(
        self,
        replies: dict[str, str],
        entry_edits: list[tuple[str, str, str]],
        expected_updated: dict[str, str],
        edits: list[str] | None,
    ) -> list[Comment]:
        """Append agent replies and rewrite agent entries under one lock and one save.

        Every comment in expected_updated is checked against the store first; nothing is
        written unless all of it can be. An entry is named by its comment and its own id,
        (comment_id, entry_id, text); an entry that is not in that thread, or a human's,
        is refused."""
        if not replies and not entry_edits:
            raise ValueError("nothing to apply")
        if any(not text.strip() for text in replies.values()) or any(not text.strip() for _, _, text in entry_edits):
            raise ValueError("thread text must not be empty")
        if len({entry_id for _, entry_id, _ in entry_edits}) != len(entry_edits):
            raise ValueError("each entry appears at most once")
        with self._locked():
            comments = self._all()
            by_id = {comment.id: comment for comment in comments}
            entries = {(comment.id, entry.id): entry for comment in comments for entry in comment.thread}
            touched: dict[str, Comment] = {}
            for comment_id, entry_id, _ in entry_edits:
                if (comment_id, entry_id) not in entries:
                    raise KeyError(f"thread entry {entry_id!r} not found in {comment_id!r}")
                entry = entries[(comment_id, entry_id)]
                if entry.author != "agent":
                    raise ValueError(f"thread entry {entry_id} was written by {entry.author}")
                touched[comment_id] = by_id[comment_id]
            for comment_id in replies:
                if comment_id not in by_id:
                    raise KeyError(f"comment {comment_id!r} not found")
                touched[comment_id] = by_id[comment_id]
            for comment_id, comment in touched.items():
                if comment_id not in expected_updated:
                    raise ValueError(f"no updated stamp given for {comment_id}")
                if comment.updated != expected_updated[comment_id]:
                    raise ValueError(f"stale: {comment_id} changed since it was read")
            now = _now()
            for comment_id, entry_id, text in entry_edits:
                entry = entries[(comment_id, entry_id)]
                entry.text = text.strip()
                entry.updated_at = now
            for comment_id, text in replies.items():
                by_id[comment_id].thread.append(
                    ThreadEntry(author="agent", at=now, text=text.strip(), edits=list(edits or [])))
            for comment in touched.values():
                comment.updated = now
            self._save(comments)
            return list(touched.values())

    def reply(
        self,
        comment_id: str,
        text: str,
        author: Author,
        edits: list[str] | None = None,
    ) -> Comment:
        if not text.strip():
            raise ValueError("reply text must not be empty")
        return self._append_entry(comment_id, author, text, edits=edits)

    def suggest(
        self,
        comment_id: str,
        expected_updated: str,
        text: str,
        build: Callable[[Comment], SuggestedEdit],
    ) -> Comment:
        """Put a suggestion on an open thread and say why, in one entry.

        *build* turns what the agent quoted into the stored ``{old, new}`` against the
        file as it stands.  Calling this again replaces the suggestion and adds another
        entry: a thread carries one live suggestion and its whole conversation, so a
        second reading never costs the first one's replies.
        """
        if not text.strip():
            raise ValueError("a suggestion must say why")
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                self._check_suggestable(comment, expected_updated)
                suggestion = build(comment)
                now = _now()
                comment.thread.append(ThreadEntry(author="agent", at=now, text=text.strip()))
                comment.suggestion = suggestion
                comment.updated = now
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    def draft_overleaf(
        self, comment_id: str, expected_updated: str, draft: OverleafDraft
    ) -> Comment:
        """Put the agent's draft of an Overleaf reply on an open thread. A thread holds one,
        which this replaces; it adds no entry, since nothing has been said until it is sent."""
        if not draft.text.strip():
            raise ValueError("a draft must not be empty")
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                if comment.updated != expected_updated:
                    raise ValueError("stale thread: comment changed since it was read")
                if comment.status != "open":
                    raise ValueError("a draft belongs to a comment that is open")
                kept = comment.overleaf_draft.thread if comment.overleaf_draft is not None else None
                comment.overleaf_draft = OverleafDraft(draft.text.strip(), draft.edits, draft.checks, kept)
                comment.updated = _now()
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    def sent_to_overleaf(self, comment_id: str, text: str, commit: str) -> Comment:
        """Record the reviewer's reply as sent to Overleaf at *commit*, taking the draft it
        came from off the thread, with the ranges that draft named."""
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                now = _now()
                edits = comment.overleaf_draft.edits if comment.overleaf_draft is not None else []
                comment.thread.append(ThreadEntry(author="human", at=now, text=text, edits=list(edits),
                                                  overleaf=commit))
                comment.overleaf_draft = None
                comment.updated = now
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    def withdraw_suggestion(
        self, comment_id: str, expected_updated: str, text: str
    ) -> Comment:
        """Take the suggestion off a thread and say why, keeping every entry.

        A proposal the agent no longer stands behind has to stop offering its Apply
        button without the thread being deleted to silence it.
        """
        if not text.strip():
            raise ValueError("a withdrawal must say why")
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                if comment.updated != expected_updated:
                    raise ValueError("stale thread: comment changed since it was read")
                if comment.suggestion is None:
                    raise ValueError("the comment carries no suggestion")
                now = _now()
                comment.thread.append(ThreadEntry(author="agent", at=now, text=text.strip()))
                comment.suggestion = None
                comment.updated = now
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    @staticmethod
    def _check_suggestable(comment: Comment, expected_updated: str) -> None:
        """Refuse a suggestion the anchored source cannot be read for."""
        if comment.updated != expected_updated:
            raise ValueError("stale thread: comment changed since it was read")
        if comment.status != "open":
            raise ValueError("a suggestion belongs to a comment that is open")
        if not any(entry.author == "human" for entry in comment.thread):
            raise ValueError(
                "a suggestion belongs to a thread the reviewer has written in; "
                "open a comment and let them answer before proposing on it"
            )
        # A detached anchor is not in the way: the pieces are found in the file, not in
        # the range the comment was written against. A thread whose text moved is the one
        # most likely to need a proposal, so it is where one has to be possible.

    def apply_suggestion(
        self,
        comment_id: str,
        expected_updated: str,
        write_to_source: Callable[[Comment], list[str]],
    ) -> Comment:
        """Write a thread's proposal into the source and take it off the thread.

        *write_to_source* performs the edit and names the ranges it changed. The
        proposal is cleared because it is no longer a proposal: what it asked for is
        in the file, and the entry it leaves says where.
        """
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                if comment.updated != expected_updated:
                    raise ValueError("stale thread: comment changed since it was read")
                if comment.status != "open":
                    raise ValueError("suggestion belongs to a comment that is not open")
                if comment.suggestion is None:
                    raise ValueError("the comment carries no suggestion")

                edits = write_to_source(comment)
                now = _now()
                comment.thread.append(
                    ThreadEntry(author="human", at=now, text="Applied suggestion.", edits=edits)
                )
                comment.suggestion = None
                comment.updated = now
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    def resolve(
        self,
        comment_id: str,
        summary: str,
        edits: list[str] | None = None,
        author: Author = "agent",
    ) -> Comment:
        return self._append_entry(
            comment_id, author, summary, edits=edits, new_status="resolved"
        )

    def reopen(self, comment_id: str, author: Author = "human") -> Comment:
        """Reopen a closed comment: the status flips, the thread stays as it is."""
        return self._append_entry(comment_id, author, "", new_status="open")

    def archive(self, comment_id: str, author: Author = "human") -> Comment:
        """Set the thread aside to be read again; its entries stay as they are."""
        return self._append_entry(comment_id, author, "", new_status="archived")

    def edit_entry(
        self,
        comment_id: str,
        index: int,
        text: str,
        author: Author = "human",
    ) -> Comment:
        """Rewrite one thread entry in place, keeping its author and time."""
        if not text.strip():
            raise ValueError("thread text must not be empty")
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                if not 0 <= index < len(comment.thread):
                    raise IndexError(f"comment {comment_id!r} has no thread entry {index}")
                entry = comment.thread[index]
                if entry.author != author:
                    raise ValueError(f"thread entry {index} was written by {entry.author}")
                entry.text = text.strip()
                comment.updated = _now()
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    def rewrite_entry(self, comment_id: str, entry_id: str, text: str) -> Comment:
        """Rewrite the entry named by its id, keeping its author and time."""
        return self._change_entry(comment_id, entry_id, text)

    def remove_entry(self, comment_id: str, entry_id: str) -> Comment:
        """Take the entry named by its id out of its thread."""
        return self._change_entry(comment_id, entry_id, None)

    def _change_entry(self, comment_id: str, entry_id: str, text: str | None) -> Comment:
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                index = next((index for index, entry in enumerate(comment.thread) if entry.id == entry_id), None)
                if index is None:
                    raise KeyError(f"thread entry {entry_id!r} not found in {comment_id!r}")
                if text is None:
                    del comment.thread[index]
                else:
                    comment.thread[index].text = text
                comment.updated = _now()
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    def place(
        self,
        comment_id: str,
        anchor: Anchor,
        resolved_source: ResolvedSource | None,
        source_selector: SourceSelector | None,
    ) -> Comment:
        """Put a comment that was on the whole paper onto the words it was written about,
        found only now."""
        with self._locked():
            comments = self._all()
            for position, comment in enumerate(comments):
                if comment.id != comment_id:
                    continue
                comment.anchor = anchor
                comment.resolved_source = resolved_source
                comment.source_selector = source_selector
                # Placed on the file as it is now: the next refresh records that text.
                comment.base = None
                comment.stale = False
                comment.updated = _now()
                comments[position] = comment
                self._save(comments)
                return comment
        raise KeyError(f"comment {comment_id!r} not found")

    def delete(self, comment_id: str) -> bool:
        with self._locked():
            comments = self._all()
            before = len(comments)
            comments = [c for c in comments if c.id != comment_id]
            if len(comments) == before:
                return False
            self._save(comments)
        return True

    # ----- staleness -----

    @property
    def _bases(self) -> Path:
        return self.path.parent / "anchor-texts"

    def _follow_edits(self, comments: list[Comment], watch_dir: Path) -> tuple[set[str], bool]:
        """Move source-range comments through the edits made to their files since the text
        they were last exact in, one smallest edit per file. Comments with no base text known
        are left to be found by their words. The ids moved, and whether any changed."""
        groups: dict[tuple[str, str], list[Comment]] = {}
        for comment in comments:
            if (isinstance(comment.anchor, SourceRangeAnchor) and comment.resolved_source is not None
                    and comment.base is not None):
                groups.setdefault((comment.resolved_source.file, comment.base), []).append(comment)
        handled, changed = set(), False
        for (name, base_sha), group in groups.items():
            try:
                base = (self._bases / f"{base_sha}.txt").read_text(encoding="utf-8")
                now = (watch_dir / name).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if now != base:
                try:
                    placed = follow_edits(base, now, group)
                except ValueError:
                    continue
                for comment in group:
                    resolved, selector, edited = placed[comment.id]
                    if (resolved, selector) != (comment.resolved_source, comment.source_selector):
                        comment.resolved_source, comment.source_selector = resolved, selector
                        changed = True
                    if edited and not comment.reanchored:
                        comment.reanchored = changed = True
            handled.update(comment.id for comment in group)
        return handled, changed

    def _keep_text(self, text: str) -> str:
        """Keep *text* under anchor-texts by its hash, which is returned."""
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        kept = self._bases / f"{sha}.txt"
        if not kept.exists():
            self._bases.mkdir(parents=True, exist_ok=True)
            staging = kept.with_suffix(".tmp")
            staging.write_text(text, encoding="utf-8")
            os.replace(staging, kept)
        return sha

    def _keep_bases(self, comments: list[Comment], watch_dir: Path, kinds: tuple[type, ...]) -> bool:
        """Record for each comment of *kinds*, the ones just found in the files as they are,
        the text its range is exact in now, by hash, and let go of texts no comment names.
        Whether any comment's base changed."""
        changed, texts = False, {}
        for comment in comments:
            if not isinstance(comment.anchor, kinds) or comment.resolved_source is None or comment.stale:
                continue
            name = comment.resolved_source.file
            if name not in texts:
                try:
                    texts[name] = (watch_dir / name).read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    texts[name] = None
            if texts[name] is None:
                continue
            sha = self._keep_text(texts[name])
            if comment.base != sha:
                comment.base, changed = sha, True
        named = {comment.base for comment in comments if comment.base is not None}
        if self._bases.is_dir():
            for kept in self._bases.glob("*.txt"):
                if kept.stem not in named:
                    kept.unlink(missing_ok=True)
        return changed

    def refresh_source_anchors(self, watch_dir: Path) -> None:
        """Update source comments even when automatic compilation is disabled."""
        with self._locked():
            comments = self._all()
            handled, changed = self._follow_edits(comments, watch_dir)
            for comment in comments:
                if not isinstance(comment.anchor, SourceRangeAnchor):
                    continue
                if comment.id in handled:
                    changed |= comment.stale
                    comment.stale = False
                    continue
                stale, modified = self._refresh_anchor(comment, watch_dir, None, "", None, None, None)
                changed |= modified or stale != comment.stale
                comment.stale = stale
            changed |= self._keep_bases(comments, watch_dir, (SourceRangeAnchor,))
            if changed:
                self._save(comments)

    def refresh_anchors(
        self,
        watch_dir: Path,
        pdf_path: Path,
        sections_resolver=None,
        text_resolver=None,
    ) -> list[str]:
        """Reattach source selectors and regenerate text-selection rectangles.

        ``sections_resolver`` is an optional callable
        ``(title: str, label: str | None, near: tuple[str, int] | None) -> ResolvedSource | None``
        used for SectionAnchor comments (typically wraps :func:`structure.parse_structure`);
        *near* is where the heading is now.
        ``text_resolver`` maps a text selection to a verified source range.

        Returns the list of comment IDs that became stale on this pass
        (i.e. were not stale before, but are now).
        """
        with self._locked():
            comments = self._all()
            newly_stale: list[str] = []
            handled, changed = self._follow_edits(comments, watch_dir)
            digest = pdf_digest(pdf_path)
            text_index = (
                PdfTextIndex(pdf_path)
                if any(isinstance(c.anchor, TextSelectionAnchor) for c in comments)
                else None
            )
            try:
                for c in comments:
                    was_stale = c.stale
                    if c.id in handled:
                        changed |= c.stale
                        c.stale = False
                        continue
                    new_stale, modified = self._refresh_anchor(
                        c,
                        watch_dir,
                        pdf_path,
                        digest,
                        sections_resolver,
                        text_resolver,
                        text_index,
                        self._heading_place(c, watch_dir) if isinstance(c.anchor, SectionAnchor) else None,
                    )

                    if new_stale != was_stale:
                        c.stale = new_stale
                        modified = True
                        if new_stale:
                            newly_stale.append(c.id)
                    if modified:
                        changed = True
            finally:
                if text_index is not None:
                    text_index.close()

            changed |= self._keep_bases(comments, watch_dir, (SourceRangeAnchor, SectionAnchor))
            if changed:
                self._save(comments)

        return newly_stale

    def _heading_place(self, comment: Comment, watch_dir: Path) -> tuple[str, int] | None:
        """Where a section comment's heading is now, which tells apart headings with one
        title: the line it was last found on, carried through the edits to its file since."""
        source = comment.resolved_source
        if source is None:
            return None
        if comment.base is None:
            return source.file, source.line_start
        try:
            base = (self._bases / f"{comment.base}.txt").read_text(encoding="utf-8")
            now = (watch_dir / source.file).read_text(encoding="utf-8")
            return source.file, moved_line(base, now, source.line_start)
        except (OSError, UnicodeError, ValueError):
            return source.file, source.line_start

    @staticmethod
    def _refresh_anchor(
        c: Comment,
        watch_dir: Path,
        pdf_path: Path | None,
        digest: str,
        sections_resolver,
        text_resolver,
        text_index: PdfTextIndex | None,
        near: tuple[str, int] | None = None,
    ) -> tuple[bool, bool]:
        """Refresh one anchor.  Returns ``(is_stale, was_modified)``. *near* is where a
        section comment's heading is now."""
        kind = c.anchor.kind

        if kind == "paper":
            # Paper anchors never go stale.
            return False, False

        if kind == "section":
            from .structure import AmbiguousSection
            anchor = c.anchor  # SectionAnchor
            try:
                resolved = (
                    sections_resolver(anchor.title, anchor.label, near)
                    if sections_resolver is not None
                    else None
                )
            except AmbiguousSection:
                resolved = None
            if resolved is None:
                return True, False
            if c.resolved_source != resolved:
                c.resolved_source = resolved
                return False, True
            return False, False

        if kind == "area":
            anchor = c.anchor
            if anchor.pdf_digest == digest:
                return False, False
            try:
                region = area_digest(pdf_path, anchor.page, anchor.bbox)
            except ValueError:
                return True, False
            if region != anchor.region:
                return True, False
            anchor.pdf_digest = digest
            return False, True

        if kind == "text_selection":
            if text_index is None:
                raise RuntimeError("PDF text index is unavailable")
            selection = text_index.locate(c.anchor.quote, hint=c.anchor.selection)
            if selection is None:
                return True, False
            modified = False
            if c.anchor.selection != selection or c.anchor.pdf_digest != digest:
                c.anchor.selection = selection
                c.anchor.pdf_digest = digest
                modified = True

            if c.source_selector is not None and c.resolved_source is not None:
                file_path = watch_dir / c.resolved_source.file
                located = (
                    find_source_selector(c.source_selector, file_path)
                    if file_path.is_file()
                    else None
                )
                if located is not None:
                    line_start, line_end = located
                    if (
                        c.resolved_source.line_start,
                        c.resolved_source.line_end,
                    ) != (line_start, line_end):
                        c.resolved_source = ResolvedSource(
                            file=c.resolved_source.file,
                            line_start=line_start,
                            line_end=line_end,
                        )
                        modified = True
                    return False, modified
                c.resolved_source = None
                c.source_selector = None
                modified = True

            if c.status == "open" and text_resolver is not None:
                resolved = text_resolver(selection)
                if resolved is not None:
                    selector = capture_source_selector(
                        watch_dir / resolved.file,
                        resolved.line_start,
                        resolved.line_end,
                    )
                    if selector is not None:
                        c.resolved_source = resolved
                        c.source_selector = selector
                        modified = True
            return False, modified

        # Source-backed anchors reattach only the exact selected lines.
        resolved = c.resolved_source
        if c.source_selector is None or resolved is None:
            return True, False
        file_path = watch_dir / resolved.file
        if not file_path.is_file():
            return True, False
        if resolved.column_start is not None:
            located = find_source_characters(c.source_selector, file_path, resolved.file)
            if located is None:
                reattached = reattach_source_characters(c.source_selector, file_path, resolved.file)
                if reattached is None:
                    return True, False
                c.resolved_source, c.source_selector = reattached
                c.reanchored = True
                return False, True
            modified = located != resolved
            c.resolved_source = located
            return False, modified
        located = find_source_selector(c.source_selector, file_path)
        if located is None:
            reattached = reattach_source_lines(c.source_selector, file_path, resolved.file)
            if reattached is None:
                return True, False
            c.resolved_source, c.source_selector = reattached
            c.reanchored = True
            return False, True
        ls, le = located
        modified = False
        if (resolved.line_start, resolved.line_end) != (ls, le):
            c.resolved_source = ResolvedSource(
                file=resolved.file, line_start=ls, line_end=le
            )
            modified = True

        return False, modified
