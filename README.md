# tex-mcp-web

**Agentic-first PDF review for LaTeX papers, with Claude Code as the author.**

You read the rendered PDF in your browser. You drop comments on paragraphs, sections, or the paper as a whole. Claude Code reads the queue (via MCP), edits the source, and replies with what changed. The PDF rebuilds in front of you. Repeat until done.

## Why this exists

tex-mcp-web is deliberately *not* an editor, *not* an IDE, *not* an Overleaf clone. The agent (Claude Code) is already smarter at reading source, parsing LaTeX, grepping citations, and editing files than any tool we could build. So we don't try.

tex-mcp-web is a **substrate** for the agentic-first writing workflow:

- A **live PDF preview** the human can watch and gesture at.
- A **comment queue** anchored to PDF regions, sections, source ranges, or the paper as a whole.
- A **structured-error compile oracle** the agent calls when it wants ground truth.

That's it. Three responsibilities. Anything that re-implements something the agent does well (file parsing, log analysis, semantic understanding) was deliberately removed.

## Install

Requires Python 3.10+ and `latexmk` (or `pdflatex`/`xelatex`/`lualatex`/`pandoc`) on your `PATH`.

```bash
pip install tex-mcp-web
pip install tex-mcp-web[mcp]   # adds MCP server for Claude Code
```

## Quick start

```bash
cd my-paper/
tex-mcp-web init       # writes .tex-mcp-web.yaml (configures main file, port)
tex-mcp-web            # starts the daemon at http://localhost:8765
```

In the browser:

- The PDF appears on the left, the comments sidebar on the right.
- **Select text in the PDF** to anchor a comment to that region. SyncTeX maps the selection back to a source line range automatically.
- **Shift-click-drag** to draw a rectangle around any region (figures, equations, whitespace) where text selection doesn't reach. Same `pdf_region` anchor; the agent can render exactly that region with `image(comment_id=...)`.
- **Suggest a rewrite** alongside any comment: the compose dialog has an optional `{old, new}` block. When you select text first, "old" pre-fills with the selected text, so you only type the replacement. The agent gets a structured edit it can apply directly.
- **"+ Note"** in the top bar for a paper-level comment ("the abstract is too long").
- **Paper tab** lists sections with **"+ comment"** buttons for section-level comments.
- **Reply / Resolve / Dismiss** are inline forms in each comment, not modals.
- **Keyboard navigation**: `j` / `k` step through comments, `r` opens a reply form, `R` opens resolve, `d` opens dismiss, `Esc` cancels.

## The Claude Code workflow

`tex-mcp-web` auto-registers an MCP server in `.mcp.json` when it starts, exposing **7 tools**:

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

Notice what's absent: there's no `tex-mcp-web_labels()`, no `tex-mcp-web_citations()`, no `tex-mcp-web_environments()`. Use `Grep`. The agent is better at it than we are.

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

Four kinds, with different staleness behavior:

| Anchor | Use when | Staleness handling |
|---|---|---|
| `pdf_region` | Reading the PDF and pointing at a paragraph. | SyncTeX resolves to source; a content snippet is captured; if Claude rewrites that region, the snippet match fails and the comment is flagged `STALE`. |
| `section` | "Expand the methods section." | Resolved by section title or `\label{...}`. Stale only if the section is removed or renamed. |
| `source_range` | When the agent already knows the lines (most common from MCP). | Snippet-matched, like `pdf_region`. |
| `paper` | Global note about the paper. | Never stale. |

## CLI

```
tex-mcp-web                 # serve (default)
tex-mcp-web init            # scaffold .tex-mcp-web.yaml
tex-mcp-web compile         # one-shot compile, structured errors
tex-mcp-web goto "Methods"  # tell the running viewer to scroll
tex-mcp             # run the MCP server (stdio)
```

That's the whole CLI. Comment management lives in the browser (for humans) and in the MCP tools (for the agent). There is no `tex-mcp-web comment add` from the shell because nobody types that.

## What changed in v0.6.0 (active review)

The framing shifted from "human reviews; agent dispatches" to "review goes both ways":

- **Suggested rewrites.** Comments now optionally carry a structured `{old, new}` block. The agent applies the rewrite directly instead of parsing prose. Browser pre-fills "old" with the selected PDF text so the human only types the replacement.
- **`audit(focus=...)`** primes agent-initiated review. The agent reads the paper, files findings back as `author="claude"` comments, and the human steps through them.
- **Refactor: anchors resolve themselves.** The dispatch that used to live in three switch statements (`server._resolve_anchor`, `imaging.resolve_image_target`, staleness check) is now methods on each anchor type. Adding a new anchor kind is a 30-line addition to one file.
- **Renamed from `texwatch`.** The old name reflected the v0.3.0 "TeX watcher" product; the v0.6.0 product is a corpus of marginal annotations, which is what the Greek *tex-mcp-web* literally meant.

## What changed in v0.5.x

Aggressive simplification with the agentic-first frame:

- **Dropped the CLI comment surface entirely.** The agent and the browser are the only sane places to manage comments.
- **Dropped `tags`, `reopen`, the `Errors` tab.** Tags were noise, reopen was Github-imitation, the Errors tab duplicated information the topbar already shows.
- **Dropped `labels` / `citations` / `inputs` from `paper()`.** Use `Grep`.
- **Folded `comments` into `paper(include_comments=True)`** for one-call orientation.
- **Inline reply / resolve / dismiss forms** in the viewer, not `prompt()` dialogs.
- **Compile lock** prevents the watcher and `compile()` from racing.
- **Visual review via `image()`** with four modes (page, page+bbox, source range, comment id). Shift-click-drag selects arbitrary rectangles for figures.

## Configuration

`.tex-mcp-web.yaml`:

```yaml
main: paper.tex
watch: ["*.tex", "*.bib", "*.md"]
ignore: ["*_backup.tex"]
compiler: auto       # auto | latexmk | pdflatex | xelatex | lualatex | pandoc
port: 8765
```

Comments live in `.tex-mcp-web/comments.json`. `git add` it to keep your review history with the paper.

## License

MIT. See `LICENSE`.
