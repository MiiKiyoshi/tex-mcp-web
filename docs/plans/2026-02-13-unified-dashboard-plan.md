# Unified Intelligence Dashboard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a unified dashboard that surfaces all paper state (structure, bibliography, environments, digest, change tracking) in one sidebar view, eliminating context-switching.

**Architecture:** New `changes.py` module for section-level diffs. Single `/dashboard` endpoint aggregates all parsers. Frontend adds tabbed sidebar with 6 collapsible panels, keyboard-navigable. New MCP tool and CLI command.

**Tech Stack:** Python difflib for diffs, vanilla JS for frontend, existing aiohttp server, existing parser modules.

**Design doc:** `docs/plans/2026-02-13-unified-dashboard-design.md`

**Security note:** Frontend JS must use safe DOM construction (createElement/textContent) for all user-provided content. No raw string interpolation into HTML.

---

### Task 1: Create `texwatch/changes.py` — Data Classes

**Files:**
- Create: `texwatch/changes.py`
- Create: `tests/test_changes.py`

**Step 1: Write failing test for SectionDelta and ChangeLog data classes**

```python
# tests/test_changes.py
"""Tests for section-level change tracking."""

from texwatch.changes import SectionDelta, ChangeLog


class TestSectionDelta:
    """Tests for SectionDelta data class."""

    def test_create_delta(self):
        delta = SectionDelta(
            section_title="Introduction",
            section_file="intro.tex",
            section_line=5,
            lines_added=10,
            lines_removed=3,
            words_added=85,
            words_removed=20,
            is_dirty=True,
            diff_snippet="@@ -5,3 +5,12 @@\n+New paragraph",
            timestamp="2026-02-13T14:28:00Z",
        )
        assert delta.section_title == "Introduction"
        assert delta.is_dirty is True


class TestChangeLog:
    """Tests for ChangeLog ring buffer."""

    def test_create_empty(self):
        log = ChangeLog()
        assert log.deltas == []
        assert log.last_compiled_snapshots == {}

    def test_add_deltas(self):
        log = ChangeLog()
        delta = SectionDelta(
            section_title="Intro", section_file="main.tex",
            section_line=1, lines_added=5, lines_removed=0,
            words_added=30, words_removed=0, is_dirty=True,
            diff_snippet="", timestamp="2026-02-13T00:00:00Z",
        )
        log.record([delta])
        assert len(log.deltas) == 1

    def test_ring_buffer_limit(self):
        log = ChangeLog(maxlen=3)
        for i in range(5):
            log.record([SectionDelta(
                section_title=f"S{i}", section_file="main.tex",
                section_line=i, lines_added=1, lines_removed=0,
                words_added=5, words_removed=0, is_dirty=True,
                diff_snippet="", timestamp=f"2026-02-13T00:0{i}:00Z",
            )])
        assert len(log.deltas) == 3
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_changes.py -v`
Expected: ImportError — `texwatch.changes` does not exist

**Step 3: Write minimal implementation**

```python
# texwatch/changes.py
"""Section-level change tracking for LaTeX documents.

Computes diffs between file snapshots at compile boundaries to identify
which sections changed and by how much.
"""

import logging
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SectionDelta:
    """Change summary for a single section between two compile snapshots.

    Attributes:
        section_title: Title of the section.
        section_file: File containing the section.
        section_line: Line number of the section heading.
        lines_added: Number of lines added.
        lines_removed: Number of lines removed.
        words_added: Approximate words added.
        words_removed: Approximate words removed.
        is_dirty: True if the section changed since last compile.
        diff_snippet: First ~10 lines of unified diff.
        timestamp: ISO 8601 timestamp of when the diff was computed.
    """

    section_title: str
    section_file: str
    section_line: int
    lines_added: int
    lines_removed: int
    words_added: int
    words_removed: int
    is_dirty: bool
    diff_snippet: str
    timestamp: str


class ChangeLog:
    """Ring buffer of SectionDelta batches.

    Each compile produces a batch of deltas (one per changed section).
    The buffer retains the last *maxlen* individual deltas.
    """

    def __init__(self, maxlen: int = 50):
        self._deltas: deque[SectionDelta] = deque(maxlen=maxlen)
        self.last_compiled_snapshots: dict[str, str] = {}

    @property
    def deltas(self) -> list[SectionDelta]:
        return list(self._deltas)

    def record(self, deltas: list[SectionDelta]) -> None:
        """Append a batch of deltas from a compile cycle."""
        for d in deltas:
            self._deltas.append(d)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_changes.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add texwatch/changes.py tests/test_changes.py
git commit -m "Add changes.py: SectionDelta and ChangeLog data classes"
```

---

### Task 2: Create `compute_changes()` — Section-Level Diffing

**Files:**
- Modify: `texwatch/changes.py`
- Modify: `tests/test_changes.py`

**Step 1: Write failing tests for compute_changes**

```python
# Append to tests/test_changes.py

from texwatch.changes import compute_changes
from texwatch.structure import Section


class TestComputeChanges:
    """Tests for compute_changes diffing function."""

    def test_no_changes(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nHello world.\n"}
        new = {"main.tex": "\\section{Intro}\nHello world.\n"}
        deltas = compute_changes(sections, old, new)
        assert len(deltas) == 1
        assert deltas[0].is_dirty is False
        assert deltas[0].lines_added == 0

    def test_added_lines(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nLine one.\n"}
        new = {"main.tex": "\\section{Intro}\nLine one.\nLine two.\nLine three.\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].is_dirty is True
        assert deltas[0].lines_added == 2
        assert deltas[0].words_added >= 4

    def test_removed_lines(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nLine one.\nLine two.\n"}
        new = {"main.tex": "\\section{Intro}\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].is_dirty is True
        assert deltas[0].lines_removed == 2

    def test_multiple_sections(self):
        sections = [
            Section(level="section", title="Intro", file="main.tex", line=1),
            Section(level="section", title="Methods", file="main.tex", line=4),
        ]
        old = {"main.tex": "\\section{Intro}\nOld intro.\n\n\\section{Methods}\nOld methods.\n"}
        new = {"main.tex": "\\section{Intro}\nNew intro.\n\n\\section{Methods}\nOld methods.\n"}
        deltas = compute_changes(sections, old, new)
        intro = [d for d in deltas if d.section_title == "Intro"][0]
        methods = [d for d in deltas if d.section_title == "Methods"][0]
        assert intro.is_dirty is True
        assert methods.is_dirty is False

    def test_diff_snippet_present(self):
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        old = {"main.tex": "\\section{Intro}\nOld text.\n"}
        new = {"main.tex": "\\section{Intro}\nNew text.\n"}
        deltas = compute_changes(sections, old, new)
        assert deltas[0].diff_snippet != ""

    def test_new_file_not_in_old(self):
        sections = [Section(level="section", title="Intro", file="new.tex", line=1)]
        deltas = compute_changes(sections, {}, {"new.tex": "\\section{Intro}\nNew.\n"})
        assert deltas[0].is_dirty is True

    def test_empty_sections(self):
        assert compute_changes([], {}, {}) == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_changes.py::TestComputeChanges -v`
Expected: ImportError — `compute_changes` not defined

**Step 3: Write implementation**

Add to `texwatch/changes.py`:

```python
import difflib
import re

from .structure import Section

_WORD_RE = re.compile(r"(?<!\\)\b[a-zA-Z]{2,}\b")
_MAX_SNIPPET_LINES = 10


def _section_line_range(
    sec_idx: int, sections: list[Section], file_lines: int,
) -> tuple[int, int]:
    """Return (start, end) 0-indexed line range for a section."""
    sec = sections[sec_idx]
    start = sec.line - 1
    end = file_lines
    for j in range(sec_idx + 1, len(sections)):
        if sections[j].file == sec.file:
            end = sections[j].line - 1
            break
    return start, end


def _count_words(lines: list[str]) -> int:
    """Heuristic word count for a list of lines."""
    return sum(len(_WORD_RE.findall(line)) for line in lines)


def compute_changes(
    sections: list[Section],
    old_contents: dict[str, str],
    new_contents: dict[str, str],
    timestamp: str = "",
) -> list[SectionDelta]:
    """Compute section-level diffs between old and new file contents."""
    if not sections:
        return []
    deltas: list[SectionDelta] = []
    for idx, sec in enumerate(sections):
        old_text = old_contents.get(sec.file, "")
        new_text = new_contents.get(sec.file, "")
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        _, new_end = _section_line_range(idx, sections, len(new_lines))
        new_slice = new_lines[sec.line - 1 : new_end]
        old_end = min(new_end, len(old_lines))
        old_start = min(sec.line - 1, len(old_lines))
        old_slice = old_lines[old_start:old_end]
        diff_lines = list(difflib.unified_diff(
            old_slice, new_slice,
            fromfile=f"a/{sec.file}", tofile=f"b/{sec.file}", lineterm="",
        ))
        lines_added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        lines_removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        added_words = _count_words([l[1:] for l in diff_lines if l.startswith("+") and not l.startswith("+++")])
        removed_words = _count_words([l[1:] for l in diff_lines if l.startswith("-") and not l.startswith("---")])
        is_dirty = lines_added > 0 or lines_removed > 0
        snippet = "\n".join(diff_lines[:_MAX_SNIPPET_LINES]) if diff_lines else ""
        deltas.append(SectionDelta(
            section_title=sec.title, section_file=sec.file, section_line=sec.line,
            lines_added=lines_added, lines_removed=lines_removed,
            words_added=added_words, words_removed=removed_words,
            is_dirty=is_dirty, diff_snippet=snippet, timestamp=timestamp,
        ))
    return deltas
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_changes.py -v`
Expected: All passed

**Step 5: Commit**

```bash
git add texwatch/changes.py tests/test_changes.py
git commit -m "Add compute_changes: section-level diffing with difflib"
```

---

### Task 3: Add `/dashboard` Endpoint — Server

**Files:**
- Modify: `texwatch/server.py` (import, builder, routes, WS event)
- Modify: `tests/test_server.py`

**Step 1: Write failing test**

```python
# Append to tests/test_server.py

class TestDashboardEndpoint:
    """Tests for GET /dashboard."""

    async def test_dashboard_returns_all_sections(self, cli, project_dir):
        main = project_dir / "main.tex"
        main.write_text(
            "\\documentclass{article}\n\\title{Test}\n"
            "\\begin{document}\n\\section{Intro}\nHello.\n\\end{document}\n"
        )
        resp = await cli.get("/dashboard")
        assert resp.status == 200
        data = await resp.json()
        for key in ("health", "sections", "issues", "bibliography", "changes", "environments"):
            assert key in data

    async def test_dashboard_health_fields(self, cli, project_dir):
        main = project_dir / "main.tex"
        main.write_text("\\documentclass{article}\n\\title{Test Paper}\n\\begin{document}\n\\end{document}\n")
        resp = await cli.get("/dashboard")
        data = await resp.json()
        health = data["health"]
        assert health["title"] == "Test Paper"
        assert health["documentclass"] == "article"
        assert "compile_status" in health
        assert "error_count" in health

    async def test_dashboard_per_project(self, cli, project_dir):
        main = project_dir / "main.tex"
        main.write_text("\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
        resp = await cli.get("/p/test/dashboard")
        assert resp.status == 200
```

**Step 2: Run test to verify it fails** (404 — route does not exist)

**Step 3: Write implementation**

In `texwatch/server.py`:

1. Add import: `from .changes import ChangeLog, compute_changes`
2. Add `self.change_log = ChangeLog()` to ProjectInstance.__init__
3. Add `_build_dashboard_response(self, proj)` method — aggregates all parsers into single JSON response. Uses `parse_structure`, `parse_bibliography`, `parse_environments`, `parse_digest` with exception fallbacks. Builds health, sections (with dirty markers from change_log), issues (errors + undefined citations + TODOs), bibliography summary, changes, environments.
4. Add routes: `self.app.router.add_get(r"/p/{name:.+}/dashboard", self._handle_project_dashboard)` and `self.app.router.add_get("/dashboard", self._handle_root_dashboard)`
5. Add handlers following existing pattern (see bibliography handler)
6. Add `await proj.broadcast({"type": "dashboard_updated"})` after compile completes

See design doc for full `/dashboard` JSON response schema.

**Step 4: Run tests**

Run: `python -m pytest tests/test_server.py::TestDashboardEndpoint -v && python -m pytest tests/ -q`

**Step 5: Commit**

```bash
git add texwatch/server.py tests/test_server.py
git commit -m "Add /dashboard endpoint: aggregated paper state"
```

---

### Task 4: Integrate Change Tracking into Compile Cycle

**Files:**
- Modify: `texwatch/server.py` (in `do_compile` method)
- Modify: `tests/test_changes.py`

**Step 1: Write test for snapshot-and-diff cycle**

```python
class TestChangeLogIntegration:
    def test_snapshot_and_diff_cycle(self):
        log = ChangeLog()
        sections = [Section(level="section", title="Intro", file="main.tex", line=1)]
        new_contents = {"main.tex": "\\section{Intro}\nHello.\n"}
        deltas = compute_changes(sections, log.last_compiled_snapshots, new_contents)
        log.record(deltas)
        log.last_compiled_snapshots = dict(new_contents)
        assert deltas[0].is_dirty is True
        # Same content -> not dirty
        deltas2 = compute_changes(sections, log.last_compiled_snapshots, new_contents)
        assert deltas2[0].is_dirty is False
```

**Step 2: Wire into server's do_compile method**

After compilation completes, read current file contents, call `compute_changes`, record deltas, update snapshots. Wrap in try/except for resilience.

**Step 3: Run full test suite**

**Step 4: Commit**

```bash
git add texwatch/server.py tests/test_changes.py
git commit -m "Wire change tracking into compile cycle"
```

---

### Task 5: Add MCP Tool — `texwatch_dashboard`

**Files:**
- Modify: `texwatch/mcp_server.py`
- Modify: `tests/test_mcp.py`

**Step 1: Write test (adapt to existing test harness)**

**Step 2: Add tool**

```python
@mcp.tool()
async def texwatch_dashboard(port: int = 8765, project: str | None = None) -> str:
    """Get unified paper dashboard: health, sections, issues, bibliography, changes, and environments."""
    return await _get("/dashboard", port, project)
```

**Step 3: Run tests, commit**

```bash
git add texwatch/mcp_server.py tests/test_mcp.py
git commit -m "Add texwatch_dashboard MCP tool"
```

---

### Task 6: Add CLI Command — `texwatch dashboard`

**Files:**
- Modify: `texwatch/cli.py`
- Modify: `tests/test_cli.py`

**Step 1: Write test (adapt to existing test harness)**

**Step 2: Add `cmd_dashboard` function**

Uses `_fetch_endpoint("/dashboard", "dashboard", args)`. Formats human-readable output:
- Health summary line (title, class, words, pages)
- Section table with dirty markers, word counts, citation counts
- Issues list grouped by severity
- Bibliography summary
- Changes since last compile

**Step 3: Add subparser and dispatch entry**

```python
p_dashboard = subparsers.add_parser("dashboard", help="Show unified paper dashboard")
_add_server_options(p_dashboard)
```

Add `"dashboard": cmd_dashboard` to `_DISPATCH`.

**Step 4: Run tests, commit**

```bash
git add texwatch/cli.py tests/test_cli.py
git commit -m "Add texwatch dashboard CLI command"
```

---

### Task 7: Frontend — HTML Tab Switcher & Dashboard Container

**Files:**
- Modify: `texwatch/static/index.html`

Replace file-tree-pane header (currently just "Files" span + toggle button) with a tabbed structure:
- Two tab buttons: Files | Dashboard
- tree-container (existing, shown by default)
- dashboard-container (new, hidden by default) with 6 `<details>` panels:
  - panel-health, panel-sections, panel-issues, panel-bibliography, panel-changes, panel-environments
- Each panel has a `<summary>` with badge span and a `.panel-content` div

Add `<script src="/static/dashboard.js"></script>` before the other scripts.

**Commit:**

```bash
git add texwatch/static/index.html
git commit -m "Add sidebar tab switcher and dashboard panel containers"
```

---

### Task 8: Frontend — Dashboard CSS

**Files:**
- Modify: `texwatch/static/style.css`

Add styles for:
- `#sidebar-tabs` — flex row with tab buttons
- `.sidebar-tab` — tab button styling (active state, hover)
- `#dashboard-container` — scrollable panel area
- `.dashboard-panel` — collapsible panel with border
- `.badge` — count badges (with .error, .warning, .info variants)
- `.section-row` — clickable section with dirty marker, title, stats
- `.issue-row` — clickable issue with icon, text, location
- `.change-row` — change entry with diff stats and snippet
- `.env-chip` — environment type count chip
- `.env-item` — clickable environment entry
- `.diff-snippet` — monospace diff display with colored +/- lines
- `.health-summary` — label/value pairs for health panel

Color palette: use existing CSS variables, plus red/green/yellow/blue for severity.

**Commit:**

```bash
git add texwatch/static/style.css
git commit -m "Add dashboard panel CSS styles"
```

---

### Task 9: Frontend — `dashboard.js` Panel Rendering

**Files:**
- Create: `texwatch/static/dashboard.js`

**Security requirement:** Use `document.createElement` + `textContent` for all user-provided content. No string interpolation into HTML. Use a DOM builder pattern.

Structure:
- `initTabs()` — tab switching between Files and Dashboard views
- `fetchDashboard()` — GET `/dashboard`, call renderAll()
- `renderHealth(data)` — title, words, pages, compile status, error/warning badges
- `renderSections(data)` — section rows with dirty dot, title, stats. Click -> gotoFileLine
- `renderIssues(data)` — issue rows with icon, text, location. Click -> gotoFileLine
- `renderBibliography(data)` — defined/cited counts, undefined/uncited keys
- `renderChanges(data)` — dirty section changes with word +/- stats, diff snippet
- `renderEnvironments(data)` — env type chips with counts, expandable item list
- `gotoFileLine(file, line)` — dispatch `texwatch:goto-line` custom event
- Keyboard navigation: j/k/Enter/Tab/o when sidebar focused. Track highlighted panel/item.
- `init()` — set up tabs, keyboard handler, listen for `texwatch:dashboard-updated` event

**Commit:**

```bash
git add texwatch/static/dashboard.js
git commit -m "Add dashboard.js: panel rendering and keyboard navigation"
```

---

### Task 10: Frontend — Wire WebSocket `dashboard_updated` Event

**Files:**
- Modify: `texwatch/static/viewer.js`

In `handleMessage(data)` switch statement, add:

```javascript
case 'dashboard_updated':
    window.dispatchEvent(new Event('texwatch:dashboard-updated'));
    break;
```

**Commit:**

```bash
git add texwatch/static/viewer.js
git commit -m "Wire dashboard_updated WebSocket event to frontend"
```

---

### Task 11: Run Full Test Suite & Verify

**Step 1:** `python -m pytest tests/ --cov=texwatch --cov-report=term-missing -q`
**Step 2:** `python -m mypy texwatch/ --ignore-missing-imports`
**Step 3:** `python -m ruff check texwatch/`
**Step 4:** Manual test with real LaTeX project — verify all panels, navigation, keyboard, auto-refresh

---

## File Summary

| Action | File | Task |
|--------|------|------|
| Create | `texwatch/changes.py` | 1-2 |
| Create | `tests/test_changes.py` | 1-2, 4 |
| Create | `texwatch/static/dashboard.js` | 9 |
| Modify | `texwatch/server.py` | 3-4 |
| Modify | `texwatch/mcp_server.py` | 5 |
| Modify | `texwatch/cli.py` | 6 |
| Modify | `texwatch/static/index.html` | 7 |
| Modify | `texwatch/static/style.css` | 8 |
| Modify | `texwatch/static/viewer.js` | 10 |
| Modify | `tests/test_server.py` | 3 |
| Modify | `tests/test_mcp.py` | 5 |
| Modify | `tests/test_cli.py` | 6 |
