# tex-mcp-web

Review a LaTeX paper from its rendered PDF while Claude Code or Codex edits the source.

![A PDF comment and an agent reply in tex-mcp-web](docs/images/discussion.png)

You read the PDF in a browser and leave comments on selected text, sections, or the whole paper. The coding agent reads those comments through MCP, edits the LaTeX source, compiles it, and records its response. The rebuilt PDF appears in the same browser.

## Install

tex-mcp-web requires Python 3.10 or newer and a supported document compiler on `PATH`. LaTeX projects use `latexmk` by default; `pdflatex`, `xelatex`, `lualatex`, and `pandoc` are also supported.

```bash
pip install "tex-mcp-web[mcp] @ git+https://github.com/MiiKiyoshi/tex-mcp-web"
```

## Start a paper

Run these commands in the directory that contains the paper:

```bash
cd my-paper
tex-mcp-web init --main main.tex
tex-web
```

Open [http://localhost:8765](http://localhost:8765) and keep `tex-web` running while you review the paper. The first command creates `.tex-mcp-web.yaml`, which identifies the main source file and the browser port.

## Connect Claude Code or Codex

Register the MCP server once for the agent you use.

Claude Code:

```bash
claude mcp add --scope user tex-mcp -- tex-mcp
```

Codex:

```bash
codex mcp add tex-mcp -- tex-mcp
```

Start the agent from the paper directory or one of its subdirectories. `tex-mcp` searches upward for `.tex-mcp-web.yaml`, so the same registration works across papers.

## Review with the agent

Select text in the PDF and write a comment. The comment dialog can also carry an exact replacement. Use **+ Note** for a paper-level comment or the **Sections** tab for a section-level comment.

Then ask the agent:

> Process the open tex-mcp-web comments.

The agent reads the open comments and nearby source, makes the requested edits, compiles the paper, verifies the result, and only then replies to or resolves each comment. You can inspect the rebuilt PDF, reply in the same thread, or add another comment.

Other useful requests include:

> Read my replies and continue the revision.

> Review the Methods section and add comments without editing the paper.

The web interface also provides **Reply**, **Resolve**, and **Dismiss** actions. `j` and `k` move between comments, `r` opens a reply, `R` opens resolve, `d` opens dismiss, `Esc` cancels the current action, and `\` collapses the sidebar. `Ctrl`/`Cmd` + wheel zooms around the pointer.

## Try the included demo

The repository includes a fictional one-page paper that is unrelated to any real manuscript:

```bash
cd examples/demo-paper
tex-web
```

Open [http://localhost:8876](http://localhost:8876). The demo starts with an empty comment queue. Its source is [`examples/demo-paper/main.tex`](examples/demo-paper/main.tex), and its project settings are [`examples/demo-paper/.tex-mcp-web.yaml`](examples/demo-paper/.tex-mcp-web.yaml).

## Project files and privacy

`.tex-mcp-web.yaml` contains the project settings. Review conversations are stored in `.tex-mcp-web/comments.json` beside the paper. That file can contain selected manuscript text and discussion, so decide whether to track or ignore it according to the paper's privacy requirements.

## Troubleshooting

If the browser opens without a PDF, inspect the **Compile** tab or run `tex-mcp-web compile` in the paper directory. If the agent opens the wrong paper, check that its working directory is inside the directory containing the intended `.tex-mcp-web.yaml`. Agent-triggered compilation and viewer navigation require the matching `tex-web` process to remain running.

## Command reference

```text
tex-web                         Start the PDF viewer and file watcher
tex-mcp                         Start the MCP server over stdio
tex-mcp-web init --main FILE    Create .tex-mcp-web.yaml
tex-mcp-web config              Print the current project settings
tex-mcp-web compile             Compile once and report errors
tex-mcp-web goto TARGET         Move the running viewer to a section, page, or source line
```

tex-mcp-web is a hard fork of [queelius/scholia](https://github.com/queelius/scholia) v0.6.1 and is independently developed under the MIT license. See [`LICENSE`](LICENSE).
