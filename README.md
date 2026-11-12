<img src="docs/images/logo.png" alt="" width="96">

# tex-mcp-web

Review a LaTeX paper from its rendered PDF, on a desktop, tablet, or phone, while Claude Code or Codex edits the source.

> ⭐ **If this helps your writing, please give it a star.** It helps others find the project.

![A caption flagged in the PDF, the source line the agent proposes to change marked in red, and its proposed \vspace with Apply suggestion.](docs/images/discussion.png)

## Quick start

1. Paste this into Claude Code or Codex, and start the agent again once it is done:

   ```
   Install tex-mcp-web by following https://raw.githubusercontent.com/MiiKiyoshi/tex-mcp-web/main/INSTALL.md
   ```

   The agent checks for Python 3.10 or newer and `latexmk`, shows where it will install and
   which agents it will register with, and installs once you agree.

2. Start the agent in the paper's folder and say `do tex init`. It finds the main `.tex` file
   and a free port, shows you the settings, and writes `.tex-mcp-web.yaml` once you agree.

3. Open `http://localhost:<port>`. With the plugin the install adds, a recent Claude Code
   session started in the paper's folder listens for your comments by itself. Elsewhere,
   say `do tex listen`, and again after the agent restarts.

## Comment on the PDF, and the agent edits the source

Select words in the PDF, or draw a box over part of a figure, and write what should change.
Press **Call agent** when your comments are ready. The agent edits the source, compiles, and
answers in the same thread, naming the lines it changed. A wording it proposes shows as a −/+
pair with **Apply suggestion**. **Resolve** a thread you are happy with, or **Reply** when you
are not.

## Answer your co-authors' Overleaf comments, with the agent

Paste the Overleaf project's link in **Settings**, and its comment threads come into the leaf
**Comments** tab, on the words they were written about. Each thread holds everything said
about those words, in the order it was said: your co-authors' messages from Overleaf, the
replies you sent, and the talk you have with the agent in between, where you correct it,
think aloud and settle what to answer. That talk stays on this page. Overleaf gets only the
reply you send, which the agent drafts under the thread and you edit first.

How the paper's files keep in step with Overleaf, by git, by Get and Send, or live, is in
[Working with Overleaf](docs/overleaf.md).

## Go through the comments by keyboard

The comment list goes by keys, as a notebook's cells do. The ringed comment stays a third of
the way down the list, and Space shows its words in the source a third of the way down the
editor, so the eye stays put.

| Key | Does |
|---|---|
| `j` `k` | Next and previous comment |
| Space | Open the comment and show its words |
| `r` | Reply to the agent |
| `R` | Resolve |
| `aa` | Apply the suggestion |
| `ss` | Send the reply to Overleaf |
| `?` | Every key |

Keys that change the file, post to Overleaf or delete are pressed twice. Once you move by keys,
the ringed comment's buttons show their letters. The editor has Vim keys too. Both are in
[Reviewing in detail](docs/reviewing.md).

## Review on a tablet or phone

![The review page on a tablet and a phone, zoomed to a commented caption: the PDF above, its thread below.](docs/images/mobile.png)

Forward the port you chose at init over SSH, from Termux on Android or iSH on iOS, then open
`http://localhost:<port>` in the phone's browser.

```
ssh -L <port>:localhost:<port> <server>
```

## Writes in your field's terms

Left alone, an agent describes a concept in words of its own where the papers of your field
use one term. Convert the papers yours builds on to Markdown with
[marker](https://github.com/datalab-to/marker), keep them in one folder, and set `references`
to it. In Claude Code the tools then stay locked until the agent has read the terms those
papers share, and lock again after every compaction.

## Configuration

`.tex-mcp-web.yaml` sits at the paper's root. A saved change applies at once, except `port` and
`dir`, which apply when Claude Code or Codex restarts.

```yaml
main: main.tex
watch:
- '*.tex'
- '*.bib'
ignore:
- '*_backup.tex'
compiler: auto
port: 8765
guideline: neutral-paper
translation:
  language: Korean
  instructions: Keep cell names in English.
overleaf: https://www.overleaf.com/project/0123456789abcdef01234567
```

| Field | Effect |
|---|---|
| `main` | Top-level source file compiled into the PDF. |
| `dir` | Folder holding the paper, relative to this file or absolute. Unset means this file's folder. |
| `watch` | Source files the Source tab offers. |
| `ignore` | Patterns excluded even when they match `watch`. |
| `compiler` | `auto` (latexmk for LaTeX, pandoc for Markdown or text), `latexmk`, `pdflatex`, `xelatex`, `lualatex`, or `pandoc`. |
| `port` | This paper's review page port. Any free port works. Init picks the first free one from 8765, so each paper can have its own. |
| `guideline` | The writing guideline the agent reads before editing, by its folder's name. `neutral-paper` is installed. A folder of your own under `~/.config/tex-mcp-web/guidelines/<name>/` holding a `GUIDELINE.md` is chosen the same way, and replaces an installed one of the same name. A guideline whose file opens with front matter `extends: <name>` is read after the one it names. Unset means none. |
| `references` | Folder holding only the papers yours builds on, converted to Markdown, relative to this file or absolute. Unset means no list of their terms. |
| `translation` | `language` puts a button for that language beside **+ Note** that shows each paragraph of the source translated by `claude -p`, with those on the current page marked. `instructions` adds rules for the translator, one per line. A paragraph is translated once per text, and of a figure or table only the caption is. Unset means no translation. |
| `overleaf` | The paper's Overleaf project, by its link, which an open page follows and the sync button reads. Unset means none. |
| `overleaf_add` | Patterns for the files only here that go to the Overleaf project, by **Send** or under **Live** by themselves, in a folder that is no git clone. Unset means the paper's sources and figures: `*.tex`, `*.bib`, `*.sty`, `*.cls`, `*.bst`, `*.png`, `*.jpg`, `*.jpeg`, `*.pdf`, `*.eps` and `*.svg`. A file of a kind the project already holds is added too, and the paper's compiled PDF never is. |
| `overleaf_sync` | `true` is **Live** in Settings, which keeps the paper's files and the project's the same both ways while a page is open, and brings new threads in by themselves. Unset is **By hand**: files change only by git pull and commit & push in a git clone, or by Get and Send otherwise. |

## See also

[html-mcp-web](https://github.com/MiiKiyoshi/html-mcp-web) reviews an AI agent's HTML slides or report the same way and hands finished slides over as an editable PowerPoint deck.

## Acknowledgements

tex-mcp-web is a hard fork of [queelius/scholia at commit `e6c7454`](https://github.com/queelius/scholia/commit/e6c745400d2ad70fb43eca053e31183d48765f89) (version 0.6.1), independently developed since under the MIT license. See [`LICENSE`](LICENSE). PDF viewing uses [EmbedPDF](https://github.com/embedpdf/embed-pdf-viewer) and its PDFium WebAssembly engine, whose notices are in [`tex_mcp_web/static/embedpdf/LICENSE`](tex_mcp_web/static/embedpdf/LICENSE) and [`tex_mcp_web/static/embedpdf/LICENSE.pdfium`](tex_mcp_web/static/embedpdf/LICENSE.pdfium). The bundled viewer differs from its release in its zoom gesture alone: a wheel or pinch inside a column keeps that column, the half of the page it is on, whole in the pane while the pane holds it and filling the pane once it is wider. On the middle of the page it zooms about the pointer, and a mouse notch steps by a quarter. Source highlighting and completion use [Ace Editor builds 1.44.0](https://github.com/ajaxorg/ace-builds/tree/v1.44.0) under the BSD-3-Clause license, and its notice is in [`tex_mcp_web/static/ace/LICENSE`](tex_mcp_web/static/ace/LICENSE).
