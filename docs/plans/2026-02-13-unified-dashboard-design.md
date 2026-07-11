# Unified Intelligence Dashboard

## Problem

texwatch has 17 MCP tools, 15+ CLI commands, 20+ API endpoints, and a web UI
with editor/viewer/file tree. The data pipeline is complete but fragmented
across surfaces. The primary pain point is context switching: bouncing between
terminal, browser, and editor to understand the paper's state.

## Target User

Solo power-users: local LaTeX, git, CLI-comfortable, Claude Code integration.
Not trying to replicate Overleaf's zero-setup model.

## Design

### Architecture

A sidebar dashboard in the existing web UI. Collapsible panels that surface
all intelligence texwatch already extracts, plus section-level change tracking.

Data flow (unchanged pipeline, new consumer):

    File change -> Watcher -> Compiler -> Parsers -> Server cache
                                                        |
                                              /dashboard endpoint
                                                        |
                                              Frontend panels render

Single aggregated `/dashboard` endpoint. Frontend makes one request instead of
six. WebSocket `dashboard_updated` event triggers re-fetch after each compile.

No new dependencies. Python difflib for diffs, vanilla JS for frontend.

### New Backend: `texwatch/changes.py`

Section-level change tracking.

- On each compile, diffs current content against last-compiled snapshot
- Diffs computed per-section: map sections to line ranges, `difflib.unified_diff`
- Produces `SectionDelta` objects:

```python
@dataclass
class SectionDelta:
    section_title: str
    section_file: str
    section_line: int
    lines_added: int
    lines_removed: int
    words_added: int
    words_removed: int
    is_dirty: bool          # changed since last compile
    diff_snippet: str       # first ~10 lines of unified diff
    timestamp: str          # ISO 8601
```

- Server stores `ChangeLog` (ring buffer, last ~50 deltas) per project
- Diffs computed at compile boundaries, not on every keystroke

### `/dashboard` Endpoint

```
GET /p/{name}/dashboard    (per-project)
GET /dashboard             (effective project)
```

Returns aggregated JSON:

```json
{
  "health": {
    "title": "...", "author": "...", "documentclass": "...",
    "word_count": 12450, "page_count": 42, "page_limit": null,
    "compile_status": "success", "last_compile": "...",
    "error_count": 0, "warning_count": 2
  },
  "sections": [
    {
      "title": "Introduction", "level": "section",
      "file": "intro.tex", "line": 5,
      "word_count": 1200, "citation_count": 8, "todo_count": 1,
      "figure_count": 1, "table_count": 0, "is_dirty": true
    }
  ],
  "issues": [
    {"type": "error", "message": "...", "file": "...", "line": 12},
    {"type": "undefined_citation", "key": "smith2024", "file": "...", "line": 45},
    {"type": "todo", "tag": "TODO", "text": "expand this", "file": "...", "line": 88}
  ],
  "bibliography": {
    "defined": 24, "cited": 21,
    "undefined_keys": ["smith2024"],
    "uncited_keys": ["old2019", "draft2023", "note2020"]
  },
  "changes": [
    {
      "section_title": "Introduction",
      "lines_added": 12, "lines_removed": 3,
      "words_added": 85, "words_removed": 20,
      "is_dirty": true,
      "diff_snippet": "@@ -5,3 +5,12 @@\n+New paragraph about...",
      "timestamp": "2026-02-13T14:28:00Z"
    }
  ],
  "environments": {
    "theorem": 4, "equation": 12, "figure": 3, "table": 2,
    "proof": 3, "lemma": 2,
    "items": [
      {"env_type": "theorem", "label": "thm:main", "name": "Main Result",
       "file": "main.tex", "start_line": 40}
    ]
  }
}
```

### Frontend

Layout change:

    Current:  [File Tree] [Editor] [PDF Viewer]
    New:      [Sidebar: Files|Dashboard tabs] [Editor] [PDF Viewer]

Both DOM trees stay mounted, tab switching via display toggle.

Six collapsible panels:

1. **Paper Health** -- title, author, word count, page count, compile status,
   summary line
2. **Section Map** -- hierarchical section list with word count, citation count,
   TODO count, figure/table count per row. Dirty markers (dot) on changed
   sections. Color intensity by word count.
3. **Issues** -- unified list: compile errors, undefined citations, uncited
   entries, TODOs. Grouped by severity (red/yellow/blue). Count badge on header.
4. **Bibliography Health** -- defined vs cited counts, undefined/uncited keys,
   red/green indicator.
5. **Recent Changes** -- timeline of edits grouped by section. Magnitude
   (lines changed), diff preview on click. "Since last compile" scope.
6. **Environments** -- collapsible by type with counts. Each item shows
   label, name/caption, file:line.

Every item is clickable -> navigates editor + PDF via existing goto mechanism.

Keyboard navigation (active when sidebar focused):

- `j`/`k` -- move highlight through items in active panel
- `Enter` -- navigate to highlighted item
- `Tab`/`Shift+Tab` -- cycle between panels
- `o` -- toggle panel open/closed

Styling: compact, information-dense, monospace for numbers, proportional for
titles. Existing CSS variables. Minimal diff coloring (green +, red -).

New files: `texwatch/static/dashboard.js`
Modified: `index.html`, `style.css`, `viewer.js`

No new JS dependencies.

### CLI Command

    texwatch dashboard [--json] [--project NAME] [--port PORT]

Human-readable output:

    My Paper (article) -- 12,450 words, 42 pages
    Compile: success (14:30:00)

    Sections:
      * Introduction      1,200 words  8 cites  1 TODO
        Methods            2,100 words  3 cites
      * Results            3,400 words 12 cites  2 figs
        Discussion         2,800 words  5 cites  1 TODO

    Issues (4):
      x undefined citation: smith2024 (intro.tex:45)
      ! uncited: old2019, draft2023, note2020
      - TODO: expand this (results.tex:88)

    Changes since last compile:
      Introduction: +85 words, -20 words
      Results: +142 words (new)

`--json` returns the raw dashboard JSON.

### MCP Tool

    texwatch_dashboard(port, project) -> GET /dashboard -> full JSON blob

One tool replaces calling structure + bibliography + environments + digest
separately. Claude Code gets the complete picture in a single call.

## Files

Created:
- `texwatch/changes.py`
- `texwatch/static/dashboard.js`
- `tests/test_changes.py`

Modified:
- `texwatch/server.py` -- dashboard endpoint + WS event
- `texwatch/mcp_server.py` -- dashboard tool
- `texwatch/cli.py` -- dashboard command
- `texwatch/static/index.html` -- tab switcher + dashboard container
- `texwatch/static/style.css` -- panel styles
- `texwatch/static/viewer.js` -- WS dashboard_updated handler
