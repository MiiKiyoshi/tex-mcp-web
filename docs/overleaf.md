# Working with Overleaf

The review page follows the paper's Overleaf project: its comment threads and suggested edits
come into their own tab, your co-authors show in the top bar, and the paper's files keep in
step with the project in one of three ways.

## Connecting

**Settings** takes the project's link (`https://www.overleaf.com/project/…`) and writes it
into `.tex-mcp-web.yaml` as `overleaf:`. **Disconnect** there takes it out. A project on
another Overleaf server is named in the file by hand. Once connected, Settings holds
everything Overleaf: the cookie, how the files follow Overleaf, who is in the project, and
where the folder stands against Overleaf's git branch.

Overleaf is read with your Overleaf session cookie (`overleaf_session2`, copied once from
the browser's developer tools). The cookie is kept in
`~/.config/tex-mcp-web/overleaf-cookie`, readable by you alone, or given by
`TEX_MCP_WEB_OVERLEAF_COOKIE`, and serves every paper you open.

The cookie signs in as you, so it goes only to the server the project's link names, over
https (plain http only to this machine), and never on to a place that server redirects to.
Git pulls and pushes use your own git sign-in, not the cookie, and the page never hands the
cookie back: Settings shows only where it comes from. The page answers on this machine
alone, at `localhost` or `127.0.0.1`, and only its own pages, so another site open in your
browser cannot act through it. Anyone with an account on the same machine can still reach
its port, as with any local server, and on a shared machine could act as you on the paper
and on Overleaf through it, unless `page_token` is on: then the page answers only a browser
that opened its link with your token. A browser sends that token to every port of the
address, so on the shared machine's own browser, open no page another account serves there.

## Comment threads

- The threads come into the leaf **Comments** tab beside **Comments**, kept as Overleaf has
  them. The editor and the PDF mark the comments of the tab shown. The tab lists the
  threads in the order the paper reads them and opens on **unanswered**, the open threads
  whose last message on Overleaf is someone else's. A reply sent from here, or one written
  on Overleaf under your name, moves a thread to **answered**. The list atop the tab also
  shows the open, resolved and all threads.
- The sync button with the leaf atop the tab brings Overleaf's open threads in as comments
  on the words they were written about, with their replies. While a page is open, a new
  thread on Overleaf is counted on that button within seconds, and threads already here
  follow theirs by themselves. In a git clone the button waits until the folder is at
  Overleaf's commit, since a thread is on words. With the files following Overleaf live
  there is no button: a new thread comes in by itself whenever every file is in step.
- A file whose text has the hash of Overleaf's text holds the same text, and there each
  comment sits exactly where Overleaf has it and moves when Overleaf moves it. In a file
  that differs, a comment goes on its words, and this file's own edits keep it there. A
  thread whose words are on Overleaf but not yet in the file here sits on the paper, with
  its place on Overleaf shown under it, and moves onto its words once they reach the file.
- A thread is open, resolved or gone as it is on Overleaf. **Resolve** and **Reopen**, on
  its card or on the picked threads, do it on Overleaf first, for everyone in the project,
  and then here. What Overleaf refuses stays as it was. Overleaf has no archive and deletes
  a thread only there, so the page offers neither. A message reworded or deleted there is
  reworded or deleted here, a thread deleted there is deleted here, and one deleted here
  comes back.

## Replying

- **Reply** on a thread stays here: it is where you talk the draft over with the agent,
  which answers in the same thread and redrafts the reply for Overleaf as you go. That talk
  never reaches Overleaf, and it sits in the thread in time order with the messages from
  Overleaf and the replies you sent, each entry marked as written on Overleaf, sent there,
  or here only, so the thread reads whole. An entry Overleaf holds goes by the name
  Overleaf gives its writer, and is answered rather than edited.
- The box under the thread is the reply on Overleaf, which everyone in the project reads.
  The agent puts its draft there (`write_comments` with `action="overleaf"`), one per
  thread, and you edit it and send it with **Send to** and the leaf. The send button atop
  the tab sends the reply of every picked thread, one after another, and a refused one
  keeps its words and says why on its card while the rest go. While a reply is on its way,
  and while the sync button brings threads in, the button stays pressed in and dimmed with
  an ellipsis until the list shows what it did.
- A draft naming source ranges in a file that is not the same here as on Overleaf may
  describe an edit Overleaf does not have yet, so the page asks first whether to send it
  anyway. In a git clone that is a change not committed, not pushed or not pulled, read
  against Overleaf's branch afresh. Picked replies sent together ask once, at the end,
  naming every such file. Overleaf takes the reply first, so what it refuses is not shown
  as said here.
- The agent can also draft a new thread on source lines (`write_comments` with
  `action="overleaf"` and an `anchor` instead of an `id`). It waits in the tab as a card
  with nothing said in it yet, the draft being its first message, and you edit and send it
  the same way, alone or with the picked threads, or delete it. Sent, it opens on Overleaf
  as Overleaf's editor opens a thread: the lines are looked for in Overleaf's file, the
  occurrence nearest the line they are on here, then the message goes, then an edit that
  changes no text puts the thread on them. Lines Overleaf does not have yet leave nothing
  behind there, and the card keeps its draft. A draft naming the ranges it describes goes
  only once Overleaf has what those ranges held, and the refusal says to press commit &
  push first, or, when the files here no longer hold those words, that the lines changed
  after the draft and it is drafted again.

## Suggested edits

An edit made on Overleaf with track changes on is a suggested edit until someone accepts or
rejects it. Overleaf's text holds a suggested insertion's words and not a suggested deletion's,
and so do the files here once they hold Overleaf's text.

- The leaf Comments tab's **suggested edits** view lists them in the order the paper reads
  them, and the tab counts them with its open threads. A card names who suggested the edit and
  when, and shows the deleted words on a − line and the inserted ones on a + line, as a
  proposal's are shown. Words typed over others make one card. A click on the card, or Space,
  shows the edit in the source.
- The editor marks them whichever tab is shown: the inserted words underlined in their
  author's colour, a red bar where words were deleted, and a mark beside the line that opens
  the card, as `K` does on the words. An edit is marked where the file here holds the text
  around it as Overleaf has it. Otherwise its card shows its line on Overleaf, and the editor
  leaves it unmarked.
- **Accept** keeps the words as they are and **Reject** takes them back, on Overleaf, for
  everyone in the project, as Overleaf's own buttons do. The buttons atop the view do the same
  for the picked edits, after asking once, and `aa` and `dd` press a card's Accept and Reject.
  Rejecting changes Overleaf's text, which the file takes as any edit made there: at once when
  the files follow Overleaf live, by Get by hand, and by git pull in a git clone. In Overleaf's
  newer kind of document they are accepted and rejected on Overleaf.
- The page reads them when it joins the project, when a comment changes there, when you
  come back to the page or open the view, and, in the files that follow Overleaf live, a few
  seconds after someone typing with track changes on stops.
- While track changes is on for you on Overleaf, the edits the page makes there, by Send or
  Live, are suggested edits too, as Overleaf's editor makes yours, and Settings says so. In a
  git clone of the project, git push makes them as they are.

## Your co-authors on the page

With `overleaf` set and the cookie kept, an open page sits in the project. The top bar shows
a face for each collaborator there, which goes to the file and line they are on, their
cursors are drawn in the editor on that file, and a file edited there says so above the
editor until this folder holds those edits too. Settings lists who is where and those files.
A leaf button shows in the top bar only when something needs a look: signed out,
reconnecting, comments not coming in, or files waiting for a choice. The page opens the main
file and puts one cursor at its top, which is what makes the others' editors send their
cursors at once, so to them it is one more of your tabs, idle. It sends Overleaf no edit to
the text unless the files follow it live or you press Send.

## Keeping the files in step

Settings chooses how the files follow Overleaf.

| | In a git clone of the project | In any other folder |
|---|---|---|
| **By hand** (default) | **git pull** and **commit & push** in the top bar | **Get** and **Send** in the top bar |
| **Live** (`overleaf_sync: true`) | edits go both ways while a page is open, git pull and commit & push beside them | edits go both ways while a page is open |

### git pull and commit & push

A folder is a git clone of the project when it has a remote whose address ends in the
project's id, as `https://git.overleaf.com/<id>` does. Overleaf's git integration is for paid
plans. Opening Settings fetches Overleaf's branch and says where the folder stands: at
Overleaf's commit, with commits Overleaf lacks, lacking commits Overleaf has, or both.

- **git pull** takes Overleaf's commits in, as a fast-forward or as a merge git makes
  without conflicts, and changes nothing when the merge would conflict or write over a
  change not committed.
- **commit & push** lists the files git tracks whose edits are not committed, commits them
  under a message you may change, the time to begin with, and then sends the folder's
  commits by git push, never forced. Files git does not track stay out of the commit. A
  token in the remote's address never reaches the page, and git push refuses a commit that
  holds the page's own records.

### Get and Send

In a folder that is no git clone, the top bar's buttons are **Get** and **Send**, with the
leaf, each shown with the number of files it moves. They need no git: the page reads and
edits Overleaf's documents through the editor session it keeps in the project. Opening
Settings, the page, or the window after a minute reads where the files stand.

- **Get** writes Overleaf's edits into the files, and where each side changed other places
  of one file it writes both.
- **Send** makes the files' edits on Overleaf as small edits, after asking once, for
  everyone in the project, and reads each file back.
- A file both sides changed in one place, or one that differs with nothing to say which
  side changed it, waits for a choice, and so does an edit that would delete every word of
  an open comment. Comments sync once nothing is left to Get or Send.
- Figures and other uploaded files move whole: Overleaf names each by a hash of its bytes,
  so the page compares that with the file here without downloading it, and a file replaced
  on both sides waits for a choice. A file added on Overleaf comes in by Get. A file only
  here goes by Send when it matches `overleaf_add`, or is of a kind the project already
  holds, into its folder, which Send makes on Overleaf when missing. The paper's compiled
  PDF never goes. A file deleted on one side is listed in Settings with a choice, to delete
  it there too or to bring it back.

### Live

Live keeps the paper's files and the project's the same both ways while a page is open. An
edit on Overleaf is written into the file within a second, and what changes in a file here,
from the page, an editor or the agent, is sent as the smallest edit that makes it, so
comments on words left alone keep them, and words written over commented words take the
comment, as if typed inside it. A save from the page over an edit from Overleaf takes both.
Edits made on either side while no page was open come together when one is.

Nothing is overwritten on a guess. A file that differs with nothing to say which side changed
it, an edit that would delete every word of an open comment, and an edit Overleaf turns away
each stop that file: a bar across the top of the page, and the editor on that file, say why
and ask which text stands, and edits to the file stay here until then. The text that gives
way is kept in `.tex-mcp-web/overleaf-sync/kept/`. A file that already holds Overleaf's text,
brought some other way, agrees with it. Only Overleaf's older kind of document
(`sharejs-text-ot`) is followed. Figures and other uploaded files are copied whole when one
side replaces them, a new file here that `overleaf_add` names goes up by itself, and a file
deleted on one side waits for a choice, as under By hand. In a git clone all of these come by
git instead. Each
file keeps the same record of the text both sides last agreed on under By hand and Live, so
switching picks up where the other left off.

In a git clone, git pull and commit & push go on beside Live, and files added, renamed or
deleted on Overleaf come by git. The files Live keeps hold Overleaf's text already when its
commit arrives, so a git pull takes them as they are and brings in the rest, such as a figure
added on Overleaf, and reading Overleaf's branch takes its commits in by itself when it only
moves forward. commit & push leaves those files out of its commit, and git push first merges
Overleaf's commits, keeping Overleaf's text for those files and the folder's own changes to
the others, so it sends figures and new files and never older text, and a commit that holds
other text for one of those files is not pushed. A file edited since Overleaf's commit was
read stops a git pull until the next read, and while a file waits for a choice, git pull and
commit & push wait too. Live needs the paper's folder to be the top of
the clone, where Overleaf's files are.

## The page's records

The page keeps its records, `.tex-mcp-web/`, with the comments, copies of the text and the
page's locks, beside the paper. In any git repository the paper's folder is in, a clone of
the Overleaf project or one of your own, the page names them in the repository's local
exclude file when it starts, so a commit of every file leaves them out.

## What the agent can do

The agent reads where the folder stands with `overleaf()`, which reads Overleaf again and
names only what differs: files with edits on Overleaf not here, files with edits here not on
Overleaf, files waiting for a choice, and in a git clone the commits behind and ahead. It
also lists the suggested edits, one line each, with where each is in the file here.
`read_comments(new=True)` adds one line when Overleaf has edits this folder lacks.
`overleaf(action="get")` brings those edits in, by Get or by git pull in a git clone, and
changes nothing when they clash. `overleaf(action="push")` runs git push in a git clone.
Sending replies, Send, Resolve and Reopen, and accepting and rejecting suggested edits, stay
yours, from the page.
