# tex-mcp-web

**Agentic-first PDF review for LaTeX papers, with Claude Code as the author.**

Hard fork of [queelius/scholia](https://github.com/queelius/scholia) v0.6.1 (MIT). Renamed and independently developed since.

You read the rendered PDF in your browser. You drop comments on paragraphs, sections, or the paper as a whole. Claude Code reads the queue (via MCP), edits the source, and replies with what changed. The PDF rebuilds in front of you. Repeat until done.

## Why this exists

tex-mcp-web is deliberately *not* an editor, *not* an IDE, *not* an Overleaf clone. The agent (Claude Code) is already smarter at reading source, parsing LaTeX, grepping citations, and editing files than any tool we could build. So we don't try.

tex-mcp-web is a **substrate** for the agentic-first writing workflow:

- A **live PDF preview** the human can watch and gesture at.
- A **comment queue** anchored to PDF text, visual areas, sections, source ranges, or the paper as a whole.
- A **structured-error compile oracle** the agent calls when it wants ground truth.

That's it. Three responsibilities. Anything that re-implements something the agent does well (file parsing, log analysis, semantic understanding) was deliberately removed.

## Install

Requires Python 3.10+ and `latexmk` (or `pdflatex`/`xelatex`/`lualatex`/`pandoc`) on your `PATH`.

```bash
pip install "tex-mcp-web[mcp] @ git+https://github.com/MiiKiyoshi/tex-mcp-web"
```

The `[mcp]` extra adds the MCP server for Claude Code. PyMuPDF is a core
dependency because text anchors are relocated in every new PDF after compilation.

## Quick start

```bash
cd my-paper/
tex-mcp-web init --main main.tex   # writes .tex-mcp-web.yaml (main file, port)
tex-web                            # starts the daemon at http://localhost:8765
```

In the browser:

- The PDF appears on the left, the comments sidebar on the right.
- **Select text in the PDF** and use the selection menu to comment. EmbedPDF returns glyph positions, exact text, and per-line rectangles; official `synctex edit` maps every visual line back to one source range.
- **Suggest a rewrite** alongside any comment: the compose dialog has an optional `{old, new}` block. When you select text first, "old" pre-fills with the selected text, so you only type the replacement. The agent gets a structured edit it can apply directly.
- **"+ Note"** in the top bar for a paper-level comment ("the abstract is too long").
- **Sections tab** is the table of contents: numbered headings, click to jump, **"+ comment"** for section-level comments.
- **Compile tab** lists compile errors and warnings; tab badges carry the open-comment and error/warning counts.
- **Reply / Resolve / Dismiss** are inline forms in each comment, not modals.
- **Keyboard navigation**: `j` / `k` step through comments, `r` opens a reply form, `R` opens resolve, `d` opens dismiss, `Esc` cancels, `\` collapses the sidebar.
- **Ctrl/Cmd + wheel** zooms the PDF around the cursor.

## The Claude Code workflow

Register the MCP server once, globally:

```bash
claude mcp add --scope user tex-mcp -- tex-mcp
```

The server locates `.tex-mcp-web.yaml` by searching upward from Claude Code's working directory, so the same registration serves every paper: open Claude Code in a paper directory and the tools point at that paper.

This exposes **7 tools**:

| Tool | What it does |
|---|---|
| `paper(include_comments=True)` | Paper state in one call: sections (with line ranges), the comments queue, last-compile cache, main-file paths. |
| `compile()` | Recompile and return structured errors with source context. |
| `comment(action, ...)` | `add` / `reply` / `resolve` / `dismiss` / `delete`. Optional `suggestion={"old", "new"}` on add. |
| `image(...)` | Render PDF region as PNG. Modes: `page=N`, `page+bbox`, `source="file:lstart-lend"`, `comment_id="c-..."`. |
| `section(name)` | Deep-dive: source slice + rendered image + scoped comments for one section in one call. |
| `audit(focus=...)` | Workflow primer for agent-initiated review. Returns guidance; the agent then files comments back as `author="claude"`. |
| `goto(target)` | Scroll the running viewer to a section / page / line / label. |

When a compile finishes, the WebSocket broadcasts `{"type": "compiled", ..., "pages_changed": [3, 7]}` so the agent can verify only the pages that actually shifted, not re-render the whole document.

Notice what's absent: there's no `labels()`, no `citations()`, no `environments()`. Use `Grep`. The agent is better at it than we are.

**Visual review** is the killer mode of `image`. Claude is multimodal; pure text won't tell it whether a figure caption attaches to the right figure or whether an equation rendered correctly. The `comment_id` mode is the fast path: human draws a rectangle around a figure, files the comment, agent renders that region, sees what the human pointed at, fixes the LaTeX.

**Active review** runs the loop in either direction:

```
You:    [drop 8 comments on the PDF; for "rephrase X" comments, fill
         in the suggested rewrite (agent applies it directly)]
        "Process the open comments."

Claude: paper()                           # see comments + sections
        for each: Read/Edit source; if suggestion present, apply it
        comment(action="resolve", id=..., summary="...")
        compile()                         # verify build

You:    [PDF rebuilds; reply / dismiss as needed]

You:    "Audit my methods section for notation drift."

Claude: audit(focus="math")               # guidance primer
        Read paper.tex; image() to inspect rendering
        comment(action="add", author="claude",
                        anchor=..., text="...", suggestion=...)
        # filed back into the queue, distinct visual treatment
```

## Comment anchors

Five kinds, with different staleness behavior:

| Anchor | Use when | Staleness handling |
|---|---|---|
| `text_selection` | Reading the PDF and selecting rendered text. | Stores the exact quote, glyph range, page rectangles, PDF digest, and exact source selector. Recompilation reattaches the source and regenerates rectangles from the new PDF. |
| `area` | Pointing at a figure, equation, or whitespace. | Coordinate-only and bound to one PDF digest. A new compile marks it stale. |
| `section` | "Expand the methods section." | Resolved by section title or `\label{...}`. Stale only if the section is removed or renamed. |
| `source_range` | When the agent already knows the lines (most common from MCP). | Exact selected lines are matched separately from prefix and suffix context, so reattachment never widens the range. |
| `paper` | Global note about the paper. | Never stale. |

## CLI

```
tex-mcp-web                 # serve (default)
tex-mcp-web init            # scaffold .tex-mcp-web.yaml
tex-mcp-web config          # print .tex-mcp-web.yaml
tex-mcp-web config port 9000   # set a value (main/port/compiler/watch/ignore/page_limit)
tex-mcp-web compile         # one-shot compile, structured errors
tex-mcp-web goto "Methods"  # tell the running viewer to scroll
tex-web                     # alias for `tex-mcp-web serve`
tex-mcp                     # run the MCP server (stdio)
```

That's the whole CLI. Comment management lives in the browser (for humans) and in the MCP tools (for the agent). There is no `tex-mcp-web comment add` from the shell because nobody types that.

## Configuration

`.tex-mcp-web.yaml`:

```yaml
main: main.tex
watch: ["*.tex", "*.bib", "*.md", "*.txt", "**/*.tex"]
ignore: ["*_backup.tex"]
compiler: auto       # auto | latexmk | pdflatex | xelatex | lualatex | pandoc
port: 8765
```

`tex-mcp-web init` scaffolds this; `tex-mcp-web config <key> <value>` edits it.

Comments live in `.tex-mcp-web/comments.json`. `git add` it to keep your review history with the paper.

The browser viewer is self-contained. EmbedPDF 2.14.4 runtime files are vendored in
`tex_mcp_web/static/embedpdf/`. The viewer uses the direct PDFium engine because the
worker engine does not complete initialization in the supported Firefox environment.
The default stamp manifests are empty, so the viewer does not fetch runtime assets
from a CDN. The full-page preview and visible tiles render at a minimum device-pixel
ratio of 1.5 so DPR 1 displays do not show one-raster-pixel-per-CSS-pixel text.

## License

MIT. See `LICENSE`.
