"""MCP server for tex-mcp-web v0.4.0.

Exposes 5 tools to Claude Code via stdio:

    paper()                 paper state (sections, labels, citations, comments)
    compile()               recompile, return structured errors
    comments_data(status, tags)  list comments (queue of work)
    comment(action, ...)    add/reply/resolve/dismiss/reopen/delete
    goto(target)            tell the daemon to scroll the viewer (requires daemon)

The MCP server reads/writes the same files the daemon does
(``.tex-mcp-web/comments.json`` and the .tex sources).  It does not require
the daemon to be running for paper/compile/comment operations.  The
``goto`` tool is the exception: it speaks HTTP to a running daemon.

Requires: pip install "mcp>=1.0"  (and httpx for goto)
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from .config import DEFAULT_PORT

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ImageContent, TextContent

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_deps() -> None:
    if not HAS_MCP:
        print(
            "Error: MCP server requires the 'mcp' package.\n"
            "Install with:\n"
            "  pip install tex-mcp-web[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)


def _load_project():
    """Resolve config + watch_dir + comment store from cwd."""
    from .comments import CommentStore
    from .config import get_watch_dir, load_config

    cfg = load_config()
    watch_dir = get_watch_dir(cfg)
    store = CommentStore(watch_dir / ".tex-mcp-web" / "comments.json")
    return cfg, watch_dir, store


def _result_to_dict(result) -> dict[str, Any]:
    if result is None:
        return {"success": None}
    return {
        "success": result.success,
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "warnings": [dataclasses.asdict(w) for w in result.warnings],
        "output_file": str(result.output_file) if result.output_file else None,
        "duration_seconds": result.duration_seconds,
        "timestamp": result.timestamp.isoformat() if result.timestamp else None,
    }


def _err(message: str) -> str:
    return json.dumps({"error": message})


def _ok(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


# In-process SyncTeX cache for the MCP server.  Each call to image
# / _comment_add would otherwise re-parse the .synctex.gz from disk; on a
# 50+ page paper that is tens of MB of gzipped data per call.  Keyed by
# the synctex file's mtime so a recompile transparently invalidates.
_synctex_cache: dict[Path, tuple[float, Any]] = {}


def _load_synctex_cached(main_file: Path):
    """Load and cache SyncTeX for *main_file*'s rendered PDF.

    Cache key is the SyncTeX file path; cache entry is (mtime, data).
    A rebuild that bumps mtime invalidates the entry on next call.
    """
    from .server import load_synctex_for_main
    from .synctex import find_synctex_file

    pdf_path = main_file.with_suffix(".pdf")
    if not pdf_path.exists():
        return None
    synctex_path = find_synctex_file(pdf_path)
    if synctex_path is None:
        return None
    try:
        mtime = synctex_path.stat().st_mtime
    except OSError:
        return None
    cached = _synctex_cache.get(synctex_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = load_synctex_for_main(main_file)
    if data is not None:
        _synctex_cache[synctex_path] = (mtime, data)
    return data


def _comment_add(
    store,
    cfg,
    watch_dir: Path,
    text: str | None,
    anchor: dict[str, Any] | None,
    author: str,
    suggestion: dict[str, Any] | None = None,
) -> str:
    """Implementation of ``comment(action="add", ...)``.

    Resolves source-bearing anchors via the same helpers the HTTP server
    uses, so the two surfaces produce identical ``resolved_source``
    metadata.  For ``pdf_region`` anchors, loads the cached SyncTeX file
    next to the rendered PDF (no daemon required).
    """
    from .comments import ResolvedSource, anchor_from_dict, capture_snippet
    from .config import get_main_file
    from .server import (
        load_synctex_for_main,
        resolve_pdf_region_to_source,
        resolve_section_to_source,
    )
    from .structure import parse_structure

    if not text or not anchor:
        return _err("add requires text and anchor")

    a = anchor_from_dict(anchor)
    resolved: ResolvedSource | None = None
    snippet: str | None = None
    kind = anchor.get("kind")

    if kind == "source_range":
        file = anchor["file"]
        ls = int(anchor["line_start"])
        le = int(anchor["line_end"])
        snippet = capture_snippet(watch_dir / file, ls, le) or None
        resolved = ResolvedSource(file=file, line_start=ls, line_end=le)
    elif kind == "section":
        resolved = resolve_section_to_source(
            parse_structure(watch_dir, get_main_file(cfg)),
            watch_dir,
            anchor.get("title"),
            anchor.get("label"),
        )
    elif kind == "pdf_region":
        synctex = _load_synctex_cached(get_main_file(cfg))
        bbox = anchor.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        resolved = resolve_pdf_region_to_source(
            synctex,
            int(anchor.get("page", 1)),
            (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        )
        if resolved is not None:
            snippet = (
                capture_snippet(
                    watch_dir / resolved.file, resolved.line_start, resolved.line_end
                )
                or None
            )

    from .server import _suggestion_from_dict

    comment = store.add(
        anchor=a,
        text=text,
        author=author,
        resolved_source=resolved,
        snippet=snippet,
        suggestion=_suggestion_from_dict(suggestion),
    )
    return _ok(comment.to_dict())


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def create_server(daemon_port: int = DEFAULT_PORT) -> "FastMCP":
    _check_deps()
    mcp = FastMCP("tex-mcp-web")

    @mcp.tool()
    async def paper(
        include_comments: bool = True,
        comments_status: str = "open",
    ) -> str:
        """The "what does my paper look like right now" oracle.

        Returns:
          - main_file, watch_dir
          - sections (with title, label, file, line, line_end)
          - compile_cache (pdf path + whether it exists)
          - comments[] (full list of comments at the requested status)

        Section parsing is deliberately the only structure we hand back;
        for labels, citations, and \\input refs, use ``Grep``.

        Args:
            include_comments: if False, the ``comments`` field is omitted
                (useful when you only need orientation, not the queue).
            comments_status: ``"open"`` (default), ``"resolved"``,
                ``"dismissed"``, or ``"all"``.
        """
        from .config import get_main_file
        from .server import structure_to_dict
        from .structure import parse_structure

        cfg, watch_dir, store = _load_project()
        main = get_main_file(cfg)
        structure = parse_structure(watch_dir, main)
        pdf_path = main.with_suffix(".pdf")

        result: dict[str, Any] = {
            "main_file": cfg.main,
            "watch_dir": str(watch_dir),
            **structure_to_dict(structure, watch_dir),
            "compile_cache": {
                "pdf_exists": pdf_path.exists(),
                "pdf_path": str(pdf_path),
            },
        }
        if include_comments:
            s = None if comments_status == "all" else comments_status
            comments = store.list(status=s)  # type: ignore[arg-type]
            result["comments"] = [c.to_dict() for c in comments]
        return _ok(result)

    @mcp.tool()
    async def compile() -> str:
        """Recompile the paper.  Returns structured errors and warnings, each
        with file/line/message and (when possible) source context lines.

        Use after editing source: read the structured errors instead of
        re-reading the full latexmk log.
        """
        from .compiler import compile_tex
        from .config import get_main_file

        cfg, watch_dir, _ = _load_project()
        result = await compile_tex(
            get_main_file(cfg), compiler=cfg.compiler, work_dir=watch_dir
        )
        return _ok(_result_to_dict(result))

    @mcp.tool()
    async def comment(
        action: str,
        id: str | None = None,
        text: str | None = None,
        anchor: dict[str, Any] | None = None,
        summary: str | None = None,
        reason: str | None = None,
        edits: list[str] | None = None,
        suggestion: dict[str, str] | None = None,
        author: str = "claude",
    ) -> str:
        """Mutate a comment.

        Actions:
          - add:     create new comment.   Required: text + anchor.
          - reply:   append a thread entry.  Required: id + text.
          - resolve: mark resolved.          Required: id + summary.
          - dismiss: mark dismissed.         Required: id + reason.
          - delete:  permanently remove.     Required: id.

        Anchor formats (only for add):
          {"kind": "paper"}
          {"kind": "section", "title": "Methods", "label": "sec:methods"}
          {"kind": "source_range", "file": "intro.tex", "line_start": 42, "line_end": 58}
          {"kind": "pdf_region", "page": 3, "bbox": [x1, y1, x2, y2]}

        Optional ``suggestion={"old": "...", "new": "..."}`` (add only):
        a structured rewrite the agent can apply directly instead of
        parsing prose.  Use it when the comment proposes a concrete
        edit; leave it off for open-ended discussion ("expand this").

        Edits is an optional list of strings describing what changed
        when resolving: ["intro.tex:42-58 -> :42-78"].

        To list the comment queue, call ``paper()`` (which
        returns comments by default).
        """
        cfg, watch_dir, store = _load_project()
        try:
            if action == "add":
                return _comment_add(store, cfg, watch_dir, text, anchor, author, suggestion)
            if action == "reply":
                if not id or not text:
                    return _err("reply requires id and text")
                return _ok(
                    store.reply(id, text=text, author=author, edits=edits or []).to_dict()
                )
            if action == "resolve":
                if not id or not summary:
                    return _err("resolve requires id and summary")
                return _ok(
                    store.resolve(
                        id, summary=summary, edits=edits or [], author=author
                    ).to_dict()
                )
            if action == "dismiss":
                if not id or not reason:
                    return _err("dismiss requires id and reason")
                return _ok(store.dismiss(id, reason=reason, author=author).to_dict())
            if action == "delete":
                if not id:
                    return _err("delete requires id")
                return _ok({"deleted": id, "ok": store.delete(id)})
            return _err(f"unknown action: {action}")
        except KeyError as exc:
            return _err(f"comment not found: {exc}")
        except (ValueError, TypeError) as exc:
            return _err(str(exc))

    @mcp.tool()
    async def image(
        page: int | None = None,
        bbox: list[float] | None = None,
        source: str | None = None,
        comment_id: str | None = None,
        dpi: int = 150,
    ) -> list[ImageContent | TextContent]:
        """Render a PDF region as PNG and return it for visual analysis.

        Use exactly one of:
          page=N                         full page
          page=N + bbox=[x1,y1,x2,y2]    region in PDF points
          source="file.tex:lstart-lend"  SyncTeX-resolved region
          comment_id="c-..."             the region a comment is anchored to

        Returns ImageContent (base64 PNG) on success.  Use this when text
        alone won't tell you what's wrong: figure layout, equation
        rendering, overfull boxes, table positioning, or to verify a fix
        looks right.

        Combine with paper() for the workflow:
          1. paper()                          # see open comments
          2. for each: image(comment_id=...)  # see the rendered
                                                       # region the human
                                                       # anchored
          3. Read source, Edit, then compile + image
             again to verify visually.
        """
        import base64

        from . import imaging
        from .config import get_main_file
        from .server import _clamp_dpi, _parse_source_range

        cfg, watch_dir, store = _load_project()
        pdf_path = get_main_file(cfg).with_suffix(".pdf")
        if not pdf_path.exists():
            return [TextContent(type="text",
                text=_err("no PDF on disk; run compile() first"))]

        parsed_bbox: tuple[float, float, float, float] | None = None
        if bbox is not None:
            if len(bbox) != 4:
                return [TextContent(type="text",
                    text=_err("bbox must have exactly 4 values"))]
            parsed_bbox = (float(bbox[0]), float(bbox[1]),
                           float(bbox[2]), float(bbox[3]))
        parsed_source = _parse_source_range(source) if source else None

        clamped_dpi = _clamp_dpi(dpi)
        synctex = _load_synctex_cached(get_main_file(cfg))

        try:
            resolved_page, resolved_bbox = imaging.resolve_image_target(
                synctex=synctex,
                comment_lookup=store.get,
                page=page,
                bbox=parsed_bbox,
                source=parsed_source,
                comment_id=comment_id,
                watch_dir=watch_dir,
            )
            if resolved_bbox is None:
                png = await asyncio.to_thread(
                    imaging.render_page, pdf_path, resolved_page, clamped_dpi
                )
            else:
                png = await asyncio.to_thread(
                    imaging.render_region,
                    pdf_path,
                    resolved_page,
                    resolved_bbox,
                    clamped_dpi,
                )
        except (ValueError, imaging.ImagingError) as exc:
            return [TextContent(type="text", text=_err(str(exc)))]

        return [ImageContent(
            type="image",
            data=base64.b64encode(png).decode("ascii"),
            mimeType="image/png",
        )]

    @mcp.tool()
    async def section(
        name: str,
        include_image: bool = True,
        dpi: int = 150,
    ) -> list[ImageContent | TextContent]:
        """Deep-dive on one section: source + image + scoped comments in one call.

        *name* matches the section title (case-insensitive) or a
        ``\\label{...}`` value.  Returns:

          - JSON with the section metadata, the verbatim source slice,
            and any open comments anchored within the section's lines.
          - Optionally an ImageContent of the rendered section.

        Use this when zooming in on one section instead of the whole
        paper.  Saves the agent the round-trips of paper +
        Read(file:line-range) + image(source=...).
        """
        import base64

        from . import imaging
        from .comments import (
            PdfRegionAnchor,
            SectionAnchor,
            SourceRangeAnchor,
        )
        from .config import get_main_file
        from .server import resolve_section_to_source
        from .structure import parse_structure

        cfg, watch_dir, store = _load_project()
        structure = parse_structure(watch_dir, get_main_file(cfg))
        resolved = resolve_section_to_source(structure, watch_dir, name, name)
        if resolved is None:
            return [TextContent(type="text",
                text=_err(f"no section matches {name!r}"))]

        # Read the verbatim source slice.  Section line ranges include the
        # heading itself; we keep that for context.
        source_path = watch_dir / resolved.file
        try:
            lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
            slice_text = "\n".join(lines[resolved.line_start - 1 : resolved.line_end])
        except OSError:
            slice_text = ""

        # Find comments scoped to this section.  Three cases:
        #   - SectionAnchor matching by title or label
        #   - Source/PDF anchors whose resolved_source lies in this range
        #   - PdfRegionAnchor with no resolved_source: harder to scope,
        #     skip (they show up in the global queue instead)
        scoped: list = []
        lc_name = name.lower()
        for c in store.list(status="open"):
            if isinstance(c.anchor, SectionAnchor):
                title_match = c.anchor.title.lower() == lc_name
                label_match = c.anchor.label is not None and c.anchor.label == name
                if title_match or label_match:
                    scoped.append(c)
                    continue
            if c.resolved_source is None:
                continue
            if (
                c.resolved_source.file == resolved.file
                and resolved.line_start <= c.resolved_source.line_start <= resolved.line_end
            ):
                scoped.append(c)

        payload = {
            "section": {
                "name": name,
                "file": resolved.file,
                "line_start": resolved.line_start,
                "line_end": resolved.line_end,
            },
            "source": slice_text,
            "comments": [c.to_dict() for c in scoped],
        }

        results: list[ImageContent | TextContent] = [
            TextContent(type="text", text=_ok(payload))
        ]

        if include_image:
            from .server import _clamp_dpi
            pdf_path = get_main_file(cfg).with_suffix(".pdf")
            if pdf_path.exists():
                synctex = _load_synctex_cached(get_main_file(cfg))
                pair = imaging.resolve_source_to_region(
                    synctex,
                    resolved.file,
                    resolved.line_start,
                    resolved.line_end,
                ) if synctex else None
                if pair is not None:
                    page, bbox = pair
                    try:
                        png = await asyncio.to_thread(
                            imaging.render_region,
                            pdf_path, page, bbox, _clamp_dpi(dpi),
                        )
                        results.append(ImageContent(
                            type="image",
                            data=base64.b64encode(png).decode("ascii"),
                            mimeType="image/png",
                        ))
                    except imaging.ImagingError:
                        pass  # rendering failed; the JSON payload still suffices

        return results

    @mcp.tool()
    async def audit(focus: str | None = None) -> str:
        """Workflow primer for agent-initiated review.

        tex-mcp-web is bidirectional: comments aren't just human→agent.  When
        the human says "review my methods section," the agent should
        read the paper and file *its own* comments back as
        ``author="claude"`` — these show up in the sidebar with a
        distinct visual treatment so the human can step through them
        like any other queue.

        This tool returns *guidance*, not state.  It does no work itself.
        The agent then uses the existing tools to do the review:

          1. paper()
                 -> sections, last compile, current open comments
          2. For relevant sections / pages:
               - Read or Grep the source
               - image(source="file.tex:N-M") to see rendering
               - image(page=N) for a full page
          3. File findings:
               comment(
                 action="add",
                 anchor={...},                  # section/source/pdf/paper
                 text="<concise observation>",
                 suggestion={"old":..., "new":...},   # when concrete
                 author="claude",
               )
          4. Skip nitpicks and high-confidence-low-value findings.

        Args:
            focus: optional theme.  ``"math"``, ``"prose"``,
                ``"citations"``, ``"consistency"``, or None (general).

        Returns guidance JSON that can be printed back to the agent's
        context as a prompt scaffold.
        """
        guidance = {
            "math": (
                "Look for: undefined symbols and notation drift between "
                "sections; theorem statements that don't match their "
                "proofs; missing assumptions; equation references to "
                "labels that don't exist; sign errors; bounds that "
                "should be strict vs. non-strict."
            ),
            "prose": (
                "Look for: paragraphs that bury the contribution; "
                "passive voice where active is clearer; redundant "
                "exposition; weak transitions; abstract phrasing where "
                "concrete examples would land; overlong sentences; "
                "ambiguous antecedents."
            ),
            "citations": (
                "Look for: claims missing citations; citations that "
                "don't support the claim; references in the bib but not "
                "cited; cited references that don't appear in the bib; "
                "outdated work where a more recent reference exists."
            ),
            "consistency": (
                "Look for: notation that drifts between sections; "
                "definitions used before introduced; cross-references "
                "to nonexistent labels; theorem numbers that don't "
                "match their text; figure/table captions that describe "
                "the wrong content."
            ),
        }
        focus_text = guidance.get(focus or "", "General editorial pass: prioritize clarity, correctness, and contribution-framing over polish.")
        return _ok({
            "focus": focus or "general",
            "guidance": focus_text,
            "workflow": [
                "1. paper() — see sections + open comments + last compile",
                "2. For each candidate region: Read/Grep source, image() for visuals",
                "3. comment(action='add', author='claude', anchor=..., text=..., suggestion=...)",
                "4. Surface high-confidence findings only; skip nitpicks",
            ],
            "anchor_guidance": {
                "paper": "global concerns (abstract length, contribution framing)",
                "section": "section-level structure or coverage",
                "source_range": "specific lines (preferred when concrete)",
                "pdf_region": "visual issues only an image conveys",
            },
            "tip": (
                "Use suggestion={old, new} when proposing a concrete "
                "rewrite; the human can apply it with one gesture and "
                "you save a round-trip."
            ),
        })

    @mcp.tool()
    async def goto(target: str, port: int = daemon_port) -> str:
        """Tell a running daemon to scroll the viewer to a target.

        target: a section title, "pN" (page), file:line, or just N (line in main file).
        Requires the tex-mcp-web server to be running.
        """
        try:
            import httpx
        except ImportError:
            return _err("httpx not installed; install tex-mcp-web[mcp]")

        cfg, _, _ = _load_project()
        body = parse_goto_target(target, default_file=cfg.main)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"http://127.0.0.1:{port}/goto", json=body)
                return resp.text
        except Exception as exc:
            return _err(f"daemon at port {port} not reachable: {exc}")

    return mcp


import re

# A LaTeX-style label: short alpha prefix + colon + identifier without spaces.
# Matches ``sec:methods``, ``eq:foo-bar``, ``thm:main``.  Does *not* match
# ``Introduction: A Survey`` (space) or filenames (long prefix / has dot).
_LABEL_LIKE = re.compile(r"^[a-zA-Z]{2,8}:[A-Za-z0-9_.\-:]+$")


def parse_goto_target(target: str, default_file: str) -> dict[str, Any]:
    """Convert a CLI/MCP goto target string into a request body for ``/goto``.

    Recognized forms (in order):
      ``pN``         -> ``{"page": N}``
      ``N``          -> ``{"line": N, "file": default_file}``
      ``FILE:N``     -> ``{"file": FILE, "line": N}``  (right-hand side digits)
      ``sec:foo``    -> ``{"label": "sec:foo"}``       (LaTeX label syntax)
      anything else  -> ``{"section": target}``
    """
    if target.startswith("p") and target[1:].isdigit():
        return {"page": int(target[1:])}
    if target.isdigit():
        return {"line": int(target), "file": default_file}
    if ":" in target and target.rsplit(":", 1)[1].isdigit():
        file, line = target.rsplit(":", 1)
        return {"file": file, "line": int(line)}
    if _LABEL_LIKE.match(target):
        return {"label": target}
    return {"section": target}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def main(port: int = DEFAULT_PORT) -> None:
    """Run the MCP server with stdio transport."""
    _check_deps()
    mcp = create_server(daemon_port=port)
    asyncio.run(mcp.run_stdio_async())
