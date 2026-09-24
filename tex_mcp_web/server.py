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
import json
import logging
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from .comments import (
    AreaAnchor,
    Comment,
    CommentStore,
    PageSelection,
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
    pdf_digest,
    locate_fragments,
    source_offset,
)
from .compiler import CompileResult, compile_tex, source_dependencies
from .config import Config, get_main_file, get_watch_dir
from .structure import (
    DocumentStructure,
    _files_reachable_from,
    find_section,
    parse_structure,
)
from .synctex import (
    SyncTeXData,
    find_synctex_file,
    parse_synctex,
    selection_to_source_range,
    source_to_page,
)
from .watcher import Watcher, is_watched_source

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _comment_to_dict(c: Comment) -> dict[str, Any]:
    return c.to_dict()


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
        raise TypeError("a suggestion needs a comment anchored to source")
    return SuggestedEdit(file=file, changes=[(d["old"], d["new"])])


def derive_suggestion(
    watch_dir: Path, comment: Comment, edits: list[tuple[str, str]]
) -> SuggestedEdit:
    """Build a suggestion from fragments the agent quoted out of the anchored source.

    The agent sends only what changes. Each piece is checked against the file now, so
    what the reviewer is offered is measured against the source rather than against
    what the agent remembered of it.
    """
    source = comment.resolved_source
    if source is None:
        raise ValueError("the comment has no resolved source range")
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
    structure: DocumentStructure, watch_dir: Path, title: str | None, label: str | None
) -> ResolvedSource | None:
    """Look up a section in *structure* and return a fully-resolved source range.

    Returns None when no matching section exists.  When the section runs to
    end-of-file (``line_end < 0`` from :func:`find_section`), reads the file
    to compute the true EOF line.
    """
    match = find_section(structure, title=title, label=label)
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
        match = find_section(structure, title=s.title, label=s.label)
        line_end = match[2] if match else -1
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


class TexMcpWebServer:
    """Single-paper watch + serve + comment store."""

    # A parked waiter is answered within this long even when nothing happened, so the
    # script's curl never trips its own timeout and a lost connection is noticed.
    REVIEW_POLL_TIMEOUT = 55.0

    def __init__(self, config: Config):
        self.config = config
        self.watch_dir = get_watch_dir(config)
        self.main_file = get_main_file(config)

        self.last_result: CompileResult | None = None
        self.synctex_data: SyncTeXData | None = None
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
        self.closing = False
        self.websockets: set[web.WebSocketResponse] = set()
        self.watcher: Watcher | None = None
        self._runner: web.AppRunner | None = None
        self._store_watch: asyncio.Task[None] | None = None
        self._initial_compile: asyncio.Task[CompileResult] | None = None

        self.app = self._build_app()

    # ----- routes -----

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_get("/", self._handle_root)
        app.router.add_get("/static/{name:.*}", self._handle_static)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/pdf", self._handle_pdf)
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
        app.router.add_post(r"/comments/{id}/resolve", self._handle_resolve_comment)
        app.router.add_post(r"/comments/{id}/reopen", self._handle_reopen_comment)
        app.router.add_post(r"/comments/{id}/archive", self._handle_archive_comment)
        app.router.add_post(r"/comments/{id}/edit", self._handle_edit_comment_entry)
        app.router.add_delete(r"/comments/{id}", self._handle_delete_comment)
        app.router.add_get("/synctex/source-to-pdf", self._handle_synctex_forward)
        app.router.add_post("/goto", self._handle_goto)
        app.router.add_get("/image", self._handle_image)
        app.router.add_get("/reference-preview", self._handle_reference_preview)
        app.router.add_post("/review-request", self._handle_review_request)
        app.router.add_get("/wait-review", self._handle_wait_review)
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
                    sections_resolver=lambda title, label: resolve_section_to_source(
                        self.structure, self.watch_dir, title, label
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

    async def on_file_change(self, changed_path: str) -> None:
        try:
            path = Path(changed_path).resolve()
            relative = path.relative_to(self.watch_dir.resolve()).as_posix()
            revision = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            self.comments.refresh_source_anchors(self.watch_dir)
            await self.broadcast(
                {"type": "source_changed", "path": relative, "revision": revision}
            )
        except (OSError, ValueError):
            pass

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
        try:
            async for _ in ws:
                # The viewer is read-only; we don't accept any client-sent
                # messages.  Iteration just keeps the socket alive.
                pass
        finally:
            self.websockets.discard(ws)
        return ws

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
        # The line counts what a wake-up is for: open threads whose last word is the
        # reviewer's. A count of open threads sent an agent to read one it had answered.
        waiting = sum(comment.thread[-1].author == "human" for comment in self.comments.list(status="open"))
        return (f"[review] reviewer called (press #{self.review_calls}): "
                + (f"{waiting} unanswered comments" if waiting else "no unanswered comments")
                + " -- read them with read_comments(unanswered=True)")

    async def _handle_review_request(self, request: web.Request) -> web.Response:
        self.review_calls += 1
        self._save_review_state()
        delivered = self.review_waiters > 0
        released = self.review_called
        self.review_called = asyncio.Event()
        released.set()
        await self.broadcast({"type": "review_requested", "calls": self.review_calls, "delivered": delivered})
        return web.json_response({"calls": self.review_calls, "delivered": delivered})

    async def _handle_wait_review(self, request: web.Request) -> web.Response:
        # The consumption watermark lives here, not in the waiter: a waiter carrying its
        # own could never see a press made before its script was written. Waiting does
        # not consume: the response can die on the wire after the watermark moved, and
        # the wake-up died with it. The waiter acks what it printed (the X-Press header
        # names it), and until then the press is offered again.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.REVIEW_POLL_TIMEOUT
        self.review_waiters += 1
        await self.broadcast({"type": "review_waiters", "waiters": self.review_waiters})
        try:
            while self.review_calls <= self.review_consumed:
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

    # ----- API: paper / compile -----

    async def _handle_paper(self, request: web.Request) -> web.Response:
        if self.structure is None:
            self.structure = parse_structure(self.watch_dir, self.main_file)

        return web.json_response(
            {
                "main_file": self.config.main,
                "watch_dir": str(self.watch_dir),
                "compiling": self.compiling,
                "last_compile": _result_to_dict(self.last_result),
                "pdf_digest": self.pdf_digest,
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

    async def _handle_sources(self, request: web.Request) -> web.Response:
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
        return web.json_response({"main_file": self.config.main, "files": sorted(set(files))})

    async def _handle_source(self, request: web.Request) -> web.Response:
        path = self._resolve_source_path(request.query.get("path"))
        text, revision = self._read_source(path)
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

        _, current_revision = self._read_source(path)
        if data["revision"] != current_revision:
            return web.json_response(
                {"error": "source changed after it was opened", "revision": current_revision},
                status=409,
            )

        encoded = data["text"].encode("utf-8")
        self._replace_source(path, encoded)
        revision = hashlib.sha256(encoded).hexdigest()
        self.comments.refresh_source_anchors(self.watch_dir)
        return web.json_response(
            {
                "path": path.relative_to(self.watch_dir.resolve()).as_posix(),
                "revision": revision,
            }
        )

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
        try:
            anchor = anchor_from_dict(anchor_d)
            suggestion = _suggestion_from_dict(
                data["suggestion"] if "suggestion" in data else None,
                anchor.file if isinstance(anchor, SourceRangeAnchor) else None,
            )
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

        resolved, source_selector = self._resolve_anchor(anchor)
        if isinstance(anchor, SourceRangeAnchor) and source_selector is None:
            return web.json_response({"error": "Source selection is out of bounds"}, status=400)
        comment = self.comments.add(
            anchor=anchor,
            text=text,
            author="human",
            resolved_source=resolved,
            source_selector=source_selector,
            suggestion=suggestion,
        )
        await self.broadcast({"type": "comment_added", "comment": _comment_to_dict(comment)})
        return web.json_response(_comment_to_dict(comment), status=201)

    def _resolve_anchor(
        self, anchor: Any
    ) -> tuple[ResolvedSource | None, SourceSelector | None]:
        """Resolve one new anchor and capture its exact source selector."""
        from .comments import ResolveContext

        if isinstance(anchor, (PaperAnchor, AreaAnchor)):
            return None, None

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

        # Lazy-load structure for section resolution.
        if isinstance(anchor, SectionAnchor) and self.structure is None:
            self.structure = parse_structure(self.watch_dir, self.main_file)

        ctx = ResolveContext(
            watch_dir=self.watch_dir,
            structure=self.structure,
            synctex=self.synctex_data,
        )
        resolved = anchor.resolve_source(ctx)
        if resolved is None:
            return None, None

        if isinstance(anchor, SectionAnchor):
            return resolved, None

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
        return await self._mutate_comment(
            cid,
            lambda: self.comments.reply(
                cid,
                text=text,
                author="human",
                edits=data.get("edits") or [],
            ),
        )

    async def _handle_resolve_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
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
        return await self._mutate_comment(
            cid,
            lambda: self.comments.reopen(cid, author="human"),
            acknowledge_only=True,
        )

    async def _handle_archive_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        return await self._mutate_comment(
            cid,
            lambda: self.comments.archive(cid, author="human"),
            acknowledge_only=True,
        )

    async def _handle_delete_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        if not self.comments.delete(cid):
            return web.json_response({"error": f"no comment {cid}"}, status=404)
        await self.broadcast({"type": "comment_deleted", "id": cid})
        return web.json_response({"deleted": cid})

    # ----- SyncTeX -----

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
            match = find_section(
                self.structure,
                title=section if section else None,
                label=label if label else None,
            )
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
        """Bind the port, start watching, and kick off the first compile.

        The port binds before compiling so a caller that waits for readiness
        is not blocked by a LaTeX run.
        """
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
