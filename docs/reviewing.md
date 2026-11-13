# Reviewing in detail

## Commenting

- Drag over PDF text and write what should change.
- With a paper that follows an Overleaf project, the leaf beside **+ Comment** in the
  editor's bar makes a comment on source text start a thread for Overleaf, which the agent
  drafts from what you write and you send. [Working with Overleaf](overleaf.md) says more.
- The box tool, beside pan and pointer in the PDF's toolbar, turns a drag on the PDF into a
  box to comment on what has no text to select, such as part of a figure. The box holds
  while a compile leaves what it covers as it was.
- **+ Note** comments on the whole paper, and the **Sections** tab comments on a section. A
  section comment stays on the heading it was made on as the file is edited, also when
  another heading has the same title. Text selected in the Source tab takes a comment by
  **+ Comment**.
- **Source** beside **Comment** on a PDF selection selects the same words in the source
  editor, at the lines SyncTeX names, or those lines when a macro prints the words. The
  other way, **→ Preview** beside **+ Comment** in the editor's bar marks where the Preview
  prints the selected words. Either turns the view to Split. Both need a PDF built with
  SyncTeX, which **Compile** builds. Cmd (macOS) or Ctrl with a double click on a PDF word
  does what **Source** does for that word.
- Press **Call agent**, the bell in the Comments tab, when your comments are ready. The
  listening agent edits the source, compiles, and replies in the same thread. Without
  listen, ask the agent in chat to handle the tex comments.

## The PDF

A wheel or pinch over one column of a two-column page keeps that column in view: whole while
it fits the pane, and filling the pane's width once it is wider. Over the middle of the page the
zoom centres on the pointer. A mouse-wheel notch zooms by a quarter. Two fingers on a touch
screen zoom and move the page as they go, the point between them staying between them.

## Threads

- The Comments tab opens on **to do**: the comments whose suggestion waits for you to apply
  it, each gone from it once applied. The number on the tab counts them. **open** lists every open comment, and a comment you
  write, or open from its mark in the PDF or the editor, shows there. The PDF and the editor
  mark every open comment of the tab, whatever the list shows.
- **Resolve** a thread whose edit satisfies you, or **Reply** in it when it does not.
  **Archive** sets a thread aside, and it still takes replies.
- A proposed edit shows as the source lines the agent would change, as a −/+ pair, and the
  Source tab marks the same text in red. **Apply suggestion** writes the change into the
  file. Proposing again replaces the proposal.
- A comment on source text follows the file's edits as a comment on Overleaf does: text
  inserted before it moves it, inside it widens it, words written over its words take it,
  and deleting all of them leaves it where they were. A paragraph moved elsewhere takes its
  comments along. The card says **EDITED** once its words changed, and **STALE** only when
  its file is gone.
- The row above the comments acts on the threads picked by their boxes. The buttons pressed
  less often, **Expand all**, **Archive** and **Reopen**, sit at the row's end, and behind
  **⋯** with their names when the panel is too narrow for the whole row.

## Comment keys

The comments go by keys, as a notebook's cells do. The keys act on the comment with the ring
around it while the Comments tab is shown and nothing is being typed in. `?`, or **Keyboard
shortcuts** in the ⋯ menu, lists them on the page, and a comment's buttons name their keys in
their tooltips. Once you move through the comments by keys, the ringed comment's buttons show
their keys' letters at their top-left corners, until the mouse moves or a box takes the
typing.

| Keys | Do |
|---|---|
| `j` `k`, ↓ ↑ | Next and previous comment. The ringed card stays a third of the way down the list. |
| `gg` `G`, Home End | First and last comment |
| Space | Open or close the card, and show where the comment is in the PDF or the source, its line a third of the way down the editor |
| Shift+Space | Open or close every card |
| `K`, Ctrl+Alt+K | Go into the source at the comment's words, the keys going with the cursor. `K` in the editor, or Ctrl+Alt+K without Vim keys, comes back |
| `r` | Reply here, where the agent reads it and Overleaf does not |
| `i` | Write the thread's reply for Overleaf |
| `e` | Edit the last thing you wrote in the thread |
| `c` | Add a paper note |
| Escape | Leave a box: a reply or an edit being written is dropped, a reply for Overleaf keeps its words |
| `R` | Resolve, or reopen a comment that is not open |
| `A` | Archive |
| `aa` | Apply the suggestion to the file, or accept a suggested edit on Overleaf |
| `ss` | Send the reply to Overleaf, for everyone in the project |
| `dd` | Delete, once you say yes, or reject a suggested edit on Overleaf |
| `x` | Pick the comment, or let it go |
| `*` | Pick every comment, or let them all go |
| `v` | Hold a range, which the moving keys stretch. `R`, `A`, `ss` and `x` then act on the whole range, and Escape or `v` lets it go |
| `f` `F` | Next and previous view in the list atop the tab: to do, open, … |
| `t` `T` | Next and previous tab |
| `?` | These keys |

With comments picked, `R`, `A` and `ss` act on them. A click on a card puts the ring on it,
and choosing a comment tab or a view in the list atop gives the keys back to the comments.
With a Korean input method on, a letter key is read by where it sits on the keyboard, so the
keys work as they are.

## Editor keys

The Vim button in the editor's bar turns on Vim keys, and the page remembers the choice.

- `:w` saves and `:make` compiles. `jj` typed quickly, Escape or Ctrl+[ leaves Insert mode.
- `gc` comments on the text a motion or a visual selection covers, and `gs` marks that text
  in the Preview.
- `K` opens the comment under the cursor in the Comments tab, or the edit suggested on
  Overleaf in the leaf tab's suggested edits view, where the comment keys then act on it, and
  off any comment gives the keys to the list as it was. `K` in the list comes
  back to the comment's words.
- `gd` or Ctrl+] goes from `\input`, `\ref` or `\cite` to the file, label or `.bib` entry,
  and Ctrl+O or Ctrl+T comes back. Browsers on Windows keep Ctrl+T for a new tab.
- A yank also goes to the system clipboard.

With or without Vim keys, Ctrl+S saves, F12 and Shift+F12 go to a definition and back,
Ctrl+Alt+M comments on the selection, Ctrl+Alt+P marks it in the Preview, and Ctrl+Alt+K
does what `K` does, in the editor and in the list alike. Ctrl+Enter compiles, or sends a comment being written,
and Ctrl+Alt+Enter presses Call agent. On macOS, Cmd takes the place of Ctrl and Option of
Alt in these. Ctrl+[ drops a comment being written, with Ctrl on macOS too.

As you type, the editor offers what fits there: the paper's labels after `\ref` and its kin,
its `.bib` entries after `\cite` and its kin (a title's words find an entry too),
environments after `\begin` (which then gets its `\end`) and the open one after `\end`,
`.tex` files after `\input`, pictures after `\includegraphics`, and commands after a
backslash, those the paper defines among them. Enter or Tab takes the choice, and Enter after
a command typed out in full starts the next line. Ctrl+Space opens the list by hand.

A browser extension that drives pages with Vim keys takes Escape and Ctrl+[ before the editor
does and moves the focus out of it, which leaves the editor in Insert mode. Turn the
extension off for the review page's address.

## Settings and the ⋯ menu

- **Look** picks **System**, where the page starts, which follows the system's light or dark
  as it changes, or **Light**, **Dark**, or **Warm**, a softer paper tone for long writing.
  The PDF's pages stay as compiled in each.
- **Text size** sets the top bar's and the panels' text from 90% to 130%, and leaves the
  editor, the translation, the PDF, the panel's tabs and the dialogs as they are.
- **Send with** chooses whether Enter or Shift+Enter sends a comment being written.
- **Key letters** shows or hides the keys' letters on the ringed comment's buttons.
- **Editor cursor** at **A third down** keeps the cursor's line a third of the way down the
  editor as the keys move it, Vim keys or not, so the text moves and the eyes stay. The first
  lines stay at the top and the last ones at the bottom, and a click leaves the text where it
  is. **Anywhere** lets the cursor move over the text as usual.
- **Download PDF** in the ⋯ menu saves the PDF. As on Overleaf, the file is named after the
  Overleaf project, with each character other than a letter or a digit turned to `_`. A
  paper with no Overleaf project keeps the PDF's own name, such as `paper.pdf`.

The page remembers each choice. Edits in the Source tab are saved when you press **Save**.

## Translation

With `translation` set, the language's button shows the source translated under the Preview
or the editor. In Split, the menu beside the view tabs puts it under the Preview, under
Source, beside Source, or instead of Source. The grips between panes are dragged to share the
room, each place keeps its own share, and the page remembers both. Each paragraph shows its
source lines, and the one under the editor's cursor is marked and brought into view. The
**A−** and **A+** buttons step the type size.

## Listening

Presses of Call agent go to one session per paper. A Claude Code session that starts while
another listens leaves the presses to it, and `do tex listen` in any session moves them
there. When the session serving the page ends, the page goes with it, and the next session to
call a tex tool serves it again and listens.
