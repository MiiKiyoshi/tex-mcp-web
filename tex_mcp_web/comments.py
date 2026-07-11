"""Comment thread storage for paper review.

tex-mcp-web v0.4.0 reframes the tool as a code-review-style commenting system
for LaTeX papers: the human is the reviewer, Claude Code is the author.
This module provides the data model, JSON-backed storage, and anchor-
durability logic.

Anchor types
------------
- ``pdf_region``: a bounding box on a PDF page; resolved to source lines via SyncTeX
- ``section``: a logical section by title and/or label
- ``source_range``: an explicit file + line range
- ``paper``: a global comment, no anchor

Threads
-------
Each comment carries an ordered list of :class:`ThreadEntry` so the human
and Claude Code can converse about a region (request → action → follow-up).

Staleness
---------
When source code shifts beneath an anchor (line numbers move, sections
renamed, content rewritten), comments must either reattach themselves to
the new location or report that they have lost the thread.  We use:

1. A ``snippet`` (a few lines of source content captured at comment creation),
   used as a content-addressed anchor that survives line shifts.
2. Structural anchors (section title/label) when applicable.
3. A boolean ``stale`` flag set when neither resolution succeeds.
"""

from __future__ import annotations

import contextlib
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


AnchorKind = Literal["pdf_region", "section", "source_range", "paper"]

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
class PdfRegionAnchor:
    page: int
    bbox: BBox  # (x1, y1, x2, y2) PDF points
    kind: Literal["pdf_region"] = "pdf_region"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "page": self.page, "bbox": list(self.bbox)}

    def resolve_source(self, ctx: ResolveContext) -> "ResolvedSource | None":
        if ctx.synctex is None:
            return None
        from .synctex import page_to_source
        _, y1, _, y2 = self.bbox
        src = page_to_source(ctx.synctex, self.page, (y1 + y2) / 2)
        if src is None:
            return None
        return ResolvedSource(file=src.file, line_start=src.line, line_end=src.line)

    def image_target(self, ctx: ResolveContext) -> tuple[int, BBox] | None:
        # PDF anchors carry their own coordinates; no SyncTeX needed.
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


Anchor = PdfRegionAnchor | SectionAnchor | SourceRangeAnchor | PaperAnchor


def anchor_from_dict(d: dict[str, Any]) -> Anchor:
    """Reconstruct an Anchor from its dict form."""
    kind = d.get("kind")
    if kind == "pdf_region":
        bbox = d.get("bbox") or [0, 0, 0, 0]
        return PdfRegionAnchor(
            page=int(d.get("page", 1)),
            bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        )
    if kind == "section":
        return SectionAnchor(title=str(d.get("title", "")), label=d.get("label"))
    if kind == "source_range":
        return SourceRangeAnchor(
            file=str(d.get("file", "")),
            line_start=int(d.get("line_start", 0)),
            line_end=int(d.get("line_end", 0)),
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

    For PDF region / section anchors, this is computed by the server from
    SyncTeX or document structure.  Stored alongside the comment so Claude
    Code can read the comment without re-resolving.  Source content lives
    in :attr:`Comment.snippet`; we don't duplicate it here.
    """

    file: str
    line_start: int
    line_end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResolvedSource":
        return cls(
            file=str(d.get("file", "")),
            line_start=int(d.get("line_start", 0)),
            line_end=int(d.get("line_end", 0)),
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
        return cls(old=str(d.get("old", "")), new=str(d.get("new", "")))


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
            author=d.get("author", "human"),  # type: ignore[arg-type]
            at=str(d.get("at", _now())),
            text=str(d.get("text", "")),
            edits=list(d.get("edits", []) or []),
        )


@dataclass
class Comment:
    id: str
    anchor: Anchor
    thread: list[ThreadEntry] = field(default_factory=list)
    status: Status = "open"
    resolved_source: ResolvedSource | None = None
    snippet: str | None = None
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
        if self.snippet is not None:
            d["snippet"] = self.snippet
        if self.suggestion is not None:
            d["suggestion"] = self.suggestion.to_dict()
        if self.stale:
            d["stale"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Comment":
        rs = d.get("resolved_source")
        sugg = d.get("suggestion")
        return cls(
            id=str(d["id"]),
            anchor=anchor_from_dict(d["anchor"]),
            thread=[ThreadEntry.from_dict(e) for e in d.get("thread", [])],
            status=d.get("status", "open"),  # type: ignore[arg-type]
            resolved_source=ResolvedSource.from_dict(rs) if rs else None,
            snippet=d.get("snippet"),
            suggestion=SuggestedEdit.from_dict(sugg) if sugg else None,
            created=str(d.get("created", _now())),
            updated=str(d.get("updated", _now())),
            stale=bool(d.get("stale", False)),
        )


# ---------------------------------------------------------------------------
# Snippet capture and fuzzy match
# ---------------------------------------------------------------------------


def capture_snippet(
    file: Path, line_start: int, line_end: int, context: int = 2
) -> str:
    """Read source lines [line_start..line_end] +/- *context*.

    Returns a string used as a content-addressed anchor for staleness
    checks.  Empty string if the file can't be read.
    """
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(0, line_start - 1 - context)
    end = min(len(lines), line_end + context)
    return "\n".join(lines[start:end])


def _strip_for_match(s: str) -> str:
    """Normalize whitespace for fuzzy snippet matching."""
    return re.sub(r"\s+", " ", s).strip()


def find_snippet(
    snippet: str, file: Path, near_line: int | None = None
) -> tuple[int, int] | None:
    """Locate *snippet* in *file*, return ``(start_line, end_line)`` 1-indexed.

    Tries exact match first, then whitespace-normalized match.  When
    *near_line* is provided and the snippet appears multiple times,
    returns the occurrence whose start line is closest to it (defends
    against re-anchoring to a duplicate after edits move the original).

    Returns None if the snippet can no longer be located.
    """
    if not snippet.strip():
        return None
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    candidates: list[tuple[int, int]] = []

    # Exact occurrences (overlapping search)
    pos = 0
    while True:
        idx = text.find(snippet, pos)
        if idx < 0:
            break
        before = text[:idx]
        start_line = before.count("\n") + 1
        end_line = start_line + snippet.count("\n")
        candidates.append((start_line, end_line))
        pos = idx + 1

    if not candidates:
        # Whitespace-normalized fallback: line-by-line sliding window
        src_lines = text.splitlines()
        snip_lines = snippet.splitlines()
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

    if not candidates:
        return None
    if len(candidates) == 1 or near_line is None:
        return candidates[0]
    return min(candidates, key=lambda c: abs(c[0] - near_line))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


STORE_VERSION = 1


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
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("comments: failed to read %s (%s); starting empty", self.path, exc)
            return {"version": STORE_VERSION, "comments": []}

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
        return [Comment.from_dict(c) for c in data.get("comments", [])]

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
        snippet: str | None = None,
        suggestion: SuggestedEdit | None = None,
    ) -> Comment:
        now = _now()
        comment = Comment(
            id=_new_id(),
            anchor=anchor,
            thread=[ThreadEntry(author=author, at=now, text=text)],
            status="open",
            resolved_source=resolved_source,
            snippet=snippet,
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

    def check_staleness(
        self,
        watch_dir: Path,
        sections_resolver=None,
    ) -> list[str]:
        """Re-check every open comment; mark stale ones, update line ranges.

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

            for c in comments:
                if c.status != "open":
                    continue
                was_stale = c.stale
                new_stale, modified = self._recheck_anchor(c, watch_dir, sections_resolver)

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
    def _recheck_anchor(
        c: Comment,
        watch_dir: Path,
        sections_resolver,
    ) -> tuple[bool, bool]:
        """Re-resolve a single comment.  Returns (is_stale, modified_resolved)."""
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

        # source_range / pdf_region: use the captured snippet to relocate.
        resolved = c.resolved_source
        if not c.snippet or resolved is None:
            # Can't verify without a snippet; leave alone.
            return False, False
        file_path = watch_dir / resolved.file
        if not file_path.is_file():
            return True, False
        # Prefer the match closest to the previous line range; defends
        # against re-anchoring to a duplicate when the same snippet
        # appears multiple times in the file.
        located = find_snippet(c.snippet, file_path, near_line=resolved.line_start)
        if located is None:
            return True, False
        ls, le = located
        if (resolved.line_start, resolved.line_end) != (ls, le):
            c.resolved_source = ResolvedSource(
                file=resolved.file, line_start=ls, line_end=le
            )
            return False, True
        return False, False
