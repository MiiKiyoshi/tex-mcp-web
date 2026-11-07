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
- The row above the comments acts on the threads picked by their boxes. The buttons
  pressed less often, **Expand all**, **Archive**, **Reopen** and the eye for Overleaf's
  comments, sit together at the row's end, and behind **⋯** with their names when the
  panel is too narrow for the whole row.
- Edits in the Source tab are saved when you press **Save**.
- The top bar's menu picks the page's look: **Light**, **Dark**, or **Warm**, a softer paper
  tone for long writing, as a screen looks with its blue light turned down. The PDF's pages
  stay as compiled in each. The page remembers the choice.
- With `translation` set, the language's button shows the source translated under the
  Preview or the editor. In Split, the menu beside the view tabs puts it under the Preview,
  under Source, beside Source, or instead of Source. The grips between panes are dragged to
  share the room, each place keeps its own share, and the page remembers both. Each
  paragraph shows its source lines, and the one under the editor's cursor is marked and
  brought into view. The **A−** and **A+** buttons step the type size.
- **Connect to Overleaf…** in the top bar's menu takes the project's link
  (`https://www.overleaf.com/project/…`) and writes it into `.tex-mcp-web.yaml` as
  `overleaf:`. **Disconnect** there takes it out. A project on another Overleaf server is
  named in the file by hand. Once connected, the same dialog holds everything Overleaf:
  the cookie, who is in the project, where the folder stands against Overleaf's git
  branch, and **Sync comments**.
- Overleaf is read with your Overleaf session cookie (`overleaf_session2`, copied once
  from the browser's developer tools). The cookie is kept in
  `~/.config/tex-mcp-web/overleaf-cookie`, readable by you alone, or given by
  `TEX_MCP_WEB_OVERLEAF_COOKIE`, and serves every paper you open.
- When the paper's folder is a git clone of the project (a remote whose address ends in
  the project's id, as `https://git.overleaf.com/<id>` does), opening the dialog fetches
  Overleaf's branch and says where the folder stands: at Overleaf's commit, with commits
  Overleaf lacks, lacking commits Overleaf has, or both. **Pull** takes Overleaf's commits
  in, as a fast-forward or as a merge git makes without conflicts, and changes nothing when
  the merge would conflict or write over a change not committed. **Push** sends the
  folder's commits, never forced, and leaves changes not committed where they are. A token
  in the remote's address never reaches the page.
- Comments from Overleaf are kept as Overleaf has them. **Sync comments** brings its open
  threads in as comments on the words they were written about, with their replies. In a
  git clone it waits until the folder is at Overleaf's commit, and with `overleaf_sync` on
  until every file is in step, since a thread is on words.
  A file whose text has the hash of Overleaf's text holds the same text, and there each
  comment sits exactly where Overleaf has it and moves when Overleaf moves it. In a file
  that differs, a comment goes on its words, and this file's own edits keep it there. A
  thread whose words are on Overleaf but not yet in the file here sits on the paper, with
  its place on Overleaf shown under it, and moves onto its words once they reach the file.
- A thread from Overleaf is open, resolved or gone as it is on Overleaf, so the page offers
  no Resolve, Reopen, Archive or Delete on it. A message reworded or deleted there is
  reworded or deleted here, a thread deleted there is deleted here, and one deleted here
  comes back. Replies written here stay in their thread.
- Once comments have come in from Overleaf, the eye button with the leaf above the comments
  hides them from the list, the editor and the PDF, and shows them again. It changes
  no comment and is kept per browser. While they are hidden, the list says how many.
- With `overleaf` set and the cookie kept, an open page also sits in the Overleaf project,
  read-only. The top bar shows a face for each collaborator there, which goes to the file
  and line they are on, their cursors are drawn in the editor on that file, and a file
  they edit says so above the editor. The dialog lists who is where and what was edited
  while the page was open. A leaf button shows in the top bar only when something needs a
  look: signed out, reconnecting, comments not coming in, or files waiting for a choice.
  The page sends Overleaf no edit unless `overleaf_sync` is on (below). It opens the main
  file and puts one cursor at its top, which is what makes the others' editors send their
  cursors at once, so to them it is one more of your tabs, idle.
- While a page is open that way, a new thread on Overleaf is counted on **Sync comments**
  within seconds, and threads already here follow theirs by themselves.
- A thread from Overleaf has a box under it for a reply on Overleaf, which everyone in the
  project reads. The agent can put its draft there (`write_comments` with
  `action="overleaf"`), one per thread, and you edit it and send it with **Send to** and
  the leaf. The send button with the leaf above the comments sends the reply of every
  picked thread from Overleaf, one thread after another, and a refused one keeps its words
  and says why on its card while the rest go. The agent cannot send. A plain reply stays
  here, since it may be an instruction to the agent, and each entry on such a thread says
  whether it went to Overleaf or stayed here. A draft naming the source ranges it describes goes only once Overleaf has what
  those ranges held, read from Overleaf's branch in a git clone and from the files kept in
  step otherwise, and the refusal says whether to commit or to push first. Overleaf takes
  the reply first, so what it refuses is not shown as said here.
- With `overleaf_sync: true`, the paper's files and the project's are kept the same both
  ways while a page is open: an edit on Overleaf is written into the file within a second,
  and what changes in a file here, from the page, an editor or the agent, is sent as the
  smallest edit that makes it, so comments on words left alone keep them, and words
  written over commented words take the comment, as if typed inside it. A save from
  the page over an edit from Overleaf takes both. Edits made on either side while no page
  was open come together when one is. Nothing is overwritten on a guess: a file that
  differs with nothing to say which side changed it, an edit that would delete every word
  of an open comment, and an edit Overleaf turns away each stop that file: a bar across
  the top of the page, and the editor on that file, say why and ask which text stands, and
  edits to the file stay here until then. The text that gives way is kept in
  `.tex-mcp-web/overleaf-sync/kept/`. A file that already holds Overleaf's text, brought
  some other way, agrees with it. Only Overleaf's older kind of document
  (`sharejs-text-ot`) is followed, and files added, renamed or deleted on Overleaf are not
  mirrored beyond taking up a document's text. A git clone of the project follows Overleaf
  by pull and push instead, and is not offered this.

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
| `overleaf` | The paper's Overleaf project, by its link, which an open page follows and **Sync comments** reads. Unset means none. |
| `overleaf_sync` | `true` keeps the paper's files and that project's the same both ways while a page is open, unless the folder is a git clone of the project. Unset means files are not touched and no edit is sent. |

## See also

[html-mcp-web](https://github.com/MiiKiyoshi/html-mcp-web) reviews an AI agent's HTML slides or report the same way and hands finished slides over as an editable PowerPoint deck.

## Acknowledgements

tex-mcp-web is a hard fork of [queelius/scholia at commit `e6c7454`](https://github.com/queelius/scholia/commit/e6c745400d2ad70fb43eca053e31183d48765f89) (version 0.6.1), independently developed since under the MIT license. See [`LICENSE`](LICENSE). PDF viewing uses [EmbedPDF](https://github.com/embedpdf/embed-pdf-viewer) and its PDFium WebAssembly engine, whose notices are in [`tex_mcp_web/static/embedpdf/LICENSE`](tex_mcp_web/static/embedpdf/LICENSE) and [`tex_mcp_web/static/embedpdf/LICENSE.pdfium`](tex_mcp_web/static/embedpdf/LICENSE.pdfium). The bundled viewer differs from its release in its zoom gesture alone: a wheel or pinch inside a column keeps that column, the half of the page it is on, whole in the pane while the pane holds it and filling the pane once it is wider; on the middle of the page it zooms about the pointer; and a mouse notch steps by a quarter. Source highlighting and completion use [Ace Editor builds 1.44.0](https://github.com/ajaxorg/ace-builds/tree/v1.44.0) under the BSD-3-Clause license, and its notice is in [`tex_mcp_web/static/ace/LICENSE`](tex_mcp_web/static/ace/LICENSE).
