"""aiohttp web server for tex-mcp-web v0.4.0.

Single paper, no workspace abstraction.  Three responsibilities:

1. Watch the project directory and recompile on .tex/.bib changes.
2. Serve the resulting PDF + a thin viewer with an annotation layer.
3. Expose a JSON API for comments, paper state, errors, and SyncTeX
   resolution (used by the browser viewer and the MCP server).

There is no editor.  The human writes comments; Claude Code edits files
through its own tools (Edit/Write) and the watcher picks up the changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from .comments import (
    Comment,
    CommentStore,
    PaperAnchor,
    PdfRegionAnchor,
    ResolvedSource,
    SectionAnchor,
    SourceRangeAnchor,
    anchor_from_dict,
    capture_snippet,
)
from .compiler import CompileResult, compile_tex
from .config import Config, get_main_file, get_watch_dir
from .structure import (
    DocumentStructure,
    find_section,
    parse_structure,
)
from .synctex import (
    SyncTeXData,
    find_synctex_file,
    page_to_source,
    parse_synctex,
    source_to_page,
)
from .watcher import Watcher

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


def _clamp_dpi(value: str | int, default: int = 150) -> int:
    """Parse and clamp a DPI value to a sane render range.

    Rendering at unbounded DPI is a denial-of-service vector
    (``?dpi=10000`` allocates a multi-gigabyte pixmap).  Clamp to a
    range that covers screen viewing (~96–150) up to high-detail
    extraction (~600).
    """
    n = int(value) if not isinstance(value, int) else value
    return max(36, min(n, 600))


def _suggestion_from_dict(d: Any) -> "SuggestedEdit | None":
    """Build a SuggestedEdit from a dict, or None if absent/malformed.

    Lenient: missing keys default to empty strings.  An empty old/new
    pair is treated as "no suggestion" so the agent doesn't see a
    SuggestedEdit({old: '', new: ''}).
    """
    from .comments import SuggestedEdit

    if not isinstance(d, dict):
        return None
    sugg = SuggestedEdit.from_dict(d)
    if not sugg.old and not sugg.new:
        return None
    return sugg


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


def structure_to_dict(
    structure: DocumentStructure, watch_dir: Path
) -> dict[str, list[dict[str, Any]]]:
    """JSON-serializable view of :class:`DocumentStructure`.

    Sections only — labels / citations / inputs are deliberately omitted;
    Claude Code can ``Grep`` for those better than our regex.
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
                "title": s.title,
                "file": s.file,
                "line": s.line,
                "line_end": line_end,
                "label": s.label,
            }
        )
    return {"sections": sections}


def resolve_pdf_region_to_source(
    synctex: SyncTeXData | None,
    page: int,
    bbox: tuple[float, float, float, float],
) -> ResolvedSource | None:
    """Use SyncTeX to map a PDF bbox center to a source range."""
    if synctex is None:
        return None
    _, y1, _, y2 = bbox
    src = page_to_source(synctex, page, (y1 + y2) / 2)
    if src is None:
        return None
    return ResolvedSource(file=src.file, line_start=src.line, line_end=src.line)


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

    def __init__(self, config: Config):
        self.config = config
        self.watch_dir = get_watch_dir(config)
        self.main_file = get_main_file(config)

        self.last_result: CompileResult | None = None
        self.synctex_data: SyncTeXData | None = None
        self.structure: DocumentStructure | None = None
        self.compiling = False
        # Serialize do_compile so the watcher and compile() don't
        # race; second caller awaits the in-flight build instead of
        # starting a parallel one.
        self._compile_lock = asyncio.Lock()
        # Per-page text hashes from the previous compile, for change
        # detection.  Empty after process startup; populated on each
        # successful build.
        self._prev_page_hashes: list[str] = []

        self.comments = CommentStore(self.watch_dir / ".tex-mcp-web" / "comments.json")
        self.websockets: set[web.WebSocketResponse] = set()
        self.watcher: Watcher | None = None

        self.app = self._build_app()

    # ----- routes -----

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_get("/", self._handle_root)
        app.router.add_static("/static/", STATIC_DIR, name="static")
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/pdf", self._handle_pdf)
        app.router.add_get("/paper", self._handle_paper)
        app.router.add_post("/compile", self._handle_compile)
        app.router.add_get("/comments", self._handle_list_comments)
        app.router.add_post("/comments", self._handle_create_comment)
        app.router.add_get(r"/comments/{id}", self._handle_get_comment)
        app.router.add_post(r"/comments/{id}/reply", self._handle_reply_comment)
        app.router.add_post(r"/comments/{id}/resolve", self._handle_resolve_comment)
        app.router.add_post(r"/comments/{id}/dismiss", self._handle_dismiss_comment)
        app.router.add_delete(r"/comments/{id}", self._handle_delete_comment)
        app.router.add_get("/synctex/source-to-pdf", self._handle_synctex_forward)
        app.router.add_post("/goto", self._handle_goto)
        app.router.add_get("/image", self._handle_image)
        return app

    # ----- compile + watch -----

    async def do_compile(self) -> CompileResult:
        # Serialize: if a build is already running, await it.
        async with self._compile_lock:
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
                # Re-check staleness of open comments
                if self.structure:
                    self.comments.check_staleness(
                        self.watch_dir,
                        sections_resolver=lambda title, label: resolve_section_to_source(
                            self.structure, self.watch_dir, title, label
                        ),
                    )
                # Compute per-page text hashes and diff against the
                # previous compile.  Gives the agent a cheap "what
                # changed visually" signal without rendering everything.
                if self.last_result.success and self.last_result.output_file:
                    from . import imaging
                    new_hashes = imaging.page_text_hashes(self.last_result.output_file)
                    changed_pages = imaging.diff_page_hashes(
                        self._prev_page_hashes, new_hashes
                    )
                    self._prev_page_hashes = new_hashes

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
                }
                if changed_pages:
                    msg["pages_changed"] = changed_pages
                await self.broadcast(msg)
            return self.last_result

    async def on_file_change(self, changed_path: str) -> None:
        logger.info("File change (%s); recompiling…", changed_path)
        await self.do_compile()

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

    # ----- static / PDF -----

    async def _handle_root(self, request: web.Request) -> web.Response:
        index = STATIC_DIR / "index.html"
        return web.FileResponse(index)

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
                **structure_to_dict(self.structure, self.watch_dir),
                "comments": self._comment_summary(),
            }
        )

    def _comment_summary(self) -> dict[str, int]:
        open_comments = self.comments.list(status="open")
        return {
            "open": len(open_comments),
            "resolved": len(self.comments.list(status="resolved")),
            "dismissed": len(self.comments.list(status="dismissed")),
            "stale": sum(1 for c in open_comments if c.stale),
        }

    async def _handle_compile(self, request: web.Request) -> web.Response:
        result = await self.do_compile()
        return web.json_response(_result_to_dict(result))

    # ----- API: comments -----

    async def _handle_list_comments(self, request: web.Request) -> web.Response:
        status = request.query.get("status")
        if status not in ("open", "resolved", "dismissed"):
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

        anchor_d = data.get("anchor")
        text = data.get("text", "").strip()
        if not anchor_d or not text:
            return web.json_response(
                {"error": "anchor and text are required"}, status=400
            )
        try:
            anchor = anchor_from_dict(anchor_d)
        except (ValueError, KeyError, TypeError) as exc:
            return web.json_response({"error": f"invalid anchor: {exc}"}, status=400)

        resolved, snippet = self._resolve_anchor(anchor)
        suggestion = _suggestion_from_dict(data.get("suggestion"))
        comment = self.comments.add(
            anchor=anchor,
            text=text,
            author=data.get("author", "human"),
            resolved_source=resolved,
            snippet=snippet,
            suggestion=suggestion,
        )
        await self.broadcast({"type": "comment_added", "comment": _comment_to_dict(comment)})
        return web.json_response(_comment_to_dict(comment), status=201)

    def _resolve_anchor(self, anchor: Any) -> tuple[ResolvedSource | None, str | None]:
        """Resolve a freshly-created anchor to (source location, snippet).

        Dispatch lives on the anchors themselves
        (:meth:`Anchor.resolve_source`).  We capture a snippet only for
        anchors that need content-addressed staleness (source / pdf
        region) — section anchors re-resolve via the structure parser
        on every recheck, so they don't need a content snapshot.
        """
        from .comments import ResolveContext

        if isinstance(anchor, PaperAnchor):
            return None, None

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

        # Section anchors are structurally re-resolvable; everything
        # else falls back to snippet matching.
        if isinstance(anchor, SectionAnchor):
            return resolved, None

        snippet = capture_snippet(
            self.watch_dir / resolved.file, resolved.line_start, resolved.line_end
        )
        return resolved, snippet or None

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
    ) -> web.Response:
        """Run *action* (a no-arg call into the store), broadcast, return the updated comment."""
        try:
            updated = action()
        except KeyError:
            return web.json_response({"error": f"no comment {cid}"}, status=404)
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
                author=data.get("author", "human"),
                edits=data.get("edits") or [],
            ),
        )

    async def _handle_resolve_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        data, err = await self._read_json(request)
        if err is not None:
            return err
        summary = (data.get("summary") or "").strip()
        if not summary:
            return web.json_response({"error": "summary is required"}, status=400)
        return await self._mutate_comment(
            cid,
            lambda: self.comments.resolve(
                cid,
                summary=summary,
                edits=data.get("edits") or [],
                author=data.get("author", "claude"),
            ),
        )

    async def _handle_dismiss_comment(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        data, err = await self._read_json(request)
        if err is not None:
            return err
        reason = (data.get("reason") or "").strip()
        if not reason:
            return web.json_response({"error": "reason is required"}, status=400)
        return await self._mutate_comment(
            cid,
            lambda: self.comments.dismiss(
                cid, reason=reason, author=data.get("author", "human")
            ),
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

        Body keys (use exactly one of section/label/line/page):
            section  section title (case-insensitive title match)
            label    \\label{...} value
            line + file  source line number in *file*
            page     PDF page number

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

        if resolved_file is None or resolved_line is None:
            return web.json_response({"error": "could not resolve target"}, status=404)

        # Try to map to a PDF page via SyncTeX.
        target_page = None
        if self.synctex_data is not None:
            pos = source_to_page(self.synctex_data, resolved_file, resolved_line)
            if pos:
                target_page = pos.page

        # Broadcast whatever we know — viewer scrolls if there's a page.
        await self.broadcast(
            {
                "type": "goto",
                "page": target_page,
                "file": resolved_file,
                "line": resolved_line,
            }
        )
        return web.json_response(
            {"page": target_page, "file": resolved_file, "line": resolved_line}
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
        except ValueError:
            return web.json_response({"error": "invalid dpi"}, status=400)

        pdf_path = self.last_result.output_file
        try:
            page, bbox = self._resolve_image_target(request)
            if bbox is None:
                png = await asyncio.to_thread(imaging.render_page, pdf_path, page, dpi)
            else:
                png = await asyncio.to_thread(
                    imaging.render_region, pdf_path, page, bbox, dpi
                )
        except (ValueError, imaging.ImagingError) as exc:
            return web.json_response({"error": str(exc)}, status=400)

        return web.Response(
            body=png,
            content_type="image/png",
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

    async def start(self, port: int) -> None:
        # Initial compile
        await self.do_compile()
        # Start watcher
        loop = asyncio.get_running_loop()
        self.watcher = Watcher(
            watch_dir=self.watch_dir,
            watch_patterns=self.config.watch,
            ignore_patterns=self.config.ignore,
            on_change=self.on_file_change,
        )
        self.watcher.start(loop)
        # Start aiohttp
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        logger.info("tex-mcp-web serving on http://127.0.0.1:%d", port)
        # Run until cancelled
        try:
            await asyncio.Event().wait()
        finally:
            if self.watcher:
                self.watcher.stop()
            await runner.cleanup()


def run(config: Config, port: int) -> None:
    """Synchronous entry point: build server, run until KeyboardInterrupt."""
    server = TexMcpWebServer(config)
    try:
        asyncio.run(server.start(port))
    except KeyboardInterrupt:
        logger.info("Shutting down")
