// The button atop the leaf Comments tab that brings Overleaf's new threads in, and the note under
// it that says what stands in their way.
//
// createOverleafSync takes overleaf, the page's knowledge of the project (overleaf-project.js), of
// which it reads project(), cookie(), fullSync() and waiting(); commentTab(), the comment tab
// shown; gitWait, the milliseconds between reads of the folder's git standing while comments wait
// on it; gitStanding(), gitTrouble(), filesByHand() and readOverleafGitState(), of the folder's
// git standing; showSettings(status); refreshComments(); cookieHeard(refused), of the cookie;
// loadOverleafStatus(); and overleafFailed(error). It gives attach() and render().

import { $, h } from "../shared/dom.js";
import { responseError } from "../shared/comment-api.js";
import { commits, OVERLEAF_COOKIE_WANTED } from "./leaf.js";

export function createOverleafSync({
  overleaf, commentTab, gitWait, gitStanding, gitTrouble, filesByHand, readOverleafGitState, showSettings,
  refreshComments, cookieHeard, loadOverleafStatus, overleafFailed,
}) {
  const state = {
    // Whether Overleaf's threads are being synced: the button shows the work under way until the
    // list shows what it did.
    syncingComments: false,
    // The reading of the folder's git standing again every few seconds, while comments wait on it.
    overleafGitWatch: null,
  };

  // Comments sync at the commit Overleaf is at: a thread is on words, and only the same text
  // puts it exactly where Overleaf has it.
  // Sync comments, atop the comment list.
  function renderOverleafThreadSync() {
    const side = $("#overleaf-sync-comments-btn");
    const waiting = overleaf.waiting();
    // Files kept the same both ways bring new threads in by themselves: nothing to press.
    side.hidden = !overleaf.project() || overleaf.fullSync() || commentTab() !== "overleaf-comments";
    side.querySelector(".count").textContent = waiting ? String(waiting) : "";
    const words = waiting ? `Sync comments from Overleaf (${waiting} new)` : "Sync comments from Overleaf";
    side.title = words;
    side.setAttribute("aria-label", words);
    const files = filesByHand();
    // Comments sync once the folder and Overleaf hold the same files: until then the tab says what
    // stands in the way and which button in the top bar clears it, since a greyed button says nothing.
    // Commits that change no file, which Overleaf makes many of, stand in no one's way.
    const git = gitStanding();
    const gitWhy = git !== null && git.state !== "same" && git.apart
      ? {
        behind: `This folder is ${commits(git.behind)} behind Overleaf. Comments sync after you run git pull, from the top bar or a terminal.`,
        ahead: `This folder is ${commits(git.ahead)} ahead of Overleaf. Comments sync after you run commit & push, from the top bar or a terminal.`,
        diverged: "This folder and Overleaf have diverged. Comments sync after you run git pull, then commit & push.",
      }[git.state]
      : "";
    // Live, new threads come in by themselves once every file is the same as on Overleaf.
    const why = overleaf.fullSync()
      ? (waiting ? `${waiting} new Overleaf thread${waiting === 1 ? "" : "s"} will appear once every file is in sync.` : "")
      : !overleaf.cookie() ? OVERLEAF_COOKIE_WANTED
      : gitTrouble() || gitWhy || (files === null ? ""
        : !files.read ? "Reading Overleaf's files…"
        : files.get.length + files.send.length > 0
          ? "This folder and Overleaf have different files. Comments sync after you press Get or Send in the top bar."
        : files.choose.length > 0
          ? "A file differs from Overleaf's version. Use the banner above to choose which version to keep, and comments will sync."
        : "");
    side.disabled = state.syncingComments || why !== "";
    side.classList.toggle("busy", state.syncingComments);
    if (why) side.title = why;
    let note = $("#overleaf-sync-why");
    if (!note) {
      note = h("p", { id: "overleaf-sync-why", class: "panel-note" });
      $("#comments-list").before(note);
    }
    note.hidden = !overleaf.project() || commentTab() !== "overleaf-comments" || !why;
    // The missing cookie is pasted in Settings, which the sentence opens.
    note.replaceChildren(why === OVERLEAF_COOKIE_WANTED
      ? h("button", { type: "button", class: "panel-note-go", onclick: () => showSettings() }, why) : why);
    // git pull in a terminal is seen here too: while comments wait on it, the folder's
    // standing is read again every few seconds, which asks Overleaf nothing.
    clearInterval(state.overleafGitWatch);
    state.overleafGitWatch = why === gitWhy && gitWhy && !side.hidden
      ? setInterval(() => { if (!document.hidden) readOverleafGitState().catch((error) => console.error(error)); }, gitWait)
      : null;
  }

  function overleafBroughtIn(done) {
    const parts = [`${done.added} new comments`, `${done.replies} new replies`];
    if (done.resolved) parts.push(`${done.resolved} resolved threads skipped`);
    if (done.unplaced) parts.push(`${done.unplaced} not found in the source and attached to the whole paper`);
    return `Synced: ${parts.join(", ")}.`;
  }

  // From the comment list it works quietly, and only what did not go through opens the dialog.
  async function syncOverleafComments() {
    state.syncingComments = true;
    renderOverleafThreadSync();
    try {
      const response = await fetch("/overleaf/comments", { method: "POST" });
      if (!response.ok) {
        // A cookie Overleaf turned away is asked for again.
        if (response.status === 401) cookieHeard(true);
        showSettings(`Could not sync comments: ${await responseError(response)}`);
        return;
      }
      const done = overleafBroughtIn(await response.json());
      if ($("#settings-dialog").open) showSettings(done);
      await refreshComments();
    } finally {
      state.syncingComments = false;
      await loadOverleafStatus();
    }
  }

  function attach() {
    $("#overleaf-sync-comments-btn").addEventListener("click", () => syncOverleafComments().catch(overleafFailed));
  }

  return { attach, render: renderOverleafThreadSync };
}
