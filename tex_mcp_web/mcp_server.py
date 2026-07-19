"""MCP server for tex-mcp-web v0.7.0.

Exposes 6 tools to agents via stdio:

    paper()                 paper state (sections and compact comments)
    compile()               recompile, return structured errors
    comment(action, ...)    add/reply/resolve/dismiss/delete
    image(...)              render a PDF page or exact region
    section(name)           section source, comments, and optional image
    goto(target)            tell the daemon to scroll the viewer (requires daemon)

The MCP server reads/writes the same comment store as the daemon. ``compile``
and ``goto`` call the running daemon so compilation, PDF refresh, anchor
reattachment, and viewer notification remain one transaction.

Requires: pip install "mcp>=1.0"  (and httpx for goto)
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from .config import DEFAULT_PORT

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ImageContent, TextContent
    from pydantic import BaseModel, ConfigDict, Field, model_validator

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


if HAS_MCP:
    class _InputModel(BaseModel):
        model_config = ConfigDict(extra="forbid")


    class PaperAnchorInput(_InputModel):
        kind: Literal["paper"]


    class SectionAnchorInput(_InputModel):
        kind: Literal["section"]
        title: Annotated[str, Field(min_length=1)]
        label: str | None = None


    class SourceRangeAnchorInput(_InputModel):
        kind: Literal["source_range"]
        file: Annotated[str, Field(min_length=1)]
        line_start: Annotated[int, Field(ge=1)]
        line_end: Annotated[int, Field(ge=1)]

        @model_validator(mode="after")
        def validate_range(self):
            if self.line_end < self.line_start:
                raise ValueError("line_end must be at least line_start")
            return self


    class AreaAnchorInput(_InputModel):
        kind: Literal["area"]
        page: Annotated[int, Field(ge=1)]
        bbox: tuple[float, float, float, float]

        @model_validator(mode="after")
        def validate_bbox(self):
            x1, y1, x2, y2 = self.bbox
            if x2 <= x1 or y2 <= y1:
                raise ValueError("bbox must have positive width and height")
            return self


    CommentAnchorInput = Annotated[
        PaperAnchorInput | SectionAnchorInput | SourceRangeAnchorInput | AreaAnchorInput,
        Field(discriminator="kind"),
    ]


    class SuggestedEditInput(_InputModel):
        old: str
        new: str


    class ResolutionInput(_InputModel):
        id: Annotated[str, Field(min_length=1)]
        summary: Annotated[str, Field(min_length=1)]
        edits: list[str] = Field(default_factory=list)


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


def _agent_comment_to_dict(comment) -> dict[str, Any]:
    """Return only the comment information an agent can act on."""
    from .comments import AreaAnchor, SectionAnchor, TextSelectionAnchor

    anchor = comment.anchor
    payload: dict[str, Any] = {
        "id": comment.id,
        "status": comment.status,
        "kind": anchor.kind,
        "comment": comment.text,
    }
    if isinstance(anchor, TextSelectionAnchor):
        payload.update({
            "quote": anchor.quote,
            "page": anchor.selection.page,
        })
    elif isinstance(anchor, AreaAnchor):
        payload["page"] = anchor.page
    elif isinstance(anchor, SectionAnchor):
        payload["section"] = anchor.title
        if anchor.label is not None:
            payload["label"] = anchor.label
    if comment.resolved_source is not None:
        payload["source"] = comment.resolved_source.to_dict()
    if len(comment.thread) > 1:
        payload["replies"] = [entry.to_dict() for entry in comment.thread[1:]]
    if comment.suggestion is not None:
        payload["suggestion"] = comment.suggestion.to_dict()
    if comment.stale:
        payload["stale"] = True
    return payload


def _comment_add(
    store,
    cfg,
    watch_dir: Path,
    text: str | None,
    anchor: "CommentAnchorInput | None",
    suggestion: "SuggestedEditInput | None" = None,
) -> str:
    """Implementation of ``comment(action="add", ...)``.

    Source ranges and sections receive the same source selectors as browser
    comments. Area anchors are tied to the current compiled PDF.
    """
    from .comments import (
        ResolvedSource,
        anchor_from_dict,
        capture_source_selector,
        pdf_digest,
    )
    from .config import get_main_file
    from .server import (
        resolve_section_to_source,
    )
    from .structure import parse_structure

    if not text or not anchor:
        return _err("add requires text and anchor")

    resolved: ResolvedSource | None = None
    source_selector = None
    anchor_data = anchor.model_dump(exclude_none=True)
    kind = anchor_data["kind"]

    if kind == "source_range":
        file = anchor_data["file"]
        ls = int(anchor_data["line_start"])
        le = int(anchor_data["line_end"])
        source_selector = capture_source_selector(watch_dir / file, ls, le)
        if source_selector is None:
            return _err("source_range does not identify readable source lines")
        resolved = ResolvedSource(file=file, line_start=ls, line_end=le)
    elif kind == "section":
        resolved = resolve_section_to_source(
            parse_structure(watch_dir, get_main_file(cfg)),
            watch_dir,
            anchor_data["title"],
            anchor_data["label"] if "label" in anchor_data else None,
        )
    elif kind == "area":
        pdf_path = get_main_file(cfg).with_suffix(".pdf")
        if not pdf_path.is_file():
            return _err("area requires a compiled PDF")
        anchor_data["pdf_digest"] = pdf_digest(pdf_path)

    a = anchor_from_dict(anchor_data)

    from .server import _suggestion_from_dict

    comment = store.add(
        anchor=a,
        text=text,
        author="agent",
        resolved_source=resolved,
        source_selector=source_selector,
        suggestion=_suggestion_from_dict(
            suggestion.model_dump() if suggestion is not None else None
        ),
    )
    return _ok(_agent_comment_to_dict(comment))


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def create_server(daemon_port: int = DEFAULT_PORT) -> "FastMCP":
    _check_deps()
    mcp = FastMCP(
        "tex-mcp-web",
        instructions=(
            "Read the open queue and auto_compile mode with paper(). Use a "
            "comment's source location when present; otherwise locate its quote "
            "in the TeX source. After all source edits, call compile() once when "
            "auto_compile is false; when it is true, the watcher owns compilation. "
            "Use image() only for rendered evidence before resolving the comment."
        ),
    )

    @mcp.tool()
    async def paper(
        include_comments: bool = True,
        comments_status: Literal["open", "resolved", "dismissed", "all"] = "open",
    ) -> str:
        """Return the main file, automatic compilation mode, PDF path, section
        source ranges, and optionally comments filtered by status. Other TeX
        structure is intentionally omitted.
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
            "auto_compile": cfg.auto_compile,
            **structure_to_dict(structure, watch_dir),
            "pdf": {
                "exists": pdf_path.exists(),
                "path": str(pdf_path),
            },
        }
        if include_comments:
            if comments_status not in {"open", "resolved", "dismissed", "all"}:
                return _err(
                    "comments_status must be open, resolved, dismissed, or all"
                )
            s = None if comments_status == "all" else comments_status
            comments = store.list(status=s)  # type: ignore[arg-type]
            result["comments"] = [_agent_comment_to_dict(c) for c in comments]
        return _ok(result)

    @mcp.tool()
    async def compile() -> str:
        """Request a manual compile through the running daemon and return
        structured errors, warnings, and ``pages_changed``. Call once after a
        batch of source edits when ``paper().auto_compile`` is false. When it is
        true, the watcher owns compilation. ``pages_changed`` compares extracted
        PDF text and excludes visual-only changes.
        """
        try:
            import httpx
        except ImportError:
            return _err("httpx not installed; install tex-mcp-web[mcp]")

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    f"http://127.0.0.1:{daemon_port}/compile"
                )
        except Exception as exc:
            return _err(f"daemon at port {daemon_port} not reachable: {exc}")
        if response.status_code != 200:
            return _err(
                f"daemon compile failed with HTTP {response.status_code}: {response.text}"
            )
        return response.text

    @mcp.tool()
    async def comment(
        action: Literal[
            "add", "reply", "resolve", "resolve_many", "dismiss", "delete"
        ],
        id: str | None = None,
        text: str | None = None,
        anchor: CommentAnchorInput | None = None,
        summary: str | None = None,
        reason: str | None = None,
        edits: list[str] | None = None,
        suggestion: SuggestedEditInput | None = None,
        resolutions: list[ResolutionInput] | None = None,
    ) -> str:
        """Mutate a comment.

        ``add`` requires text and anchor; ``reply`` requires id and text;
        ``resolve`` requires id and summary; ``resolve_many`` requires resolutions;
        ``dismiss`` requires id and reason; ``delete`` requires id. ``suggestion``
        is an add-only rewrite, while ``edits`` records changed source ranges.
        """
        cfg, watch_dir, store = _load_project()
        try:
            if action == "add":
                return _comment_add(store, cfg, watch_dir, text, anchor, suggestion)
            if action == "reply":
                if not id or not text:
                    return _err("reply requires id and text")
                updated = store.reply(id, text=text, author="agent", edits=edits or [])
                return _ok(_agent_comment_to_dict(updated))
            if action == "resolve":
                if not id or not summary:
                    return _err("resolve requires id and summary")
                store.resolve(
                    id, summary=summary, edits=edits or [], author="agent"
                )
                return _ok({"id": id, "status": "resolved"})
            if action == "resolve_many":
                if not resolutions:
                    return _err("resolve_many requires resolutions")
                updated = store.resolve_many(
                    [
                        (item.id, item.summary, item.edits)
                        for item in resolutions
                    ],
                    author="agent",
                )
                return _ok({
                    "resolved": [
                        {"id": item.id, "status": item.status}
                        for item in updated
                    ]
                })
            if action == "dismiss":
                if not id or not reason:
                    return _err("dismiss requires id and reason")
                updated = store.dismiss(id, reason=reason, author="agent")
                return _ok(_agent_comment_to_dict(updated))
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
        page: Annotated[int, Field(ge=1)] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        source: Annotated[
            str, Field(pattern=r"^.+:\d+(?:-\d+)?$")
        ] | None = None,
        comment_id: str | None = None,
        dpi: int = 150,
        margin: Annotated[float, Field(ge=0)] = 12.0,
    ) -> list[ImageContent | TextContent]:
        """Render one PDF target as PNG.

        Choose exactly one of page (with optional bbox),
        source="file.tex:lstart-lend", or comment_id. Margin expands an exact
        bbox only while rendering. A multi-page source range renders the page
        with the most SyncTeX matches. The metadata reports any grayscale or
        DPI reduction applied to fit the response size limit.
        """
        import base64

        from . import imaging
        from .config import get_main_file
        from .server import _clamp_dpi, _parse_source_range

        cfg, watch_dir, store = _load_project()
        pdf_path = get_main_file(cfg).with_suffix(".pdf")
        if not pdf_path.exists():
            return [TextContent(type="text",
                text=_err("no PDF on disk; start tex-web and wait for its initial compile"))]

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
            def render(dpi_val: int, gray_val: bool) -> bytes:
                if resolved_bbox is None:
                    return imaging.render_page(pdf_path, resolved_page, dpi_val, gray=gray_val)
                return imaging.render_region(
                    pdf_path,
                    resolved_page,
                    resolved_bbox,
                    dpi_val,
                    margin=margin,
                    gray=gray_val,
                )

            # Claude Code truncates MCP tool output around 25k tokens,
            # and base64 tokenizes at ~1.8 chars/token (measured: a 46k-
            # char payload tripped the cap) — so budget ~36k chars.
            # Grayscale first: it halves a text page's PNG at full
            # resolution; only then trade DPI (PNG size ~ dpi^2).
            MAX_B64_CHARS = 36_000
            gray = False
            png = await asyncio.to_thread(render, clamped_dpi, gray)
            b64 = base64.b64encode(png).decode("ascii")
            for _ in range(5):
                if len(b64) <= MAX_B64_CHARS:
                    break
                if not gray:
                    gray = True
                elif clamped_dpi > 30:
                    clamped_dpi = max(30, int(clamped_dpi * (MAX_B64_CHARS / len(b64)) ** 0.5 * 0.9))
                else:
                    break
                png = await asyncio.to_thread(render, clamped_dpi, gray)
                b64 = base64.b64encode(png).decode("ascii")
        except (ValueError, imaging.ImagingError) as exc:
            return [TextContent(type="text", text=_err(str(exc)))]

        meta = {
            "page": resolved_page,
            "bbox": list(resolved_bbox) if resolved_bbox else None,
            "dpi": clamped_dpi,
            "margin": margin if resolved_bbox else None,
            "grayscale": gray,
        }
        return [
            ImageContent(type="image", data=b64, mimeType="image/png"),
            TextContent(type="text", text=json.dumps(meta)),
        ]

    @mcp.tool()
    async def section(
        name: str,
        include_image: bool = False,
        dpi: int = 150,
    ) -> list[ImageContent | TextContent]:
        """Return one section's unexpanded source slice and its open section or
        source-range comments. Name matches a title or label. ``include_image``
        adds one crop from the page with the most SyncTeX matches, not an entire
        multi-page section.
        """
        import base64

        from . import imaging
        from .comments import SectionAnchor
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
        except OSError as exc:
            return [TextContent(type="text", text=_err(
                f"could not read section source {resolved.file}: {exc}"
            ))]

        # Area anchors have no source range and remain in the global queue.
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
            "comments": [_agent_comment_to_dict(c) for c in scoped],
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
                if pair is None:
                    results.append(TextContent(type="text", text=_err(
                        "section image requested but SyncTeX has no PDF coverage"
                    )))
                    return results
                page, bbox = pair
                try:
                    png = await asyncio.to_thread(
                        imaging.render_region,
                        pdf_path, page, bbox, _clamp_dpi(dpi),
                    )
                except imaging.ImagingError as exc:
                    results.append(TextContent(type="text", text=_err(str(exc))))
                    return results
                results.append(ImageContent(
                    type="image",
                    data=base64.b64encode(png).decode("ascii"),
                    mimeType="image/png",
                ))
            else:
                results.append(TextContent(type="text", text=_err(
                    "section image requested but no PDF exists"
                )))
                return results

        return results

    @mcp.tool()
    async def goto(target: Annotated[str, Field(min_length=1)]) -> str:
        """Scroll the running viewer to a section title, label, exact PDF quote,
        ``pN``, ``file:line``, or a line number in the main file. Positioned
        targets remain highlighted until the next pointer action.
        """
        try:
            import httpx
        except ImportError:
            return _err("httpx not installed; install tex-mcp-web[mcp]")

        cfg, _, _ = _load_project()
        body = parse_goto_target(target, default_file=cfg.main)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{daemon_port}/goto", json=body
                )
                return resp.text
        except Exception as exc:
            return _err(f"daemon at port {daemon_port} not reachable: {exc}")

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
