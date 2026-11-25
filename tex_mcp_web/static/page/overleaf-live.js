// While a page is open the server sits in the paper's Overleaf project, read-only, and
// tells the page who is there, where their cursors are, and which files they changed. A
// face per person in the top bar goes to where they are; a collaborator's cursor in the
// open file is drawn in the editor; a file changed on Overleaf says so above the editor;
// Settings lists all of it.
// The reviewer's own cursor in the editor is told to the server too, which shows it on Overleaf,
// unless Settings' choice under Overleaf hides it. It owns the reviewer's own name on Overleaf,
// which the session tells it, and that choice.
//
// createOverleafLive takes source (ace(), file(), resize()), the editor the cursors are drawn on;
// showInSource(file, line), which shows a file at a line in the Source (places.js); socket's open()
// and send(message); refreshComments(); overleaf, the page's knowledge of the project
// (overleaf-project.js), whose project() shows the choice, which follows changed(), and which
// heardCookie(refused) and heardFollowed(files) are told what the session says of the cookie and
// of the files it keeps in step; and byHand(),
// heardByHand(standing) and readOverleafGitSoon(delay), of the folder's git standing. It gives
// attach(); render(snapshot), for each snapshot the socket brings; renderCursors(); me(), the
// reviewer's own name on Overleaf; and tellCursor().

import { $, $$, h } from "../shared/dom.js";
import { responseError } from "../shared/comment-api.js";
import { overleafColor } from "./leaf.js";

function overleafInitials(name) {
  const words = String(name || "?").trim().split(/\s+/);
  return (words[0][0] + (words.length > 1 ? words.at(-1)[0] : "")).toUpperCase();
}

function overleafWhen(seconds) {
  const ago = Date.now() / 1000 - seconds;
  if (ago < 60) return "just now";
  if (ago < 3600) return `${Math.round(ago / 60)} min ago`;
  return `at ${new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function overleafItem(dot, text, where, onClick) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  if (dot) {
    const mark = document.createElement("span");
    mark.className = "overleaf-dot";
    mark.style.background = dot;
    button.append(mark);
  }
  const label = document.createElement("span");
  label.textContent = text;
  const place = document.createElement("span");
  place.className = "where";
  place.textContent = where;
  button.append(label, place);
  button.addEventListener("click", onClick);
  item.append(button);
  return item;
}

// Files kept in step with Overleaf: how many are, and a choice for each that stopped.
const OVERLEAF_CHOICES = {
  differs: [["overleaf", "Use Overleaf's version"], ["here", "Send this file's version"]],
  stopped: [["overleaf", "Use Overleaf's version"], ["here", "Send this file's version"]],
  held: [["anyway", "Send it anyway"], ["overleaf", "Restore Overleaf's version"]],
  gone: [["delete-there", "Delete on Overleaf"], ["restore", "Restore here"]],
  dropped: [["delete-here", "Delete here"], ["restore", "Restore on Overleaf"]],
};

// What a file waiting for a choice says, by why it waits.
const OVERLEAF_WAITS = {
  held: "Not sent to Overleaf",
  differs: "Out of sync with Overleaf",
  stopped: "Sync stopped",
  gone: "Deleted here",
  dropped: "Deleted on Overleaf",
};

// Settings shows the reviewer's cursor on Overleaf unless the viewer chose Hidden.
function overleafCursorShown() {
  try { return localStorage.getItem("texMcpOverleafCursor") !== "hidden"; } catch { return true; }
}

export function createOverleafLive({
  source, showInSource, socket, refreshComments, overleaf, byHand, heardByHand, readOverleafGitSoon,
}) {
  // The last snapshot, and the reviewer's own name on Overleaf (me) once the page sits in the
  // project.
  const overleafLive = { snapshot: null, me: "", layer: null, notice: "", settled: false };
  const state = {
    overleafEdits: "",
  };
  let cursorTelling = null;

  function overleafChoiceItem(file, withOpen) {
    const item = document.createElement("li");
    item.className = "overleaf-sync-file";
    item.dataset.path = file.path;
    const name = document.createElement("strong");
    name.textContent = withOpen ? `${OVERLEAF_WAITS[file.status] ?? "Waiting"}: ${file.path}` : file.path;
    const why = document.createElement("span");
    why.textContent = file.message;
    item.append(name, why);
    const choices = [...(OVERLEAF_CHOICES[file.status] ?? [])];
    if (withOpen) choices.unshift(["open", "Open the file"]);
    for (const [choice, label] of choices) {
      const choose = document.createElement("button");
      choose.type = "button";
      choose.className = `overleaf-sync-${choice}`;
      choose.textContent = label;
      choose.addEventListener("click", () => (choice === "open"
        ? goToOverleafPlace(file.path, null)
        : chooseOverleafText(file.path, choice, label)).catch(
        (error) => alert(`Could not apply your choice for ${file.path}: ${error.message}`)));
      item.append(choose);
    }
    return item;
  }

  function renderOverleafChoices(files, choosing) {
    const head = $("#overleaf-sync-head");
    const followed = files.filter((file) => file.status !== "unsupported").length;
    head.hidden = !files.length || (Boolean(byHand()) && !choosing.length);
    head.textContent = byHand() ? `${choosing.length} file${choosing.length === 1 ? " needs" : "s need"} a decision.`
      : choosing.length
      ? `${choosing.length} file${choosing.length === 1 ? " needs" : "s need"} a decision; the rest are in sync with Overleaf.`
      : `${followed} file${followed === 1 ? "" : "s"} in sync with Overleaf.`;
    $("#overleaf-sync-files").replaceChildren(...choosing.map((file) => overleafChoiceItem(file, false)));
    // A file that waits is said across the top of the page until it is settled, since edits
    // to it go nowhere meanwhile.
    const alertBox = $("#overleaf-sync-alert");
    const wasHidden = alertBox.hidden;
    alertBox.hidden = !choosing.length;
    $("#overleaf-sync-alert-head").textContent = choosing.length === 1
      ? "A file needs a decision. Edits to it won't sync until you choose."
      : `${choosing.length} files need a decision. Edits to them won't sync until you choose.`;
    $("#overleaf-sync-alert-files").replaceChildren(...choosing.map((file) => overleafChoiceItem(file, true)));
    if (wasHidden !== alertBox.hidden) requestAnimationFrame(() => source.resize());
  }

  async function chooseOverleafText(path, choice, label) {
    const asked = {
      overleaf: `${label} for ${path}? A copy of this file's text is saved in .tex-mcp-web/overleaf-sync/kept/.`,
      here: `${label} for ${path}? A copy of Overleaf's text is saved in .tex-mcp-web/overleaf-sync/kept/.`,
      "delete-there": `Delete ${path} on Overleaf, for everyone in the project? Overleaf keeps it in its history.`,
      "delete-here": `Delete ${path} here? A copy is kept in .tex-mcp-web/overleaf-sync/kept/.`,
    }[choice];
    if (asked && !confirm(asked)) return;
    const response = await fetch("/overleaf/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, choice }),
    });
    if (!response.ok) throw new Error(await responseError(response));
  }

  function renderOverleafLive(snapshot) {
    overleafLive.snapshot = snapshot;
    if ((snapshot.me ?? "") !== overleafLive.me) {
      overleafLive.me = snapshot.me ?? "";
      // Which threads wait for the reviewer's answer, in unanswered and in the tab's number too.
      refreshComments().catch((error) => console.error(error));
    }
    heardByHand(snapshot.by_hand ?? null);
    const { status, users = [], changes = [] } = snapshot;
    // Signed out with a cookie is Overleaf turning it away, until a session gets in with one.
    overleaf.heardCookie(status === "signed_out" ? true : status === "live" ? false : undefined);
    const button = $("#overleaf-live-btn");
    const off = status === "off";
    const commentsTrouble = status === "live" ? snapshot.comments_trouble : "";
    const files = snapshot.files ?? [];
    overleaf.heardFollowed(files);
    const choosing = files.filter((file) => ["differs", "stopped", "held", "gone", "dropped"].includes(file.status));
    // The leaf button connects an unconnected paper, and otherwise shows only when something
    // needs a look in the dialog; a session that is up shows faces alone.
    const trouble = !off && (status === "signed_out" || status === "reconnecting" || !!commentsTrouble
      || choosing.length > 0);
    button.classList.toggle("off", off);
    button.classList.toggle("trouble", trouble);
    button.hidden = !off && !trouble;
    button.title = off ? "Connect this paper to its Overleaf project" : "Overleaf needs your attention";
    renderOverleafChoices(files, choosing);
    $("#overleaf-live-label").textContent = off ? "Connect Overleaf"
      : choosing.length ? `${choosing.length} waiting`
      : commentsTrouble ? "Comments not syncing"
      : { signed_out: "Signed out", reconnecting: "Reconnecting" }[status] ?? "";
    $("#overleaf-live").hidden = off;
    $("#overleaf-tracking").hidden = snapshot.tracking !== true;
    // Someone edited on Overleaf: its branch is read again once the typing settles.
    const edited = changes.map((change) => `${change.file}@${change.at}`).join("|");
    if (edited !== state.overleafEdits) {
      state.overleafEdits = edited;
      if (edited) readOverleafGitSoon(15000);
    }
    // The session begins again when files are added, renamed or deleted on Overleaf, and the
    // files kept the same come into step after a choice: the branch is read again then too.
    const settled = status === "live" && files.every((file) => ["synced", "unsupported"].includes(file.status));
    if (settled && !overleafLive.settled) readOverleafGitSoon(2000);
    overleafLive.settled = settled;
    $("#overleaf-live-faces").replaceChildren(...users.slice(0, 4).map((user) => {
      const where = user.file ? `${user.file}:${(user.row ?? 0) + 1}` : "";
      const said = where ? `${user.name || "Someone"} is on Overleaf at ${where}. Click to go to that line.`
        : `${user.name || "Someone"} is on Overleaf.`;
      return h("button", {
        class: "overleaf-face", type: "button", title: said, "aria-label": said,
        style: { background: overleafColor(user.user_id || user.id) },
        onclick: () => goToOverleafPlace(user.file, user.row).catch((error) => alert(error.message)),
      }, overleafInitials(user.name));
    }));
    $("#overleaf-live-status").textContent = commentsTrouble ? `Overleaf comments are not syncing: ${commentsTrouble}` : {
      live: users.length ? "In the Overleaf project now:" : "No one else is in the Overleaf project.",
      connecting: "Joining the Overleaf project…",
    }[status] ?? (snapshot.message || "Not connected to Overleaf.");
    $("#overleaf-live-users").replaceChildren(...users.map((user) => overleafItem(
      overleafColor(user.user_id || user.id), user.name || "Someone",
      user.file ? `${user.file}:${(user.row ?? 0) + 1}` : "",
      () => goToOverleafPlace(user.file, user.row))));
    $("#overleaf-live-changes-head").hidden = !changes.length;
    $("#overleaf-live-changes").replaceChildren(...changes.map((change) => overleafItem(
      null, change.file, `${change.by}, ${overleafWhen(change.at)}`, () => goToOverleafPlace(change.file, null))));
    renderOverleafCursors();
  }

  async function goToOverleafPlace(file, row) {
    if (!file) return;
    if ($("#settings-dialog").open) $("#settings-dialog").close();
    await showInSource(file, row === null || row === undefined ? null : row + 1).catch((error) => alert(error.message));
  }

  // Collaborators' cursors in the open file, drawn over the editor after each render.
  function renderOverleafCursors() {
    const editor = source.ace();
    if (!editor) return;
    const container = editor.container;
    if (!overleafLive.layer || overleafLive.layer.parentNode !== container) {
      overleafLive.layer = document.createElement("div");
      overleafLive.layer.className = "overleaf-cursors";
      container.append(overleafLive.layer);
    }
    const snapshot = overleafLive.snapshot ?? { users: [], changes: [] };
    const here = snapshot.users.filter((user) => user.file && user.file === source.file() && user.row !== null);
    const box = container.getBoundingClientRect();
    const height = editor.renderer.lineHeight;
    overleafLive.layer.replaceChildren(...here.flatMap((user) => {
      const row = Math.min(user.row, editor.session.getLength() - 1);
      const column = Math.min(user.column ?? 0, editor.session.getLine(row).length);
      const at = editor.renderer.textToScreenCoordinates(row, column);
      const top = at.pageY - (box.top + window.scrollY);
      const left = at.pageX - (box.left + window.scrollX);
      if (top < -height || top > box.height) return [];
      const color = overleafColor(user.user_id || user.id);
      const caret = document.createElement("div");
      caret.className = "overleaf-cursor";
      caret.dataset.user = user.name;
      Object.assign(caret.style, { top: `${top}px`, left: `${left}px`, height: `${height}px`, background: color });
      const name = document.createElement("span");
      name.className = "overleaf-cursor-name";
      name.style.background = color;
      name.textContent = user.name;
      caret.append(name);
      return [caret];
    }));
    const change = snapshot.changes.find((item) => item.file === source.file());
    const waiting = (snapshot.files ?? []).find((file) => file.path === source.file()
      && OVERLEAF_WAITS[file.status]);
    const notice = waiting
      ? `${OVERLEAF_WAITS[waiting.status]}: ${waiting.message}`
      : change
      ? `${change.by} edited this file on Overleaf ${overleafWhen(change.at)} (${change.count} ${change.count === 1 ? "edit" : "edits"}). Update this file from Overleaf before you save.`
      : "";
    $("#overleaf-file-notice").classList.toggle("waiting", !!waiting);
    if (notice !== overleafLive.notice) {
      overleafLive.notice = notice;
      $("#overleaf-file-notice").textContent = notice;
      $("#overleaf-file-notice").hidden = !notice;
      requestAnimationFrame(() => editor.resize());
    }
  }

  // Where the editor's cursor is, in the file open there, told at most twice a second and
  // always at its last place, read when told: the server shows it as the reviewer's cursor on
  // Overleaf. Nothing is told while no file is open, the cursor is hidden, or the page is out of
  // sight, as a tab behind another is when a file changing on disk moves its cursor.
  function tellCursor() {
    if (cursorTelling !== null) return;
    cursorTelling = setTimeout(() => {
      cursorTelling = null;
      if (!source.ace() || !source.file() || !overleafCursorShown() || document.visibilityState !== "visible"
        || !socket.open()) return;
      const { row, column } = source.ace().getCursorPosition();
      socket.send({ type: "cursor", path: source.file(), row, column });
    }, 500);
  }

  function chooseOverleafCursor(choice) {
    try { localStorage.setItem("texMcpOverleafCursor", choice); } catch { /* the page keeps showing it */ }
    if (choice === "show") tellCursor();
    else if (socket.open()) socket.send({ type: "cursor", hidden: true });
  }

  // The choice in Settings shows with a connected project, as the reviewer last made it.
  function renderCursorChoice() {
    $("#overleaf-cursor").hidden = !overleaf.project();
    for (const box of $$('#overleaf-cursor input[name="overleaf-cursor"]')) {
      box.checked = box.value === (overleafCursorShown() ? "show" : "hidden");
    }
  }

  overleaf.changed(renderCursorChoice);

  function attach() {
    for (const box of $$('#overleaf-cursor input[name="overleaf-cursor"]')) {
      box.addEventListener("change", () => chooseOverleafCursor(box.value));
    }
  }

  return {
    attach,
    render: renderOverleafLive,
    renderCursors: renderOverleafCursors,
    me: () => overleafLive.me,
    tellCursor,
  };
}
