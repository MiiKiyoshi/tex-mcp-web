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
When the session serving the page ends, the page goes with it, and the next session to
call a tex tool serves it again and listens.

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
  A section comment stays on the heading it was made on as the file is edited, also when
  another heading has the same title. You can also select text in the Source tab and press
  **+ Comment**.
- Press **Call agent**, the bell in the Comments tab, when your comments are ready. The
  listening agent edits the source, compiles, and replies in the same thread. Without
  listen, ask the agent in chat to handle the tex comments.
- **Resolve** a thread whose edit satisfies you, or **Reply** in it when it does not.
  **Archive** sets a thread aside, and it still takes replies.
- A comment on source text follows the file's edits as a comment on Overleaf does: text
  inserted before it moves it, inside it widens it, words written over its words take it,
  and deleting all of them leaves it where they were. A paragraph moved elsewhere takes its
  comments along. The card says **EDITED** once its words changed, and **STALE** only when
  its file is gone.
- The row above the comments acts on the threads picked by their boxes. The buttons
  pressed less often, **Expand all**, **Archive** and **Reopen**, sit together at the row's
  end, and behind **⋯** with their names when the panel is too narrow for the whole row.
- Edits in the Source tab are saved when you press **Save**.
- **Settings**, the gear in the top bar (in the ⋯ menu on a phone), picks the page's look:
  **Light**, **Dark**, or **Warm**, a softer paper tone for long writing, as a screen looks with its blue light turned down. The PDF's pages
  stay as compiled in each. The page remembers the choice.
- **Download PDF** in the ⋯ menu saves the PDF. As on Overleaf, the file is named after the
  Overleaf project, with each character other than a letter or a digit turned to `_`. A
  paper with no Overleaf project keeps the PDF's own name, such as `paper.pdf`.
- With `translation` set, the language's button shows the source translated under the
  Preview or the editor. In Split, the menu beside the view tabs puts it under the Preview,
  under Source, beside Source, or instead of Source. The grips between panes are dragged to
  share the room, each place keeps its own share, and the page remembers both. Each
  paragraph shows its source lines, and the one under the editor's cursor is marked and
  brought into view. The **A−** and **A+** buttons step the type size.
- **Settings** takes the Overleaf project's link (`https://www.overleaf.com/project/…`) and
  writes it into `.tex-mcp-web.yaml` as `overleaf:`. **Disconnect** there takes it out. A
  project on another Overleaf server is named in the file by hand. Once connected,
  Settings holds everything Overleaf: the cookie, how the files follow Overleaf (below),
  who is in the project, and where the folder stands against Overleaf's git branch.
- Overleaf is read with your Overleaf session cookie (`overleaf_session2`, copied once
  from the browser's developer tools). The cookie is kept in
  `~/.config/tex-mcp-web/overleaf-cookie`, readable by you alone, or given by
  `TEX_MCP_WEB_OVERLEAF_COOKIE`, and serves every paper you open.
- When the paper's folder is a git clone of the project (a remote whose address ends in
  the project's id, as `https://git.overleaf.com/<id>` does), opening Settings fetches
  Overleaf's branch and says where the folder stands: at Overleaf's commit, with commits
  Overleaf lacks, lacking commits Overleaf has, or both. **git pull** in the top bar takes
  Overleaf's commits in, as a fast-forward or as a merge git makes without conflicts, and
  changes nothing when the merge would conflict or write over a change not committed.
  **commit & push** lists the files git tracks whose edits are not committed, commits them
  under a message you may change, the time to begin with, and then sends the folder's
  commits by git push, never forced. Files git does not track stay out of the commit. A
  token in the remote's address never reaches the page, and git push refuses a commit that
  holds the page's own records.
- The page's own records, `.tex-mcp-web/`, with the comments, copies of the text and the
  page's locks, sit beside the paper. In any git repository the paper's folder is in, a
  clone of the Overleaf project or one of your own, such as a backup kept on a free plan,
  the page names them in the repository's local exclude file when it starts, so a commit
  of every file leaves them out. A repository with no remote of the project is no git
  clone of it here, and the files follow Overleaf by **Get** and **Send**, or Live.
- Comments from Overleaf are kept as Overleaf has them, in their own tab: the leaf
  **Comments** tab beside **Comments**, shown once the paper names an Overleaf project or
  holds threads from one. The editor and the PDF mark the comments of the tab shown. The
  tab lists the threads in the order the paper reads them and opens on **unanswered**, the
  open threads whose last message on Overleaf is someone else's: a reply sent from here,
  or one written on Overleaf under your name, takes a thread out of it and into
  **answered**. The list atop the tab also shows the open, resolved and all threads. The sync button with the leaf atop
  that tab brings Overleaf's open threads in as comments on the words they were written
  about, with their replies. In a git clone it waits until
  the folder is at Overleaf's commit, since a thread is on words.
  With the files following Overleaf live there is no sync button: a new thread comes
  in by itself whenever every file is in step.
  A file whose text has the hash of Overleaf's text holds the same text, and there each
  comment sits exactly where Overleaf has it and moves when Overleaf moves it. In a file
  that differs, a comment goes on its words, and this file's own edits keep it there. A
  thread whose words are on Overleaf but not yet in the file here sits on the paper, with
  its place on Overleaf shown under it, and moves onto its words once they reach the file.
- A thread from Overleaf is open, resolved or gone as it is on Overleaf. **Resolve** and
  **Reopen**, on its card or on the picked threads, do it on Overleaf first, for everyone
  in the project, and then here. What Overleaf refuses stays as it was. The agent cannot
  resolve or reopen. Overleaf has no archive and deletes a thread only there, so the page
  offers neither on it. A message reworded or deleted there is reworded or deleted here,
  a thread deleted there is deleted here, and one deleted here comes back. Replies
  written here stay in their thread.
- With `overleaf` set and the cookie kept, an open page also sits in the Overleaf project.
  The top bar shows a face for each collaborator there, which goes to the file and line
  they are on, their cursors are drawn in the editor on that file, and a file edited there
  says so above the editor until this folder holds those edits too, by git pull, by Get, or
  by itself when the files follow Overleaf live. Settings lists who is where and those
  files. A leaf button shows in the top bar only when something needs a look: signed
  out, reconnecting, comments not coming in, or files waiting for a choice. The page sends
  Overleaf no edit to the text unless the files follow it live or you press Send (below).
  It opens the main file and puts one cursor at its top, which is what makes the others'
  editors send their cursors at once, so to them it is one more of your tabs, idle.
- While a page is open that way, a new thread on Overleaf is counted on the sync button
  within seconds, or comes in by itself when the files follow Overleaf live, and threads
  already here follow theirs by themselves.
- A thread from Overleaf has a box under it for a reply on Overleaf, which everyone in the
  project reads. The agent can put its draft there (`write_comments` with
  `action="overleaf"`), one per thread, and you edit it and send it with **Send to** and
  the leaf. The send button with the leaf atop the leaf **Comments** tab sends the reply
  of every picked thread, one thread after another, and a refused one keeps its words and
  says why on its card while the rest go. The agent cannot send. **Reply** on such a
  thread stays here: it is where you talk the draft over with the agent, which answers in
  the same thread and redrafts the reply for Overleaf as you go. That talk never reaches
  Overleaf, and it sits in the thread in time order with the messages from Overleaf and
  the replies you sent, each entry marked as written on Overleaf, sent there, or here
  only, so the thread reads whole. An entry Overleaf holds goes by the name
  Overleaf gives its writer, as there, and is answered rather than edited. A reply whose
  draft names source ranges in a file that is not the same here as on Overleaf may
  describe an edit Overleaf does not have yet, so the page asks first whether to send it
  anyway. In a git clone that is a change not committed, not pushed or not pulled, read
  against Overleaf's branch afresh. Picked replies sent together ask once, at the end,
  naming every such file. Overleaf takes the reply first, so what it refuses is not shown
  as said here.
- The agent can also draft a new thread for Overleaf on source lines (`write_comments` with
  `action="overleaf"` and an `anchor` instead of an `id`). It waits in the leaf
  **Comments** tab as a card with nothing said in it yet, the draft being its first
  message, and you edit it and send it the same way, alone or with the picked threads, or
  delete it. Sent, it opens on Overleaf as Overleaf's editor opens a thread: the lines are
  looked for in Overleaf's file first, the occurrence nearest the line they are on here,
  then the message goes, then an edit that changes no text puts the thread on them. Lines
  Overleaf does not have yet leave nothing behind there, and the card keeps its draft.
  A draft naming the source ranges it describes goes only once Overleaf has what those
  ranges held, read from the files when they follow Overleaf live and from Overleaf's
  branch in a git clone otherwise, and the refusal says to press commit & push first, or,
  when the files here no longer hold those words, that the lines changed after the draft
  and it is drafted again.
- Settings chooses how the files follow Overleaf. **By hand**, the default, changes them
  only when you ask: by git pull and commit & push in a git clone, and by **Get** and **Send**
  otherwise (below). **Live** (`overleaf_sync: true`) keeps the paper's files and the
  project's the same both ways while a page is open: an edit on Overleaf is written into
  the file within a second, and what changes in a file here, from the page, an editor or
  the agent, is sent as the smallest edit that makes it, so comments on words left alone
  keep them, and words written over commented words take the comment, as if typed inside
  it. A save from the page over an edit from Overleaf takes both. Edits made on either
  side while no page was open come together when one is. Nothing is overwritten on a
  guess: a file that differs with nothing to say which side changed it, an edit that would
  delete every word of an open comment, and an edit Overleaf turns away each stop that
  file: a bar across the top of the page, and the editor on that file, say why and ask
  which text stands, and edits to the file stay here until then. The text that gives way
  is kept in `.tex-mcp-web/overleaf-sync/kept/`. A file that already holds Overleaf's
  text, brought some other way, agrees with it. Only Overleaf's older kind of document
  (`sharejs-text-ot`) is followed. Figures and other uploaded files are copied whole when
  one side replaces them, a new file here that `overleaf_add` names goes up by itself, as
  Send sends it under By hand, and in a git clone all of these come by git instead. A file
  deleted on one side waits for a choice, as under By hand. In a git clone, files added,
  renamed or deleted on Overleaf come by git.
- By hand in a folder that is no git clone (Overleaf's git integration is for paid plans),
  the top bar's two buttons are **Get** and **Send**, with the leaf, each shown with the
  number of files it moves. They need no git: the page reads and edits Overleaf's
  documents through the editor session it keeps in the project, as Live does. Opening
  Settings, the page, or the window after a minute reads where the files stand. Get writes
  Overleaf's edits into the files, and where each side changed other places of one file it
  writes both. Send makes the files' edits on Overleaf as small edits, after asking once,
  for everyone in the project, and reads each file back. A file both sides changed in one
  place, or one that differs with nothing to say which side changed it, waits for a choice
  as under Live, and so does an edit that would delete every word of an open comment. Each
  file keeps the same record of the text both sides last agreed on as under Live, so
  switching between By hand and Live picks up where the other left off. Comments sync once
  nothing is left to Get or Send. Figures and other uploaded files move whole: Overleaf
  names each by a hash of its bytes, so the page compares that with the file here without
  downloading it, and a file replaced on both sides waits for a choice. A file added on
  Overleaf comes in by Get. A file only here goes by Send when it matches `overleaf_add`,
  or is of a kind the project already holds, into its folder, which Send makes on Overleaf
  when missing. The paper's compiled PDF never goes. A file deleted on one side is not
  deleted on the other: it is listed in Settings with a choice, to delete it there too or
  to bring it back.
- The agent reads where the folder stands with `overleaf()`, which reads Overleaf again and
  names only what differs: files with edits on Overleaf not here, files with edits here
  not on Overleaf, files waiting for a choice, and in a git clone the commits behind and
  ahead. `read_comments(new=True)` adds one line when Overleaf has edits this folder lacks,
  from what was last read, and nothing otherwise. `overleaf(action="get")` brings those
  edits in, by Get or by git pull in a git clone, and changes nothing when they clash.
  `overleaf(action="push")` runs git push in a git clone. Send stays the reviewer's,
  from the page, as do replies sent to Overleaf and Resolve and Reopen.
- In a git clone, git pull and commit & push go on beside Live. The files Live keeps hold
  Overleaf's text already when its commit arrives, so a git pull takes them as they are
  and brings in the rest, such as a figure added on Overleaf, and reading Overleaf's
  branch takes its commits in by itself when it only moves forward. commit & push leaves
  those files out of its commit, and git push first merges Overleaf's commits, keeping Overleaf's text for those files and the folder's own changes
  to the others, so it sends figures and new files and never older text, and a commit that
  holds other text for one of those files is not pushed. A file edited since Overleaf's
  commit was read stops a git pull until the next read, and while a file waits for a
  choice, git pull and commit & push wait too. Live needs the paper's folder to be the top of
  the clone, where Overleaf's files are.

## Suggested edits

The thread shows the source lines the agent would change as a −/+ pair, and the Source tab
marks the same text in red. **Apply suggestion** writes the change into the file. Proposing
again replaces the proposal.

## Editor keys

The Vim button in the editor's bar turns on Vim keys, and the page remembers the choice.

- `:w` saves and `:make` compiles. `jj` typed quickly, Escape or Ctrl+[ leaves Insert mode.
- `gc` comments on the text a motion or a visual selection covers, and `gs` marks that text
  in the Preview.
- `K` opens the comment under the cursor in the Comments tab, where the
  [comment keys](#comment-keys) then act on it.
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

## Comment keys

The comments go by keys, as a notebook's cells do. The keys act on the comment with the
ring around it while the Comments tab is shown and nothing is being typed in. `?`, or
**Comment keys** in the ⋯ menu, lists them on the page, and a comment's buttons name their
keys in their tooltips. Once you move through the comments by keys, the ringed comment's
buttons show their keys' letters at their top-left corners, until the mouse moves or a box
takes the typing. **Key letters** in Settings turns them off, and the page remembers the
choice.

- `j` and `k`, or ↓ and ↑, move the ring to the next and the previous comment, and leave
  every card as it was. The list moves under the ring, whose card keeps its middle a third
  of the way down but at the list's two ends, so the eye stays put. `gg` or Home goes to the first, and `G`
  or End to the last. Space opens or closes the ringed card, and an opened card shows its
  end, where the thread's latest words and the draft for Overleaf are. `O` opens or closes
  every card.
- Enter shows where the comment is, in the PDF or the source, and the keys stay with the
  list. A click on a comment does the same and puts the ring on it, and choosing a comment
  tab or a view in the list atop gives the keys back to the comments.
- `r` replies here, where the agent reads it and Overleaf does not, `i` puts the cursor in
  the thread's reply for Overleaf, `e` edits the last thing you wrote in the thread, and `c`
  adds a paper note. Escape leaves the box and gives the keys back to the comments: a reply
  or an edit being written is dropped, and a reply for Overleaf keeps its words.
- `R` resolves the comment, or reopens one that is not open, and `A` archives it.
- A key that changes the file, posts to Overleaf or deletes is a letter pressed twice: `aa`
  applies the suggestion, `ss` sends the reply to Overleaf, and `dd` deletes once you say
  yes.
- `x` picks the comment or lets it go, and `*` picks every comment or lets them all go.
  With comments picked, `R`, `A` and `ss` act on them.
- `v` holds a range from the ringed comment, or from the first with none ringed, which the
  keys that move stretch. `R`, `A` and `ss` then act on the whole range at once, `x` picks
  it, or lets it go when it is all picked, and Escape or `v` lets the range go.
- `f` and `F` go to the next and the previous view in the list atop the tab, and `t` and
  `T` to the next and the previous tab.

With a Korean input method on, a letter key is read by where it sits on the keyboard, so
the keys work as they are.

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
| `translation` | `language` puts a button for that language beside **+ Note**: it shows each paragraph of the source translated by `claude -p`, with those on the current page marked. `instructions` adds rules for the translator, one per line. A paragraph is translated once per text, and of a figure or table only the caption is. Unset means no translation. |
| `overleaf` | The paper's Overleaf project, by its link, which an open page follows and the sync button reads. Unset means none. |
| `overleaf_add` | Patterns for the files only here that go to the Overleaf project, by **Send** or under **Live** by themselves, in a folder that is no git clone. Unset means the paper's sources and figures: `*.tex`, `*.bib`, `*.sty`, `*.cls`, `*.bst`, `*.png`, `*.jpg`, `*.jpeg`, `*.pdf`, `*.eps` and `*.svg`. A file of a kind the project already holds is added too, and the paper's compiled PDF never is. |
| `overleaf_sync` | `true` is **Live** in Settings: it keeps the paper's files and that project's the same both ways while a page is open, and brings new threads in by themselves. Unset is **By hand**: files change only by git pull and commit & push in a git clone, or by Get and Send otherwise. |

## See also

[html-mcp-web](https://github.com/MiiKiyoshi/html-mcp-web) reviews an AI agent's HTML slides or report the same way and hands finished slides over as an editable PowerPoint deck.

## Acknowledgements

tex-mcp-web is a hard fork of [queelius/scholia at commit `e6c7454`](https://github.com/queelius/scholia/commit/e6c745400d2ad70fb43eca053e31183d48765f89) (version 0.6.1), independently developed since under the MIT license. See [`LICENSE`](LICENSE). PDF viewing uses [EmbedPDF](https://github.com/embedpdf/embed-pdf-viewer) and its PDFium WebAssembly engine, whose notices are in [`tex_mcp_web/static/embedpdf/LICENSE`](tex_mcp_web/static/embedpdf/LICENSE) and [`tex_mcp_web/static/embedpdf/LICENSE.pdfium`](tex_mcp_web/static/embedpdf/LICENSE.pdfium). The bundled viewer differs from its release in its zoom gesture alone: a wheel or pinch inside a column keeps that column, the half of the page it is on, whole in the pane while the pane holds it and filling the pane once it is wider; on the middle of the page it zooms about the pointer; and a mouse notch steps by a quarter. Source highlighting and completion use [Ace Editor builds 1.44.0](https://github.com/ajaxorg/ace-builds/tree/v1.44.0) under the BSD-3-Clause license, and its notice is in [`tex_mcp_web/static/ace/LICENSE`](tex_mcp_web/static/ace/LICENSE).
