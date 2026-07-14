"""Comment thread storage for paper review.

tex-mcp-web v0.4.0 reframes the tool as a code-review-style commenting system
for LaTeX papers: the human is the reviewer, Claude Code is the author.
This module provides the data model, JSON-backed storage, and anchor-
durability logic.

Anchor types
------------
- ``text_selection``: exact rendered text, glyph positions, and per-line PDF rectangles
- ``area``: a visual PDF rectangle tied to one compiled PDF
- ``section``: a logical section by title and/or label
- ``source_range``: an explicit file + line range
- ``paper``: a global comment, no anchor

Threads
-------
Each comment carries an ordered list of :class:`ThreadEntry` so the human
and Claude Code can converse about a region (request → action → follow-up).

Staleness
---------
Source selectors keep exact selected lines separate from their prefix and
suffix.  Reattachment never widens the selected range to include context.
Text selections also carry the exact rendered quote; after a compile the
quote is found again in the new PDF and its rectangles are regenerated.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anchor types
# ---------------------------------------------------------------------------


AnchorKind = Literal["text_selection", "area", "section", "source_range", "paper"]

# bbox in PDF points: (x1, y1, x2, y2) with top-left origin.
BBox = tuple[float, float, float, float]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
class GlyphPosition:
    page: int
    index: int

    def to_dict(self) -> dict[str, int]:
        return {"page": self.page, "index": self.index}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GlyphPosition":
        return cls(page=int(data["page"]), index=int(data["index"]))


@dataclass
class TextSelectionAnchor:
    quote: str
    segments: list[PageSelection]
    start: GlyphPosition
    end: GlyphPosition
    pdf_digest: str
    kind: Literal["text_selection"] = "text_selection"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "quote": self.quote,
            "segments": [segment.to_dict() for segment in self.segments],
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "pdf_digest": self.pdf_digest,
        }

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        return None

    def image_target(self, ctx: ResolveContext) -> tuple[int, BBox] | None:
        if not self.segments:
            return None
        first = self.segments[0]
        return first.page, first.bbox


@dataclass
class AreaAnchor:
    page: int
    bbox: BBox
    pdf_digest: str
    kind: Literal["area"] = "area"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "page": self.page,
            "bbox": list(self.bbox),
            "pdf_digest": self.pdf_digest,
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
    kind: Literal["source_range"] = "source_range"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        # Already a literal source range; the file existence check is
        # left to staleness, not creation.
        return ResolvedSource(
            file=self.file, line_start=self.line_start, line_end=self.line_end
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
            segments=[PageSelection.from_dict(segment) for segment in d["segments"]],
            start=GlyphPosition.from_dict(d["start"]),
            end=GlyphPosition.from_dict(d["end"]),
            pdf_digest=str(d["pdf_digest"]),
        )
    if kind == "area":
        bbox = d["bbox"]
        return AreaAnchor(
            page=int(d["page"]),
            bbox=tuple(float(value) for value in bbox),
            pdf_digest=str(d["pdf_digest"]),
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

    For text-selection / section anchors, this is computed by the server from
    SyncTeX or document structure.  Stored alongside the comment so Claude
    Code can read the comment without re-resolving.
    """

    file: str
    line_start: int
    line_end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResolvedSource":
        return cls(
            file=str(d["file"]),
            line_start=int(d["line_start"]),
            line_end=int(d["line_end"]),
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


Author = Literal["human", "claude"]
Status = Literal["open", "resolved", "dismissed"]


@dataclass
class SuggestedEdit:
    """A concrete rewrite proposed alongside a comment.

    When a reviewer says "rephrase this to be tighter," it's much faster
    for the agent to read a structured ``{old, new}`` than to parse the
    intent out of prose.  ``old`` should be a verbatim slice of the
    rendered text or source the comment anchors to; ``new`` is the
    proposed replacement.

    The agent can either apply the suggestion verbatim, modify it, or
    discuss it via ``reply``.  ``old`` is advisory: the agent should
    locate it in the source itself rather than trusting line numbers.
    """

    old: str
    new: str

    def to_dict(self) -> dict[str, str]:
        return {"old": self.old, "new": self.new}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SuggestedEdit":
        return cls(old=str(d["old"]), new=str(d["new"]))


@dataclass
class ThreadEntry:
    author: Author
    at: str
    text: str
    edits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"author": self.author, "at": self.at, "text": self.text}
        if self.edits:
            d["edits"] = list(self.edits)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ThreadEntry":
        return cls(
            author=d["author"],  # type: ignore[arg-type]
            at=str(d["at"]),
            text=str(d["text"]),
            edits=list(d["edits"]) if "edits" in d else [],
        )


@dataclass
class Comment:
    id: str
    anchor: Anchor
    thread: list[ThreadEntry] = field(default_factory=list)
    status: Status = "open"
    resolved_source: ResolvedSource | None = None
    source_selector: SourceSelector | None = None
    suggestion: SuggestedEdit | None = None
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)
    stale: bool = False

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
        if self.resolved_source is not None:
            d["resolved_source"] = self.resolved_source.to_dict()
        if self.source_selector is not None:
            d["source_selector"] = self.source_selector.to_dict()
        if self.suggestion is not None:
            d["suggestion"] = self.suggestion.to_dict()
        if self.stale:
            d["stale"] = True
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
            created=str(d["created"]),
            updated=str(d["updated"]),
            stale=bool(d["stale"]) if "stale" in d else False,
        )


# ---------------------------------------------------------------------------
# Source selector capture and reattachment
# ---------------------------------------------------------------------------


def capture_source_selector(
    file: Path, line_start: int, line_end: int, context: int = 2
) -> SourceSelector | None:
    """Capture selected source separately from its surrounding context."""
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if line_start < 1 or line_end < line_start or line_end > len(lines):
        return None
    selected_start = line_start - 1
    return SourceSelector(
        exact="\n".join(lines[selected_start:line_end]),
        prefix="\n".join(lines[max(0, selected_start - context):selected_start]),
        suffix="\n".join(lines[line_end:min(len(lines), line_end + context)]),
    )


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


def locate_pdf_quote(
    pdf_path: Path,
    quote: str,
    hint: list[PageSelection] | None = None,
) -> list[PageSelection] | None:
    """Find one exact rendered quote and return its per-line rectangles."""
    import pymupdf

    normalized_quote = _strip_for_match(quote)
    if not normalized_quote:
        return None

    document = pymupdf.open(pdf_path)
    matched_page = None
    occurrence_count = 0
    for page_index, page in enumerate(document):
        normalized_page = _strip_for_match(page.get_text("text"))
        count = normalized_page.count(normalized_quote)
        if count:
            occurrence_count += count
            matched_page = page_index
    if occurrence_count != 1 or matched_page is None:
        if hint is None or len(hint) != 1:
            document.close()
            return None
        hinted = hint[0]
        page = document[hinted.page - 1]
        candidates = page.search_for(normalized_quote)
        x1, y1, x2, y2 = hinted.bbox
        matches = [
            rect
            for rect in candidates
            if rect.x1 >= x1 and rect.x0 <= x2 and rect.y1 >= y1 and rect.y0 <= y2
        ]
        if not matches:
            document.close()
            return None
        matched_page = hinted.page - 1

    page = document[matched_page]
    if occurrence_count == 1:
        matches = page.search_for(normalized_quote)
    if not matches:
        document.close()
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
    document.close()
    return [PageSelection(page=matched_page + 1, bbox=bbox, rects=rects)]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


STORE_VERSION = 2


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
    ) -> Comment:
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
            all_comments = self._all()
            all_comments.append(comment)
            self._save(all_comments)
        return comment

    def _append_entry(
        self,
        comment_id: str,
        author: Author,
        text: str,
        *,
        edits: list[str] | None = None,
        new_status: Status | None = None,
    ) -> Comment:
        """Locate a comment, append a thread entry, optionally update status."""
        with self._locked():
            comments = self._all()
            for i, c in enumerate(comments):
                if c.id == comment_id:
                    c.thread.append(
                        ThreadEntry(author=author, at=_now(), text=text, edits=list(edits or []))
                    )
                    if new_status is not None:
                        c.status = new_status
                    c.updated = _now()
                    comments[i] = c
                    self._save(comments)
                    return c
        raise KeyError(f"comment {comment_id!r} not found")

    def reply(
        self,
        comment_id: str,
        text: str,
        author: Author,
        edits: list[str] | None = None,
    ) -> Comment:
        return self._append_entry(comment_id, author, text, edits=edits)

    def resolve(
        self,
        comment_id: str,
        summary: str,
        edits: list[str] | None = None,
        author: Author = "claude",
    ) -> Comment:
        return self._append_entry(
            comment_id, author, summary, edits=edits, new_status="resolved"
        )

    def dismiss(
        self,
        comment_id: str,
        reason: str,
        author: Author = "human",
    ) -> Comment:
        return self._append_entry(
            comment_id, author, reason, new_status="dismissed"
        )

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

    def refresh_anchors(
        self,
        watch_dir: Path,
        pdf_path: Path,
        sections_resolver=None,
    ) -> list[str]:
        """Reattach source selectors and regenerate text-selection rectangles.

        ``sections_resolver`` is an optional callable
        ``(title: str, label: str | None) -> ResolvedSource | None`` used
        for SectionAnchor comments (typically wraps :func:`structure.parse_structure`).

        Returns the list of comment IDs that became stale on this pass
        (i.e. were not stale before, but are now).
        """
        with self._locked():
            comments = self._all()
            newly_stale: list[str] = []
            changed = False

            digest = pdf_digest(pdf_path)
            for c in comments:
                was_stale = c.stale
                new_stale, modified = self._refresh_anchor(
                    c, watch_dir, pdf_path, digest, sections_resolver
                )

                if new_stale != was_stale:
                    c.stale = new_stale
                    modified = True
                    if new_stale:
                        newly_stale.append(c.id)
                if modified:
                    changed = True

            if changed:
                self._save(comments)

        return newly_stale

    @staticmethod
    def _refresh_anchor(
        c: Comment,
        watch_dir: Path,
        pdf_path: Path,
        digest: str,
        sections_resolver,
    ) -> tuple[bool, bool]:
        """Refresh one anchor.  Returns ``(is_stale, was_modified)``."""
        kind = c.anchor.kind

        if kind == "paper":
            # Paper anchors never go stale.
            return False, False

        if kind == "section":
            anchor = c.anchor  # SectionAnchor
            resolved = (
                sections_resolver(anchor.title, anchor.label)
                if sections_resolver is not None
                else None
            )
            if resolved is None:
                return True, False
            if c.resolved_source != resolved:
                c.resolved_source = resolved
                return False, True
            return False, False

        if kind == "area":
            return c.anchor.pdf_digest != digest, False

        # Source-backed anchors reattach only the exact selected lines.
        resolved = c.resolved_source
        if c.source_selector is None or resolved is None:
            return True, False
        file_path = watch_dir / resolved.file
        if not file_path.is_file():
            return True, False
        located = find_source_selector(c.source_selector, file_path)
        if located is None:
            return True, False
        ls, le = located
        modified = False
        if (resolved.line_start, resolved.line_end) != (ls, le):
            c.resolved_source = ResolvedSource(
                file=resolved.file, line_start=ls, line_end=le
            )
            modified = True

        if kind == "text_selection":
            segments = locate_pdf_quote(pdf_path, c.anchor.quote)
            if segments is None:
                return True, modified
            if c.anchor.segments != segments or c.anchor.pdf_digest != digest:
                c.anchor.segments = segments
                c.anchor.pdf_digest = digest
                modified = True
        return False, modified
