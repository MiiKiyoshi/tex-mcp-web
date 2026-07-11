# MCP Consolidation & Auto-Registration Design

**Goal:** Make texwatch's MCP integration effortless and efficient — auto-register
on startup, consolidate 18 tools down to 8, and simplify the CLI by redirecting
analysis commands through the dashboard.

**Architecture:** The existing `/dashboard` endpoint becomes the single source of
truth for paper state by absorbing context, files, and activity data. The MCP
tool surface shrinks from 18 to 8 by collapsing all read-state tools into one
unified `texwatch` tool. CLI analysis commands become thin wrappers around
`dashboard --section`.

---

## 1. Auto-register MCP server

When `texwatch serve` starts, it writes itself into `.mcp.json` in the project
root so Claude Code discovers the tools automatically.

### Behavior

- **Startup:** Read existing `.mcp.json` (or `{}`), add/update the `"texwatch"`
  key, write back. The entry points to `texwatch mcp --port {port}`.
- **Shutdown:** Best-effort removal of the `"texwatch"` key. Not critical — Claude
  Code handles missing servers gracefully.
- **`--no-mcp` flag:** Skip registration entirely.
- **Git:** `.mcp.json` is typically gitignored, so this is invisible to VCS.

### Implementation

- New `_register_mcp(port, project_dir)` in `server.py`, called during startup.
- Corresponding `_unregister_mcp(project_dir)` on shutdown.
- The `.mcp.json` entry:

```json
{
  "mcpServers": {
    "texwatch": {
      "command": "texwatch",
      "args": ["mcp", "--port", "8765"]
    }
  }
}
```

---

## 2. Consolidate MCP tools (18 → 8)

### Enhanced `/dashboard` endpoint

The existing `/dashboard` response gains three new top-level fields:

```json
{
  "health": { ... },
  "sections": [ ... ],
  "issues": [ ... ],
  "bibliography": { ... },
  "changes": [ ... ],
  "environments": { ... },
  "context": {
    "editor": { "file": "main.tex", "line": 42, "section": "Introduction" },
    "viewer": { "page": 3, "total_pages": 12, "visible_lines": [80, 120] }
  },
  "files": [
    { "name": "main.tex", "type": "file", "path": "main.tex" }
  ],
  "activity": [
    { "type": "compile_finish", "timestamp": "...", "success": true }
  ]
}
```

- `context` comes from the existing `/context` endpoint data.
- `files` comes from the existing `/files` endpoint data.
- `activity` is the last 10 events from the existing `/activity` endpoint.

### Final tool set

| Tool | Purpose | Method |
|------|---------|--------|
| `texwatch` | Full paper state (enhanced dashboard) | GET /dashboard |
| `texwatch_source` | Read a source file | GET /source |
| `texwatch_history` | File version history | GET /history/{file} |
| `texwatch_goto` | Navigate to line/page/section | POST /goto |
| `texwatch_compile` | Trigger recompile | POST /compile |
| `texwatch_write_source` | Edit a source file | POST /source |
| `texwatch_capture` | Screenshot a PDF page | GET /capture |
| `texwatch_project` | Show/switch current project | GET/POST /current |

### What gets removed from `mcp_server.py`

The following MCP tool definitions are deleted (their HTTP endpoints remain for
the web UI):

- `texwatch_status` (subsumed by `texwatch` → health)
- `texwatch_context` (subsumed by `texwatch` → context)
- `texwatch_errors` (subsumed by `texwatch` → issues)
- `texwatch_structure` (subsumed by `texwatch` → sections)
- `texwatch_activity` (subsumed by `texwatch` → activity)
- `texwatch_bibliography` (subsumed by `texwatch` → bibliography)
- `texwatch_environments` (subsumed by `texwatch` → environments)
- `texwatch_digest` (subsumed by `texwatch` → health)
- `texwatch_dashboard` (renamed to `texwatch`)
- `texwatch_files` (subsumed by `texwatch` → files)
- `texwatch_current` (merged into `texwatch_project`)
- `texwatch_switch` (merged into `texwatch_project`)

### `texwatch_project` merges current + switch

- Call with no args → returns current project + available list (GET /current)
- Call with `project` arg → switches to that project (POST /current)
- Call with `project: null` → clears current project

---

## 3. Collapse CLI analysis commands

### Redirect pattern

The three granular analysis commands become aliases for `texwatch dashboard`
with a section filter:

- `texwatch bibliography` → `texwatch dashboard --section bibliography`
- `texwatch environments` → `texwatch dashboard --section environments`
- `texwatch digest` → `texwatch dashboard --section health`

### `--section` filter on dashboard

`texwatch dashboard` gains an optional `--section` argument that filters the
response to a single section. Valid values: `health`, `sections`, `issues`,
`bibliography`, `changes`, `environments`.

When `--section` is used, only that section's data is returned (both in
human-readable and JSON output).

### What stays unchanged

- HTTP endpoints `/bibliography`, `/environments`, `/digest` remain (web UI)
- No commands are removed — they redirect silently
- All other CLI commands (goto, capture, config, status, view, etc.) unchanged

---

## Files to modify

| Action | File | What |
|--------|------|------|
| Modify | `texwatch/server.py` | Add `_register_mcp`/`_unregister_mcp`, enrich `/dashboard` with context/files/activity, add `--no-mcp` flag |
| Modify | `texwatch/mcp_server.py` | Remove 10 tool definitions, rename `texwatch_dashboard` → `texwatch`, merge current/switch → `texwatch_project` |
| Modify | `texwatch/cli.py` | Add `--section` to dashboard, redirect bibliography/environments/digest, add `--no-mcp` to serve |
| Modify | `tests/test_server.py` | Test enriched dashboard, MCP registration |
| Modify | `tests/test_mcp.py` | Update for consolidated tools |
| Modify | `tests/test_cli.py` | Test --section filter, redirect aliases |

## Verification

```bash
python -m pytest tests/ --cov=texwatch --cov-report=term-missing -q
python -m mypy texwatch/ --ignore-missing-imports
python -m ruff check texwatch/
```

Manual test: start `texwatch serve`, verify `.mcp.json` is written, restart
Claude Code, confirm `texwatch` tool appears and returns full paper state.
