<img src="docs/images/logo.png" alt="" width="96">

# tex-mcp-web

Review a LaTeX paper from its rendered PDF, on a desktop, tablet, or phone, while Claude Code or Codex edits the source.

> ⭐ **If this helps your writing, please give it a star.** It helps others find the project.

![A caption flagged in the PDF, the source line the agent proposes to change marked in red, and its proposed \vspace with Apply suggestion.](docs/images/discussion.png)

## Writes in your field's terms

Left alone, an agent describes a concept in words of its own where the papers of your field
use one term. Give tex-mcp those papers and, in Claude Code, its tools stay locked until the
agent has read the terms they share, and they lock again after every compaction.

To set it up, convert the papers yours builds on to Markdown with
[marker](https://github.com/datalab-to/marker), keep them in one folder, and set `references`
to it.

## Install

Paste this into Claude Code or Codex:

```
Install tex-mcp-web by following https://raw.githubusercontent.com/MiiKiyoshi/tex-mcp-web/main/INSTALL.md
```

The agent checks for Python 3.10 or newer and `latexmk`, shows where it will install and
which agents it will register with, and installs once you agree. Start the agent again
afterwards.

## Init

Once per paper, start the agent in the paper's folder and say:

```
do tex init
```

The agent finds the main `.tex` file and a free port, shows you the settings, and writes
`.tex-mcp-web.yaml` once you agree. A folder that already has one keeps it.

## Listen

With the plugin installed, a recent Claude Code, one that runs plugin monitors, needs no
command: a session started in the paper's folder listens for Call agent for as long as it
runs, however long the page sits idle. In Codex, or in a Claude Code without plugin
monitors, say:

```
do tex listen
```

and say it again after the agent restarts.

Either way, open `http://localhost:<port>` with the port you chose at init. The agent serves
the page, so there is nothing else to start.

Presses go to one session per paper. A Claude Code session that starts while another
listens leaves the presses to it, and `do tex listen` in any session moves them there.

## Reviewing

- Drag over PDF text and write what should change. Add a suggested wording in the
  replacement box when you have one.
- **Source** beside **Comment** on a PDF selection selects the same words in the source
  editor, at the lines SyncTeX names, or those lines when a macro prints the words. The other
  way, **→ Preview** beside **+ Comment** in the editor's bar marks where the Preview prints
  the selected words. Either turns the view to Split. Both need a PDF built with SyncTeX,
  which **Compile** builds. Cmd (macOS) or Ctrl with a double click on a PDF word does what
  **Source** does for that word.
- The box tool, beside pan and pointer in the PDF's toolbar, turns a drag on the PDF into a
  box to comment on what has no text to select, such as part of a figure. The box holds
  while a compile leaves what it covers as it was.
- **+ Note** comments on the whole paper, and the **Sections** tab comments on a section.
  You can also select text in the Source tab and press **+ Comment**.
- Press **Call agent**, the bell in the Comments tab, when your comments are ready. The
  listening agent edits the source, compiles, and replies in the same thread. Without
  listen, ask the agent in chat to handle the tex comments.
- **Resolve** a thread whose edit satisfies you, or **Reply** in it when it does not.
  **Archive** sets a thread aside, and it still takes replies.
- Edits in the Source tab are saved when you press **Save**.
- With `translation` set, the language's button shows the source translated under the
  Preview or the editor. In Split, the menu beside the view tabs puts it under the Preview,
  under Source, beside Source, or instead of Source. The grips between panes are dragged to
  share the room, each place keeps its own share, and the page remembers both. Each
  paragraph shows its source lines, and the one under the editor's cursor is marked and
  brought into view. The **A−** and **A+** buttons step the type size.
- **Import Overleaf comments** in the top bar's menu brings Overleaf's open threads in as
  comments on the words they were written about, with their replies. With `overleaf` set,
  **Bring in from Overleaf** reads them from the project itself, signed in with your
  Overleaf session cookie (`overleaf_session2`, copied once from the browser's developer
  tools). The cookie is kept in `~/.config/tex-mcp-web/overleaf-cookie`, readable by you
  alone, or given by `TEX_MCP_WEB_OVERLEAF_COOKIE`, and serves every paper you open.
  Without it, choose the `comments.json` that Overleaf Comments Export writes. Bringing
  them in again adds only new threads and replies; a comment you deleted here stays deleted.
- With `overleaf` set and the cookie kept, an open page also sits in the Overleaf project,
  read-only: the top bar shows a face for each collaborator there, its list says which file
  and line each is on, their cursors are drawn in the editor on that file, and a file they
  edit says so above the editor. It sends Overleaf no edit. It opens the main file and
  puts one cursor at its top, which is what makes the others' editors send their cursors
  at once, so to them it is one more of your tabs, idle.
- While a page is open that way, comments come in by themselves, within seconds, as do
  their replies, and they close and open with Overleaf, unless you changed that one here.
  A thread deleted on Overleaf closes here and keeps what was said. A message reworded on
  Overleaf stays as it came in. Opening the page reads what was said in the meantime.
- With `overleaf_write: true`, a thread from Overleaf also has **Reply on Overleaf**,
  **Resolve on Overleaf** and **Reopen on Overleaf**. Only these go there, for everyone in
  the project to read: a plain reply stays here, since it may be an instruction to the
  agent, and the agent's never go. Overleaf takes it first, so what it refuses is not
  shown as done here.
- With `overleaf_sync: true`, the paper's files and the project's are kept the same both
  ways while a page is open: an edit on Overleaf is written into the file within a second,
  and what changes in a file here, from the page, an editor or the agent, is sent as the
  smallest edit that makes it, so comments on words left alone keep them. A save from
  the page over an edit from Overleaf takes both. Edits made on either side while no page
  was open come together when one is. Nothing is overwritten on a guess: a file that
  differs with nothing to say which side changed it, an edit that would delete every word
  of an open comment, and an edit Overleaf turns away each stop that file, and the
  Overleaf list in the top bar asks which text stands. The text that gives way is kept in
  `.tex-mcp-web/overleaf-sync/kept/`. Only Overleaf's older kind of document
  (`sharejs-text-ot`) is followed, and files added, renamed or deleted on Overleaf are not
  mirrored beyond taking up a document's text.

## Suggested edits

The thread shows the source lines the agent would change as a −/+ pair, and the Source tab
marks the same text in red. **Apply suggestion** writes the change into the file. Proposing
again replaces the proposal.

## Editor keys

The Vim button in the editor's bar turns on Vim keys, and the page remembers the choice.

- `:w` saves and `:make` compiles. `jj` typed quickly, Escape or Ctrl+[ leaves Insert mode.
- `gc` comments on the text a motion or a visual selection covers, and `gs` marks that text
  in the Preview.
- `K` opens the comment under the cursor in the Comments tab.
- `gd` or Ctrl+] goes from `\input`, `\ref` or `\cite` to the file, label or `.bib` entry,
  and Ctrl+O or Ctrl+T comes back. Browsers on Windows keep Ctrl+T for a new tab.
- A yank also goes to the system clipboard.

With or without Vim keys, Ctrl+S saves, F12 and Shift+F12 go to a definition and back,
Ctrl+Alt+M comments on the selection, Ctrl+Alt+P marks it in the Preview, and Ctrl+Alt+K
opens the comment under the cursor. Ctrl+Enter compiles, or sends a comment being written,
and Ctrl+Alt+Enter presses Call agent. On macOS, Cmd takes the place of Ctrl and Option of
Alt in these. Ctrl+[ drops a comment being written, with Ctrl on macOS too.

As you type, the editor offers what fits there: the paper's labels after `\ref` and its
kin, its `.bib` entries after `\cite` and its kin (a title's words find an entry too),
environments after `\begin` (which then gets its `\end`) and the open one after `\end`,
`.tex` files after `\input`, pictures after `\includegraphics`, and commands after a
backslash, those the paper defines among them. Enter or Tab takes the choice; Enter after a
command typed out in full starts the next line. Ctrl+Space opens the list by hand.

A browser extension that drives pages with Vim keys takes Escape and Ctrl+[ before the
editor does and moves the focus out of it, which leaves the editor in Insert mode. Turn the
extension off for the review page's address.

## On a tablet or phone

![The review page on a tablet and a phone, zoomed to a commented caption: the PDF above, its thread below.](docs/images/mobile.png)

Forward the port you chose at init over SSH, from Termux on Android or iSH on iOS, then open
`http://localhost:<port>` in the phone's browser.

```
ssh -L <port>:localhost:<port> <server>
```

## Configuration

Edit `.tex-mcp-web.yaml` at the paper's root. A saved change applies at once, except `port`
and `dir`, which apply when Claude Code or Codex restarts.

```yaml
main: main.tex
watch:
- '*.tex'
- '*.bib'
ignore:
- '*_backup.tex'
compiler: auto
port: 8765
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
| `references` | Folder holding only the papers yours builds on, converted to Markdown, relative to this file or absolute. Unset means no list of their terms. |
| `translation` | `language` puts a button for that language beside **+ Note**: it shows each paragraph of the source translated by `claude -p`, with those on the current page marked. `instructions` adds rules for the translator, one per line. A paragraph is translated once per text, and of a figure or table only the caption is. Unset means no translation. |
| `overleaf` | The paper's Overleaf project, by its link, whose comments an open page follows and **Bring in from Overleaf** reads. Unset means comments come in only from a file. |
| `overleaf_write` | `true` lets you send replies and closes to that project, each with its own button. Unset means nothing is sent there. |
| `overleaf_sync` | `true` keeps the paper's files and that project's the same both ways while a page is open. Unset means files are not touched and no edit is sent. |

## See also

[html-mcp-web](https://github.com/MiiKiyoshi/html-mcp-web) reviews an AI agent's HTML slides or report the same way and hands finished slides over as an editable PowerPoint deck.

## Acknowledgements

tex-mcp-web is a hard fork of [queelius/scholia at commit `e6c7454`](https://github.com/queelius/scholia/commit/e6c745400d2ad70fb43eca053e31183d48765f89) (version 0.6.1), independently developed since under the MIT license. See [`LICENSE`](LICENSE). PDF viewing uses [EmbedPDF](https://github.com/embedpdf/embed-pdf-viewer) and its PDFium WebAssembly engine, whose notices are in [`tex_mcp_web/static/embedpdf/LICENSE`](tex_mcp_web/static/embedpdf/LICENSE) and [`tex_mcp_web/static/embedpdf/LICENSE.pdfium`](tex_mcp_web/static/embedpdf/LICENSE.pdfium). The bundled viewer differs from its release in its zoom gesture alone: a wheel or pinch inside a column keeps that column, the half of the page it is on, whole in the pane while the pane holds it and filling the pane once it is wider; on the middle of the page it zooms about the pointer; and a mouse notch steps by a quarter. Source highlighting and completion use [Ace Editor builds 1.44.0](https://github.com/ajaxorg/ace-builds/tree/v1.44.0) under the BSD-3-Clause license, and its notice is in [`tex_mcp_web/static/ace/LICENSE`](tex_mcp_web/static/ace/LICENSE).
