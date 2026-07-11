---
name: tex-mcp-web
description: Use when working on TeX/LaTeX documents to view PDF output, navigate to lines, or check compile status
---

# tex-mcp-web - TeX File Watcher Integration

This skill provides integration with the tex-mcp-web tool for TeX/LaTeX document editing.

## Quick Reference

```bash
# Check if tex-mcp-web is running and get status
tex-mcp-web status

# Navigate PDF to a specific line
tex-mcp-web goto 42

# Navigate to a specific page
tex-mcp-web goto p5

# Navigate to a section by name
tex-mcp-web goto "Introduction"

# Force recompile
tex-mcp-web compile

# Capture PDF page as PNG
tex-mcp-web capture output.png --page 1 --dpi 150
```

## Commands

### Check Status

Get the current compile state, errors, warnings, and viewer position:

```bash
tex-mcp-web status
tex-mcp-web status --json
```

This shows:
- Main file being watched
- Whether compilation is in progress
- Last compile success/failure
- Current errors and warnings
- Viewer page and visible source lines

### Navigate to Source Line

Jump the PDF viewer to show where a specific source line renders:

```bash
tex-mcp-web goto <target>
```

Examples:
- `tex-mcp-web goto 42` - Go to line 42 of the main file
- `tex-mcp-web goto p3` - Go to page 3
- `tex-mcp-web goto "Introduction"` - Go to section matching "Introduction"

### View Errors

When compilation fails or produces warnings:

```bash
tex-mcp-web status --json | jq '.errors'
```

Or use the `/errors` endpoint for errors with source context:

```bash
curl localhost:8765/errors
```

### Force Recompile

Trigger recompilation manually (normally automatic on file save):

```bash
tex-mcp-web compile
```

### Capture PDF Page

Screenshot a PDF page to a PNG file:

```bash
tex-mcp-web capture output.png
tex-mcp-web capture output.png --page 2 --dpi 300
```

## MCP Tools

tex-mcp-web provides an MCP (Model Context Protocol) server that allows Claude Code to interact with the running tex-mcp-web instance directly.

### Setup

Add to `.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "tex-mcp-web": {
      "command": "tex-mcp-web",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

Or with a custom port:

```json
{
  "mcpServers": {
    "tex-mcp-web": {
      "command": "tex-mcp-web",
      "args": ["mcp", "--port", "9000"],
      "env": {}
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `tex-mcp-web_status` | Compilation status, errors, warnings |
| `tex-mcp-web_context` | What the user is looking at (editor + viewer + section) |
| `tex-mcp-web_errors` | Errors with source context lines |
| `tex-mcp-web_structure` | Paper outline: sections, TODOs, inputs, word count |
| `goto` | Navigate to line, page, or section |
| `compile` | Trigger recompilation |
| `tex-mcp-web_capture` | Screenshot current PDF page as PNG |
| `tex-mcp-web_source` | Read source file content |

All tools accept `port` (default: 8765) and `project` (optional) parameters.

#### tex-mcp-web_status

Returns JSON with compile state, errors, warnings, viewer position, and editor state.

#### tex-mcp-web_context

Returns a combined snapshot of what the user is currently working on:
- Editor position (file and line)
- Viewer state (page, total pages, visible lines)
- Current section name (based on editor cursor position)
- Error and warning counts
- Word count

#### tex-mcp-web_errors

Returns errors and warnings from the last compilation, including source context lines around each error.

#### tex-mcp-web_structure

Returns the full document structure:
- Sections (with level, title, file, line)
- TODOs found in comments
- Input/include files
- Word count

#### goto

Navigate the PDF viewer. Accepts exactly one of:
- `line`: Jump to where a source line renders in the PDF
- `page`: Jump to a specific page number
- `section`: Jump to a section by name (case-insensitive substring match)

#### compile

Triggers a recompilation and returns the result including success status, errors, and warnings.

#### tex-mcp-web_capture

Screenshots the current PDF page as a PNG image. Parameters:
- `page`: Page number (default: viewer's current page)
- `dpi`: Resolution (default: 150, range: 72-600)

Returns the image as base64-encoded PNG data.

#### tex-mcp-web_source

Reads source file content from the project. Parameters:
- `file`: File path relative to project root (default: main file)

## Workflow Tips

### Starting a Session

1. Navigate to your TeX project directory
2. Run `tex-web` (or just `tex-mcp-web` if .tex-mcp-web.yaml exists)
3. Open http://localhost:8765 in a browser
4. Edit .tex files - PDF auto-reloads on save

### Using with Claude Code

When editing TeX files:
1. Use `tex-mcp-web_context` to understand what the user is looking at
2. Use `tex-mcp-web_errors` to check for compile errors with context
3. Use `tex-mcp-web_structure` to understand the document outline
4. Use `goto` to navigate the viewer to specific locations
5. Use `tex-mcp-web_capture` to see what the PDF looks like

### SyncTeX Navigation

- Click anywhere in the PDF to see the corresponding source line in the status bar
- Use `tex-mcp-web goto <line>` to jump from source to PDF position
- The viewer reports visible source line ranges for context

## Configuration

tex-mcp-web uses `.tex-mcp-web.yaml` in the project root:

```yaml
main: main.tex
watch:
  - "*.tex"
  - "sections/*.tex"
ignore:
  - "*_backup.tex"
compiler: latexmk  # or pdflatex, xelatex, lualatex
port: 8765
```

Create with: `tex-mcp-web init`

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | JSON with compile state, errors, viewer position |
| `/context` | GET | Editor + viewer state, current section, word count |
| `/errors` | GET | Errors and warnings with source context |
| `/structure` | GET | Document outline: sections, TODOs, inputs, word count |
| `/goto` | POST | Navigate: `{"line": N}`, `{"page": N}`, or `{"section": "..."}` |
| `/compile` | POST | Force recompile, returns result |
| `/capture` | GET | Screenshot PDF page as PNG (params: page, dpi) |
| `/source` | GET | Read source file content (param: file) |
| `/source` | POST | Write source file content |
| `/config` | GET | Current configuration |
| `/pdf` | GET | Serve the compiled PDF |
| `/files` | GET | Project file tree |
| `/projects` | GET | List all projects (multi-project mode) |

All per-project endpoints are also available under `/p/{project_name}/`.

## Troubleshooting

**"No tex-mcp-web instance running"**
- Start tex-mcp-web: `tex-web`

**"Compiler not found"**
- Install latexmk: `sudo apt install latexmk` or `brew install latexmk`
- Or use a different compiler in .tex-mcp-web.yaml

**PDF not updating**
- Check for compile errors: `tex-mcp-web status`
- Force recompile: `tex-mcp-web compile`
- Check browser console for WebSocket issues

**Navigation not working**
- SyncTeX requires compilation with `-synctex=1` (enabled by default)
- Check that .synctex.gz file exists next to the PDF

**MCP server not connecting**
- Ensure tex-mcp-web HTTP server is running: `tex-web`
- Check the port matches: `tex-mcp-web status -p 8765`
- Install MCP dependencies: `pip install tex-mcp-web[mcp]`
