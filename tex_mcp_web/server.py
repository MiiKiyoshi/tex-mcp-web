"""aiohttp web server for tex-mcp-web v0.7.0.

Single paper, no workspace abstraction.  Three responsibilities:

1. Watch the project directory and recompile on .tex/.bib changes.
2. Serve the resulting PDF + a thin viewer with an annotation layer.
3. Expose a JSON API for comments, paper state, errors, and SyncTeX
   resolution (used by the browser viewer and the MCP server).

The browser also exposes watched UTF-8 source files through a small editor;
external agent edits and browser saves share the same watcher and compiler.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import re
import stat
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web

from .comments import (
    AreaAnchor,
    Comment,
    CommentStore,
    PageSelection,
    PdfTextIndex,
    PaperAnchor,
    ResolvedSource,
    SectionAnchor,
    SourceRangeAnchor,
    SourceSelector,
    SuggestedEdit,
    TextSelectionAnchor,
    anchor_from_dict,
    capture_source_selector,
    canonicalize_pdf_selection,
    locate_pdf_quote,
    area_digest,
    pdf_digest,
    locate_fragments,
    source_offset,
)
from .compiler import CompileResult, compile_tex, source_dependencies
from .config import Config, get_main_file, get_watch_dir, load_config
from .mcp_server import short_time
from .structure import (
    AmbiguousSection,
    DocumentStructure,
    _files_reachable_from,
    _relative,
    find_section,
    parse_structure,
    reading_places,
    section_end,
)
from .synctex import (
    SyncTeXData,
    find_synctex_file,
    has_synctex_record,
    parse_synctex,
    printed_words,
    selection_to_source_range,
    source_lines_to_boxes,
    source_to_page,
)
from . import overleaf
from . import overleaf_git
from . import page_token
from . import plugin_monitor
from . import overleaf_ot
from .overleaf_live import TRACKED, LivePresence
from .overleaf_sync import HandSync, NotConnected, ProjectSync, Uploads
from .completion import bib_entries, defined_commands, defined_environments, labels
from .translate import Translator, TranslationStore, load_glossary, split_paragraphs, store_name
from .watcher import Watcher, is_watched_source, matches_patterns

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _comment_to_dict(c: Comment) -> dict[str, Any]:
    d = c.to_dict()
    # Each entry's time as the agent reads it too, MM-DD HH:MM on this server's clock, so
    # the page and the agent name a moment alike.
    for entry in d["thread"]:
        entry["when"] = short_time(entry["at"])
    if c.overleaf_draft is not None:
        # The lines each range a draft describes is on now, or its file alone while it is
        # not found, and whether those lines were edited after the draft was written.
        d["overleaf_draft"]["ranges"] = [
            {"range": r.source.file, "stale": True} if r.stale
            else {"range": r.label, **({"edited": True} if r.selector.exact != r.drafted else {})}
            for r in c.overleaf_draft.ranges]
    return d


def _result_to_dict(r: CompileResult | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "success": r.success,
        "errors": [dataclasses.asdict(e) for e in r.errors],
        "warnings": [dataclasses.asdict(w) for w in r.warnings],
        "output_file": str(r.output_file) if r.output_file else None,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "duration_seconds": r.duration_seconds,
        "pages_changed": r.pages_changed,
    }


def _eof_line(path: Path, fallback: int) -> int:
    """Total line count of *path*, or *fallback* on failure."""
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return fallback


def _overleaf_place(entry: dict[str, Any]) -> dict[str, Any]:
    """Where a comment is on Overleaf, as the ledger keeps it while its words are not here."""
    return {key: entry.get(key) for key in ("pathname", "line", "anchored_text", "offset", "text_sha")}


def _place_name(place: dict[str, Any]) -> str:
    path = str(place.get("pathname") or "").lstrip("/") or "an unknown file"
    return f"{path}:{place['line']}" if isinstance(place.get("line"), int) else path


def _suggestion_groups(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggested edits as Overleaf's review panel shows them, in the order the text reads: a
    deletion just after one person's insertion is one change of words, the deleted words
    replaced by the inserted ones, and every other edit stands alone. Positions are in the
    document's text, which holds the inserted words and not the deleted ones."""
    ordered = sorted(changes, key=lambda change: (change["at"], change["kind"] == "insert"))
    groups: list[dict[str, Any]] = []
    for change in ordered:
        last = groups[-1] if groups else None
        if (change["kind"] == "delete" and last is not None and not last["deleted"] and change["user_id"]
                and last["user_id"] == change["user_id"] and last["end"] == change["at"]):
            last["ids"].append(change["id"])
            last["deleted"] = change["text"]
            last["ts"] = max(last["ts"], change["ts"])
            continue
        inserted = change["text"] if change["kind"] == "insert" else ""
        groups.append({"ids": [change["id"]], "at": change["at"], "end": change["at"] + len(inserted),
                       "inserted": inserted, "deleted": "" if inserted else change["text"],
                       "user_id": change["user_id"], "name": change["name"], "ts": change["ts"]})
    return groups


def _place_suggestions(theirs: str, here: str, groups: list[dict[str, Any]]) -> list[tuple[int, int] | None]:
    """Where each group of suggested edits in Overleaf's text *theirs* is in the file's text
    *here*: its span, carried through the smallest edit between the two texts as a comment is,
    or None when the text around it differs, as it does when the file lacks Overleaf's edit or
    changed the words."""
    if theirs == here:
        return [(group["at"], group["end"]) for group in groups]
    margin = 24
    ranges = []
    for index, group in enumerate(groups):
        start, end = max(0, group["at"] - margin), min(len(theirs), group["end"] + margin)
        ranges.append({"t": f"c{index}", "p": overleaf_ot.js_len(theirs[:group["at"]]),
                       "n": overleaf_ot.js_len(theirs[group["at"]:group["end"]])})
        ranges.append({"t": f"w{index}", "p": overleaf_ot.js_len(theirs[:start]),
                       "n": overleaf_ot.js_len(theirs[start:end])})
    moved = {r["t"]: r for r in overleaf_ot.move_comments(ranges, overleaf_ot.diff(theirs, here, ranges))}
    placed: list[tuple[int, int] | None] = []
    for index, group in enumerate(groups):
        start, end = max(0, group["at"] - margin), min(len(theirs), group["end"] + margin)
        window = moved[f"w{index}"]
        around = here[overleaf_ot.py_index(here, window["p"]):overleaf_ot.py_index(here, window["p"] + window["n"])]
        if around != theirs[start:end]:
            placed.append(None)
            continue
        span = moved[f"c{index}"]
        placed.append((overleaf_ot.py_index(here, span["p"]), overleaf_ot.py_index(here, span["p"] + span["n"])))
    return placed


def _suggested_when(stamp: str) -> str:
    """When Overleaf says an edit was suggested, as the page shows a thread's times. Overleaf
    writes the time zone as Z, which Python reads from 3.11 on."""
    try:
        return short_time(re.sub(r"Z$", "+00:00", stamp))
    except ValueError:
        return ""


def _line_column(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    return before.count("\n") + 1, len(before) - (before.rfind("\n") + 1)


def _parse_source_range(spec: str) -> tuple[str, int, int]:
    """Parse ``FILE:LSTART-LEND`` (or ``FILE:LINE``).  Raises ValueError."""
    try:
        file, lines = spec.rsplit(":", 1)
        ls_str, _, le_str = lines.partition("-")
        ls = int(ls_str)
        le = int(le_str) if le_str else ls
    except (ValueError, IndexError):
        raise ValueError("source must be FILE:LSTART-LEND")
    return file, ls, le


def _clamp_dpi(value: str | int) -> int:
    """Parse and clamp a DPI value to a sane render range.

    Rendering at unbounded DPI is a denial-of-service vector
    (``?dpi=10000`` allocates a multi-gigabyte pixmap).  Clamp to a
    range that covers screen viewing (~96–150) up to high-detail
    extraction (~600).
    """
    n = int(value) if not isinstance(value, int) else value
    return max(36, min(n, 600))


def _suggestion_from_dict(d: Any, file: str | None) -> SuggestedEdit | None:
    """Build the browser's one-piece proposal, omitting an explicitly empty pair.

    The page offers a replacement for the text the reviewer selected, which is one
    piece of the file their comment is anchored to.
    """
    if d is None:
        return None
    if not isinstance(d, dict):
        raise TypeError("suggestion must be an object")
    if (
        "old" not in d
        or "new" not in d
        or not isinstance(d["old"], str)
        or not isinstance(d["new"], str)
    ):
        raise TypeError("suggestion old and new must be strings")
    if not d["old"] and not d["new"]:
        return None
    if not d["new"]:
        # The page offers a replacement, and an empty box is a box nobody filled in.
        # An agent that means to remove text says so through its own call.
        raise TypeError("a replacement must not be empty")
    if file is None:
        raise TypeError("a suggestion needs a comment whose place in the source is known")
    return SuggestedEdit(file=file, changes=[(d["old"], d["new"])])


def derive_suggestion(
    watch_dir: Path, source: ResolvedSource | None, edits: list[tuple[str, str]]
) -> SuggestedEdit:
    """Build a suggestion from fragments the agent quoted out of the source a comment sits in.

    The agent sends only what changes. Each piece is checked against the file now, so
    what the reviewer is offered is measured against the source rather than against
    what the agent remembered of it.
    """
    # Any comment whose place in the source is known can carry one: a source range, or
    # PDF text and sections through the source they resolve to. A whole-paper note
    # names no file.
    if source is None:
        raise ValueError("a suggestion needs a comment whose place in the source is known")
    try:
        text = (watch_dir / source.file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"source cannot be read: {source.file}") from error
    # Each piece has to be findable in the file the comment sits in. The comment's own
    # range says which thread this belongs to, not how far the rewrite may reach: a
    # reviewer underlines a phrase to point at something, and what it asks for is
    # regularly wider than what they underlined.
    locate_fragments(text, edits)
    if all(old == new for old, new in edits):
        raise ValueError("the edits leave the source as it is")
    return SuggestedEdit(file=source.file, changes=list(edits))


def _carry_across(position: int, spans: list[tuple[int, int, str]]) -> int:
    """Where *position* lands once every span before it has been replaced."""
    delta = 0
    for start, end, replacement in spans:
        if end <= position:
            delta += len(replacement) - (end - start)
    return position + delta


def read_anchored_source(watch_dir: Path, comment: Comment) -> str | None:
    """Return what the file holds at a comment's anchored source range.

    This is the text the agent quotes fragments out of, so it is read from the file
    rather than from the selector captured when the comment was written.
    """
    source = comment.resolved_source
    if source is None or not isinstance(comment.anchor, SourceRangeAnchor):
        return None
    try:
        text = (watch_dir / source.file).read_text(encoding="utf-8")
        start, end = _resolved_source_span(text, source)
    except (OSError, UnicodeError, ValueError):
        return None
    return text[start:end]


def _source_coordinate(text: str, offset: int) -> tuple[int, int]:
    """Return a 1-based line and UTF-16 column for a Python text offset."""
    if offset < 0 or offset > len(text):
        raise ValueError("Source position is out of bounds")
    before = text[:offset]
    return (
        before.count("\n") + 1,
        len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
    )


def _resolved_source_span(text: str, source: ResolvedSource) -> tuple[int, int]:
    """Return the exact Python offsets covered by a resolved source range."""
    if source.column_start is not None:
        if source.column_end is None:
            raise ValueError("Source range has incomplete columns")
        start = source_offset(text, source.line_start, source.column_start)
        end = source_offset(text, source.line_end, source.column_end)
    else:
        lines = text.split("\n")
        if source.line_end < source.line_start or source.line_end > len(lines):
            raise ValueError("Source range is out of bounds")
        start = source_offset(text, source.line_start, 0)
        end_column = len(lines[source.line_end - 1].encode("utf-16-le")) // 2
        end = source_offset(text, source.line_end, end_column)
    if end <= start:
        raise ValueError("Source range is empty or out of bounds")
    return start, end


def _parse_bbox(spec: str) -> tuple[float, float, float, float]:
    """Parse ``x1,y1,x2,y2`` in PDF points."""
    try:
        parts = [float(p) for p in spec.split(",")]
    except ValueError:
        raise ValueError("bbox must be x1,y1,x2,y2 (PDF points)")
    if len(parts) != 4:
        raise ValueError("bbox must have exactly 4 values")
    return parts[0], parts[1], parts[2], parts[3]


def resolve_section_to_source(
    structure: DocumentStructure, watch_dir: Path, title: str | None, label: str | None,
    near: tuple[str, int] | None = None,
) -> ResolvedSource | None:
    """Look up a section in *structure* and return a fully-resolved source range.

    Returns None when no matching section exists.  When the section runs to
    end-of-file (``line_end < 0`` from :func:`find_section`), reads the file
    to compute the true EOF line. *near* tells apart headings with one title.
    """
    match = find_section(structure, title=title, label=label, near=near)
    if match is None:
        return None
    file, line_start, line_end = match
    if line_end < 0:
        line_end = _eof_line(watch_dir / file, line_start)
    return ResolvedSource(file=file, line_start=line_start, line_end=line_end)


def resolve_text_selection_to_source(
    pdf_path: Path,
    selection: PageSelection,
    watch_dir: Path,
) -> ResolvedSource | None:
    """Return a source range only when every selected PDF line reverse-syncs."""
    match = selection_to_source_range(
        pdf_path,
        selection.page,
        selection.rects,
        watch_dir,
    )
    if match is None:
        return None
    file, line_start, line_end = match
    return ResolvedSource(file=file, line_start=line_start, line_end=line_end)


def structure_to_dict(
    structure: DocumentStructure, watch_dir: Path
) -> dict[str, list[dict[str, Any]]]:
    """JSON-serializable view of :class:`DocumentStructure`.

    Sections only — labels / citations / inputs are deliberately omitted;
    The coding agent can search those directly.
    """
    sections: list[dict[str, Any]] = []
    for s in structure.sections:
        line_end = section_end(structure, s)
        if line_end < 0:
            line_end = _eof_line(watch_dir / s.file, s.line)
        sections.append(
            {
                "level": s.level,
                "number": s.number,
                "title": s.title,
                "file": s.file,
                "line": s.line,
                "line_end": line_end,
                "label": s.label,
            }
        )
    return {"sections": sections}


def load_synctex_for_main(main_file: Path) -> SyncTeXData | None:
    """Find and parse the .synctex.gz next to *main_file*'s rendered PDF.

    Returns None if no PDF/SyncTeX exists yet (first compile hasn't run,
    or the compiler doesn't produce SyncTeX, e.g. pandoc).  Useful for
    out-of-process callers (the MCP server) that need to do PDF-region
    resolution without owning the daemon's state.
    """
    pdf_path = main_file.with_suffix(".pdf")
    if not pdf_path.exists():
        return None
    synctex_path = find_synctex_file(pdf_path)
    if synctex_path is None:
        return None
    return parse_synctex(synctex_path)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------



_OVERLEAF_HOSTS = ("www.overleaf.com", "overleaf.com")


def _overleaf_link(raw: Any) -> str:
    """A project's link given on the page, as the config keeps it. Only overleaf.com, or
    plain http to this machine, since the reviewer's cookie goes wherever it points."""
    from urllib.parse import urlsplit

    server, project = overleaf.project_of(str(raw))
    host = urlsplit(server).hostname
    if host not in _OVERLEAF_HOSTS and not server.startswith("http://"):
        raise ValueError("give the project's link on overleaf.com; another server is named in "
                         ".tex-mcp-web.yaml itself")
    if host in _OVERLEAF_HOSTS:
        server = "https://www.overleaf.com"
    return f"{server}/project/{project}"


def _set_config_keys(path: Path, changes: dict[str, Any]) -> None:
    """Set or remove top-level ``key: value`` lines of a config, leaving every other line,
    comment and order as it was. A value None removes the key; True is written ``true``."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for key, value in changes.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*:")
        found = [i for i, line in enumerate(lines) if pattern.match(line)]
        text = None if value is None else f"{key}: {'true' if value is True else value}\n"
        for i in reversed(found[1:]):
            del lines[i]
        if found:
            if text is None:
                del lines[found[0]]
            else:
                lines[found[0]] = text
        elif text is not None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(text)
    new = "".join(lines)
    yaml.safe_load(new)              # still a config before it replaces the old one
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(new, encoding="utf-8")
    os.replace(temp, path)


_LOCAL_NAMES = ("localhost", "127.0.0.1", "::1")


@web.middleware
async def _only_its_own_pages(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
    """The page answers this machine alone, and only its own pages. A request addressed by
    another name, as from a site that points its name at this machine, and one sent by
    another site's page, which the browser marks with that page's origin, are turned away:
    either could act as the reviewer on the paper and on Overleaf. A tool on this machine
    sends no origin and is answered."""
    from urllib.parse import urlsplit

    if urlsplit(f"//{request.host}").hostname not in _LOCAL_NAMES:
        raise web.HTTPForbidden(text="tex-mcp-web answers only at localhost")
    origin = request.headers.get("Origin")
    if origin is not None and urlsplit(origin).netloc != request.host:
        raise web.HTTPForbidden(text="tex-mcp-web answers only its own pages")
    return await handler(request)


class TexMcpWebServer:
    """Single-paper watch + serve + comment store."""

    # A parked waiter is answered within this long even when nothing happened, so the
    # script's curl never trips its own timeout and a lost connection is noticed.
    REVIEW_POLL_TIMEOUT = 55.0
    # How long a session that asked to listen has to start its waiter before a session
    # starting up may listen in its place.
    LISTENER_START = 60.0

    def __init__(self, config: Config):
        self.config = config
        self.watch_dir = get_watch_dir(config)
        self.main_file = get_main_file(config)
        # The token exists before the first request, so the tools on this machine send it then.
        if config.page_token:
            page_token.ensure_token()

        self.last_result: CompileResult | None = None
        self.synctex_data: SyncTeXData | None = None
        # The translation panel's translator; built on the first request for it and
        # again when the configured language or instructions change.
        self.translator: Translator | None = None
        self.structure: DocumentStructure | None = None
        self.compiling = False
        self.pdf_digest: str | None = None
        self._compile_task: asyncio.Task[CompileResult] | None = None
        # Per-page text hashes from the previous compile, for change
        # detection.  Empty after process startup; populated on each
        # successful build.
        self._prev_page_hashes: list[str] = []

        self.comments = CommentStore(self.watch_dir / ".tex-mcp-web" / "comments.json")
        # The reviewer's Call agent presses: how many were made and how many a waiter
        # confirmed it delivered. Kept on disk so a press outlives a server restart.
        self.review_state_path = self.watch_dir / ".tex-mcp-web" / "review-state.json"
        self.review_calls, self.review_consumed = self._load_review_state()
        self.review_called = asyncio.Event()
        self.review_waiters = 0
        # Presses go to one waiter, named by the id listen() gave it: two sessions answering
        # one press would edit the same threads. Its pid, which its polls report, says
        # whether it still runs, and the Claude Code session that named it, when known,
        # whose own earlier waiter hears that it is that session listening on.
        self.review_listener: str | None = None
        self.review_listener_pid: int | None = None
        self.review_listener_session: int | None = None
        self.review_listener_claimed = 0.0
        self.closing = False
        self.websockets: set[web.WebSocketResponse] = set()
        self.overleaf_live: LivePresence | None = None
        self._overleaf_live_stop: asyncio.TimerHandle | None = None
        self._overleaf_live_push: asyncio.TimerHandle | None = None
        # One look at a time at whether the session should be open: two at once would each
        # find none open and open one.
        self._overleaf_live_turn = asyncio.Lock()
        self._overleaf_import = asyncio.Lock()
        # One git command at a time on the paper's folder: a fetch under a merge would
        # report a state that is about to change.
        self._overleaf_git = asyncio.Lock()
        # Open threads on Overleaf not yet brought in, as the last reading counted them.
        self._overleaf_waiting = 0
        # Whether every file kept the same both ways was in step when last looked at.
        self._overleaf_was_in_step = False
        self.overleaf_sync: ProjectSync | None = None
        # The files brought together with Overleaf by Get and Send, in a folder that is no
        # git clone of the project and does not follow it live.
        self.overleaf_hand: HandSync | None = None
        # Where a git clone stood against Overleaf's branch when last read.
        self._overleaf_git_now: dict[str, Any] | None = None
        # Texts the page may save over, by file and revision, for a save made over an
        # edit from Overleaf to be brought together with it.
        self._source_texts: dict[str, dict[str, str]] = {}
        self._overleaf_reads = 0
        self._overleaf_reader: asyncio.Future | None = None
        self._overleaf_deleted: set[str] = set()
        # The suggested edits on Overleaf by document, with the text they are in, as last read,
        # and the reading that waits for edits made as suggestions to settle.
        self._overleaf_tracked: dict[str, dict[str, Any]] = {}
        self._overleaf_tracked_read: asyncio.TimerHandle | None = None
        self.watcher: Watcher | None = None
        self._runner: web.AppRunner | None = None
        self._store_watch: asyncio.Task[None] | None = None
        self._initial_compile: asyncio.Task[CompileResult] | None = None

        self.app = self._build_app()

    # ----- routes -----

    @web.middleware
    async def _only_with_its_token(self, request: web.Request,
                                   handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
        """With page_token on, the page answers a request that carries the reviewer's token:
        the cookie a browser keeps once it opened the page's link, or the header a tool on this
        machine sends. The link sets the cookie and goes on to the same page without the token,
        so the address bar, and a bookmark made from it, hold the plain address."""
        if not self.config.page_token:
            return await handler(request)
        token = page_token.ensure_token().encode()
        given = request.headers.get(page_token.HEADER) or request.cookies.get(page_token.COOKIE) or ""
        if hmac.compare_digest(given.encode(), token):
            return await handler(request)
        if request.method == "GET" and hmac.compare_digest(request.query.get("token", "").encode(), token):
            rest = request.rel_url.with_query({key: value for key, value in request.rel_url.query.items()
                                               if key != "token"})
            opened = web.HTTPSeeOther(str(rest))
            opened.set_cookie(page_token.COOKIE, token.decode(), httponly=True, samesite="Strict", path="/",
                              max_age=400 * 24 * 3600)
            raise opened
        raise web.HTTPForbidden(text="This review page opens from its link, which `tex-mcp-web link` prints "
                                     "in the paper's folder.")

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=8 * 1024 * 1024,
                              middlewares=[_only_its_own_pages, self._only_with_its_token])
        app.router.add_get("/", self._handle_root)
        app.router.add_get("/static/{name:.*}", self._handle_static)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/pdf", self._handle_pdf)
        app.router.add_get("/pdf/download", self._handle_pdf_download)
        app.router.add_get("/paper", self._handle_paper)
        app.router.add_get("/sources", self._handle_sources)
        app.router.add_get("/source", self._handle_source)
        app.router.add_put("/source", self._handle_save_source)
        app.router.add_post("/compile", self._handle_compile)
        app.router.add_get("/comments", self._handle_list_comments)
        app.router.add_post("/comments", self._handle_create_comment)
        app.router.add_get(r"/comments/{id}", self._handle_get_comment)
        app.router.add_post(
            r"/comments/{id}/apply-suggestion",
            self._handle_apply_suggestion,
        )
        app.router.add_post(r"/comments/{id}/reply", self._handle_reply_comment)
        app.router.add_post("/comments/status", self._handle_set_status)
        app.router.add_post(r"/comments/{id}/resolve", self._handle_resolve_comment)
        app.router.add_post(r"/comments/{id}/reopen", self._handle_reopen_comment)
        app.router.add_post(r"/comments/{id}/archive", self._handle_archive_comment)
        app.router.add_post(r"/comments/{id}/edit", self._handle_edit_comment_entry)
        app.router.add_delete(r"/comments/{id}", self._handle_delete_comment)
        app.router.add_get("/synctex/source-to-pdf", self._handle_synctex_forward)
        app.router.add_post("/synctex/pdf-to-source", self._handle_synctex_backward)
        app.router.add_post("/synctex/source-to-pdf-words", self._handle_source_to_pdf_words)
        app.router.add_get("/translations", self._handle_translations)
        app.router.add_get("/completions", self._handle_completions)
        app.router.add_post("/goto", self._handle_goto)
        app.router.add_get("/image", self._handle_image)
        app.router.add_get("/reference-preview", self._handle_reference_preview)
        app.router.add_post("/review-request", self._handle_review_request)
        app.router.add_get("/overleaf", self._handle_overleaf)
        app.router.add_post("/overleaf/cookie", self._handle_overleaf_cookie)
        app.router.add_delete("/overleaf/cookie", self._handle_overleaf_cookie)
        app.router.add_post("/overleaf/comments", self._handle_overleaf_comments)
        app.router.add_post("/overleaf/changes/read", self._handle_overleaf_changes_read)
        app.router.add_post(r"/overleaf/changes/{action:accept|reject}", self._handle_overleaf_changes)
        app.router.add_post(r"/overleaf/git/{action:fetch|state|pull|push}", self._handle_overleaf_git)
        app.router.add_post(r"/overleaf/files/{action:read|get|send}", self._handle_overleaf_files)
        app.router.add_get("/overleaf/upstream", self._handle_overleaf_upstream)
        app.router.add_post("/overleaf/upstream", self._handle_overleaf_upstream)
        app.router.add_get("/overleaf/live", self._handle_overleaf_live)
        app.router.add_post("/overleaf/sync", self._handle_overleaf_sync)
        app.router.add_post("/overleaf/connect", self._handle_overleaf_connect)
        app.on_cleanup.append(lambda _app: self._stop_overleaf_live())
        app.router.add_get("/wait-review", self._handle_wait_review)
        app.router.add_post("/wait-review/listen", self._handle_claim_review)
        app.router.add_post("/wait-review/ack", self._handle_ack_review)
        return app

    # ----- compile + watch -----

    async def do_compile(self) -> CompileResult:
        """Return the active build when compile requests overlap."""
        active = self._compile_task
        if active is not None and not active.done():
            return await asyncio.shield(active)

        task = asyncio.create_task(self._compile_once())
        self._compile_task = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._compile_task is task:
                self._compile_task = None

    async def _compile_once(self) -> CompileResult:
        self.compiling = True
        await self.broadcast({"type": "compiling", "status": True})
        changed_pages: list[int] = []
        try:
            self.last_result = await compile_tex(
                main_file=self.main_file,
                compiler=self.config.compiler,
                work_dir=self.watch_dir,
            )
            # Reload SyncTeX
            if self.last_result.output_file and self.main_file.suffix.lower() == ".tex":
                synctex_path = find_synctex_file(self.last_result.output_file)
                if synctex_path:
                    self.synctex_data = parse_synctex(synctex_path)
            # Refresh structure
            self.structure = parse_structure(self.watch_dir, self.main_file)
            # Compute per-page text hashes and diff against the
            # previous compile.  Gives the agent a cheap "what
            # changed visually" signal without rendering everything.
            if self.last_result.success and self.last_result.output_file:
                from . import imaging
                # The run's dependency record is authoritative for what to watch; a
                # failed run leaves the last good set in place.
                if self.watcher is not None:
                    self.watcher.set_roots(self.source_roots())
                self.pdf_digest = pdf_digest(self.last_result.output_file)
                self.comments.refresh_anchors(
                    self.watch_dir,
                    self.last_result.output_file,
                    sections_resolver=lambda title, label, near: resolve_section_to_source(
                        self.structure, self.watch_dir, title, label, near
                    ),
                    text_resolver=lambda selection: resolve_text_selection_to_source(
                        self.last_result.output_file,
                        selection,
                        self.watch_dir,
                    ),
                )
                new_hashes = imaging.page_text_hashes(self.last_result.output_file)
                changed_pages = imaging.diff_page_hashes(
                    self._prev_page_hashes, new_hashes
                )
                self._prev_page_hashes = new_hashes

            self.last_result.pages_changed = changed_pages
            logger.info(
                "Compile %s in %.2fs%s",
                "succeeded" if self.last_result.success else "failed",
                self.last_result.duration_seconds,
                f" (pages changed: {changed_pages})" if changed_pages else "",
            )
        finally:
            self.compiling = False
            await self.broadcast({"type": "compiling", "status": False})
            msg: dict[str, Any] = {
                "type": "compiled",
                "result": _result_to_dict(self.last_result),
                "pdf_digest": self.pdf_digest,
                "pages_changed": changed_pages,
            }
            await self.broadcast(msg)
        return self.last_result

    async def apply_config(self) -> None:
        """Take up an edited .tex-mcp-web.yaml. The port and the paper's folder stay
        what the server started with: the page, the listen script and the comment store
        are bound to them."""
        config = load_config(self.config.config_path)
        if config.port != self.config.port:
            raise ValueError(
                f"port changed from {self.config.port} to {config.port}. "
                "The new port applies when the agent restarts"
            )
        if get_watch_dir(config).resolve() != self.watch_dir.resolve():
            raise ValueError("dir changed. The new folder applies when the agent restarts")
        rebuild = (config.main, config.compiler) != (self.config.main, self.config.compiler)
        if config.page_token:
            page_token.ensure_token()
        self.config = config
        self.main_file = get_main_file(config)
        if self.watcher is not None:
            self.watcher.update_patterns(config.watch, config.ignore)
            self.watcher.set_roots(self.source_roots())
        await self.broadcast({"type": "config_reloaded"})
        await self._sync_overleaf_live()
        # A new main file or compiler makes a different PDF, which the page would
        # otherwise go on showing the old one of.
        if rebuild:
            await self.do_compile()

    async def on_file_change(self, changed_path: str) -> None:
        if (self.config.config_path is not None
                and Path(changed_path).resolve() == self.config.config_path.resolve()):
            try:
                await self.apply_config()
            except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
                await self.broadcast({"type": "config_error", "error": str(error)})
            return
        try:
            path = Path(changed_path).resolve()
            relative = path.relative_to(self.watch_dir.resolve()).as_posix()
            data = path.read_bytes() if path.is_file() else None
            revision = hashlib.sha256(data).hexdigest() if data is not None else None
            if data is not None and self.overleaf_sync is not None and self.overleaf_sync.owns(relative):
                self._remember_source(relative, revision, data.decode("utf-8", errors="replace"))
            self.comments.refresh_source_anchors(self.watch_dir)
            await self.broadcast(
                {"type": "source_changed", "path": relative, "revision": revision}
            )
            await self._place_overleaf_comments_in(relative)
            # A suggested edit's place in the file moves with the file.
            if any(str(held["pathname"]).lstrip("/") == relative for held in self._overleaf_tracked.values()):
                await self.broadcast({"type": "comments_changed"})
        except (OSError, ValueError):
            pass
        finally:
            if self.overleaf_sync is not None:
                self.overleaf_sync.poke()
            if self.overleaf_hand is not None:
                self.overleaf_hand.local_changed()

    # ----- websocket -----

    async def broadcast(self, msg: dict) -> None:
        if not self.websockets:
            return
        for ws in list(self.websockets):
            try:
                await ws.send_json(msg)
            except Exception:
                self.websockets.discard(ws)
                # Best-effort close so the underlying socket releases its
                # fd / task; a flaky reconnect loop would otherwise leak.
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)  # heartbeat handles ping/pong
        await ws.prepare(request)
        self.websockets.add(ws)
        # Send initial state
        await ws.send_json(
            {
                "type": "state",
                "compiling": self.compiling,
                "result": _result_to_dict(self.last_result),
                "review_waiters": self.review_waiters,
            }
        )
        if self.overleaf_live is not None:
            await ws.send_json({"type": "overleaf_live", **self.overleaf_live.snapshot()})
        await self._sync_overleaf_live()
        try:
            async for _ in ws:
                # The viewer is read-only; we don't accept any client-sent
                # messages.  Iteration just keeps the socket alive.
                pass
        finally:
            self.websockets.discard(ws)
            # A handler cancelled as its page goes must still schedule the session's stop.
            await asyncio.shield(self._sync_overleaf_live())
        return ws

    # ----- Overleaf: who is in the project -----

    # Seconds the connection outlives the last page, so a reload does not drop it.
    OVERLEAF_LIVE_GRACE = 30
    # Seconds before reading comments after news of them, twice: the thread is announced
    # before the words it is on are stored.
    OVERLEAF_COMMENT_SETTLE = 2
    # Seconds of quiet after an edit made as a suggestion, someone's typing, before the
    # suggested edits are read.
    OVERLEAF_TRACKED_SETTLE = 3

    async def _sync_overleaf_live(self, restart: bool = False) -> None:
        """Keep the Overleaf session open while a page is: the reviewer looking at
        the paper is the reviewer in the project. It needs the config's overleaf: and the
        reviewer's cookie; a new cookie or link starts it again."""
        async with self._overleaf_live_turn:
            await self._sync_overleaf_live_now(restart)

    async def _sync_overleaf_live_now(self, restart: bool) -> None:
        wanted = bool(self.websockets) and bool(self.config.overleaf) and overleaf.read_cookie() is not None
        syncing = wanted and self.config.overleaf_sync and await self._overleaf_live_misplaced() is None
        live = self.overleaf_live
        if live is not None and (restart or live.link != self.config.overleaf or not self.config.overleaf
                                 or overleaf.read_cookie() is None
                                 or (wanted and (live.sync is not None) != syncing)):
            await self._stop_overleaf_live()
            live = None
        if wanted:
            if self._overleaf_live_stop is not None:
                self._overleaf_live_stop.cancel()
                self._overleaf_live_stop = None
            if live is None:
                if syncing:
                    self.overleaf_sync = ProjectSync(self.watch_dir, self._overleaf_live_changed,
                                                     written=self._overleaf_written, adding=self._overleaf_adding)
                    await self.overleaf_sync.start()
                self.overleaf_live = LivePresence(self.config.overleaf, overleaf.read_cookie,
                                                  self._overleaf_live_changed, comments=self._overleaf_comment_news,
                                                  sync=self.overleaf_sync)
                if self.overleaf_sync is not None:
                    self.overleaf_sync.tracking = self.overleaf_live.tracking
                # Without a git clone the uploaded files, figures and the like, follow Overleaf
                # whole by Overleaf's own routes too, live or by Get and Send.
                if await self._overleaf_git_link() is None:
                    uploads = Uploads(self.watch_dir, self.overleaf_live.tree, lambda: self.config.overleaf,
                                      overleaf.read_cookie, written=self._overleaf_written)
                    if self.overleaf_sync is not None:
                        self.overleaf_sync.uploads = uploads
                    else:
                        self.overleaf_hand = HandSync(self.watch_dir, self._overleaf_live_changed,
                                                      self.overleaf_live.editor, written=self._overleaf_written,
                                                      adding=self._overleaf_adding)
                        self.overleaf_hand.uploads = uploads
                        self.overleaf_hand.tracking = self.overleaf_live.tracking
                        self.overleaf_live.files = self.overleaf_hand
                await self.overleaf_live.start()
        elif live is not None and self._overleaf_live_stop is None:
            loop = asyncio.get_running_loop()
            self._overleaf_live_stop = loop.call_later(
                self.OVERLEAF_LIVE_GRACE, lambda: asyncio.ensure_future(self._stop_overleaf_live(idle=True)))

    async def _stop_overleaf_live(self, idle: bool = False) -> None:
        if idle and self.websockets:
            self._overleaf_live_stop = None
            return
        if self._overleaf_live_stop is not None:
            self._overleaf_live_stop.cancel()
            self._overleaf_live_stop = None
        self._overleaf_reads = 0
        self._overleaf_was_in_step = False
        self._overleaf_deleted.clear()
        self._overleaf_tracked = {}
        if self._overleaf_tracked_read is not None:
            self._overleaf_tracked_read.cancel()
            self._overleaf_tracked_read = None
        reader, self._overleaf_reader = self._overleaf_reader, None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        live, self.overleaf_live = self.overleaf_live, None
        self.overleaf_hand = None
        if live is not None:
            await live.stop()
        sync, self.overleaf_sync = self.overleaf_sync, None
        if sync is not None:
            await sync.stop()
            await self.broadcast({"type": "overleaf_live", **live.snapshot()})

    def _overleaf_live_changed(self) -> None:
        """Changes come in bursts (a typist's cursor); the page hears of them a little later,
        once. Threads that waited while a file kept the same both ways was out of step come
        in once every file is in step."""
        in_step = self._overleaf_in_step()
        if in_step and not self._overleaf_was_in_step and self._overleaf_waiting:
            self._overleaf_comment_news("", [])
        self._overleaf_was_in_step = in_step
        if self._overleaf_live_push is not None:
            return

        def push() -> None:
            self._overleaf_live_push = None
            if self.overleaf_live is not None:
                asyncio.ensure_future(self.broadcast({"type": "overleaf_live", **self.overleaf_live.snapshot()}))

        self._overleaf_live_push = asyncio.get_running_loop().call_later(0.2, push)

    def _overleaf_comment_news(self, name: str, args: list) -> None:
        """Comments changed on Overleaf, or the session opened after a time away: read them.
        A session opened also reads where the files brought together by hand stand. Suggested
        edits come as someone types, and are read with the comments once the typing settles."""
        if name == TRACKED:
            if self._overleaf_tracked_read is not None:
                self._overleaf_tracked_read.cancel()
            self._overleaf_tracked_read = asyncio.get_running_loop().call_later(
                self.OVERLEAF_TRACKED_SETTLE, lambda: self._overleaf_comment_news("", []))
            return
        if name == "joined" and self.overleaf_hand is not None:
            asyncio.ensure_future(self._read_overleaf_files())
        if name == "delete-thread" and args:
            self._overleaf_deleted.add(str(args[0]))
            if self.overleaf_sync is not None:
                self.overleaf_sync.forget_thread(str(args[0]))
        self._overleaf_reads = 2
        if self._overleaf_reader is None or self._overleaf_reader.done():
            self._overleaf_reader = asyncio.ensure_future(self._follow_overleaf_comments())

    async def _follow_overleaf_comments(self) -> None:
        while self._overleaf_reads > 0:
            self._overleaf_reads -= 1
            await asyncio.sleep(self.OVERLEAF_COMMENT_SETTLE)
            await self._read_overleaf_comments()

    async def _read_overleaf_comments(self) -> None:
        live, cookie = self.overleaf_live, overleaf.read_cookie()
        if live is None or cookie is None:
            return
        trouble = ""
        try:
            async with self._overleaf_import:
                data = await overleaf.fetch_export(live.link, cookie)
                deleted, self._overleaf_deleted = self._overleaf_deleted, set()
                # With files kept the same both ways a new thread comes in by itself, at a
                # moment every file is in step, and waits for that moment otherwise. Without,
                # it waits for Sync comments.
                new = self._overleaf_in_step()
                await self._bring_in_overleaf(data, deleted, new=new)
                await self._take_tracked(data)
            # Every file came into step while this reading counted threads as waiting.
            if not new and self._overleaf_waiting and self._overleaf_in_step():
                self._overleaf_comment_news("", [])
        except overleaf.OverleafError as error:
            trouble = str(error)
        if live.comments_trouble != trouble:
            live.comments_trouble = trouble
            self._overleaf_live_changed()

    async def _take_tracked(self, data: dict[str, Any]) -> None:
        """The suggested edits a reading found, which the page shows once they differ."""
        tracked = data.get("tracked") or {}
        if tracked != self._overleaf_tracked:
            self._overleaf_tracked = tracked
            await self.broadcast({"type": "comments_changed"})

    def _tracked_view(self) -> list[dict[str, Any]]:
        """The suggested edits on Overleaf for the page, by file in the order the paper reads
        them: who suggested what and when, the line on Overleaf, and the characters in the file
        here, or no place when the text around them is not here as on Overleaf."""
        shown = []
        for doc, held in sorted(self._overleaf_tracked.items(), key=lambda item: item[1]["pathname"]):
            path = str(held["pathname"]).lstrip("/")
            groups = _suggestion_groups(held["changes"])
            try:
                here = (self.watch_dir / path).read_text(encoding="utf-8")
                places = _place_suggestions(held["text"], here, groups)
            except (OSError, UnicodeDecodeError, ValueError):
                here, places = "", [None] * len(groups)
            for group, place in zip(groups, places):
                start = end = None
                if place is not None:
                    line_start, column_start = _line_column(here, place[0])
                    line_end, column_end = _line_column(here, place[1])
                    start = {"line_start": line_start, "column_start": column_start,
                             "line_end": line_end, "column_end": column_end}
                shown.append({"id": f"tc-{group['ids'][0]}", "doc": doc, "ids": group["ids"], "file": path,
                              "line": _line_column(held["text"], group["at"])[0],
                              "inserted": group["inserted"], "deleted": group["deleted"],
                              "user_id": group["user_id"], "name": group["name"],
                              "when": _suggested_when(group["ts"]), "here": start})
        return shown

    def _suggested_lines(self) -> list[str]:
        """The suggested edits on Overleaf in a line each, for the agent: where each is in the
        file here, who suggested it and what it changes."""
        def cut(words: str) -> str:
            return repr(words if len(words) <= 80 else words[:77] + "...")

        lines = []
        for shown in self._tracked_view():
            what = (f"changed {cut(shown['deleted'])} to {cut(shown['inserted'])}"
                    if shown["inserted"] and shown["deleted"]
                    else f"added {cut(shown['inserted'])}" if shown["inserted"] else f"deleted {cut(shown['deleted'])}")
            where = (f"{shown['file']}:{shown['here']['line_start']}" if shown["here"] is not None
                     else f"{shown['file']}:{shown['line']} on Overleaf, not in the file here yet")
            lines.append(f"{where} {shown['name'] or 'someone'} {what}")
        return lines

    async def _handle_overleaf_changes_read(self, request: web.Request) -> web.Response:
        """Read Overleaf's suggested edits again, as the page asks when the reviewer comes back
        to it. Overleaf tells the session of an edit only in a file the session has open, so an
        edit suggested in another file comes to no session but one that follows every file live."""
        if self.overleaf_live is None:
            return web.json_response({"error": "The page is not connected to Overleaf right now."}, status=409)
        self._overleaf_comment_news("", [])
        return web.json_response({"ok": True})

    async def _handle_overleaf_changes(self, request: web.Request) -> web.Response:
        """Accept or reject suggested edits on Overleaf, for everyone in the project."""
        action = request.match_info["action"]
        data, err = await self._read_json(request)
        if err is not None:
            return err
        doc, changes = str(data.get("doc", "")), [str(change) for change in data.get("ids") or []]
        live, cookie = self.overleaf_live, overleaf.read_cookie()
        if not changes:
            return web.json_response({"error": "no suggested edit was named"}, status=400)
        if live is None or cookie is None:
            return web.json_response({"error": "The page is not connected to Overleaf right now."}, status=409)
        try:
            # A reading under way ends first, so what it read before this cannot bring the
            # edits back, and one after reads what this left.
            async with self._overleaf_import:
                if action == "accept":
                    why = await live.reviewable(doc)
                    if why is None:
                        await overleaf.accept_changes(live.link, cookie, doc, changes)
                else:
                    why = await live.reject(doc, changes)
                if why is None:
                    # Gone from the list at once. The next reading says what Overleaf holds.
                    held = self._overleaf_tracked.get(doc)
                    if held is not None:
                        held["changes"] = [change for change in held["changes"] if change["id"] not in changes]
                        if not held["changes"]:
                            del self._overleaf_tracked[doc]
        except overleaf.SignedOut as error:
            return web.json_response({"error": f"{error} (the cookie has expired or is wrong)"}, status=401)
        except overleaf.OverleafError as error:
            return web.json_response({"error": str(error)}, status=502)
        except (ConnectionError, asyncio.TimeoutError):
            return web.json_response({"error": "The connection to Overleaf closed before it answered. "
                                               "Try again."}, status=502)
        if why is not None:
            return web.json_response({"error": why}, status=409)
        # Overleaf is read again soon, and a file brought together by hand counts the edit a
        # rejection made there to Get.
        self._overleaf_comment_news(TRACKED, [])
        if action == "reject" and self.overleaf_hand is not None:
            asyncio.ensure_future(self._read_overleaf_files())
        await self.broadcast({"type": "comments_changed"})
        return web.json_response({"ok": True})

    async def _overleaf_written(self, path: str) -> None:
        """A file written with an edit from Overleaf: the page is told at once."""
        await self.on_file_change(str(self.watch_dir / path))

    async def _handle_overleaf_sync(self, request: web.Request) -> web.Response:
        """The reviewer's choice for a file that stopped: which text stands."""
        follower = self.overleaf_sync or self.overleaf_hand
        if follower is None:
            return web.json_response({"error": "no file here is brought together with Overleaf"}, status=409)
        data, err = await self._read_json(request)
        if err is not None:
            return err
        try:
            error = await follower.decide(str(data.get("path", "")), str(data.get("choice", "")))
        except NotConnected as error:
            return web.json_response({"error": str(error)}, status=409)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response({"ok": True})

    def _remember_source(self, relative: str, revision: str, text: str) -> None:
        texts = self._source_texts.setdefault(relative, {})
        texts.pop(revision, None)
        texts[revision] = text
        while len(texts) > 20:
            texts.pop(next(iter(texts)))

    async def _handle_overleaf_live(self, request: web.Request) -> web.Response:
        snapshot = (self.overleaf_live.snapshot() if self.overleaf_live is not None else
                    {"status": "off", "message": "", "project": self.config.overleaf, "users": [], "changes": [],
                     "comments_trouble": ""})
        return web.json_response(snapshot, headers={"Cache-Control": "no-store"})

    # ----- API: Call agent -----

    def _load_review_state(self) -> tuple[int, int]:
        try:
            data = json.loads(self.review_state_path.read_text(encoding="utf-8"))
            calls, consumed = int(data["calls"]), int(data["consumed"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return 0, 0
        if calls < 0 or not 0 <= consumed <= calls:
            return 0, 0
        return calls, consumed

    def _save_review_state(self) -> None:
        self.review_state_path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.review_state_path.with_name(self.review_state_path.name + ".new")
        staging.write_text(json.dumps(
            {"calls": self.review_calls, "consumed": self.review_consumed}), encoding="utf-8")
        staging.replace(self.review_state_path)

    def _review_line(self) -> str:
        # The line counts what a wake-up is for: the open threads read_comments(new=True)
        # gives, with words for the agent it has neither answered nor carried before. A
        # count of open threads sent an agent to read one it had answered.
        from .mcp_server import _on_overleaf, cursor_moment

        edge = cursor_moment(self.watch_dir)
        brought = _on_overleaf(self.watch_dir)
        waiting = sum(bool(comment.waiting(edge, comment.id in brought)) for comment in self.comments.list(status="open"))
        return (f"[review] reviewer called (press #{self.review_calls}): "
                + (f"{waiting} new comments" if waiting else "no new comments")
                + " -- read them with read_comments(new=True)")

    async def _overleaf_status(self) -> dict[str, Any]:
        """The paper's Overleaf project and where the reviewer's cookie comes from; never
        the cookie itself. Read from this machine alone: what Overleaf's branch holds is
        read by /overleaf/git/fetch."""
        ledger = self._overleaf_ledger()
        brought = [known["comment"] for known in ledger.values()]
        link = await self._overleaf_git_link()
        return {"project": self.config.overleaf, "cookie": overleaf.cookie_source(),
                "sync": self.config.overleaf_sync,
                # Whether the files follow Overleaf live while a page is open: chosen, signed
                # in, and the folder is where Overleaf's files are.
                "live": bool(self.config.overleaf and self.config.overleaf_sync and overleaf.cookie_source()
                             and await self._overleaf_live_misplaced() is None),
                "settable": self.config.config_path is not None,
                # The git remote the paper's folder follows Overleaf by, or None.
                "git": link.remote if link is not None else None,
                "waiting": self._overleaf_waiting,
                # Every comment brought in from Overleaf, which the page may leave out of view
                # and offers to reply to there, and where on Overleaf those not yet on their
                # words are.
                "imported": brought,
                "unplaced": {known["comment"]: _place_name(known["place"]) for known in ledger.values()
                             if "place" in known},
                # The edits made on Overleaf with track changes on, waiting to be accepted or rejected.
                "suggested": self._tracked_view()}

    async def _handle_overleaf(self, request: web.Request) -> web.Response:
        return web.json_response(await self._overleaf_status(), headers={"Cache-Control": "no-store"})

    async def _overleaf_git_link(self) -> overleaf_git.Link | None:
        if not self.config.overleaf:
            return None
        return await overleaf_git.link_of(self.watch_dir, overleaf.project_of(self.config.overleaf)[1])

    async def _overleaf_live_misplaced(self) -> str | None:
        """Why the files cannot follow Overleaf live here, or None. In a git clone of the
        project, Overleaf's files are the clone's from its top, so the paper's folder must be
        that top for the two to name a file alike."""
        link = await self._overleaf_git_link()
        where = await overleaf_git.prefix(link) if link is not None else ""
        return (f"this folder is {where.rstrip('/')} inside a git clone of the project, so its files and "
                "Overleaf's are not named alike. Open the clone's top folder to follow Overleaf live."
                if where else None)

    async def _handle_overleaf_git(self, request: web.Request) -> web.Response:
        """Where the paper's folder stands against Overleaf's branch, read afresh, or the
        same after a pull or a push the reviewer asked for. state reads it without asking
        Overleaf, after a file changed here. A push given a message and files commits the
        changes to those files first. While the files are also kept the same with Overleaf,
        git takes them as Overleaf has them (see overleaf_git), a read takes Overleaf's
        commits in when it only has to move forward, and a pull or a push waits until every
        file is in step."""
        link = await self._overleaf_git_link()
        if link is None:
            return web.json_response({"error": "this folder is not a git clone of the Overleaf project"},
                                     status=409)
        action = request.match_info["action"]
        commit = None
        if action == "push" and request.can_read_body:
            data, err = await self._read_json(request)
            if err is not None:
                return err
            if (not isinstance(data, dict) or not isinstance(data.get("message"), str) or not data["message"].strip()
                    or not isinstance(data.get("files"), list) or not all(isinstance(f, str) for f in data["files"])):
                return web.json_response({"error": "a commit takes a message and the files to commit"}, status=400)
            commit = data["message"].strip(), data["files"]
        followed = None
        if self.overleaf_sync is not None:
            if self._overleaf_in_step():
                followed = [doc.path for doc in self.overleaf_sync.docs.values() if doc.status == "synced"]
            elif action not in ("fetch", "state"):
                apart = self._overleaf_apart()
                return web.json_response({"error": f"Not yet the same as on Overleaf: {', '.join(apart)}. Settle it first."
                                          if apart else "The files are still being read from Overleaf. Try again in a moment."},
                                         status=409)
        since = time.time()
        committed = False
        async with self._overleaf_git:
            try:
                if action == "fetch" and followed is not None:
                    now = await overleaf_git.follow(link, followed)
                elif action == "fetch":
                    await overleaf_git.fetch(link)
                    now = await overleaf_git.state(link)
                elif action == "state":
                    # Overleaf's branch as last read, so nothing seen on Overleaf is let go.
                    return web.json_response(await overleaf_git.state(link, followed or ()))
                else:
                    if commit is not None:
                        await overleaf_git.commit(link, *commit, followed or ())
                        committed = True
                    now = await (overleaf_git.pull if action == "pull" else overleaf_git.push)(link, followed)
            except overleaf_git.GitError as error:
                # A commit made before a push that did not go through stays, and goes with the next.
                return web.json_response({"error": ("Committed here. " if committed else "") + str(error)}, status=409)
        return web.json_response(self._seen_overleaf_git(now, since))

    async def _read_overleaf_git(self, link: overleaf_git.Link) -> dict[str, Any]:
        """Overleaf's branch fetched afresh, and where the folder stands against it."""
        since = time.time()
        await overleaf_git.fetch(link)
        return self._seen_overleaf_git(await overleaf_git.state(link), since)

    def _seen_overleaf_git(self, now: dict[str, Any], since: float) -> dict[str, Any]:
        """Keep where the folder stands against Overleaf's branch, read from *since* on, and
        let go of the edits seen on Overleaf before then that the folder holds."""
        self._overleaf_git_now = now
        if self.overleaf_live is not None:
            self.overleaf_live.brought_in(now["incoming"], since)
        return now

    async def _read_overleaf_hand(self, read: Callable[[], Awaitable[dict]]) -> dict:
        """Where the files stand against Overleaf's documents after *read*, a read, a Get or a
        Send by hand, with the edits seen on Overleaf before it that the files hold let go."""
        since = time.time()
        now = await read()
        if self.overleaf_live is not None:
            self.overleaf_live.brought_in(now["get"] + now["choose"], since)
        return now

    async def _overleaf_upstream(self, fresh: bool) -> dict[str, Any]:
        """Where the folder stands against Overleaf, in few words, for the agent: by git, by
        hand or live, and only what is not yet the same. Fresh reads Overleaf again, otherwise
        it is what was last read."""
        if not self.config.overleaf:
            return {"by": "none"}
        link = await self._overleaf_git_link()
        answer: dict[str, Any] = {}
        if self.overleaf_sync is not None:
            answer["by"] = "live"
            if apart := self._overleaf_apart():
                answer["apart"] = apart
        if link is not None:
            answer.setdefault("by", "git")
            answer["git"] = True
            now = self._overleaf_git_now
            if fresh or now is None:
                async with self._overleaf_git:
                    now = await self._read_overleaf_git(link)
            answer.update({key: now[key] for key in ("behind", "ahead", "uncommitted") if now[key]})
            return answer
        if self.overleaf_sync is not None:
            return answer
        hand = self.overleaf_hand
        if hand is None or (not fresh and not hand.read_once):
            return {"by": "hand", "unread": "no review page is in the Overleaf project, which By hand reads through"}
        now = await self._read_overleaf_hand(hand.read) if fresh else hand.state()
        return {"by": "hand", **{key: now[key] for key in ("get", "send", "choose") if now[key]}}

    async def _handle_overleaf_upstream(self, request: web.Request) -> web.Response:
        try:
            answer = await self._overleaf_upstream(fresh=request.method == "POST")
            if suggested := self._suggested_lines():
                answer["suggested"] = suggested
            return web.json_response(answer)
        except overleaf_git.GitError as error:
            return web.json_response({"error": str(error)}, status=409)
        except NotConnected as error:
            return web.json_response({"by": "hand", "unread": str(error)})
        except (ConnectionError, asyncio.TimeoutError):
            return web.json_response({"error": "The connection to Overleaf closed before it answered. Try again."},
                                     status=502)

    def _overleaf_adding(self) -> tuple[list[str], set[str]]:
        """The patterns of files only here that Send adds to Overleaf, and the paper's own
        compiled output, which it never adds."""
        stem = Path(self.config.main).with_suffix("")
        return list(self.config.overleaf_add), {f"{stem}.pdf", f"{stem}.synctex.gz"}

    async def _read_overleaf_files(self) -> None:
        hand = self.overleaf_hand
        try:
            if hand is not None:
                await self._read_overleaf_hand(hand.read)
        except (NotConnected, ConnectionError, asyncio.TimeoutError) as error:
            logger.info("Overleaf files: %s", error)

    async def _handle_overleaf_files(self, request: web.Request) -> web.Response:
        """Where the files stand against Overleaf's documents, read afresh, or the same after a
        Get or a Send the reviewer asked for, in a folder brought together with Overleaf by hand."""
        hand = self.overleaf_hand
        if hand is None:
            return web.json_response({"error": "this folder follows Overleaf by git or live, not by Get and Send"},
                                     status=409)
        action = request.match_info["action"]
        try:
            now = await self._read_overleaf_hand(hand.read if action == "read" else hand.get if action == "get"
                                                 else hand.send)
        except NotConnected as error:
            return web.json_response({"error": str(error)}, status=409)
        except (ConnectionError, asyncio.TimeoutError):
            return web.json_response({"error": "The connection to Overleaf closed before it answered. Try again."},
                                     status=502)
        return web.json_response(now)

    async def _handle_overleaf_connect(self, request: web.Request) -> web.Response:
        """Name the paper's Overleaf project from the page, or forget it, and turn keeping
        the files the same on or off. Each is written into .tex-mcp-web.yaml, so the file
        stays the one place that says them. A project named
        here must be on overleaf.com (or this machine): the reviewer's cookie goes to it."""
        path = self.config.config_path
        if path is None:
            return web.json_response({"error": "this paper has no .tex-mcp-web.yaml to keep it in"}, status=409)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("send {project, sync}")
            changes: dict[str, Any] = {}
            if "project" in body:
                changes["overleaf"] = _overleaf_link(body["project"]) if body["project"] else None
            project = changes["overleaf"] if "overleaf" in changes else self.config.overleaf
            if "sync" in body:
                if not isinstance(body["sync"], bool):
                    raise ValueError("sync must be true or false")
                if body["sync"] and project is None:
                    raise ValueError("connect a project first")
                if body["sync"] and (misplaced := await self._overleaf_live_misplaced()):
                    raise ValueError(misplaced)
                changes["overleaf_sync"] = body["sync"] or None
            if project is None:          # no project: nothing may send to one
                changes.update(overleaf=None, overleaf_sync=None)
        except ValueError as error:
            return web.json_response({"error": str(error)}, status=400)
        try:
            _set_config_keys(path, changes)
            await self.apply_config()
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            return web.json_response({"error": f"could not keep it in {path.name}: {error}"}, status=500)
        return web.json_response(await self._overleaf_status())

    async def _handle_overleaf_cookie(self, request: web.Request) -> web.Response:
        """Keep the reviewer's Overleaf session cookie, or forget it. Kept in their own
        config folder, so it serves every paper they open."""
        if request.method == "DELETE":
            overleaf.forget_cookie()
        else:
            try:
                body = await request.json()
                overleaf.save_cookie(str((body or {}).get("cookie", "")))
            except (ValueError, AttributeError) as error:
                return web.json_response({"error": str(error) or "paste the cookie's value"}, status=400)
        await self._sync_overleaf_live(restart=True)
        return web.json_response(await self._overleaf_status())

    def _overleaf_apart(self) -> list[str]:
        """The files kept the same both ways that are not the same as on Overleaf now."""
        if self.overleaf_sync is None:
            return []
        uploads = self.overleaf_sync.uploads
        return sorted([doc.path for doc in self.overleaf_sync.docs.values()
                       if doc.status not in ("synced", "unsupported") or doc.pending or doc.inflight is not None]
                      + (uploads.paths("get", "send", "differs") if uploads is not None else []))

    def _overleaf_in_step(self) -> bool:
        """Files are kept the same both ways, and every one is the same as on Overleaf."""
        return self.overleaf_sync is not None and bool(self.overleaf_sync.docs) and not self._overleaf_apart()

    async def _handle_overleaf_comments(self, request: web.Request) -> web.Response:
        """Bring in the comments of the project the config names, read from Overleaf with
        the reviewer's cookie. A folder whose files are kept the same both ways takes them
        only with every file in step, and otherwise a git clone only at the commit Overleaf
        is at: a thread is on words, and only the same text puts it exactly where Overleaf
        has it."""
        if not self.config.overleaf:
            return web.json_response(
                {"error": "name the project in .tex-mcp-web.yaml as overleaf: <its link>"}, status=409)
        cookie = overleaf.read_cookie()
        if cookie is None:
            return web.json_response({"error": "paste your Overleaf session cookie first"}, status=401)
        link = await self._overleaf_git_link() if self.overleaf_sync is None else None
        if link is not None:
            async with self._overleaf_git:
                try:
                    now = await self._read_overleaf_git(link)
                except overleaf_git.GitError as error:
                    return web.json_response({"error": str(error)}, status=409)
            if now["state"] != "same":
                count = now["behind"] or now["ahead"]
                commits = f"{count} commit{'' if count == 1 else 's'}"
                return web.json_response({"error": f"Overleaf has {commits} this folder lacks. Press git pull first."
                                          if now["behind"] else f"This folder has {commits} Overleaf lacks. "
                                          "Press commit & push first, so the comments sit on the text Overleaf has."}, status=409)
        elif self.overleaf_sync is not None:
            # Files kept the same both ways stand in for the commit: every one in step first.
            apart = self._overleaf_apart()
            if apart:
                return web.json_response({"error": f"Not yet the same as on Overleaf: {', '.join(apart)}. "
                                                   "Settle it first."}, status=409)
        elif self.overleaf_hand is not None:
            # Files brought together by hand stand in for the commit: nothing left to Get or
            # Send, read afresh as a git clone reads Overleaf's branch.
            try:
                now = await self._read_overleaf_hand(self.overleaf_hand.read)
            except NotConnected as error:
                return web.json_response({"error": str(error)}, status=409)
            except (ConnectionError, asyncio.TimeoutError):
                return web.json_response({"error": "The connection to Overleaf closed before it answered. "
                                                   "Try again."}, status=502)
            if now["get"] or now["choose"]:
                return web.json_response({"error": f"Overleaf has edits this folder lacks: "
                                                   f"{', '.join(now['get'] + now['choose'])}. Press Get first."}, status=409)
            if now["send"]:
                return web.json_response({"error": f"Edits here are not on Overleaf: {', '.join(now['send'])}. "
                                                   "Press Send first, so the comments sit on the text Overleaf has."},
                                         status=409)
        try:
            async with self._overleaf_import:
                data = await overleaf.fetch_export(self.config.overleaf, cookie)
                await self._take_tracked(data)
                return web.json_response(await self._bring_in_overleaf(data, new=True))
        except overleaf.SignedOut as error:
            return web.json_response({"error": f"{error} (the cookie has expired or is wrong)"}, status=401)
        except overleaf.OverleafError as error:
            return web.json_response({"error": str(error)}, status=502)

    def _overleaf_ledger(self) -> dict[str, dict[str, Any]]:
        return overleaf.ledger(self.watch_dir)

    def _save_overleaf_ledger(self, ledger: dict[str, dict[str, Any]]) -> None:
        path = overleaf.ledger_path(self.watch_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, indent=1), encoding="utf-8")

    async def _overleaf_lacks(self, checks: list[dict[str, str]]) -> tuple[str | None, str]:
        """Whether the words a new thread's draft describes are on Overleaf: why not, or None
        with the commit Overleaf's branch is at ("" for a folder that does not follow it by
        git). Files kept the same with Overleaf say what it holds; otherwise a git clone
        reads Overleaf's branch afresh."""
        link = await self._overleaf_git_link() if self.overleaf_sync is None else None
        if link is not None:
            async with self._overleaf_git:
                try:
                    now = await self._read_overleaf_git(link)
                    for check in checks:
                        if not await overleaf_git.holds(link, now["branch"], check["file"], check["text"]):
                            committed = await overleaf_git.holds(link, "HEAD", check["file"], check["text"])
                            gone = None if committed else self._changed_since_drafted(check)
                            return gone or (f"The edit to {check['file']} this reply describes is "
                                            + ("committed but not on Overleaf." if committed else "not committed.")
                                            + " Press commit & push first."), ""
                except overleaf_git.GitError as error:
                    return f"Overleaf's branch could not be read: {error}", ""
            return None, now["there"]
        if self.overleaf_hand is not None:
            for check in checks:
                try:
                    there = await self.overleaf_hand.text_of(check["file"])
                except (ConnectionError, asyncio.TimeoutError):
                    there = None
                if there is None or check["text"] not in there:
                    return (self._changed_since_drafted(check)
                            or f"The edit to {check['file']} this reply describes is not on Overleaf. Press Send first."), ""
            return None, ""
        for check in checks:
            doc = next((doc for doc in self.overleaf_sync.docs.values() if doc.path == check["file"]), None) \
                if self.overleaf_sync is not None else None
            if doc is None or doc.status != "synced" or check["text"] not in doc.server:
                return (self._changed_since_drafted(check)
                        or f"The edit to {check['file']} this reply describes is not on Overleaf"
                        + ("." if self.overleaf_sync is not None else ", and nothing here follows Overleaf: "
                           "this folder is not a git clone of the project, and keeping the files the same is off."),
                        "")
        return None, ""

    async def _overleaf_differs(self, files: list[str]) -> tuple[list[str], str]:
        """Which of *files* are not the same here as on Overleaf, with the commit Overleaf's
        branch is at ("" for a folder that does not follow it by git). In a git clone a file
        is the same when the folder has it as Overleaf's branch, read afresh, does, so with
        nothing to commit or git push and nothing to git pull. Files kept the same live say
        what Overleaf holds, and by hand Overleaf's own text is read."""
        link = await self._overleaf_git_link() if self.overleaf_sync is None else None
        if link is not None:
            async with self._overleaf_git:
                now = await self._read_overleaf_git(link)
                return [file for file in files if not await overleaf_git.same(link, now["branch"], file)], now["there"]
        if self.overleaf_sync is not None:
            docs = {doc.path: doc for doc in self.overleaf_sync.docs.values()}
            return [file for file in files if file not in docs or docs[file].status != "synced"
                    or docs[file].server != self._text_here(file)], ""
        if self.overleaf_hand is None:
            return list(files), ""
        apart = []
        for file in files:
            try:
                there = await self.overleaf_hand.text_of(file)
            except (ConnectionError, asyncio.TimeoutError):
                there = None
            if there is None or there != self._text_here(file):
                apart.append(file)
        return apart, ""

    def _text_here(self, file: str) -> str | None:
        path = self.watch_dir / file
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def _changed_since_drafted(self, check: dict[str, str]) -> str | None:
        """Why a reply cannot go when the file here no longer holds the words it describes:
        they changed after the draft was written, so the reply may not say what they hold.
        None while the file holds them."""
        path = self.watch_dir / check["file"]
        if path.is_file() and check["text"] in path.read_text(encoding="utf-8"):
            return None
        return (f"The lines of {check['file']} this reply describes changed after it was drafted. "
                "Ask the agent to draft it again.")

    async def _send_to_overleaf(self, cid: str, reply: str, anyway: bool) -> tuple[web.Response | None, str]:
        """Send the reviewer's reply to the Overleaf thread a comment came from, or open the
        thread a comment with no entries was drafted as, before anything changes here, so
        that what Overleaf does not take is not shown as said. Nothing goes while a range the
        draft describes is not found here (stale), since its lines cannot be named. A new thread goes once the
        words its draft describes are on Overleaf. A reply whose draft names files not the
        same here as on Overleaf goes only *anyway*, since it may describe an edit Overleaf
        does not have yet: the refusal names those files. Answers a refusal, or None with
        the commit Overleaf's branch was at. Readings wait meanwhile: one begun before this
        would bring the reply in again as someone else's."""
        cookie = overleaf.read_cookie()
        if cookie is None:
            return web.json_response({"error": "paste your Overleaf session cookie first"}, status=401), ""
        comment = self.comments.get(cid)
        checks = comment.overleaf_draft.checks if comment is not None and comment.overleaf_draft is not None else []
        lost = next((r.source.file for r in comment.overleaf_draft.ranges if r.stale), None) \
            if comment is not None and comment.overleaf_draft is not None else None
        if lost is not None:
            return web.json_response({"error": f"The lines of {lost} this reply describes are not found here any "
                                               "more. Ask the agent to draft it again."}, status=409), ""
        async with self._overleaf_import:
            ledger = self._overleaf_ledger()
            thread = next((key for key, known in ledger.items() if known["comment"] == cid), None)
            # A thread drafted for Overleaf, not yet opened there, opens with its draft, whatever
            # the reviewer and the agent said about the draft here.
            opening = thread is None and comment is not None and comment.overleaf_draft is not None
            if thread is None and not opening:
                return web.json_response({"error": "this comment did not come from Overleaf"}, status=409), ""
            if not self.config.overleaf:
                return web.json_response({"error": "name the Overleaf project in .tex-mcp-web.yaml first"}, status=409), ""
            try:
                if opening:
                    lacking, commit = await self._overleaf_lacks(checks)
                else:
                    apart, commit = await self._overleaf_differs(sorted({check["file"] for check in checks}))
                    lacking = None if anyway or not apart else (
                        f"{', '.join(apart)} {'is' if len(apart) == 1 else 'are'} not the same here as on "
                        "Overleaf, so this reply may describe an edit Overleaf does not have yet.")
            except overleaf_git.GitError as error:
                lacking, apart = f"Overleaf's branch could not be read: {error}", []
            except NotConnected as error:
                lacking, apart = str(error), []
            if lacking is not None:
                return web.json_response({"error": lacking, "apart": [] if opening else apart}, status=409), ""
            try:
                if opening:
                    refused, thread = await self._open_on_overleaf(comment, reply, cookie)
                    if refused is not None:
                        return web.json_response({"error": refused}, status=409), ""
                    ledger[thread] = {"comment": cid, "entries": {}}
                else:
                    await overleaf.post_reply(self.config.overleaf, cookie, thread, reply)
            except overleaf.SignedOut as error:
                return web.json_response({"error": f"{error} (the cookie has expired or is wrong)"}, status=401), ""
            except overleaf.OverleafError as error:
                return web.json_response({"error": str(error)}, status=502), ""
            except (ConnectionError, asyncio.TimeoutError):
                return web.json_response({"error": "The connection to Overleaf closed before it answered. "
                                                   "Send again."}, status=502), ""
            ledger[thread].setdefault("sent", []).append(reply)
            self._save_overleaf_ledger(ledger)
        return None, commit

    async def _open_on_overleaf(self, comment: Comment, text: str, cookie: tuple[str, str]) -> tuple[str | None, str]:
        """Open on Overleaf the thread a comment was drafted as: on the lines it sits on here,
        found in Overleaf's file nearest the line they are on here, with *text* as its first
        message. Its id is kept with the draft first, so a send that stops halfway finishes
        that thread when sent again. Why not, or None, with the thread's id."""
        source = comment.resolved_source
        words = None if comment.stale else read_anchored_source(self.watch_dir, comment)
        if source is None or not words or not words.strip():
            return "The lines this thread is on are not in the file here any more.", ""
        if self.overleaf_live is None:
            return "The page is not connected to Overleaf right now. Send again in a moment.", ""
        thread = comment.overleaf_draft.thread or overleaf.new_thread_id()
        if comment.overleaf_draft.thread is None:
            self.comments.name_overleaf_thread(comment.id, thread)
        refused = await self.overleaf_live.comment(
            source.file, words, source.line_start, thread,
            lambda: overleaf.open_thread(self.config.overleaf, cookie, thread, text))
        return refused, thread

    async def _bring_in_overleaf(self, data: dict[str, Any], deleted: Iterable[str] = (), *,
                                 new: bool) -> dict[str, int]:
        """Overleaf's threads, kept here as Overleaf has them. A thread here follows its thread
        there: new messages come in with the name Overleaf gives their writer, a message
        deleted or reworded there is deleted or reworded here, it is open or resolved as it
        is there, and it sits where Overleaf has it (see _place_overleaf_comment). A thread
        deleted there is deleted here, and one
        deleted here comes back. Entries written here stay in their thread. A thread not yet
        here comes in only when *new* says so, the reviewer having asked or every file kept
        the same both ways being in step: otherwise it is counted as waiting. A reply the reviewer sent from here is known by its words, once,
        since Overleaf does not say which message it made. The ledger is written however this
        ends, so what did come in does not come in again."""
        ledger = self._overleaf_ledger()
        for follower in (self.overleaf_sync, self.overleaf_hand):
            if follower is not None:
                follower.resolved_threads = {str(key) for key, thread in data["threads"].items()
                                             if isinstance(thread, dict) and thread.get("resolved")}
        counts = {"added": 0, "replies": 0, "resolved": 0, "unplaced": 0}
        waiting: set[str] = set()
        was_waiting = self._overleaf_waiting

        def said(message: dict[str, Any]) -> str:
            return str(message.get("content", "")).strip()

        def by(message: dict[str, Any]) -> str:
            return (message.get("user") or {}).get("name") or "someone"

        def keys(messages: list[dict[str, Any]]) -> list[str]:
            return [str(message.get("id") or f"#{index}") for index, message in enumerate(messages)]

        def entries_of(comment: Comment, known: dict[str, Any], current: list[str],
                       messages: list[dict[str, Any]]) -> dict[str, str]:
            """Overleaf's messages and the entries here holding them, by message. A ledger
            written before it kept this finds the messages it had seen by their words; one
            not found is still known, with no entry, so it does not come in twice."""
            if "entries" in known:
                return known["entries"]
            found: dict[str, str] = {}
            seen = set(known.get("seen", current[:known.get("messages", 0)]))
            for key, message in zip(current, messages):
                if key not in seen:
                    continue
                words = said(message)
                # Such a ledger is older than entries carrying a name, when a message brought
                # in began with its writer's name.
                held = next((entry for entry in comment.thread if entry.author == "human"
                             and entry.id not in found.values()
                             and entry.text in (f"[Overleaf · {by(message)}] {words}", words)), None)
                found[key] = held.id if held is not None else ""
            return found

        async def update(entry: dict[str, Any], thread: dict[str, Any], messages: list[dict[str, Any]],
                         known: dict[str, Any], comment: Comment) -> None:
            await self._place_overleaf_comment(comment, known, entry)
            comment = self.comments.get(comment.id) or comment
            current = keys(messages)
            held = entries_of(comment, known, current, messages)
            sent = known.get("sent", [])
            changed = False
            # Deleted there, deleted here; the thread's opening goes only with the thread.
            for key in [key for key in held if key not in current]:
                entry_id = held.pop(key)
                if entry_id and comment.thread[0].id != entry_id:
                    comment = self.comments.remove_entry(comment.id, entry_id)
                    changed = True
            for key, message in zip(current, messages):
                words = said(message)
                if key in held:
                    entry = next((entry for entry in comment.thread if entry.id == held[key]), None)
                    if entry is not None and (entry.text, entry.name) != (words, by(message)):
                        comment = self.comments.rewrite_entry(comment.id, entry.id, words, by(message))
                        changed = True
                    continue
                if words in sent:
                    sent.remove(words)
                    entry = next((entry for entry in comment.thread if entry.overleaf is not None
                                  and entry.text == words and entry.id not in held.values()), None)
                    held[key] = entry.id if entry is not None else ""
                    # Overleaf names who sent it, which this page did not know.
                    if entry is not None:
                        comment = self.comments.rewrite_entry(comment.id, entry.id, words, by(message))
                        changed = True
                    continue
                comment = self.comments.reply(comment.id, said(message), "human", name=by(message))
                held[key] = comment.thread[-1].id
                counts["replies"] += 1
                changed = True
            known["entries"] = held
            known.pop("seen", None)
            known.pop("messages", None)
            closed = bool(thread.get("resolved"))
            if closed and comment.status != "resolved":
                comment = self.comments.resolve(comment.id, summary="", author="human")
                changed = True
            elif not closed and comment.status != "open":
                comment = self.comments.reopen(comment.id)
                changed = True
            if changed:
                await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(comment)})

        try:
            for entry in data["comments"]:
                if not isinstance(entry, dict):
                    continue
                thread_id = str(entry.get("thread_id", ""))
                thread = data["threads"].get(thread_id)
                if not isinstance(thread, dict):
                    continue
                messages = [m for m in thread.get("messages") or []
                            if isinstance(m, dict) and str(m.get("content", "")).strip()]
                if not messages:
                    continue
                if thread_id in ledger:
                    comment = self.comments.get(ledger[thread_id]["comment"])
                    if comment is not None:
                        await update(entry, thread, messages, ledger[thread_id], comment)
                        continue
                    # Deleted here while Overleaf has it: it comes back as Overleaf has it.
                    del ledger[thread_id]
                if thread.get("resolved"):
                    counts["resolved"] += 1
                    continue
                if not new:
                    waiting.add(thread_id)
                    continue
                anchor, resolved, selector, where, _ = self._overleaf_anchor(entry)
                counts["unplaced"] += where is not None
                comment = self.comments.add(anchor=anchor, text=said(messages[0]), author="human",
                                            resolved_source=resolved, source_selector=selector,
                                            base_text=self._base_text(anchor, resolved), name=by(messages[0]))
                current = keys(messages)
                held = {current[0]: comment.thread[0].id}
                for key, message in zip(current[1:], messages[1:]):
                    comment = self.comments.reply(comment.id, said(message), "human", name=by(message))
                    held[key] = comment.thread[-1].id
                ledger[thread_id] = {"comment": comment.id, "entries": held}
                if where is not None:
                    ledger[thread_id]["place"] = _overleaf_place(entry)
                counts["added"] += 1
                await self.broadcast({"type": "comment_added", "comment": _comment_to_dict(comment)})
            # A thread Overleaf no longer has was deleted there, whether or not a page was
            # open to hear it go, and goes here too. A reading with no threads at all says
            # nothing either way.
            on_overleaf = set(data["threads"]) | {str(entry.get("thread_id", "")) for entry in data["comments"]
                                                  if isinstance(entry, dict)}
            gone = set(deleted) | ({key for key in ledger if key not in on_overleaf} if data["threads"] else set())
            for thread_id in gone:
                known = ledger.pop(thread_id, None)
                if known is not None and self.comments.delete(known["comment"]):
                    await self.broadcast({"type": "comment_deleted", "id": known["comment"]})
        finally:
            self._save_overleaf_ledger(ledger)
            self._overleaf_waiting = len(waiting)
        if self._overleaf_waiting != was_waiting:
            await self.broadcast({"type": "overleaf_waiting", "count": self._overleaf_waiting})
        return counts

    async def _place_overleaf_comment(self, comment: Comment, known: dict[str, Any], entry: dict[str, Any]) -> None:
        """Keep an open comment from Overleaf where Overleaf has it. One on words here moves
        only to where Overleaf puts it exactly, in a file holding Overleaf's text: elsewhere
        this file's own edits keep it on its words. One whose words were not in the file
        when it came in tries again, the ledger keeping where it is on Overleaf until then."""
        if comment.status != "open":
            known.pop("place", None)
            return
        anchor, resolved, selector, where, exact = self._overleaf_anchor(entry)
        if comment.anchor.kind != "paper":
            known.pop("place", None)
            if exact and anchor.to_dict() != comment.anchor.to_dict():
                moved = self.comments.place(comment.id, anchor, resolved, selector, self._base_text(anchor, resolved))
                await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(moved)})
            return
        if where is not None:
            known["place"] = _overleaf_place(entry)
            return
        known.pop("place", None)
        placed = self.comments.place(comment.id, anchor, resolved, selector, self._base_text(anchor, resolved))
        await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(placed)})

    async def _place_overleaf_comments_in(self, relative: str) -> None:
        """A file changed: comments from Overleaf waiting for words in it look again. Most
        changes have none waiting, and those do not wait for a reading or a send to finish."""
        def waits(known: dict[str, Any]) -> bool:
            return str((known.get("place") or {}).get("pathname", "")).lstrip("/") == relative

        if not any(waits(known) for known in self._overleaf_ledger().values()):
            return
        async with self._overleaf_import:
            ledger = self._overleaf_ledger()
            waiting = [known for known in ledger.values() if waits(known)]
            if not waiting:
                return
            for known in waiting:
                comment = self.comments.get(known["comment"])
                if comment is None:
                    known.pop("place")
                else:
                    await self._place_overleaf_comment(comment, known, known["place"])
            self._save_overleaf_ledger(ledger)

    def _overleaf_anchor(self, entry: dict[str, Any]) -> tuple[Any, Any, Any, str | None, bool]:
        """Where an Overleaf comment sits here, and whether that is exact. A file whose text
        has the hash Overleaf's has holds the same text, so the comment goes where Overleaf
        has it, character for character. Otherwise it goes on its words in the file, the
        occurrence nearest the line Overleaf gives, and a comment on no words takes that
        whole line. Words not in the file put it on the paper, with the place it has on
        Overleaf: the line by number may hold other words here."""
        path = str(entry.get("pathname") or "").lstrip("/")
        line = entry.get("line") if isinstance(entry.get("line"), int) else None
        where = f"{path}:{line}" if line else path or "an unknown place"
        words = str(entry.get("anchored_text") or "")
        try:
            file = self._resolve_source_path(path)
            text = file.read_text(encoding="utf-8")
        except (web.HTTPException, OSError, UnicodeError):
            return PaperAnchor(), None, None, where, False
        relative = file.relative_to(self.watch_dir.resolve()).as_posix()
        lines = text.split("\n")

        def place(offset: int) -> tuple[int, int]:
            before = text[:offset]
            return before.count("\n") + 1, len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2

        offset = entry.get("offset")
        exact = (bool(words) and isinstance(offset, int)
                 and entry.get("text_sha") == hashlib.sha256(text.encode("utf-8")).hexdigest()
                 and text[offset:offset + len(words)] == words)
        found = [match.start() for match in re.finditer(re.escape(words), text)] if words.strip() else []
        if exact or found:
            start = offset if exact else min(found, key=lambda at: abs(place(at)[0] - (line or 1)))
            (line_start, column_start), (line_end, column_end) = place(start), place(start + len(words))
            anchor = SourceRangeAnchor(file=relative, line_start=line_start, line_end=line_end,
                                       column_start=column_start, column_end=column_end)
        elif not words.strip() and line and 1 <= line <= len(lines):
            anchor = SourceRangeAnchor(file=relative, line_start=line, line_end=line)
        else:
            return PaperAnchor(), None, None, where, False
        resolved, selector = self._resolve_anchor(anchor)
        if selector is None:
            return PaperAnchor(), None, None, where, False
        return anchor, resolved, selector, None, exact

    def _wake_review_waiters(self) -> None:
        released = self.review_called
        self.review_called = asyncio.Event()
        released.set()

    def _review_listener_alive(self) -> bool:
        if self.review_listener is None:
            return False
        if self.review_listener_pid is None:
            return asyncio.get_running_loop().time() - self.review_listener_claimed < self.LISTENER_START
        try:
            os.kill(self.review_listener_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def _handle_review_request(self, request: web.Request) -> web.Response:
        self.review_calls += 1
        self._save_review_state()
        delivered = self.review_waiters > 0
        self._wake_review_waiters()
        await self.broadcast({"type": "review_requested", "calls": self.review_calls, "delivered": delivered})
        return web.json_response({"calls": self.review_calls, "delivered": delivered})

    async def _handle_claim_review(self, request: web.Request) -> web.Response:
        """Make the waiter with this listener id the one presses go to. With if_free, only
        when no other waiter runs: a session listening from its start takes presses from
        no one, and a session asked to listen takes them from whoever had them. session,
        when given, is the pid of the Claude Code process the listener runs under, which
        tells a waiter left by the same session from one another session took over."""
        try:
            listener = request.query["listener"]
            session = int(request.query["session"]) if "session" in request.query else None
        except (KeyError, ValueError) as error:
            raise web.HTTPBadRequest(text="listener is required, and session is a pid") from error
        if "if_free" in request.query and self._review_listener_alive():
            return web.json_response({"listening": False})
        self.review_listener, self.review_listener_pid = listener, None
        self.review_listener_session = session
        self.review_listener_claimed = asyncio.get_running_loop().time()
        # A waiter parked for the listener before is released to hear that it is not one.
        self._wake_review_waiters()
        return web.json_response({"listening": True})

    async def _handle_wait_review(self, request: web.Request) -> web.Response:
        # The consumption watermark lives here, not in the waiter: a waiter carrying its
        # own could never see a press made before its script was written. Waiting does
        # not consume: the response can die on the wire after the watermark moved, and
        # the wake-up died with it. The waiter acks what it printed (the X-Press header
        # names it), and until then the press is offered again.
        try:
            listener, pid = request.query["listener"], int(request.query["pid"])
        except (KeyError, ValueError) as error:
            raise web.HTTPBadRequest(text="listener and pid are required") from error
        # A restarted server knows no listener, and the first waiter back is the one.
        if self.review_listener is None:
            self.review_listener = listener
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.REVIEW_POLL_TIMEOUT
        self.review_waiters += 1
        await self.broadcast({"type": "review_waiters", "waiters": self.review_waiters})
        try:
            while True:
                if self.review_listener != listener:
                    own = (self.review_listener_session is not None
                           and plugin_monitor.session_of(pid) == self.review_listener_session)
                    return web.Response(
                        text="[moved] this session listens for Call agent with a newer waiter, so this one ends"
                        if own else "[moved] another session took over listening for Call agent, so "
                        "presses no longer come here; listen() takes them back",
                        headers={"X-Moved": "1"})
                self.review_listener_pid = pid
                if self.review_calls > self.review_consumed:
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return web.Response(status=204)
                waited = self.review_called
                try:
                    await asyncio.wait_for(waited.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return web.Response(status=204)
                if self.closing:
                    return web.Response(status=204)
            return web.Response(text=self._review_line(),
                                headers={"X-Press": str(self.review_calls)})
        finally:
            self.review_waiters -= 1
            await self.broadcast({"type": "review_waiters", "waiters": self.review_waiters})

    async def _handle_ack_review(self, request: web.Request) -> web.Response:
        try:
            upto = int(request.query["upto"])
        except (KeyError, ValueError) as error:
            raise web.HTTPBadRequest(text="upto must be an integer") from error
        # Idempotent and monotonic: a late or repeated ack never moves the watermark back,
        # and one beyond the count (a script talking to a restarted server whose counters
        # are behind it) clamps rather than marking presses that do not exist yet.
        self.review_consumed = max(self.review_consumed, min(upto, self.review_calls))
        self._save_review_state()
        return web.json_response({"calls": self.review_calls, "consumed": self.review_consumed})

    # ----- static / PDF -----

    @staticmethod
    def static_tag() -> str:
        """The newest change among the files the page loads, as one path segment.

        It rides in the path rather than in a query: a browser that had the stylesheet
        kept serving it from its cache across restarts, and a query bumped by hand was
        bumped for one file and forgotten for another.
        """
        newest = max((path.stat().st_mtime for path in STATIC_DIR.iterdir() if path.is_file()), default=0)
        return f"v{int(newest)}"

    async def _handle_root(self, request: web.Request) -> web.Response:
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(text=page.replace('"/static/', f'"/static/{self.static_tag()}/'),
                            content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-store"})

    async def _handle_static(self, request: web.Request) -> web.FileResponse:
        # The tag names a moment rather than a directory: the file beneath it is the one
        # in the static directory, whatever tag was asked for.
        target = (STATIC_DIR / re.sub(r"^v\d+/", "", request.match_info["name"])).resolve()
        if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(target, headers={"Cache-Control": "no-cache"})

    async def _handle_pdf(self, request: web.Request) -> web.StreamResponse:
        if self.last_result is None or self.last_result.output_file is None:
            return web.json_response(
                {"error": "no PDF available"}, status=404
            )
        return web.FileResponse(
            self.last_result.output_file,
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_pdf_download(self, request: web.Request) -> web.StreamResponse:
        """The PDF as a file to keep, named as Overleaf names it: after the project, with
        every character but a letter or a digit turned to _. A paper without an Overleaf
        project keeps the PDF's own name."""
        from urllib.parse import quote

        if self.last_result is None or self.last_result.output_file is None:
            return web.json_response({"error": "no PDF available"}, status=404)
        project = self.overleaf_live.project_name if self.overleaf_live is not None else ""
        name = ("".join(c if c.isalpha() or c.isdecimal() else "_" for c in project) + ".pdf"
                if project else self.last_result.output_file.name)
        plain = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in name)
        return web.FileResponse(self.last_result.output_file, headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"})

    # ----- API: paper / compile -----

    async def _handle_paper(self, request: web.Request) -> web.Response:
        if self.structure is None:
            self.structure = parse_structure(self.watch_dir, self.main_file)

        return web.json_response(
            {
                # Which config file this server runs from: peers sharing the port compare it.
                "config_path": (str(self.config.config_path.resolve())
                                if self.config.config_path is not None else None),
                "main_file": self.config.main,
                "watch_dir": str(self.watch_dir),
                "compiling": self.compiling,
                "last_compile": _result_to_dict(self.last_result),
                "pdf_digest": self.pdf_digest,
                "translation_language": self.config.translation_language,
                **structure_to_dict(self.structure, self.watch_dir),
                "comments": self._comment_summary(),
            }
        )

    def _resolve_source_path(self, raw_path: str | None) -> Path:
        """Resolve an existing editable source file inside the configured paper."""
        if not raw_path:
            raise web.HTTPBadRequest(text="path is required")
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise web.HTTPBadRequest(text="path must be relative to the paper")
        try:
            path = (self.watch_dir / relative).resolve(strict=True)
            path.relative_to(self.watch_dir.resolve())
        except FileNotFoundError as error:
            raise web.HTTPNotFound(text=f"source not found: {raw_path}") from error
        except (OSError, ValueError) as error:
            raise web.HTTPForbidden(text="source must stay inside the paper") from error
        if not path.is_file():
            raise web.HTTPNotFound(text=f"source not found: {raw_path}")
        if not is_watched_source(
            path,
            self.watch_dir,
            self.config.watch,
            self.config.ignore,
        ):
            raise web.HTTPForbidden(text="source is not included by the watch rules")
        return path

    @staticmethod
    def _read_source(path: Path) -> tuple[str, str]:
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise web.HTTPUnsupportedMediaType(
                text="source must be UTF-8 text"
            ) from error
        return text, hashlib.sha256(data).hexdigest()

    @staticmethod
    def _replace_source(path: Path, encoded: bytes) -> None:
        """Atomically replace one source file while preserving its mode."""
        mode = stat.S_IMODE(path.stat().st_mode)
        staging: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=path.parent,
                prefix=f".{path.name}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(encoded)
                staging = Path(handle.name)
            staging.chmod(mode)
            staging.replace(path)
        finally:
            if staging is not None:
                with contextlib.suppress(FileNotFoundError):
                    staging.unlink()

    def _source_files(self) -> list[str]:
        """The editable sources, relative to the paper, as the watch rules pick them."""
        files: list[str] = []
        for path in self.watch_dir.rglob("*"):
            try:
                resolved = path.resolve(strict=True)
                relative = resolved.relative_to(self.watch_dir.resolve()).as_posix()
            except (FileNotFoundError, OSError, ValueError):
                continue
            if resolved.is_file() and is_watched_source(
                resolved,
                self.watch_dir,
                self.config.watch,
                self.config.ignore,
            ):
                files.append(relative)
        return sorted(set(files))

    async def _handle_sources(self, request: web.Request) -> web.Response:
        return web.json_response({"main_file": self.config.main, "files": self._source_files()})

    GRAPHICS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")

    def _completions(self) -> dict:
        """What the source editor offers: labels, bib entries, defined commands and
        environments from the sources, and the files \\input and \\includegraphics take.
        Picture paths are relative to the main file's folder, as LaTeX reads them."""
        root = self.watch_dir.resolve()
        sources = self._source_files()
        found: dict = {"labels": [], "citations": [], "commands": [], "environments": []}
        for relative in sources:
            try:
                text = (root / relative).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if relative.endswith(".bib"):
                found["citations"] += bib_entries(text, relative)
            elif relative.endswith(".tex"):
                found["labels"] += labels(text, relative)
                found["commands"] += defined_commands(text)
                found["environments"] += defined_environments(text)
        found["commands"] = list({c["name"]: c for c in found["commands"]}.values())
        found["environments"] = list(dict.fromkeys(found["environments"]))
        found["tex_files"] = [path for path in sources if path.endswith(".tex")]
        found["bib_files"] = [path for path in sources if path.endswith(".bib")]
        base = self.main_file.resolve().parent
        output = self.main_file.resolve().with_suffix(".pdf")
        graphics = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if (path.suffix.lower() not in self.GRAPHICS or path == output or not path.is_file()
                    or any(part.startswith(".") for part in relative.parts)
                    or matches_patterns(relative, root, self.config.ignore)):
                continue
            try:
                graphics.append(path.relative_to(base).as_posix())
            except ValueError:
                continue
            if len(graphics) >= 2000:
                break
        found["graphics"] = graphics
        return found

    async def _handle_completions(self, request: web.Request) -> web.Response:
        found = await asyncio.to_thread(self._completions)
        return web.json_response(found, headers={"Cache-Control": "no-store"})

    async def _handle_source(self, request: web.Request) -> web.Response:
        path = self._resolve_source_path(request.query.get("path"))
        text, revision = self._read_source(path)
        relative = path.relative_to(self.watch_dir.resolve()).as_posix()
        if self.overleaf_sync is not None and self.overleaf_sync.owns(relative):
            self._remember_source(relative, revision, text)
        return web.json_response(
            {
                "path": path.relative_to(self.watch_dir.resolve()).as_posix(),
                "text": text,
                "revision": revision,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_save_source(self, request: web.Request) -> web.Response:
        path = self._resolve_source_path(request.query.get("path"))
        data, error = await self._read_json(request)
        if error is not None:
            return error
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("text"), str)
            or not isinstance(data.get("revision"), str)
        ):
            return web.json_response(
                {"error": "text and revision are required"}, status=400
            )

        current_text, current_revision = self._read_source(path)
        relative = path.relative_to(self.watch_dir.resolve()).as_posix()
        text, merged = data["text"], False
        if data["revision"] != current_revision:
            # A file kept in step with Overleaf changes under the page as others type:
            # what the page changed since it opened the file goes in among their edits.
            base = self._source_texts.get(relative, {}).get(data["revision"])
            if base is None or self.overleaf_sync is None or not self.overleaf_sync.owns(relative):
                return web.json_response(
                    {"error": "source changed after it was opened", "revision": current_revision},
                    status=409,
                )
            mine, _ = overleaf_ot.transform_x(overleaf_ot.diff(base, text), overleaf_ot.diff(base, current_text))
            text, merged = overleaf_ot.apply(current_text, mine), True

        encoded = text.encode("utf-8")
        self._replace_source(path, encoded)
        revision = hashlib.sha256(encoded).hexdigest()
        if self.overleaf_sync is not None and self.overleaf_sync.owns(relative):
            self._remember_source(relative, revision, text)
            self.overleaf_sync.poke()
        self.comments.refresh_source_anchors(self.watch_dir)
        answer = {"path": relative, "revision": revision}
        if merged:
            answer["text"] = text
        return web.json_response(answer)

    def _comment_summary(self) -> dict[str, int]:
        open_comments = self.comments.list(status="open")
        return {
            "open": len(open_comments),
            "resolved": len(self.comments.list(status="resolved")),
            "archived": len(self.comments.list(status="archived")),
            "stale": sum(1 for c in open_comments if c.stale),
        }

    async def _handle_compile(self, request: web.Request) -> web.Response:
        result = await self.do_compile()
        return web.json_response(_result_to_dict(result))

    # ----- API: comments -----

    async def _handle_list_comments(self, request: web.Request) -> web.Response:
        status = request.query.get("status")
        if status not in ("open", "resolved", "archived"):
            status = None  # type: ignore[assignment]
        comments = self.comments.list(status=status)  # type: ignore[arg-type]
        # The leaf tab lists Overleaf's threads in the order the paper reads them, as
        # Overleaf's review panel does: they come in many at once, in the order Overleaf
        # hands them out, and so do the threads drafted for it. A thread on no line of the
        # paper goes last. The comments written here keep the order they were written in.
        brought = {known["comment"] for known in self._overleaf_ledger().values()}
        chains = {_relative(path, self.watch_dir): chain
                  for path, chain in reading_places(self.main_file, self.watch_dir).items()}

        def place(comment: Comment) -> tuple[Any, ...]:
            if comment.id not in brought and comment.overleaf_draft is None:
                return ()
            source = comment.resolved_source
            if source is None:
                return (2,)
            at = (source.line_start, source.column_start or 0)
            chain = chains.get(source.file)
            return (0, *chain, at) if chain is not None else (1, source.file, at)

        comments.sort(key=place)
        return web.json_response(
            {"comments": [_comment_to_dict(c) for c in comments]}
        )

    async def _handle_get_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        c = self.comments.get(cid)
        if c is None:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        return web.json_response(_comment_to_dict(c))

    async def _handle_create_comment(self, request: web.Request) -> web.Response:
        data, err = await self._read_json(request)
        if err is not None:
            return err

        if "anchor" not in data or "text" not in data:
            return web.json_response(
                {"error": "anchor and text are required"}, status=400
            )
        anchor_d = data["anchor"]
        text = str(data["text"]).strip()
        if not anchor_d or not text:
            return web.json_response(
                {"error": "anchor and text are required"}, status=400
            )
        # A compile writes the PDF in place, so until it ends the file is neither the PDF
        # the selection was made on nor the next one, which the page has yet to load.
        if anchor_d.get("kind") in ("text_selection", "area") and self.compiling:
            return web.json_response(
                {"error": "the PDF is being rebuilt. Select again once it reloads"}, status=409
            )
        try:
            # A box carries what its pixels show now, read from the PDF it was drawn on.
            if anchor_d.get("kind") == "area":
                if self.last_result is None or self.last_result.output_file is None:
                    return web.json_response({"error": "no PDF available"}, status=409)
                region = area_digest(self.last_result.output_file, int(anchor_d["page"]),
                                     tuple(float(value) for value in anchor_d["bbox"]))
                anchor_d = {**anchor_d, "region": region}
            anchor = anchor_from_dict(anchor_d)
            # The Sections tab names the heading it was pressed on, which tells apart headings with one title.
            near = ((str(data["heading"]["file"]), int(data["heading"]["line"]))
                    if isinstance(anchor, SectionAnchor) and "heading" in data else None)
        except (ValueError, KeyError, TypeError) as exc:
            return web.json_response(
                {"error": f"invalid comment input: {exc}"}, status=400
            )

        if isinstance(anchor, SourceRangeAnchor) and "source_revision" in data:
            _, revision = self._read_source(self._resolve_source_path(anchor.file))
            if data["source_revision"] != revision:
                return web.json_response({"error": "Source changed after this text was selected; reload and select again"}, status=409)
        if isinstance(anchor, TextSelectionAnchor):
            if not anchor.quote.strip():
                return web.json_response(
                    {"error": "text selection requires a quote"}, status=400
                )
            if self.pdf_digest is None or anchor.pdf_digest != self.pdf_digest:
                return web.json_response(
                    {"error": "the PDF changed after this text was selected"}, status=409
                )
            if self.last_result is None or self.last_result.output_file is None:
                return web.json_response({"error": "no PDF available"}, status=409)
            canonical = canonicalize_pdf_selection(
                self.last_result.output_file, anchor.quote, hint=anchor.selection
            )
            if canonical is None:
                return web.json_response(
                    {"error": "selected text could not be verified in the current PDF"},
                    status=422,
                )
            anchor.quote, anchor.selection = canonical
        if isinstance(anchor, AreaAnchor):
            if self.pdf_digest is None or anchor.pdf_digest != self.pdf_digest:
                return web.json_response(
                    {"error": "the PDF changed after this area was selected"}, status=409
                )

        try:
            resolved, source_selector = self._resolve_anchor(anchor, near)
        except AmbiguousSection as exc:
            return web.json_response({"error": f"{exc}. Comment from the heading in the Sections tab."}, status=409)
        if isinstance(anchor, SourceRangeAnchor) and source_selector is None:
            return web.json_response({"error": "Source selection is out of bounds"}, status=400)
        # A replacement is written into the file the comment points at: its own for a
        # source range, the one PDF text or a section resolves to otherwise. Text copied
        # from the PDF has to be found in that file as written, since the rendered words
        # can differ from their source.
        try:
            if isinstance(anchor, SourceRangeAnchor):
                suggestion = _suggestion_from_dict(data.get("suggestion"), anchor.file)
            else:
                suggestion = _suggestion_from_dict(
                    data.get("suggestion"), resolved.file if resolved is not None else None)
                if suggestion is not None:
                    source_text = self._resolve_source_path(suggestion.file).read_text(encoding="utf-8")
                    locate_fragments(source_text, suggestion.changes)
        except (ValueError, KeyError, TypeError, OSError, UnicodeError) as exc:
            return web.json_response(
                {"error": f"invalid comment input: {exc}"}, status=400
            )
        comment = self.comments.add(
            anchor=anchor,
            text=text,
            author="human",
            resolved_source=resolved,
            source_selector=source_selector,
            suggestion=suggestion,
            base_text=self._base_text(anchor, resolved),
        )
        await self.broadcast({"type": "comment_added", "comment": _comment_to_dict(comment)})
        return web.json_response(_comment_to_dict(comment), status=201)

    def _base_text(self, anchor: Any, resolved: ResolvedSource | None) -> str | None:
        """The text a source-range comment's words or a section comment's heading were just
        found in, which later edits carry it from, as Overleaf carries a comment from the
        moment it is made."""
        if not isinstance(anchor, (SourceRangeAnchor, SectionAnchor)) or resolved is None:
            return None
        return self._resolve_source_path(resolved.file).read_text(encoding="utf-8")

    def _resolve_anchor(
        self, anchor: Any, near: tuple[str, int] | None = None
    ) -> tuple[ResolvedSource | None, SourceSelector | None]:
        """Resolve one new anchor and capture its exact source selector. *near* is the
        ``(file, line)`` of the heading the reviewer chose, for a section anchor."""
        from .comments import ResolveContext

        if isinstance(anchor, PaperAnchor):
            return None, None

        # A box resolves to the source of what sits at its middle, as a figure's
        # \includegraphics line does.
        if isinstance(anchor, AreaAnchor):
            if self.last_result is None or self.last_result.output_file is None:
                return None, None
            return resolve_text_selection_to_source(
                self.last_result.output_file,
                PageSelection(anchor.page, anchor.bbox, [anchor.bbox]),
                self.watch_dir,
            ), None

        if isinstance(anchor, TextSelectionAnchor):
            if self.last_result is None or self.last_result.output_file is None:
                return None, None
            resolved = resolve_text_selection_to_source(
                self.last_result.output_file,
                anchor.selection,
                self.watch_dir,
            )
            if resolved is None:
                return None, None
            selector = capture_source_selector(
                self.watch_dir / resolved.file,
                resolved.line_start,
                resolved.line_end,
            )
            return resolved, selector

        # The headings as the files hold them now, the text the comment's base records.
        if isinstance(anchor, SectionAnchor):
            return resolve_section_to_source(
                parse_structure(self.watch_dir, self.main_file), self.watch_dir,
                anchor.title, anchor.label, near), None

        ctx = ResolveContext(
            watch_dir=self.watch_dir,
            structure=self.structure,
            synctex=self.synctex_data,
        )
        resolved = anchor.resolve_source(ctx)
        if resolved is None:
            return None, None

        selector = capture_source_selector(
            self.watch_dir / resolved.file, resolved.line_start, resolved.line_end,
            column_start=resolved.column_start, column_end=resolved.column_end,
        )
        return resolved, selector

    async def _read_json(self, request: web.Request) -> tuple[dict[str, Any] | None, web.Response | None]:
        """Decode the request JSON body or return a 400 error response."""
        try:
            return await request.json(), None
        except json.JSONDecodeError:
            return None, web.json_response({"error": "invalid JSON"}, status=400)

    async def _mutate_comment(
        self,
        cid: str,
        action: Callable[[], Comment],
        acknowledge_only: bool = False,
    ) -> web.Response:
        """Run *action* (a no-arg call into the store), broadcast, return the updated comment."""
        try:
            updated = action()
        except KeyError:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(updated)})
        if acknowledge_only:
            return web.json_response({"id": updated.id, "status": updated.status})
        return web.json_response(_comment_to_dict(updated))

    async def _handle_edit_comment_entry(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        data, err = await self._read_json(request)
        if err is not None:
            return err
        try:
            index = int(data["index"])
            text = str(data["text"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "index and text are required"}, status=400)
        try:
            return await self._mutate_comment(
                cid,
                lambda: self.comments.edit_entry(cid, index, text, author="human"),
            )
        except (IndexError, ValueError) as error:
            return web.json_response({"error": str(error)}, status=400)

    def _write_comment_suggestion(self, comment: Comment) -> list[str]:
        """Write a thread's proposal into its file and name the lines it changed.

        Each piece is looked for again here, so a source that moved under the proposal
        refuses it instead of writing something the reviewer did not see.
        """
        suggestion = comment.suggestion
        if suggestion is None:
            raise ValueError("the comment carries no suggestion")

        path = self._resolve_source_path(suggestion.file)
        text, _ = self._read_source(path)
        spans = locate_fragments(text, suggestion.changes)
        updated_text = text
        # Back to front, so an earlier swap cannot move a later one's offsets.
        for start, end, replacement in reversed(spans):
            updated_text = updated_text[:start] + replacement + updated_text[end:]
        if updated_text == text:
            raise ValueError("the suggestion leaves the source as it is")

        # The comment is anchored to text this edit may have just rewritten. Carrying its
        # range across the edit keeps it attached; leaving it would mark the reviewer's
        # own comment stale the moment they accepted what it asked for.
        source = comment.resolved_source
        if source is not None and source.file == suggestion.file:
            try:
                start, end = _resolved_source_span(text, source)
            except (UnicodeError, ValueError):
                start = end = None
            if start is not None:
                # A piece that swallowed the anchored text leaves no offset to shift to,
                # so the anchor takes in what replaced it.
                swallowed = [s for s in spans if s[0] < end and s[1] > start]
                low = min([start] + [s[0] for s in swallowed])
                high = max([end] + [s[1] for s in swallowed])
                moved_start, moved_end = _carry_across(low, spans), _carry_across(high, spans)
                line_start, column_start = _source_coordinate(updated_text, moved_start)
                line_end, column_end = _source_coordinate(updated_text, moved_end)
                comment.resolved_source = ResolvedSource(
                    file=source.file,
                    line_start=line_start, line_end=line_end,
                    column_start=column_start, column_end=column_end,
                )
                comment.source_selector = SourceSelector(
                    exact=updated_text[moved_start:moved_end],
                    prefix=updated_text[max(0, moved_start - 80):moved_start],
                    suffix=updated_text[moved_end:moved_end + 80],
                )
                # The range is in the text just written: the next refresh records that text.
                comment.base = None
                comment.stale = False

        self._replace_source(path, updated_text.encode("utf-8"))
        return [
            f"{suggestion.file}:{text.count(chr(10), 0, start) + 1}"
            f"-{text.count(chr(10), 0, end) + 1}"
            for start, end, _ in spans
        ]

    async def _handle_apply_suggestion(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        data, err = await self._read_json(request)
        if err is not None:
            return err
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("updated"), str)
            or not data["updated"]
        ):
            return web.json_response({"error": "updated stamp is required"}, status=400)
        try:
            updated = self.comments.apply_suggestion(
                cid,
                data["updated"],
                self._write_comment_suggestion,
            )
        except KeyError:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        except ValueError as error:
            return web.json_response({"error": str(error)}, status=409)
        except OSError:
            return web.json_response(
                {"error": "source changed while the suggestion was being applied"},
                status=409,
            )
        # The file just changed under every anchor in it. Reattaching here rather than
        # waiting for the watcher keeps the next read from quoting a moved range.
        self.comments.refresh_source_anchors(self.watch_dir)
        updated = self.comments.get(cid) or updated
        await self.broadcast({"type": "comment_updated", "comment": _comment_to_dict(updated)})
        return web.json_response(_comment_to_dict(updated))

    async def _handle_reply_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        data, err = await self._read_json(request)
        if err is not None:
            return err
        text = (data.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "text is required"}, status=400)
        if data.get("overleaf"):
            # The lines the draft describes, where the files have them now, are what is sent
            # and recorded, not where the last change the watcher reported left them.
            self.comments.refresh_source_anchors(self.watch_dir)
            refused, commit = await self._send_to_overleaf(cid, text, bool(data.get("anyway")))
            if refused is not None:
                return refused
            return await self._mutate_comment(cid, lambda: self.comments.sent_to_overleaf(cid, text, commit))
        return await self._mutate_comment(
            cid,
            lambda: self.comments.reply(
                cid,
                text=text,
                author="human",
                edits=data.get("edits") or [],
            ),
        )

    def _follows_overleaf(self, cid: str) -> web.Response | None:
        """A thread from Overleaf is as Overleaf has it, which has no archive and deletes a
        thread only there, so it is not archived or deleted here."""
        if any(known["comment"] == cid for known in self._overleaf_ledger().values()):
            return web.json_response({"error": "this thread follows Overleaf, which has no archive: "
                                               "resolve it, or delete it on Overleaf"}, status=409)
        return None

    async def _set_on_overleaf(self, threads: dict[str, str], resolved: bool) -> web.Response | None:
        """Resolve or reopen threads from Overleaf there first, as the reviewer, and each one
        Overleaf takes here too. *threads* maps a thread on Overleaf to its comment here. What
        Overleaf refuses, and every thread after it, stays as it was on both, and the refusal
        is returned. None once Overleaf has taken them all."""
        async with self._overleaf_import:
            cookie = overleaf.read_cookie()
            if cookie is None:
                return web.json_response({"error": "paste your Overleaf session cookie first"}, status=401)
            if not self.config.overleaf:
                return web.json_response({"error": "name the Overleaf project in .tex-mcp-web.yaml first"}, status=409)
            try:
                async for thread in overleaf.set_resolved(self.config.overleaf, cookie, list(threads), resolved):
                    for follower in (self.overleaf_sync, self.overleaf_hand):
                        if follower is not None and resolved:
                            follower.resolved_threads.add(thread)
                        elif follower is not None:
                            follower.resolved_threads.discard(thread)
                    if resolved:
                        self.comments.resolve(threads[thread], summary="", author="human")
                    else:
                        self.comments.reopen(threads[thread], author="human")
            except overleaf.SignedOut as error:
                return web.json_response({"error": f"{error} (the cookie has expired or is wrong)"}, status=401)
            except overleaf.OverleafError as error:
                return web.json_response({"error": str(error)}, status=502)
        return None

    async def _resolve_on_overleaf(self, cid: str, resolved: bool) -> web.Response | None:
        """Resolve or reopen a thread from Overleaf there first, then here. None for a thread
        written here."""
        thread = next((key for key, known in self._overleaf_ledger().items() if known["comment"] == cid), None)
        if thread is None:
            return None
        if (refused := await self._set_on_overleaf({thread: cid}, resolved)) is not None:
            return refused
        return await self._mutate_comment(cid, lambda: self.comments.get(cid), acknowledge_only=True)

    async def _handle_set_status(self, request: web.Request) -> web.Response:
        """Give the picked comments one status in one call, and the page one notice. The
        threads from Overleaf go there first, together, as each would alone."""
        data, err = await self._read_json(request)
        if err is not None:
            return err
        ids, status = data.get("ids"), data.get("status")
        if not isinstance(ids, list) or not all(isinstance(cid, str) for cid in ids):
            return web.json_response({"error": "ids must be a list of comment ids"}, status=400)
        if status not in ("open", "resolved", "archived"):
            return web.json_response({"error": "status must be open, resolved or archived"}, status=400)
        present = {comment.id for comment in self.comments.list()}
        if missing := [cid for cid in ids if cid not in present]:
            return web.json_response({"error": f"no comment {', '.join(missing)}"}, status=404)
        followed = {known["comment"]: thread for thread, known in self._overleaf_ledger().items()}
        threads = {followed[cid]: cid for cid in ids if cid in followed}
        if threads and status == "archived":
            return self._follows_overleaf(next(iter(threads.values())))
        refused = await self._set_on_overleaf(threads, status == "resolved") if threads else None
        if refused is None:
            for cid in ids:
                if cid in followed:
                    continue
                if status == "resolved":
                    self.comments.resolve(cid, summary="", author="human")
                elif status == "open":
                    self.comments.reopen(cid, author="human")
                else:
                    self.comments.archive(cid, author="human")
        await self.broadcast({"type": "comments_changed"})
        return refused if refused is not None else web.json_response({"ids": ids, "status": status})

    async def _handle_resolve_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        if (answered := await self._resolve_on_overleaf(cid, True)) is not None:
            return answered
        data, err = await self._read_json(request)
        if err is not None:
            return err
        summary = (data.get("summary") or "").strip()
        return await self._mutate_comment(
            cid,
            lambda: self.comments.resolve(
                cid,
                summary=summary,
                edits=data.get("edits") or [],
                author="human",
            ),
            acknowledge_only=True,
        )

    async def _handle_reopen_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        if (answered := await self._resolve_on_overleaf(cid, False)) is not None:
            return answered
        data, err = await self._read_json(request)
        if err is not None:
            return err
        return await self._mutate_comment(
            cid,
            lambda: self.comments.reopen(cid, author="human"),
            acknowledge_only=True,
        )

    async def _handle_archive_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        if (refused := self._follows_overleaf(cid)) is not None:
            return refused
        return await self._mutate_comment(
            cid,
            lambda: self.comments.archive(cid, author="human"),
            acknowledge_only=True,
        )

    async def _handle_delete_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        if (refused := self._follows_overleaf(cid)) is not None:
            return refused
        if not self.comments.delete(cid):
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_deleted", "id": cid})
        return web.json_response({"deleted": cid})

    # ----- SyncTeX -----

    async def _handle_translations(self, request: web.Request) -> web.Response:
        """Every prose paragraph of one source file, in the configured language.

        ``?file=`` names the file; with only ``?page=N`` it is the file that
        SyncTeX places most on that page. ``on_page`` marks the paragraphs
        typeset on ``page``. Paragraphs already translated come from the store;
        the rest start one background job and come back with ``text: null``.
        """
        language = self.config.translation_language
        if language is None:
            return web.json_response(
                {"error": "no translation language is configured; set translation: "
                          "{language: ...} in .tex-mcp-web.yaml"}, status=404)
        raw_page = request.query.get("page")
        try:
            page = int(raw_page) if raw_page is not None else None
        except ValueError:
            return web.json_response({"error": "page must be a number"}, status=400)
        raw_file = request.query.get("file")
        if raw_file is None and page is None:
            return web.json_response({"error": "file or page is required"}, status=400)
        lines: dict[str, set[int]] = {}
        if page is not None and self.synctex_data is not None:
            for _, position in self.synctex_data.pdf_to_source.get(page, []):
                lines.setdefault(position.file, set()).add(position.line)
        if raw_file is None:
            if not lines:
                return web.json_response({"error": "no SyncTeX data for this page"}, status=404)
            raw_file = max(sorted(lines), key=lambda f: len(lines[f]))
        try:
            path = self._resolve_source_path(raw_file)
        except web.HTTPException as error:
            return web.json_response({"error": error.text}, status=error.status)
        file = path.relative_to(self.watch_dir.resolve()).as_posix()
        translator = self._translator(language)
        text, _ = self._read_source(path)
        paragraphs = split_paragraphs(text, file)
        on_page = lines.get(file, set())
        missing = translator.missing(paragraphs)
        failed = bool(missing) and translator.error is not None and not translator.busy()
        body = {
            "language": language,
            "file": file,
            "status": "error" if failed else "pending" if missing else "ready",
            "error": translator.error,
            "paragraphs": [{"line_start": p.line_start, "line_end": p.line_end,
                            "on_page": any(p.line_start <= line <= p.line_end for line in on_page),
                            "text": translator.store.get(p.key)} for p in paragraphs],
        }
        if missing:
            translator.start(paragraphs)   # an error clears when this retry succeeds
        return web.json_response(body)

    def _translator(self, language: str) -> Translator:
        """The translator for the configured language and instructions. Its store is kept
        beside the comments; the reference term list it keeps as written is the one
        guide() reads, beside the configuration file."""
        instructions = self.config.translation_instructions
        current = self.translator
        if current is None or (current.language, current.instructions) != (language, instructions):
            state = self.watch_dir / ".tex-mcp-web"
            config_dir = (self.config.config_path.resolve().parent
                          if self.config.config_path is not None else self.watch_dir)
            current = self.translator = Translator(
                TranslationStore(state / store_name(language)),
                load_glossary(config_dir / ".tex-mcp-web" / "terms.md"), state,
                language=language, instructions=instructions)
        return current

    async def _handle_synctex_backward(self, request: web.Request) -> web.Response:
        """Selected PDF lines -> the source lines they come from: {page, rects, pdf_digest}
        -> {file, line_start, line_end}, as a comment on the same selection resolves."""
        try:
            data = await request.json()
            selection = PageSelection.from_dict({**data, "bbox": data["rects"][0]})
            digest = str(data["pdf_digest"])
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return web.json_response({"error": f"invalid selection: {exc}"}, status=400)
        refused = self._without_synctex()
        if refused is not None:
            return refused
        if digest != self.pdf_digest:
            return web.json_response({"error": "the PDF changed after this text was selected"}, status=409)
        resolved = resolve_text_selection_to_source(
            self.last_result.output_file, selection, self.watch_dir)
        if resolved is None:
            return web.json_response(
                {"error": "SyncTeX places these lines in no single source file of the paper, "
                          "as it does for a bibliography built from the .bib"}, status=404)
        return web.json_response(
            {"file": resolved.file, "line_start": resolved.line_start, "line_end": resolved.line_end})

    def _without_synctex(self) -> web.Response | None:
        """The answer when the PDF has no SyncTeX record of its own, as one built outside
        the review server without -synctex=1 has: a lookup then finds nothing anywhere."""
        if self.last_result is None or self.last_result.output_file is None:
            return web.json_response({"error": "no PDF available"}, status=409)
        pdf = self.last_result.output_file
        if not has_synctex_record(pdf):
            return web.json_response({"error": (
                f"{pdf.name} was built without SyncTeX, so it records no source places. "
                "Press Compile to build it with SyncTeX, then try again")}, status=409)
        return None

    async def _handle_source_to_pdf_words(self, request: web.Request) -> web.Response:
        """Selected source text -> where the PDF prints it: {file, line_start, line_end,
        text} -> {page, bbox, rects, whole}. SyncTeX names the lines' boxes, and the printed
        words are found in them. Words printed another way, such as a citation, find the
        longest run of two or more printed words, or else the lines' boxes are the answer,
        and whole is false."""
        try:
            data = await request.json()
            source = self._resolve_source_path(str(data["file"]))
            line_start, line_end = int(data["line_start"]), int(data["line_end"])
            text = str(data["text"])
        except (ValueError, KeyError, TypeError) as exc:
            return web.json_response({"error": f"invalid selection: {exc}"}, status=400)
        refused = self._without_synctex()
        if refused is not None:
            return refused
        pdf = self.last_result.output_file
        boxes = await asyncio.get_running_loop().run_in_executor(
            None, source_lines_to_boxes, pdf, source, line_start, line_end)
        if not boxes:
            return web.json_response(
                {"error": "SyncTeX places none of these lines in the PDF"}, status=404)
        page = boxes[0][0]
        rects = [box for number, box in boxes if number == page]
        hint = PageSelection(page, (min(r[0] for r in rects) - 2, min(r[1] for r in rects) - 2,
                                    max(r[2] for r in rects) + 2, max(r[3] for r in rects) + 2), rects)
        words = printed_words(text)

        # The PDF's text is read once for every run tried, and off the event loop: read
        # again for each of the hundreds of runs a line of commands can try, it held every
        # other request for seconds, and they all came back at once.
        def find() -> tuple[PageSelection | None, bool]:
            index = PdfTextIndex(pdf)
            try:
                found = index.locate(words, hint=hint) if words else None
                whole = found is not None
                split = words.split()
                for size in range(len(split) - 1, 1, -1):
                    if found is not None:
                        break
                    for first in range(len(split) - size + 1):
                        found = index.locate(" ".join(split[first:first + size]), hint=hint)
                        if found is not None:
                            break
                return found, whole
            finally:
                index.close()

        found, whole = await asyncio.get_running_loop().run_in_executor(None, find)
        found = found or hint
        return web.json_response({"page": found.page, "bbox": list(found.bbox),
                                  "rects": [list(rect) for rect in found.rects], "whole": whole})

    async def _handle_synctex_forward(self, request: web.Request) -> web.Response:
        """source -> PDF: ?file=...&line=N -> {page, x, y, width, height}"""
        if self.synctex_data is None:
            return web.json_response({"error": "no SyncTeX data"}, status=404)
        file = request.query.get("file")
        try:
            line = int(request.query.get("line", "0"))
        except ValueError:
            return web.json_response({"error": "invalid line"}, status=400)
        if not file:
            return web.json_response({"error": "file is required"}, status=400)
        pos = source_to_page(self.synctex_data, file, line)
        if pos is None:
            return web.json_response({"error": "no match"}, status=404)
        return web.json_response(
            {
                "page": pos.page,
                "x": pos.x,
                "y": pos.y,
                "width": pos.width,
                "height": pos.height,
            }
        )

    async def _handle_goto(self, request: web.Request) -> web.Response:
        """Tell the viewer to scroll/highlight a target.

        Body keys (use exactly one of section/label/line/page/quote):
            section  section title (case-insensitive title match)
            label    \\label{...} value
            line + file  source line number in *file*
            page     PDF page number
            quote    exact rendered PDF text

        Returns 200 with ``{page}`` when SyncTeX resolved a page, or 200
        with ``{file, line, page: null}`` when a section/label matched a
        source location but SyncTeX is unavailable (caller can still
        report or open in editor).  404 only when nothing matches.
        """
        data, err = await self._read_json(request)
        if err is not None:
            return err

        section = data.get("section")
        label = data.get("label")
        line = data.get("line")
        page = data.get("page")
        file = data.get("file")
        quote = data.get("quote")

        # Direct page request.
        if page is not None:
            target_page = int(page)
            await self.broadcast({"type": "goto", "page": target_page})
            return web.json_response({"page": target_page})

        # Resolve section/label to a source range.
        resolved_file: str | None = None
        resolved_line: int | None = None

        if section or label:
            if self.structure is None:
                self.structure = parse_structure(self.watch_dir, self.main_file)
            try:
                match = find_section(
                    self.structure,
                    title=section if section else None,
                    label=label if label else None,
                )
            except AmbiguousSection as exc:
                return web.json_response({"error": f"{exc}. Go to its label or its line."}, status=409)
            if match:
                resolved_file, resolved_line, _ = match
        elif line and file:
            resolved_file, resolved_line = str(file), int(line)

        # An unmatched free-form target is an exact rendered quote. This lets
        # goto("some PDF text") work without adding a second MCP parameter.
        quote_text = str(quote or (section if resolved_file is None else "")).strip()
        if quote_text:
            if self.last_result is None or self.last_result.output_file is None:
                return web.json_response(
                    {"error": "quote navigation requires a compiled PDF"}, status=404
                )
            selection = locate_pdf_quote(self.last_result.output_file, quote_text)
            if selection is None:
                return web.json_response(
                    {"error": "quote is missing or ambiguous in the current PDF"},
                    status=404,
                )
            payload = {
                "type": "goto",
                "page": selection.page,
                "quote": quote_text,
                "bbox": list(selection.bbox),
                "rects": [list(rect) for rect in selection.rects],
            }
            await self.broadcast(payload)
            return web.json_response({key: value for key, value in payload.items() if key != "type"})

        if resolved_file is None or resolved_line is None:
            return web.json_response({"error": "could not resolve target"}, status=404)

        # Try to map to a PDF page via SyncTeX.
        target_page = None
        target_bbox = None
        if self.synctex_data is not None:
            pos = source_to_page(self.synctex_data, resolved_file, resolved_line)
            if pos:
                target_page = pos.page
                target_bbox = [
                    pos.x,
                    pos.y,
                    pos.x + max(pos.width, 6.0),
                    pos.y + max(pos.height, 12.0),
                ]

        # Broadcast whatever we know — viewer scrolls if there's a page.
        await self.broadcast(
            {
                "type": "goto",
                "page": target_page,
                "file": resolved_file,
                "line": resolved_line,
                "bbox": target_bbox,
            }
        )
        return web.json_response(
            {
                "page": target_page,
                "file": resolved_file,
                "line": resolved_line,
                "bbox": target_bbox,
            }
        )

    # ----- image -----

    async def _handle_image(self, request: web.Request) -> web.Response:
        """Render a PDF page or region as PNG.

        Query params (use exactly one of page / source / comment):
            page=N                          full page
            page=N&bbox=x1,y1,x2,y2         region in PDF points
            source=FILE:LSTART-LEND         SyncTeX-resolved region
            comment=cid                     anchor of an existing comment
            dpi=N                           render DPI (default 150, clamped to [36, 600])
            margin=N                        region margin in PDF points (default 12)

        Rendering runs on a worker thread so the event loop stays free
        for WebSocket heartbeats and concurrent /compile requests.
        """
        from . import imaging

        if self.last_result is None or self.last_result.output_file is None:
            return web.json_response(
                {"error": "no PDF; compile first"}, status=404
            )

        try:
            dpi = _clamp_dpi(request.query.get("dpi", "150"))
            margin = float(request.query.get("margin", "12"))
        except ValueError:
            return web.json_response({"error": "invalid dpi or margin"}, status=400)

        pdf_path = self.last_result.output_file
        try:
            page, bbox = self._resolve_image_target(request)
            if bbox is None:
                png = await asyncio.to_thread(imaging.render_page, pdf_path, page, dpi)
            else:
                png = await asyncio.to_thread(
                    imaging.render_region, pdf_path, page, bbox, dpi, margin
                )
        except (ValueError, imaging.ImagingError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

        return web.Response(
            body=png,
            content_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_reference_preview(self, request: web.Request) -> web.Response:
        """Return the bibliography entry targeted by a clicked PDF link."""
        from . import imaging

        if self.last_result is None or self.last_result.output_file is None:
            return web.json_response({"error": "no PDF; compile first"}, status=404)

        try:
            source_page = int(request.query["page"])
            source_bbox = _parse_bbox(request.query["bbox"])
        except (KeyError, ValueError):
            return web.json_response(
                {"error": "page and bbox are required"}, status=400
            )

        try:
            target_page, target_bbox = await asyncio.to_thread(
                imaging.resolve_reference_region,
                self.last_result.output_file,
                source_page,
                source_bbox,
            )
            text = await asyncio.to_thread(
                imaging.extract_region_text,
                self.last_result.output_file,
                target_page,
                target_bbox,
            )
        except imaging.ImagingError as exc:
            return web.json_response({"error": str(exc)}, status=404)

        return web.json_response(
            {"text": text},
            headers={"Cache-Control": "no-store"},
        )

    def _resolve_image_target(
        self, request: web.Request
    ) -> tuple[int, tuple[float, float, float, float] | None]:
        """Parse /image query params into (page, optional bbox).

        Raises ValueError with a user-facing message on invalid input.
        """
        from . import imaging

        page_str = request.query.get("page")
        bbox_str = request.query.get("bbox")
        source_str = request.query.get("source")
        comment_id = request.query.get("comment")

        try:
            page = int(page_str) if page_str else None
        except ValueError:
            raise ValueError("page must be an integer")
        bbox = _parse_bbox(bbox_str) if bbox_str else None
        source = _parse_source_range(source_str) if source_str else None

        return imaging.resolve_image_target(
            synctex=self.synctex_data,
            comment_lookup=self.comments.get,
            page=page,
            bbox=bbox,
            source=source,
            comment_id=comment_id,
            watch_dir=self.watch_dir,
        )

    # ----- lifecycle -----

    async def _watch_comment_store(self) -> None:
        """Broadcast when another process (the MCP server) edits comments.json.

        The store is shared cross-process via the file; WS events only
        fire for mutations that came through this daemon's HTTP routes.
        Poll the file's mtime so agent-side resolves show up in the
        browser without a manual refresh.
        """
        last: int | None = None
        while True:
            try:
                mtime = self.comments.path.stat().st_mtime_ns
            except OSError:
                mtime = None
            if last is not None and mtime != last:
                await self.broadcast({"type": "comments_changed"})
            last = mtime
            await asyncio.sleep(1.0)

    async def setup(self, port: int) -> None:
        """Keep the records out of git, bind the port, start watching, and kick off the
        first compile.

        The port binds before compiling so a caller that waits for readiness
        is not blocked by a LaTeX run.
        """
        await overleaf_git.keep_records_out(self.watch_dir)
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", port).start()
        logger.info("tex-mcp-web serving on http://127.0.0.1:%d", port)
        loop = asyncio.get_running_loop()
        self.watcher = Watcher(
            watch_dir=self.watch_dir,
            watch_patterns=self.config.watch,
            ignore_patterns=self.config.ignore,
            on_change=self.on_file_change,
            roots=self.source_roots(),
            config_path=self.config.config_path,
        )
        self.watcher.start(loop)
        self._store_watch = asyncio.create_task(self._watch_comment_store())
        self._initial_compile = asyncio.create_task(self.do_compile())

    def source_roots(self) -> list[Path]:
        """Directories the paper's sources live in: the main file's, those of the
        .tex files reachable from it through \\input and \\include, and those of every
        project-local source the last latexmk run recorded (figures and .bib among
        them). Nothing else under the project is watched."""
        files = _files_reachable_from(self.main_file, self.watch_dir)
        files += source_dependencies(self.main_file, self.watch_dir)
        return [self.main_file.parent, *(path.parent for path in files)]

    async def cleanup(self) -> None:
        # A parked waiter holds its connection for the whole poll; released here, it
        # asks again at once and finds the port closed instead of holding it half-alive.
        self.closing = True
        self.review_called.set()
        await self._stop_overleaf_live()
        # do_compile shields the build, so the build task is cancelled directly.
        # Awaiting the cancellation lets a running compiler subprocess close its
        # transport while the loop is still alive.
        for task in (self._store_watch, self._initial_compile, self._compile_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._store_watch = None
        self._initial_compile = None
        self._compile_task = None
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def start(self, port: int) -> None:
        await self.setup(port)
        try:
            await asyncio.Event().wait()
        finally:
            await self.cleanup()


def run(config: Config, port: int) -> None:
    """Synchronous entry point: build server, run until KeyboardInterrupt."""
    server = TexMcpWebServer(config)
    try:
        asyncio.run(server.start(port))
    except KeyboardInterrupt:
        logger.info("Shutting down")
