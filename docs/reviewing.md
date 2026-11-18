# Reviewing in detail

## Commenting

- Drag over PDF text and write what should change.
- With a paper that follows an Overleaf project, the leaf beside the comment button (the
  speech bubble) in the editor's bar makes a comment on source text start a thread for Overleaf: its first
  message, posted at once, a private note from which the agent drafts that message for you to send, or both.
  [Working with Overleaf](overleaf.md) says more.
- The box tool, beside pan and pointer in the PDF's toolbar, turns a drag on the PDF into a
  box to comment on what has no text to select, such as part of a figure. The box holds
  while a compile leaves what it covers as it was.
- **Add a paper note…** in the ⋯ menu comments on the whole paper, and the **Sections** tab comments on a section. A
  section comment stays on the heading it was made on as the file is edited, also when
  another heading has the same title. Text selected in the Source tab takes a comment by
  the comment button, the speech bubble in the editor's bar.
- **Source** beside **Comment** on a PDF selection selects the same words in the source
  editor, at the lines SyncTeX names, or those lines when a macro prints the words. The
  other way, the button after the comment button in the editor's bar, which reads
  *show it in the Preview* when pointed at, marks where the Preview
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

## Problems

The **Problems** tab lists what the last build said, errors first and then warnings, and
under them where the paper stands against a page limit. **BODY END** is the last line before
the bibliography: its page and column, and how many more lines fit under it in that column,
once the paper has full columns to measure by. Each **SHORT LINE** is a paragraph whose last line is narrower than
a quarter of its column, in a list item or a centered caption too, with the words on it: a slightly shorter paragraph pulls such a
line up and gives the line back. A click on either marks the line in the
Preview, where the layout is, and in Split also moves the editor to its source line, which
for a paragraph is the line that holds its last words. The number on the tab counts the errors, the warnings
and the short lines. The agent's `compile()` reports the same two things, so you and the
agent look at one list.

The bibliography is known by its `.bbl` file, through SyncTeX, at the paper's end. A paper
that sets its references otherwise, or puts an appendix after them, has its body end at the
last line of the PDF. Running heads, page numbers and the words inside an included picture are left out.

## Threads

- What is said in a thread is **private** or **public**, and each entry says which. A
  private entry stays on this page, for you and the agent. A public entry is on Overleaf,
  for everyone in the project. Everything in the Comments tab is private. A thread from
  [Overleaf](overleaf.md) holds both: your co-authors' messages and the replies you sent
  are public, and your talk with the agent between them is private.
- The Comments tab opens on **to do**: the comments the agent has yet to answer, one you
  just wrote or replied in, and those whose suggestion waits for you to apply it. An answer
  with nothing to apply takes a comment out of it, and so does applying. The number on the
  tab counts them. **open** lists every open comment, and one opened from its mark in the
  PDF or the editor shows there when to do leaves it out. The PDF and the editor mark every
  open comment of the tab, whatever the list shows.
- The search box above the comments, which `/` goes to, lists the tab's comments that hold
  every word typed, in any letter case and whatever their status: in a comment or a reply,
  the name of whoever wrote in it on Overleaf, the words it is on, its file, its suggestion
  or its draft for Overleaf. Enter gives the
  keys to the comments found, and Escape in the box, or choosing a view, puts the search
  away.
- What you wrote in a thread carries **Edit** and **Delete**. **Delete** on a thread's first
  message deletes the thread.
- **Resolve** a thread whose edit satisfies you, or **Reply privately** in it when it does not.
  **Archive** sets a thread aside, and it still takes replies.
- A proposed edit shows as the source lines the agent would change, as a −/+ pair, and the
  Source tab marks the same text in red. **Apply suggestion** writes the change into the
  file. **Reject** takes the proposal off the card, leaves the file as it is, and notes in
  the thread what was turned down. Say why in a private reply. Your next **Call agent** tells
  the agent once which of the two you did. Proposing again replaces the proposal.
- A comment on source text follows the file's edits as a comment on Overleaf does: text
  inserted before it moves it, inside it widens it, words written over its words take it,
  and deleting all of them leaves it where they were. A paragraph moved elsewhere takes its
  comments along. The card says **EDITED** once its words changed, and **STALE** only when
  its file is gone.
- The row above the comments acts on the threads picked by their boxes. From the left: the
  view list and **Expand all**; the box that picks every thread listed or lets them all go;
  **Resolve**, which reads **Reopen** when the picked threads are resolved or archived and,
  as `R` does, resolves the open ones of a mix; **Archive**, or in the Overleaf tab **Accept**,
  **Reject** and **Send to Overleaf**; then the sync button and **Call agent**. On a narrow
  panel the word **Resolve** goes first, then **Expand all**, the sync button and **Call
  agent** wait behind **⋯** with their names. What acts on the picked threads stays in the
  row, which takes two lines only when even that does not fit.

## Comment keys

The comments go by keys, as a notebook's cells do. The keys act on the comment with the ring
around it while the Comments tab is shown and nothing is being typed in. `?`, or **Keyboard
shortcuts** in the ⋯ menu, lists them on the page, and a comment's buttons name their keys in
their tooltips. Once you move through the comments by keys, the ringed comment's buttons show
their keys' letters at their top-left corners, until the mouse moves or a box takes the
typing.

| Keys | Do |
|---|---|
| `j` `k`, ↓ ↑ | Next and previous comment. The ringed card stays a third of the way down the list, and one that leaves the view, resolved, archived, deleted, applied or sent, hands the ring to the next in that same place. |
| `gg` `G`, Home End | First and last comment |
| Space | Open or close the card, and show where the comment is in the PDF or the source, its line a third of the way down the editor |
| Shift+Space | Open or close every card |
| `K`, Ctrl+Alt+K | Go into the source at the comment's words, the keys going with the cursor. `K` in the editor, or Ctrl+Alt+K without Vim keys, comes back |
| `r` | Reply privately, where the agent reads it and Overleaf does not |
| `i` | Write the thread's reply for Overleaf |
| `e` | Edit the last thing you wrote in the thread, here or on Overleaf |
| `c` | Add a paper note |
| Escape | Leave a box: a reply or an edit being written is dropped, a reply for Overleaf keeps its words |
| `R` | Resolve, or reopen a comment that is not open |
| `A` | Archive |
| `aa` | Apply the suggestion to the file, or accept a suggested edit on Overleaf |
| `nn` | Reject the suggestion, or with none the draft for Overleaf |
| `ss` | Send the public reply to Overleaf, for everyone in the project |
| `dd` | Delete, once you say yes, or reject a suggested edit on Overleaf |
| `D` | Delete the last thing you wrote in the thread, here or on Overleaf, once you say yes |
| `x` | Pick the comment, or let it go |
| `*` | Pick every comment, or let them all go |
| `v` | Hold a range, which the moving keys stretch. `R`, `A`, `ss` and `x` then act on the whole range, and Escape or `v` lets it go |
| `/` | Search the tab's comments, whatever their status |
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

- **Theme** picks **System**, where the page starts, which follows the system's light or dark
  as it changes, or **Light**, **Dark**, or **Warm**, a softer paper tone for long writing.
  The PDF's pages stay as compiled in each.
- **Text size of the top bar and panels** sets their text from 90% to 130%, and leaves the
  editor, the translation, the PDF, the panel's tabs and the dialogs as they are.
- **Send a comment with** chooses whether Enter or Shift+Enter sends a comment being written.
- **Shortcut letters on a comment's buttons** shows or hides the keys' letters on the ringed
  comment's buttons.
- **Editor cursor position** at **1/3 down** or **1/2 down** keeps the cursor's line that far
  down the editor as the keys move it, Vim keys or not, so the text moves and the eyes stay.
  The first lines stay at the top and the last ones at the bottom, and a click leaves the
  text where it is. **Anywhere** lets the cursor move over the text as usual.
- **Download PDF** in the ⋯ menu saves the PDF. As on Overleaf, the file is named after the
  Overleaf project, with each character other than a letter or a digit turned to `_`. A
  paper with no Overleaf project keeps the PDF's own name, such as `paper.pdf`.

The page remembers each choice. Edits in the Source tab are saved when you press **Save**.

## Updating

When a newer release is out, the ⋯ menu atop the page offers **Update to v…**, above the
version that runs. A click installs it. The page's server runs inside the agent, which keeps
the code it started with, so a band across the page then asks you to reconnect tex-mcp in
your agent: Claude Code does from `/mcp`, and any agent does when it starts again. The page
then asks to be reloaded. An install that cannot update itself, one made by another tool
than [INSTALL.md](../INSTALL.md)'s steps or `pip` in a virtual environment, says to ask your
agent to update tex-mcp-web. `update_check: false` in the [configuration](configuration.md)
stops the page asking for the newest release.

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
