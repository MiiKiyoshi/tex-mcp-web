// Overleaf in Settings: the paper's project, connected and disconnected, the cookie that signs in
// to it, how the files follow it, and whether the reviewer's cursor shows there.
//
// createOverleafSettings takes overleaf, the page's knowledge of the project, of which it writes
// project, cookie, cookieRefused, fullSync, waiting, remote, and git, gitTrouble and files as a
// project's link changes; openSettings(options), which opens the Settings dialog;
// readOverleafGitSoon(delay) and renderGitButtons(), of the folder's git standing;
// renderOverleafThreadSync(); renderPickedActions(); renderNoPaper(); and overleafCursorShown()
// and chooseOverleafCursor(choice). It gives attach(), show(status), the Settings dialog opened,
// saying status at Overleaf; loadStanding(); load(); renderCookie(); and failed(error), which
// says in Settings that the server could not be reached.

import { $, $$ } from "../shared/dom.js";
import { responseError } from "./response-error.js";
import { leafIcon, OVERLEAF_BY_GIT, OVERLEAF_BY_HAND } from "./leaf.js";

// Connecting names the project in the paper's config, as a line written there by hand
// would. Keeping the files the same asks once before it starts, since it changes what
// everyone in the project sees.
const OVERLEAF_SYNC_QUESTION = "While a review page is open, the paper's files and the Overleaf project stay in sync "
  + "both ways: edits here are sent to Overleaf, and edits there are written to the files. "
  + "If a file differs at the start, you choose which version to keep. New Overleaf comment threads appear here "
  + "automatically whenever every file is in sync. Figures and other uploaded files are copied in full when either "
  + "side replaces them, and new figures or source files added here are uploaded automatically. In a git clone, "
  + "these files still move only with git pull and git push, which never overwrite newer text on Overleaf. "
  + "Turn on live sync?";

export function createOverleafSettings({
  overleaf, openSettings, readOverleafGitSoon, renderGitButtons, renderOverleafThreadSync, renderPickedActions,
  renderNoPaper, overleafCursorShown, chooseOverleafCursor,
}) {
  // Settings: the paper's Overleaf project, the cookie that signs in to it, how the files
  // follow it, where they stand, and the page's look. The cookie is handed over once and is
  // never shown again. It opens at its head, where Overleaf is, unless it opens to say something
  // there.
  function showSettings(status = "") {
    $("#overleaf-status").textContent = status;
    // Opening reads Overleaf's branch afresh, so what the dialog says is what is there now.
    if (openSettings({ atHead: !status })) loadOverleafStanding().catch((error) => console.error(error));
  }

  // What the page knows of the project, read afresh, and then where a git clone or a folder
  // brought together by hand stands against Overleaf: at the start, when Settings opens, and
  // when the config changes.
  async function loadOverleafStanding() {
    await loadOverleafStatus();
    readOverleafGitSoon(0);
  }

  function renderOverleafStatus({ project, cookie, sync = false, live = false, settable = true, git = null, waiting = 0 }) {
    // Without the project or the cookie no session runs, so what it showed goes with it.
    if (!project || !cookie) {
      overleaf.files = null;
      $("#overleaf-live-faces").replaceChildren();
    }
    $("#overleaf-link-row").hidden = Boolean(project) || !settable;
    $("#overleaf-connected").hidden = !project;
    $("#overleaf-disconnect-btn").hidden = !settable;
    $("#overleaf-settable").hidden = settable || Boolean(project);
    // How the files follow Overleaf is a choice in every connected folder, by hand unless
    // live is chosen. What each changes depends on whether the folder is a git clone.
    $("#overleaf-mode").hidden = !project;
    for (const box of $$('#overleaf-mode input[name="overleaf-mode"]')) {
      box.checked = box.value === (sync ? "live" : "hand");
      box.disabled = !settable;
    }
    $("#overleaf-cursor").hidden = !project;
    for (const box of $$('#overleaf-cursor input[name="overleaf-cursor"]')) {
      box.checked = box.value === (overleafCursorShown() ? "show" : "hidden");
    }
    // The sync button and the tab it is in show drawings, so the note shows them too.
    const syncDrawing = [...$("#overleaf-sync-comments-btn").querySelectorAll("svg")].map((svg) => svg.cloneNode(true));
    // Get and Send show their drawings beside their words, which a phone leaves out.
    const drawn = (selector, word) => [...[...$(selector).querySelectorAll("svg")].map((svg) => {
      const copy = svg.cloneNode(true);
      copy.removeAttribute("hidden");
      return copy;
    }), word];
    $("#overleaf-mode-hand-note").replaceChildren(...(git
      ? ["Files change only when you press git pull or commit & push, which appear in the top bar when there is something to pull or push. Nothing reaches Overleaf until you commit & push. New Overleaf comment threads appear here when you press "]
      : ["Files change only when you press ", ...drawn("#git-pull-btn", " Get"), " or ", ...drawn("#git-push-btn", " Send"),
        ", which appear in the top bar when there are edits to get or send. Nothing reaches Overleaf until you press Send. New Overleaf comment threads appear here when you press "]),
    ...syncDrawing, " in the ", leafIcon(), " Comments tab.");
    $("#overleaf-mode-live-note").textContent = git
      ? "While this page is open, text files such as .tex sync both ways within a second, and new Overleaf comment threads appear here automatically. Figures and other uploaded files sync through git: changes made here go with commit & push, and files added on Overleaf arrive automatically, or with git pull when the top bar shows it."
      : "While this page is open, text files such as .tex sync both ways within a second, and new Overleaf comment threads appear here automatically. Figures and other uploaded files are copied in full when either side replaces them, and new figures or source files added here are uploaded automatically.";
    $("#overleaf-direct").hidden = !project;
    $("#overleaf-tracking-git").hidden = !git;
    $("#overleaf-git").hidden = !git && !overleaf.files;
    $("#overleaf-git-way").textContent = git ? OVERLEAF_BY_GIT : OVERLEAF_BY_HAND;
    overleaf.remote = git;
    if (!git) {
      overleaf.git = null;
      overleaf.gitTrouble = "";
    }
    renderGitButtons();
    overleaf.cookie = cookie ?? null;
    overleaf.project = project ?? null;
    overleaf.fullSync = live;
    overleaf.waiting = waiting;
    if (project) {
      $("#overleaf-project").href = project;
      $("#overleaf-project").textContent = project.replace(/^https?:\/\//, "");
    }
    renderOverleafCookie();
    renderOverleafThreadSync();
    renderPickedActions();
    renderNoPaper();
  }

  // The field to paste a cookie in shows while there is none and while Overleaf turns the saved
  // one away. A cookie from the environment variable, which comes before a saved one, is
  // changed where it is set.
  function renderOverleafCookie() {
    const cookie = overleaf.cookie;
    const refused = Boolean(cookie) && overleaf.cookieRefused;
    const asked = !cookie || (refused && cookie === "file");
    $("#overleaf-cookie-row").hidden = !asked;
    $("#overleaf-cookie-help").hidden = !asked;
    $("#overleaf-signed").hidden = !cookie;
    $("#overleaf-signed-text").textContent = cookie === "environment"
      ? (refused ? "Overleaf rejected the cookie in the environment variable TEX_MCP_WEB_OVERLEAF_COOKIE. "
        + "Set a new one there and restart the agent."
        : "Signed in with the cookie in the environment variable TEX_MCP_WEB_OVERLEAF_COOKIE.")
      : refused ? "Overleaf rejected this cookie. Paste a new one." : "Signed in with your saved cookie.";
    $("#overleaf-cookie-forget").hidden = cookie !== "file";
  }


  async function loadOverleafStatus() {
    const response = await fetch("/overleaf", { cache: "no-store" });
    if (!response.ok) return null;
    const linked = await response.json();
    renderOverleafStatus(linked);
    return linked;
  }


  async function connectOverleaf(change, done) {
    const response = await fetch("/overleaf/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    });
    if (!response.ok) {
      showSettings(`Could not save the change: ${await responseError(response)}`);
      await loadOverleafStatus();
      return false;
    }
    renderOverleafStatus(await response.json());
    // A git clone just connected shows where it stands in the top bar at once.
    readOverleafGitSoon(0);
    showSettings(done);
    return true;
  }

  async function switchOverleafMode(live) {
    if (live && !confirm(OVERLEAF_SYNC_QUESTION)) {
      $('#overleaf-mode input[value="hand"]').checked = true;
      return;
    }
    await connectOverleaf({ sync: live }, live ? "Files now sync live with Overleaf." : "Files now sync with Overleaf by hand.");
  }

  async function saveOverleafCookie() {
    const input = $("#overleaf-cookie");
    // A new cookie is not turned away until Overleaf says so, which the page may hear before
    // the answer to this request: so it is cleared before the request goes.
    const refused = overleaf.cookieRefused;
    overleaf.cookieRefused = false;
    const response = await fetch("/overleaf/cookie", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookie: input.value }),
    });
    if (!response.ok) {
      overleaf.cookieRefused = refused;
      showSettings(`Could not save the cookie: ${await responseError(response)}`);
      return;
    }
    input.value = "";
    renderOverleafStatus(await response.json());
    showSettings("Cookie saved.");
  }

  async function forgetOverleafCookie() {
    const response = await fetch("/overleaf/cookie", { method: "DELETE" });
    if (response.ok) renderOverleafStatus(await response.json());
    showSettings(response.ok ? "Cookie removed." : `Could not remove the cookie: ${await responseError(response)}`);
  }

  const overleafFailed = (error) => showSettings(`Could not reach the server: ${error.message}`);

  function attach() {
    $("#overleaf-connect-btn").addEventListener("click", async () => {
      const link = $("#overleaf-link").value.trim();
      if (!link) return;
      if (await connectOverleaf({ project: link }, "Connected. The top bar shows who is on Overleaf.")) {
        $("#overleaf-link").value = "";
      }
    });
    $("#overleaf-link").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); $("#overleaf-connect-btn").click(); }
    });
    $("#overleaf-disconnect-btn").addEventListener("click", () => {
      if (confirm("Disconnect this paper from its Overleaf project? Nothing will sync until you connect it again.")) {
        connectOverleaf({ project: null }, "Disconnected.");
      }
    });
    for (const box of $$('#overleaf-mode input[name="overleaf-mode"]')) {
      box.addEventListener("change", () => switchOverleafMode(box.value === "live"));
    }
    for (const box of $$('#overleaf-cursor input[name="overleaf-cursor"]')) {
      box.addEventListener("change", () => chooseOverleafCursor(box.value));
    }
    $("#overleaf-live-btn").addEventListener("click", () => showSettings());
    $("#overleaf-cookie-save").addEventListener("click", () => saveOverleafCookie().catch(overleafFailed));
    $("#overleaf-cookie").addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();  // Enter keeps the cookie rather than closing the dialog.
      saveOverleafCookie().catch(overleafFailed);
    });
    $("#overleaf-cookie-forget").addEventListener("click", () => forgetOverleafCookie().catch(overleafFailed));
  }

  return {
    attach,
    show: showSettings,
    loadStanding: loadOverleafStanding,
    load: loadOverleafStatus,
    renderCookie: renderOverleafCookie,
    failed: overleafFailed,
  };
}
