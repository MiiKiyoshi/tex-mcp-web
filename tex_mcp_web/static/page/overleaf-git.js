// Where a folder connected to Overleaf stands against it, in Settings and in the top bar, and the
// buttons that bring the two together: git pull and commit & push in a git clone of the project,
// Get and Send in a folder synced by hand, and the dialog commit & push asks in first. It owns
// where the folder stands, which the page's other parts read through it.
//
// createOverleafGit takes overleaf, the page's knowledge of the project (overleaf-project.js), of
// which it reads project(), cookie(), fullSync() and remote(); gitAfterEdit, the milliseconds it
// waits after a file changes here; showSettings(status); renderOverleafThreadSync();
// overleafFailed(error); and readSuggestedEdits(). It gives attach(); where the folder stands:
// standing(), a git clone's against Overleaf's branch, null until read, trouble(), why the last
// reading of the branch failed, and byHand(), a folder synced by hand's, or null; relinked(),
// after the project's link changes; heardByHand(standing), what the live session says of a folder
// synced by hand; render(standing), readState(), readStateSoon() and readSoon(delay).

import { $, h } from "../shared/dom.js";
import { responseError } from "./response-error.js";
import { commits, fileCount, OVERLEAF_BY_GIT, OVERLEAF_BY_HAND } from "./leaf.js";

export function createOverleafGit({
  overleaf, gitAfterEdit, showSettings, renderOverleafThreadSync, overleafFailed, readSuggestedEdits,
}) {
  const state = {
    // Where a folder cloned from the project stands against Overleaf's branch (null until read),
    // and why the last reading of the branch failed.
    git: null,
    gitTrouble: "",
    // Where a folder brought together with Overleaf by Get and Send stands, or null.
    files: null,
    overleafGitRead: 0,
    overleafGitTimer: null,
    overleafGitStateTimer: null,
    // The files commit & push showed the reviewer, whose edits its commit takes.
    committing: [],
  };

  // Where the folder stands against Overleaf's branch: said in the dialog, and Pull and Push
  // in the top bar, each shown only when it has commits to move.
  function renderOverleafGit(now) {
    state.git = now;
    state.gitTrouble = "";
    // With the files kept the same, comments come in by themselves, not after a commit.
    const then = overleaf.fullSync() ? "" : ", and comments sync after that";
    $("#overleaf-git-state").textContent = {
      same: `This folder is at Overleaf's commit (${now.there}).`,
      ahead: `This folder is ${commits(now.ahead)} ahead of Overleaf. Press commit & push in the top bar to send `
        + `${now.ahead === 1 ? "it" : "them"}${then}.`,
      behind: `This folder is ${commits(now.behind)} behind Overleaf. Press git pull in the top bar to get `
        + `${now.behind === 1 ? "it" : "them"}${then}.`,
      diverged: `This folder and Overleaf have diverged (${now.ahead} here, ${now.behind} on Overleaf). `
        + `Press git pull in the top bar to merge them, then commit & push to send the result${then}.`,
    }[now.state];
    $("#overleaf-git-uncommitted").hidden = !now.uncommitted.length;
    $("#overleaf-git-uncommitted").textContent = `Not committed yet (commit & push commits them first): ${now.uncommitted.join(", ")}`;
    renderGitButtons();
    renderOverleafThreadSync();
  }


  // A folder brought together with Overleaf by hand: Get and Send, each with the files it moves.
  function renderOverleafFiles(now) {
    state.files = now;
    const counts = [now.get.length, now.send.length];
    $("#overleaf-git").hidden = false;
    $("#overleaf-git-way").textContent = OVERLEAF_BY_HAND;
    $("#overleaf-git-state").textContent = !now.read ? "Reading Overleaf's files…"
      : counts[0] && counts[1] ? `Overleaf has new edits to ${fileCount(counts[0])}, and this folder has new edits to ${fileCount(counts[1])}. Comments sync after you press Get and Send.`
      : counts[0] ? `Overleaf has new edits to ${fileCount(counts[0])}. Comments sync after you press Get in the top bar.`
      : counts[1] ? `This folder has new edits to ${fileCount(counts[1])}. Comments sync after you press Send in the top bar.`
      : "This folder is in sync with Overleaf.";
    $("#overleaf-git-uncommitted").hidden = true;
    renderGitButtons();
    renderOverleafThreadSync();
  }

  // What the live session says of a folder synced by hand: where it stands, or nothing once the
  // folder is not one.
  function heardByHand(now) {
    if (now) {
      renderOverleafFiles(now);
      return;
    }
    if (!state.files) return;
    state.files = null;
    $("#overleaf-git").hidden = !overleaf.remote();
    renderGitButtons();
  }

  // The project's link as the server last gave it. Without the project or the cookie no session
  // runs, so where a folder synced by hand stood goes, and where a git clone stood goes with its
  // remote. Settings says how the folder syncs, and the top bar's buttons what is left to move.
  function relinked() {
    const remote = overleaf.remote();
    if (!overleaf.project() || !overleaf.cookie()) state.files = null;
    $("#overleaf-git").hidden = !remote && !state.files;
    $("#overleaf-git-way").textContent = remote ? OVERLEAF_BY_GIT : OVERLEAF_BY_HAND;
    if (!remote) {
      state.git = null;
      state.gitTrouble = "";
    }
    renderGitButtons();
  }

  function renderGitButtons() {
    const pull = $("#git-pull-btn");
    const push = $("#git-push-btn");
    const byHand = state.files;
    for (const button of [pull, push]) {
      button.querySelector(".by-hand-mark").hidden = !byHand;
      button.querySelector(".wide-label").textContent = byHand ? (button === pull ? "Get" : "Send")
        : (button === pull ? "git pull" : "commit & push");
    }
    if (byHand) {
      pull.hidden = !byHand.get.length;
      push.hidden = !byHand.send.length;
      pull.disabled = push.disabled = false;
      pull.querySelector(".git-count").textContent = byHand.get.length ? String(byHand.get.length) : "";
      push.querySelector(".git-count").textContent = byHand.send.length ? String(byHand.send.length) : "";
      pull.title = `Get Overleaf's edits to ${fileCount(byHand.get.length)}: ${byHand.get.join(", ")}`;
      push.title = `Send the edits to ${fileCount(byHand.send.length)} to Overleaf: ${byHand.send.join(", ")}`;
      return;
    }
    const now = state.git;
    // The commits git push sends: those Overleaf lacks, and one more of the edits not committed.
    const sending = (now?.ahead ?? 0) + (now?.uncommitted.length ? 1 : 0);
    pull.hidden = !now?.behind;
    // With the files kept the same, Push takes Overleaf's commits in first by itself.
    push.hidden = !sending || (Boolean(now?.behind) && !overleaf.fullSync());
    pull.disabled = push.disabled = false;
    if (!now) return;
    pull.querySelector(".git-count").textContent = now.behind ? String(now.behind) : "";
    push.querySelector(".git-count").textContent = sending ? String(sending) : "";
    pull.title = `git pull ${commits(now.behind)} from Overleaf`;
    push.title = now.uncommitted.length
      ? `Commit the edits to ${fileCount(now.uncommitted.length)} and git push ${commits(sending)} to Overleaf`
      : `git push ${commits(now.ahead)} to Overleaf`;
  }

  // Where the folder stands, read here without asking Overleaf: what is not committed yet.
  async function readOverleafGitState() {
    const response = await fetch("/overleaf/git/state", { method: "POST" });
    if (response.ok) renderOverleafGit(await response.json());
  }

  // A file changed here, so commit & push shows once there is an edit to commit.
  function readOverleafGitStateSoon() {
    if (!state.git) return;
    clearTimeout(state.overleafGitStateTimer);
    state.overleafGitStateTimer = setTimeout(
      () => readOverleafGitState().catch((error) => console.error(error)), gitAfterEdit);
  }

  // commit & push asks first: it lists the files whose edits go into one commit, under a
  // message the reviewer may change, and says what git push then sends, for everyone in the
  // project.
  async function openCommitDialog() {
    await readOverleafGitState();
    const now = state.git;
    if (!now.ahead && !now.uncommitted.length) return;
    state.committing = now.uncommitted;
    const at = new Date();
    const two = (value) => String(value).padStart(2, "0");
    $("#commit-head").textContent = now.uncommitted.length
      ? `Commit the edits to ${fileCount(now.uncommitted.length)}:` : "No edits to commit.";
    $("#commit-files").replaceChildren(...now.uncommitted.map((file) => h("li", { text: file })));
    $("#commit-message").hidden = !now.uncommitted.length;
    $("#commit-message").required = now.uncommitted.length > 0;
    $("#commit-message").value = `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())} `
      + `${two(at.getHours())}:${two(at.getMinutes())}`;
    $("#commit-sends").textContent = `Then git push sends ${commits(now.ahead + (now.uncommitted.length ? 1 : 0))} `
      + "to Overleaf, for everyone in the project.";
    $("#commit-dialog").showModal();
  }

  // Reading Overleaf's branch, or pulling from it or pushing to it, as the reviewer asked.
  // A pull or push that does not go through says why in Settings.
  async function overleafGitAction(action, commit) {
    if (state.files) return overleafFilesAction({ fetch: "read", pull: "get", push: "send" }[action]);
    for (const id of ["#git-pull-btn", "#git-push-btn", "#overleaf-sync-comments-btn"]) $(id).disabled = true;
    if (action !== "fetch") {
      $("#overleaf-git-state").textContent = action === "pull" ? "Running git pull from Overleaf…"
        : commit ? "Committing, then running git push to Overleaf…" : "Running git push to Overleaf…";
    }
    state.overleafGitRead = Date.now();
    const response = await fetch(`/overleaf/git/${action}`, commit
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(commit) }
      : { method: "POST" });
    if (response.ok) {
      renderOverleafGit(await response.json());
      if (action !== "fetch" && $("#settings-dialog").open) {
        $("#overleaf-status").textContent = action === "pull" ? "git pull done." : commit ? "commit & push done." : "git push done.";
      }
      return;
    }
    const why = await responseError(response);
    if (action === "fetch") {
      // Said in the leaf tab too, where comments wait on the branch.
      state.gitTrouble = `Could not read Overleaf's branch: ${why}`;
      $("#overleaf-git-state").textContent = state.gitTrouble;
      renderGitButtons();
      renderOverleafThreadSync();
      return;
    }
    showSettings(`Could not ${action}: ${why}`);
    await overleafGitAction("fetch");
  }

  // Overleaf's branch is read again when the page opens, a little after someone edits on
  // Overleaf, and when the reviewer comes back to the page, at most once a minute.
  // Reading where the files brought together by hand stand, or a Get or a Send the reviewer asked
  // for. One that does not go through says why in Settings.
  async function overleafFilesAction(action) {
    const sending = state.files?.send ?? [];
    if (action === "send" && !confirm(`Send the edits to ${fileCount(sending.length)} (${sending.join(", ")}) to Overleaf, `
      + "for everyone in the project?")) return;
    for (const id of ["#git-pull-btn", "#git-push-btn", "#overleaf-sync-comments-btn"]) $(id).disabled = true;
    if (action !== "read") $("#overleaf-git-state").textContent = action === "get" ? "Getting Overleaf's edits…" : "Sending to Overleaf…";
    state.overleafGitRead = Date.now();
    const response = await fetch(`/overleaf/files/${action}`, { method: "POST" });
    if (response.ok) {
      renderOverleafFiles(await response.json());
      if (action !== "read" && $("#settings-dialog").open) {
        $("#overleaf-status").textContent = action === "get" ? "Get done." : "Send done.";
      }
      return;
    }
    const why = await responseError(response);
    if (action === "read") {
      $("#overleaf-git-state").textContent = `Could not read Overleaf's files: ${why}`;
      renderGitButtons();
      return;
    }
    showSettings(`Could not ${action === "get" ? "Get" : "Send"}: ${why}`);
    await overleafFilesAction("read");
  }

  // A connected folder cloned by git, or brought together by hand, is read whether or not a
  // reading before went through, so one that failed is tried again.
  function readOverleafGitSoon(delay) {
    if (!overleaf.project() || (!overleaf.remote() && !state.files)) return;
    clearTimeout(state.overleafGitTimer);
    state.overleafGitTimer = setTimeout(() => overleafGitAction("fetch").catch((error) => console.error(error)), delay);
  }


  function attach() {
    $("#git-pull-btn").addEventListener("click", () => overleafGitAction("pull").catch(overleafFailed));
    $("#git-push-btn").addEventListener("click", () =>
      (state.files ? overleafGitAction("push") : openCommitDialog()).catch(overleafFailed));
    $("#commit-form").addEventListener("submit", () => {
      const files = state.committing;
      overleafGitAction("push", files.length ? { message: $("#commit-message").value.trim(), files } : undefined)
        .catch(overleafFailed);
    });
    $("#commit-cancel").addEventListener("click", () => $("#commit-dialog").close());
    window.addEventListener("focus", () => {
      if (Date.now() - state.overleafGitRead > 60000) readOverleafGitSoon(0);
      readSuggestedEdits();
    });
  }

  return {
    attach,
    standing: () => state.git,
    trouble: () => state.gitTrouble,
    byHand: () => state.files,
    relinked,
    heardByHand,
    render: renderOverleafGit,
    readState: readOverleafGitState,
    readStateSoon: readOverleafGitStateSoon,
    readSoon: readOverleafGitSoon,
  };
}
