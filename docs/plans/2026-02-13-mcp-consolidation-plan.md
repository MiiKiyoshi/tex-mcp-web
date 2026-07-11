# MCP Consolidation & Auto-Registration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Auto-register the texwatch MCP server on startup, consolidate 18 MCP tools down to 8, and redirect CLI analysis commands through the dashboard.

**Architecture:** The `/dashboard` endpoint absorbs context, files, and activity data to become the single read-state surface. `mcp_server.py` drops 10 tool definitions and renames the unified tool to `texwatch`. CLI analysis commands (`bibliography`, `environments`, `digest`) redirect through `cmd_dashboard` with a `--section` filter. Server startup writes `.mcp.json` automatically.

**Tech Stack:** Python 3.12, aiohttp, FastMCP, httpx, pytest

---

### Task 1: Enrich `/dashboard` with context, files, and activity

The `/dashboard` endpoint currently returns health, sections, issues, bibliography, changes, and environments. We add three new top-level keys: `context`, `files`, and `activity`.

**Files:**
- Modify: `texwatch/server.py` — `_build_dashboard_response` method (line 1108)
- Test: `tests/test_server.py` — `TestDashboardEndpoint` class (line 3533)

**Step 1: Write the failing tests**

Add to `tests/test_server.py` in `TestDashboardEndpoint`:

```python
@pytest.mark.asyncio
async def test_dashboard_includes_context(self, client, config):
    """Dashboard response includes editor/viewer context."""
    resp = await client.get("/dashboard")
    assert resp.status == 200
    data = await resp.json()
    assert "context" in data
    ctx = data["context"]
    assert "editor" in ctx
    assert "viewer" in ctx

@pytest.mark.asyncio
async def test_dashboard_includes_files(self, client, config):
    """Dashboard response includes file tree."""
    resp = await client.get("/dashboard")
    assert resp.status == 200
    data = await resp.json()
    assert "files" in data
    assert isinstance(data["files"], list)

@pytest.mark.asyncio
async def test_dashboard_includes_activity(self, client, config):
    """Dashboard response includes recent activity events."""
    resp = await client.get("/dashboard")
    assert resp.status == 200
    data = await resp.json()
    assert "activity" in data
    assert isinstance(data["activity"], list)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::TestDashboardEndpoint::test_dashboard_includes_context tests/test_server.py::TestDashboardEndpoint::test_dashboard_includes_files tests/test_server.py::TestDashboardEndpoint::test_dashboard_includes_activity -v`
Expected: FAIL — `"context" not in data`, `"files" not in data`, `"activity" not in data`

**Step 3: Implement — add context, files, activity to `_build_dashboard_response`**

In `texwatch/server.py`, modify `_build_dashboard_response` (line 1236). Before the `return web.json_response(...)`, add:

```python
# Context — editor/viewer state + current section
current_section: str | None = None
if structure and structure.sections:
    editor_file = proj.editor_state.get("file")
    editor_line = proj.editor_state.get("line")
    if editor_file and editor_line is not None:
        best: str | None = None
        best_line = -1
        for sec in structure.sections:
            if sec.file == editor_file and sec.line <= editor_line:
                if sec.line > best_line:
                    best = sec.title
                    best_line = sec.line
        current_section = best

context = {
    "editor": {**proj.editor_state, "section": current_section},
    "viewer": proj.viewer_state,
}

# Files — project file tree
watch_dir = get_watch_dir(proj.config)
files_tree = self._build_file_tree(watch_dir, watch_dir)

# Activity — last 10 events (newest first)
activity = list(reversed(proj.events))[:10]
```

Then update the return dict to include:

```python
return web.json_response({
    "health": health,
    "sections": sections_list,
    "issues": issues,
    "bibliography": bibliography,
    "changes": changes,
    "environments": environments,
    "context": context,
    "files": files_tree,
    "activity": activity,
})
```

Note: `watch_dir` is already defined at line 1111, so reuse it for the file tree. The `_build_file_tree` method already exists (line 1581).

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py::TestDashboardEndpoint -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add texwatch/server.py tests/test_server.py
git commit -m "Enrich /dashboard with context, files, and activity data"
```

---

### Task 2: Consolidate MCP tools from 18 → 8

Replace all tool definitions in `mcp_server.py`. The unified `texwatch` tool hits `/dashboard`. `texwatch_project` merges `texwatch_current` + `texwatch_switch`. Remove 10 tools.

**Files:**
- Modify: `texwatch/mcp_server.py` — tool definitions (lines 62-238)
- Test: `tests/test_mcp.py` — update all test classes

**Step 1: Write the failing tests**

Replace the URL tests in `tests/test_mcp.py::TestMcpToolUrls` with tests for the 8 new tools. Remove tests for deleted tools. Add:

```python
@pytest.mark.asyncio
async def test_texwatch_url(self):
    """Test unified texwatch tool hits /dashboard."""
    mock_resp = _mock_response(text='{"health":{}}')
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        server = create_server()
        await _call_tool(server, "texwatch", {"port": 8765})

        mock_client.get.assert_called_once_with("http://localhost:8765/dashboard")

@pytest.mark.asyncio
async def test_texwatch_url_with_project(self):
    """Test unified texwatch tool hits /p/{name}/dashboard."""
    mock_resp = _mock_response(text='{"health":{}}')
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        server = create_server()
        await _call_tool(server, "texwatch", {"port": 8765, "project": "paper1"})

        mock_client.get.assert_called_once_with("http://localhost:8765/p/paper1/dashboard")

@pytest.mark.asyncio
async def test_project_get_url(self):
    """Test texwatch_project with no project arg hits GET /current."""
    mock_resp = _mock_response(text='{"current": "alpha"}')
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        server = create_server()
        await _call_tool(server, "texwatch_project", {"port": 8765})

        mock_client.get.assert_called_once_with("http://localhost:8765/current")

@pytest.mark.asyncio
async def test_project_switch_url(self):
    """Test texwatch_project with project arg hits POST /current."""
    mock_resp = _mock_response(text='{"current": "beta"}')
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        server = create_server()
        await _call_tool(server, "texwatch_project", {"port": 8765, "project": "beta"})

        mock_client.post.assert_called_once_with(
            "http://localhost:8765/current",
            json={"project": "beta"},
        )
```

Keep existing tests for tools that remain: `texwatch_source`, `texwatch_history`, `texwatch_goto`, `texwatch_compile`, `texwatch_write_source`, `texwatch_capture`. Remove tests for: `texwatch_status`, `texwatch_context`, `texwatch_errors`, `texwatch_structure`, `texwatch_activity`, `texwatch_bibliography`, `texwatch_environments`, `texwatch_digest`, `texwatch_dashboard`, `texwatch_files`, `texwatch_current`, `texwatch_switch`.

Update `TestMcpServerCreation` to check that `create_server()` registers exactly 8 tools (was 18).

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp.py -v`
Expected: FAIL — tools not found (`texwatch` tool doesn't exist yet)

**Step 3: Implement — rewrite tool definitions in `mcp_server.py`**

Replace lines 62-238 in `texwatch/mcp_server.py` with the 8 tool definitions:

```python
@mcp.tool()
async def texwatch(port: int = 8765, project: str | None = None) -> str:
    """Get complete paper state: health, sections, issues, bibliography, changes, environments, editor/viewer context, file tree, and recent activity — all in one call."""
    return await _get("/dashboard", port, project)

@mcp.tool()
async def texwatch_source(
    file: str | None = None,
    port: int = 8765,
    project: str | None = None,
) -> str:
    """Read source file content from the texwatch project. If no file is specified, reads the main file."""
    params: dict = {}
    if file is not None:
        params["file"] = file

    base = _base_url(port, project)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base}/source", params=params)
        return resp.text

@mcp.tool()
async def texwatch_history(
    file: str,
    port: int = 8765,
    project: str | None = None,
) -> str:
    """Get previous versions of a source file (saved before each write). Returns snapshots newest-first."""
    base = _base_url(port, project)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base}/history/{file}")
        return resp.text

@mcp.tool()
async def texwatch_goto(
    line: int | None = None,
    page: int | None = None,
    section: str | None = None,
    port: int = 8765,
    project: str | None = None,
) -> str:
    """Navigate the PDF viewer to a specific line, page, or section. Exactly one of line, page, or section must be provided."""
    targets = [x for x in (line, page, section) if x is not None]
    if len(targets) != 1:
        return json.dumps({"error": "Exactly one of line, page, or section must be provided"})

    data: dict = {}
    if line is not None:
        data["line"] = line
    elif page is not None:
        data["page"] = page
    elif section is not None:
        data["section"] = section

    base = _base_url(port, project)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base}/goto", json=data)
        return resp.text

@mcp.tool()
async def texwatch_compile(port: int = 8765, project: str | None = None) -> str:
    """Trigger recompilation of the TeX document."""
    base = _base_url(port, project)
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base}/compile", json={})
        return resp.text

@mcp.tool()
async def texwatch_write_source(
    file: str,
    content: str,
    base_mtime_ns: str | None = None,
    port: int = 8765,
    project: str | None = None,
) -> str:
    """Write content to a source file. Provide base_mtime_ns for conflict detection."""
    base = _base_url(port, project)
    data: dict = {"file": file, "content": content}
    if base_mtime_ns is not None:
        data["base_mtime_ns"] = base_mtime_ns
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base}/source", json=data)
        return resp.text

@mcp.tool()
async def texwatch_capture(
    page: int | None = None,
    dpi: int = 150,
    port: int = 8765,
    project: str | None = None,
) -> list[TextContent | ImageContent]:
    """Screenshot current PDF page as PNG image. Returns the image as base64-encoded data."""
    params: dict = {"dpi": dpi}
    if page is not None:
        params["page"] = page

    base = _base_url(port, project)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{base}/capture", params=params)

        if resp.headers.get("content-type", "").startswith("image/"):
            b64_data = base64.b64encode(resp.content).decode("ascii")
            return [ImageContent(type="image", data=b64_data, mimeType="image/png")]
        return [TextContent(type="text", text=resp.text)]

@mcp.tool()
async def texwatch_project(
    project: str | None = None,
    port: int = 8765,
) -> str:
    """Show or switch the current project. Call with no args to see the current project and list of available projects. Call with a project name to switch to it."""
    async with httpx.AsyncClient() as client:
        if project is not None:
            resp = await client.post(
                f"http://localhost:{port}/current",
                json={"project": project},
            )
        else:
            resp = await client.get(f"http://localhost:{port}/current")
        return resp.text
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add texwatch/mcp_server.py tests/test_mcp.py
git commit -m "Consolidate MCP tools from 18 to 8 with unified texwatch tool"
```

---

### Task 3: Add `--section` filter to `cmd_dashboard` CLI

The `dashboard` command gains an optional `--section` flag to show only one section. This is the building block for Task 4 (redirecting analysis commands).

**Files:**
- Modify: `texwatch/cli.py` — `cmd_dashboard` (line 1099) and dashboard subparser (line 1496)
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

Add a new test class in `tests/test_cli.py`:

```python
class TestDashboardSectionFilter:
    """Tests for texwatch dashboard --section."""

    @pytest.fixture
    def handler_class(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                data = json.dumps({
                    "health": {"title": "My Paper", "compile_status": "success",
                               "word_count": 1000, "page_count": 5,
                               "page_limit": 8, "documentclass": "article",
                               "error_count": 0, "warning_count": 0,
                               "last_compile": None, "author": "Author", "date": None},
                    "sections": [{"title": "Intro", "word_count": 500,
                                  "is_dirty": False, "citation_count": 0,
                                  "todo_count": 0, "figure_count": 0,
                                  "table_count": 0, "level": "section",
                                  "file": "main.tex", "line": 1}],
                    "issues": [],
                    "bibliography": {"defined": 10, "cited": 8,
                                     "undefined_keys": [], "uncited_keys": ["foo"]},
                    "changes": [],
                    "environments": {"equation": 3, "items": []},
                    "context": {"editor": {}, "viewer": {}},
                    "files": [],
                    "activity": [],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    @pytest.fixture
    def server(self, handler_class):
        srv = HTTPServer(("localhost", 0), handler_class)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever,
                                  kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        srv.shutdown()
        thread.join(timeout=2)
        srv.server_close()

    def test_dashboard_no_section_shows_all(self, server, capsys):
        """dashboard with no --section shows all sections."""
        result = main(["dashboard", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "My Paper" in captured.out
        assert "Intro" in captured.out

    def test_dashboard_section_health(self, server, capsys):
        """dashboard --section health shows only health."""
        result = main(["dashboard", "--section", "health", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "My Paper" in captured.out
        assert "Intro" not in captured.out
        assert "Bibliography" not in captured.out

    def test_dashboard_section_bibliography(self, server, capsys):
        """dashboard --section bibliography shows only bibliography."""
        result = main(["dashboard", "--section", "bibliography", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Bibliography" in captured.out or "defined" in captured.out
        assert "My Paper" not in captured.out

    def test_dashboard_section_json(self, server, capsys):
        """dashboard --section health --json returns only health key."""
        result = main(["dashboard", "--section", "health", "--json", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "health" in data
        assert "sections" not in data

    def test_dashboard_section_invalid(self, server, capsys):
        """dashboard --section invalid returns error."""
        result = main(["dashboard", "--section", "invalid", "--port", str(server)])
        assert result == EXIT_FAIL
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py::TestDashboardSectionFilter -v`
Expected: FAIL — `--section` not recognized

**Step 3: Implement — add `--section` to dashboard**

In `texwatch/cli.py`:

Add `--section` to the dashboard subparser (around line 1496):
```python
p_dashboard = subparsers.add_parser("dashboard", help="Show unified paper dashboard")
p_dashboard.add_argument("--section", choices=["health", "sections", "issues",
                         "bibliography", "changes", "environments"],
                         default=None, help="Show only this section")
_add_server_options(p_dashboard)
```

Modify `cmd_dashboard` (line 1099) to support section filtering. Add at the top after fetching data:

```python
def cmd_dashboard(args: argparse.Namespace) -> int:
    """Handle dashboard command — show unified paper state."""
    result = _fetch_endpoint("/dashboard", "dashboard", args)
    if isinstance(result, int):
        return result
    data, _ = result

    section = getattr(args, "section", None)

    # Validate section name
    valid_sections = {"health", "sections", "issues", "bibliography", "changes", "environments"}
    if section and section not in valid_sections:
        print(f"Error: unknown section '{section}'. Valid: {', '.join(sorted(valid_sections))}")
        return EXIT_FAIL

    # JSON mode: filter to single section or return full data
    if getattr(args, "json", False):
        if section:
            print(json.dumps({section: data.get(section, {})}))
        else:
            print(json.dumps(data))
        return EXIT_OK

    # Human-readable mode: print selected section(s)
    if not section or section == "health":
        _print_dashboard_health(data)
    if not section or section == "sections":
        _print_dashboard_sections(data)
    if not section or section == "issues":
        _print_dashboard_issues(data)
    if not section or section == "changes":
        _print_dashboard_changes(data)
    if not section or section == "bibliography":
        _print_dashboard_bibliography(data)

    return EXIT_OK
```

Extract the existing display code from `cmd_dashboard` into helper functions: `_print_dashboard_health`, `_print_dashboard_sections`, `_print_dashboard_issues`, `_print_dashboard_changes`, `_print_dashboard_bibliography`. Each function takes `data` and prints its section. The existing code (lines 1107-1192) maps directly — just cut and wrap in functions.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py::TestDashboardSectionFilter -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: All pass (existing dashboard tests should still work)

**Step 6: Commit**

```bash
git add texwatch/cli.py tests/test_cli.py
git commit -m "Add --section filter to texwatch dashboard command"
```

---

### Task 4: Redirect bibliography/environments/digest through dashboard

Make the three analysis commands delegate to `cmd_dashboard` with `--section`.

**Files:**
- Modify: `texwatch/cli.py` — `cmd_bibliography`, `cmd_environments`, `cmd_digest`
- Test: `tests/test_cli.py`

**Step 1: Write the failing tests**

```python
class TestAnalysisRedirects:
    """Tests that bibliography/environments/digest redirect through dashboard."""

    @pytest.fixture
    def handler_class(self):
        """Same handler as TestDashboardSectionFilter — serves /dashboard."""
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                data = json.dumps({
                    "health": {"title": "My Paper", "compile_status": "success",
                               "word_count": 1000, "page_count": 5,
                               "page_limit": 8, "documentclass": "article",
                               "error_count": 0, "warning_count": 0,
                               "last_compile": None, "author": "Author", "date": None},
                    "sections": [],
                    "issues": [],
                    "bibliography": {"defined": 10, "cited": 8,
                                     "undefined_keys": [], "uncited_keys": ["foo"]},
                    "changes": [],
                    "environments": {"equation": 3, "items": [
                        {"env_type": "equation", "label": "eq1", "name": None,
                         "caption": None, "file": "main.tex",
                         "start_line": 10, "end_line": 12}
                    ]},
                    "context": {"editor": {}, "viewer": {}},
                    "files": [],
                    "activity": [],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    @pytest.fixture
    def server(self, handler_class):
        srv = HTTPServer(("localhost", 0), handler_class)
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever,
                                  kwargs={"poll_interval": 0.1})
        thread.daemon = True
        thread.start()
        yield port
        srv.shutdown()
        thread.join(timeout=2)
        srv.server_close()

    def test_bibliography_uses_dashboard(self, server, capsys):
        """bibliography command shows bibliography data via dashboard."""
        result = main(["bibliography", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "10" in captured.out or "defined" in captured.out.lower()

    def test_environments_uses_dashboard(self, server, capsys):
        """environments command shows environments data via dashboard."""
        result = main(["environments", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "equation" in captured.out

    def test_digest_uses_dashboard(self, server, capsys):
        """digest command shows health data via dashboard."""
        result = main(["digest", "--port", str(server)])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "My Paper" in captured.out or "article" in captured.out
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py::TestAnalysisRedirects -v`
Expected: FAIL — the commands still hit `/bibliography`, `/environments`, `/digest` which the mock doesn't serve.

**Step 3: Implement — redirect the commands**

In `texwatch/cli.py`, replace the three command functions:

```python
def cmd_bibliography(args: argparse.Namespace) -> int:
    """Handle bibliography command — redirects through dashboard."""
    args.section = "bibliography"
    return cmd_dashboard(args)


def cmd_environments(args: argparse.Namespace) -> int:
    """Handle environments command — redirects through dashboard."""
    args.section = "environments"
    return cmd_dashboard(args)


def cmd_digest(args: argparse.Namespace) -> int:
    """Handle digest command — redirects through dashboard."""
    args.section = "health"
    return cmd_dashboard(args)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py::TestAnalysisRedirects -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: All pass. Note: existing tests for `cmd_bibliography`, `cmd_environments`, `cmd_digest` in the test suite may now hit `/dashboard` instead of the old endpoints. If any existing tests fail, update their mock servers to serve `/dashboard` instead.

**Step 6: Commit**

```bash
git add texwatch/cli.py tests/test_cli.py
git commit -m "Redirect bibliography/environments/digest through dashboard"
```

---

### Task 5: Auto-register MCP server on `texwatch serve` startup

When the server starts, write a `.mcp.json` file so Claude Code discovers the texwatch MCP tools. Add `--no-mcp` flag to skip.

**Files:**
- Modify: `texwatch/server.py` — add `_register_mcp`/`_unregister_mcp`, call from `run()`
- Modify: `texwatch/cli.py` — add `--no-mcp` to serve subparser, pass to `TexwatchServer.run()`
- Test: `tests/test_server.py`

**Step 1: Write the failing tests**

Add a new test class in `tests/test_server.py`:

```python
class TestMcpRegistration:
    """Tests for automatic .mcp.json registration."""

    def test_register_mcp_creates_file(self, tmp_path):
        """_register_mcp creates .mcp.json with texwatch entry."""
        from texwatch.server import _register_mcp
        _register_mcp(8765, tmp_path)
        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        data = json.loads(mcp_file.read_text())
        assert "mcpServers" in data
        assert "texwatch" in data["mcpServers"]
        entry = data["mcpServers"]["texwatch"]
        assert entry["command"] == "texwatch"
        assert "--port" in entry["args"]
        assert "8765" in entry["args"]

    def test_register_mcp_preserves_existing(self, tmp_path):
        """_register_mcp preserves other entries in .mcp.json."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"other-tool": {"command": "other"}}
        }))
        from texwatch.server import _register_mcp
        _register_mcp(9000, tmp_path)
        data = json.loads(mcp_file.read_text())
        assert "other-tool" in data["mcpServers"]
        assert "texwatch" in data["mcpServers"]

    def test_register_mcp_updates_port(self, tmp_path):
        """_register_mcp updates port if .mcp.json already has texwatch."""
        from texwatch.server import _register_mcp
        _register_mcp(8765, tmp_path)
        _register_mcp(9000, tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "9000" in str(data["mcpServers"]["texwatch"]["args"])

    def test_unregister_mcp_removes_entry(self, tmp_path):
        """_unregister_mcp removes texwatch from .mcp.json."""
        from texwatch.server import _register_mcp, _unregister_mcp
        _register_mcp(8765, tmp_path)
        _unregister_mcp(tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "texwatch" not in data["mcpServers"]

    def test_unregister_mcp_preserves_others(self, tmp_path):
        """_unregister_mcp keeps other entries."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {
                "texwatch": {"command": "texwatch", "args": ["mcp"]},
                "other": {"command": "other"},
            }
        }))
        from texwatch.server import _unregister_mcp
        _unregister_mcp(tmp_path)
        data = json.loads(mcp_file.read_text())
        assert "texwatch" not in data["mcpServers"]
        assert "other" in data["mcpServers"]

    def test_unregister_mcp_no_file(self, tmp_path):
        """_unregister_mcp is a no-op if .mcp.json doesn't exist."""
        from texwatch.server import _unregister_mcp
        _unregister_mcp(tmp_path)  # should not raise
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py::TestMcpRegistration -v`
Expected: FAIL — `_register_mcp` not defined

**Step 3: Implement — add `_register_mcp` and `_unregister_mcp`**

Add to `texwatch/server.py` as module-level functions (near the top, after imports):

```python
def _register_mcp(port: int, project_dir: Path) -> None:
    """Write texwatch entry into .mcp.json for Claude Code discovery."""
    mcp_file = project_dir / ".mcp.json"
    try:
        data = json.loads(mcp_file.read_text()) if mcp_file.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    data.setdefault("mcpServers", {})
    data["mcpServers"]["texwatch"] = {
        "command": "texwatch",
        "args": ["mcp", "--port", str(port)],
    }
    mcp_file.write_text(json.dumps(data, indent=2) + "\n")


def _unregister_mcp(project_dir: Path) -> None:
    """Remove texwatch entry from .mcp.json."""
    mcp_file = project_dir / ".mcp.json"
    if not mcp_file.exists():
        return
    try:
        data = json.loads(mcp_file.read_text())
    except (json.JSONDecodeError, OSError):
        return
    servers = data.get("mcpServers", {})
    if "texwatch" in servers:
        del servers["texwatch"]
        mcp_file.write_text(json.dumps(data, indent=2) + "\n")
```

Then wire into `run()` method (line 1680). Add `register_mcp: bool = True` parameter:

```python
def run(self, host: str = "localhost", port: int | None = None,
        register_mcp: bool = True) -> None:
```

In the `runner()` async function, after `await site.start()` and the print statements, add:

```python
project_dir = Path.cwd()
if register_mcp:
    _register_mcp(port, project_dir)
```

In the `finally` block (after `await self.stop()`), add:

```python
if register_mcp:
    _unregister_mcp(project_dir)
```

In `texwatch/cli.py`, add `--no-mcp` to the serve subparser (around line 1522):

```python
p_serve.add_argument("--no-mcp", action="store_true",
                     help="Don't auto-register MCP server in .mcp.json")
```

And pass it to `server.run()` in the serve dispatch (find the `cmd_serve` function):

```python
server.run(host="localhost", port=port, register_mcp=not args.no_mcp)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py::TestMcpRegistration -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -q`
Expected: All pass

**Step 6: Commit**

```bash
git add texwatch/server.py texwatch/cli.py tests/test_server.py
git commit -m "Auto-register MCP server in .mcp.json on texwatch serve startup"
```

---

### Task 6: Final verification and cleanup

Run the full test suite, type checker, and linter. Verify coverage.

**Step 1: Run all verifications**

```bash
python -m pytest tests/ --cov=texwatch --cov-report=term-missing -q
python -m mypy texwatch/ --ignore-missing-imports
python -m ruff check texwatch/
```

Expected: All pass, coverage >= 93%

**Step 2: Manual test (if texwatch server is available)**

```bash
texwatch serve                  # should create .mcp.json
cat .mcp.json                   # should show texwatch entry
texwatch dashboard              # full output
texwatch dashboard --section health  # health only
texwatch bibliography           # should work via dashboard redirect
texwatch environments           # should work via dashboard redirect
texwatch digest                 # should work via dashboard redirect
```

**Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "Final cleanup after MCP consolidation"
```
