# texwatch v0.3.0: Local Overleaf with Claude Code Integration

**Date:** 2026-03-25
**Status:** Approved

## Vision

texwatch becomes a local, Linux-native alternative to Overleaf where "local" is the superpower. Being local means full access to Claude Code (via MCP), the filesystem, git, and the Linux toolchain, things cloud Overleaf can never offer.

The architecture has three layers:
- **Web UI**: the best LaTeX editor it can be, standalone, no AI in the browser
- **Claude Code**: the AI layer, accessed via the user's Claude Code subscription
- **MCP**: a bidirectional awareness channel connecting Claude Code to the browser

## Principles

- **Claude Code is the brain, texwatch is the body.** AI lives in the terminal, not the browser.
- **MCP is eyes and hands.** It tells Claude Code what the human is doing, and lets Claude Code control the viewer. It is NOT a query/analysis engine. Claude Code can already read files and run git commands.
- **CLI stays lightweight.** `texwatch serve`, `texwatch init`, `texwatch status` and friends remain, but there's no pressure for CLI feature parity with MCP. New capabilities surface through MCP and the browser.
- **Git handles file history.** texwatch persists compilation metadata only.
- **Feature-driven decomposition.** Each new feature extracts its module from server.py as part of the work, not as upfront refactoring.

## Feature 1: Editor Enhancements

### Autocomplete Providers

Four CodeMirror completion sources, implemented in a new `static/autocomplete.js`:

1. **Command completion**: triggered on `\`. Static list of ~200 common LaTeX commands (sectioning, formatting, math, references). Shows command name + brief description.

2. **Environment completion**: triggered on `\begin{`. Static list of common environments. On accept, auto-inserts matching `\end{}`. Also completes `\end{` by matching the nearest open `\begin`.

3. **Citation completion**: triggered on `\cite{`, `\citep{`, `\citet{`, etc. Source: server's `/bibliography` endpoint, fetched on file load and cached. Shows key + title + author. Refreshes after recompile.

4. **Label/ref completion**: triggered on `\ref{`, `\eqref{`, `\autoref{`, etc. Source: new `/labels` endpoint. Shows label key + context (enclosing environment or section).

### Snippets

Implemented in a new `static/snippets.js` using CodeMirror's snippet extension:

Built-in snippets:
- `fig` expands to `\begin{figure}[htbp]\n\centering\n\includegraphics[width=\textwidth]{$1}\n\caption{$2}\n\label{fig:$3}\n\end{figure}`
- `tab` expands to table boilerplate with tabular
- `eq` expands to `\begin{equation}\n$1\n\label{eq:$2}\n\end{equation}`
- `sec` expands to `\section{$1}`

User-extensible via `snippets:` key in `.texwatch.yaml`:

```yaml
snippets:
  thm: "\\begin{theorem}\n$1\n\\end{theorem}"
  prf: "\\begin{proof}\n$1\n\\end{proof}"
```

Schema: each key is the trigger prefix, each value is the expansion string using CodeMirror `$1/$2/...` placeholders. The server delivers custom snippets to the browser via the existing `/config` endpoint (which already returns the parsed YAML). The `Config` dataclass gains an optional `snippets: dict[str, str]` field, defaulting to empty dict. `snippets.js` fetches `/config` on load, merges custom snippets with built-ins (custom overrides built-in on name collision).

### Bracket Matching

- CodeMirror's built-in `bracketMatching()` for `()`, `[]`, `{}`
- LaTeX-aware `\begin{}/\end{}` pair highlighting (custom extension)

### Server-Side Support

New `/labels` endpoint:
- Parses all `.tex` files for `\label{...}`
- Returns label key, file, line, and context (enclosing environment name or section title)
- Implementation in new `texwatch/labels.py`

Label context resolution: `labels.py` calls `parse_structure()` and `parse_environments()` from the existing modules to get section and environment data, then cross-references by file and line range to determine the enclosing context for each label. It does not duplicate parsing logic. The structure/environment results are cached on `ProjectInstance` (already computed for `/dashboard`), so `/labels` reads from the cache rather than re-parsing.

### JS Module Changes

- New `static/autocomplete.js`: four completion providers
- New `static/snippets.js`: snippet definitions and expansion
- Modified `static/editor.js`: loads autocomplete and snippet extensions

## Feature 2: Persistent Compilation History

### Database

SQLite database at `.texwatch/history.db`, created on first compile.

```sql
CREATE TABLE compiles (
    id            INTEGER PRIMARY KEY,
    project       TEXT NOT NULL,
    timestamp     TEXT NOT NULL,        -- ISO 8601
    success       BOOLEAN NOT NULL,
    duration_s    REAL,
    error_count   INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    word_count    INTEGER,
    page_count    INTEGER,
    main_file     TEXT NOT NULL
);

CREATE TABLE messages (
    id          INTEGER PRIMARY KEY,
    compile_id  INTEGER NOT NULL REFERENCES compiles(id),
    level       TEXT NOT NULL,        -- 'error' or 'warning'
    file        TEXT,
    line        INTEGER,
    message     TEXT NOT NULL
);
```

The `project` column stores `ProjectInstance.name`, which is the directory-derived project name used in route paths (`/p/{name}/`). For single-project mode this is the directory basename. This value is stable across server restarts for the same directory.

### Module

New `texwatch/persistence.py`:
- Owns SQLite connection, schema creation/migration, query methods
- `record_compile(project, result)`: called by `ProjectInstance.do_compile()`
- `query_compiles(project, since, limit, success)`: returns compile history
- `get_compile_messages(compile_id)`: returns errors/warnings for a compile

### HTTP API Changes

- New `/compiles` endpoint: returns compilation timeline with filtering (`?since=`, `?limit=`, `?success=`)
- Rename existing `/history/{file}` to `/snapshots/{file}` to avoid collision

### MCP Changes

- `texwatch` dashboard response gains `recent_compiles` field (last 5)
- New `texwatch_compiles` tool: queries SQLite compile timeline. Parameters: `since`, `limit`, `success_only`. Returns list of compile records with error/warning counts, duration, word count.
- `texwatch_history` is **removed** (file snapshots are accessible via Claude Code reading files directly, or via the `/snapshots/{file}` HTTP endpoint if needed; they do not need a dedicated MCP tool since Claude Code can already read files)

## Feature 3: Bidirectional MCP Context Channel

### Direction 1: Viewer to Server (User Focus)

The browser reports user activity to the server via WebSocket. New message types:

```json
{"type": "focus", "file": "methods.tex", "line": 47, "column": 12}
{"type": "selection", "file": "methods.tex", "start": {"line": 43, "col": 0}, "end": {"line": 51, "col": 0}}
{"type": "visible_lines", "file": "methods.tex", "start": 35, "end": 65}
{"type": "pdf_viewport", "page": 4, "scroll_y": 0.35}
```

Debounced at 200-300ms. Server stores latest state on `ProjectInstance.user_focus` as a `UserFocus` dataclass (defined in `awareness.py`).

**UserFocus dataclass fields:**
- `file: str | None` (currently open file)
- `cursor: tuple[int, int] | None` (line, column)
- `selection_start: tuple[int, int] | None` (line, col)
- `selection_end: tuple[int, int] | None` (line, col)
- `visible_lines: tuple[int, int] | None` (start, end)
- `pdf_page: int | None`
- `pdf_scroll_y: float | None`
- `timestamp: str` (ISO 8601, updated on every message)
- `ws_connected: bool` (set to False on WebSocket disconnect)

All fields except `timestamp` and `ws_connected` are nullable. Missing or malformed fields in WebSocket messages are ignored (the field retains its previous value). Integer fields (`line`, `column`, `page`) reject non-integer values silently.

**Staleness:** When the WebSocket disconnects, `ws_connected` is set to False and `timestamp` is updated. The MCP response includes both fields, so Claude Code can distinguish "user is looking at line 47 right now" from "user was last seen at line 47 before they closed the tab." No TTL-based expiration; the `ws_connected` flag is the signal.

### Direction 2: Server to MCP (Context Reporting)

The `texwatch` dashboard MCP tool response gains:

```json
{
  "user_focus": {
    "file": "methods.tex",
    "cursor": {"line": 47, "col": 12},
    "selection": null,
    "visible_lines": [35, 65],
    "pdf_page": 4,
    "ws_connected": true,
    "timestamp": "2026-03-25T14:23:01Z"
  }
}
```

Null fields are omitted from the response to keep it compact.

### Direction 3: MCP to Viewer (Claude Code Controls)

Two new MCP tools for visual feedback:

**`texwatch_highlight`**: highlight line ranges in the editor.
- Parameters: `file` (str, required), `ranges` (list of `{start: int, end: int, color: str}`, required). Color options: "yellow", "red", "green", "blue".
- Clears previous highlights on the same file before applying new ones.
- Highlights clear automatically on next user edit to that file.
- To clear all highlights: pass an empty `ranges` list.
- Posts to `/highlight` endpoint, which broadcasts via WebSocket to the browser.

**`texwatch_annotate`**: add gutter annotations in the editor.
- Parameters: `file` (str, required), `annotations` (list of `{line: int, type: str, text: str}`, required). Type options: "error", "warning", "info".
- Rendered as gutter icons with hover tooltips.
- Annotations clear automatically on next successful recompile.
- To clear all annotations: pass an empty `annotations` list.
- Posts to `/annotate` endpoint, which broadcasts via WebSocket to the browser.

Extended `texwatch_goto`:
- The MCP tool signature gains an optional `file: str | None = None` parameter. When provided, the POST to `/goto` includes the `file` field. The server-side handler already supports this field (see `server.py` `_handle_goto`), so no server change is needed.

### Direction 4: Visual Context (Screenshots)

Enhanced `texwatch_capture` MCP tool with new mode parameter:
- **Default mode** (no mode parameter): existing behavior, captures a specific page by number.
- **Viewport mode** (`mode: "viewport"`): captures the page currently visible to the user, determined from `user_focus.pdf_page`. If `user_focus` is stale (`ws_connected: false`), falls back to page 1.
- **Region mode** (`mode: "region"`, `page: int`, `bbox: [x, y, w, h]`): captures a cropped area of the specified page. Coordinates are in PDF points.

All capture modes use the existing capture infrastructure in `server.py`'s `_handle_capture` method. The `awareness.py` module handles only the viewport/region *parameter resolution* (determining which page and crop from user_focus state), not the actual PDF rendering. pymupdf remains the renderer; if not installed, capture returns an error as it does today.

**Dashboard screenshot**: the `texwatch` dashboard MCP tool accepts an optional `include_screenshot: bool = False` parameter. When true and pymupdf is available, the response includes a viewport screenshot as a base64-encoded PNG alongside the JSON dashboard data. The MCP tool returns `list[TextContent, ImageContent]` when screenshot is included, or `list[TextContent]` when not. Default is off, so dashboard calls remain fast.

### Module

New `texwatch/awareness.py`:
- `UserFocus` dataclass: all fields described above
- `HighlightState` dataclass: active highlights per file (dict of file to list of ranges)
- `AnnotationState` dataclass: active annotations per file (dict of file to list of annotations)
- `update_focus(msg: dict)`: validates and updates UserFocus from WebSocket message
- `on_ws_disconnect()`: sets `ws_connected = False`
- `set_highlights(file, ranges)`: stores highlight state, returns WebSocket broadcast payload
- `set_annotations(file, annotations)`: stores annotation state, returns WebSocket broadcast payload
- `resolve_viewport_capture(user_focus)`: returns page number and optional bbox for capture

Screenshot rendering stays in `server.py`'s existing `_handle_capture`. `awareness.py` does not import pymupdf or duplicate rendering logic.

New `static/awareness.js`:
- Listens to CodeMirror cursor/selection events, sends focus WebSocket messages
- Listens to PDF.js scroll/page events, sends viewport messages
- Receives highlight commands via WebSocket, renders CodeMirror line decorations
- Receives annotation commands via WebSocket, renders gutter markers with tooltips
- Exposes `clearHighlights(file)` and `clearAnnotations(file)` for internal use

## Module Structure Summary

### New Python Modules
| Module | Purpose |
|--------|---------|
| `texwatch/persistence.py` | SQLite compile history |
| `texwatch/awareness.py` | UserFocus, highlights, annotations, viewport resolution |
| `texwatch/labels.py` | `\label` parsing for ref completion (depends on structure.py, environments.py) |

### New JavaScript Modules
| Module | Purpose |
|--------|---------|
| `static/autocomplete.js` | 4 completion providers |
| `static/snippets.js` | Snippet definitions + expansion |
| `static/awareness.js` | Focus reporting, highlight/annotation rendering |

### Modified Modules
| Module | Change |
|--------|--------|
| `server.py` | Delegates to persistence, awareness, labels; adds `/compiles`, `/labels`, `/highlight`, `/annotate` endpoints; renames `/history/{file}` to `/snapshots/{file}` |
| `mcp_server.py` | Adds `texwatch_compiles`, `texwatch_highlight`, `texwatch_annotate`; removes `texwatch_history`; extends `texwatch_capture` with mode param; extends `texwatch_goto` with file param; adds `include_screenshot` and `user_focus` to dashboard |
| `cli.py` | No changes in this release |
| `editor.js` | Loads autocomplete, snippets, awareness extensions |
| `config.py` | Adds optional `snippets: dict[str, str]` field to Config |

### MCP Tools (10 total)
| Tool | Status |
|------|--------|
| `texwatch` | Modified: gains `user_focus`, `recent_compiles`, optional `include_screenshot` param |
| `texwatch_source` | Unchanged |
| `texwatch_write_source` | Unchanged |
| `texwatch_goto` | Extended: accepts optional `file` parameter (MCP-layer only) |
| `texwatch_compile` | Unchanged |
| `texwatch_capture` | Extended: `mode` parameter (default, viewport, region) |
| `texwatch_project` | Unchanged |
| `texwatch_compiles` | **New**: query SQLite compile timeline |
| `texwatch_highlight` | **New**: highlight line ranges in editor |
| `texwatch_annotate` | **New**: gutter annotations in editor |

Note: `texwatch_history` is removed. File snapshots remain accessible via `/snapshots/{file}` HTTP endpoint but do not need a dedicated MCP tool since Claude Code can read files directly.

## Build Sequence

Features are built in order, each extracting its module:

1. **Editor enhancements**: `labels.py`, `autocomplete.js`, `snippets.js`, editor.js modifications, Config snippets field
2. **Persistent compilation history**: `persistence.py`, `/compiles` endpoint, `texwatch_compiles` MCP tool, `texwatch_history` removal
3. **Bidirectional MCP context**: `awareness.py`, `awareness.js`, `texwatch_highlight`, `texwatch_annotate`, capture mode extension, dashboard `user_focus` + `include_screenshot`

## Not In Scope

- Inline math preview (SyncTeX + PDF pane handles this)
- Git UI in the browser (git-aware via MCP, controls stay in terminal)
- Session state persistence (cursor position, open files don't survive restart)
- AI in the web UI (Claude Code is the AI layer)
- Ambient/proactive suggestions (on-demand via Claude Code only)
- Citation manager UI (Claude Code can search and generate BibTeX via web search)
- CLI parity with MCP (CLI is lightweight convenience)
