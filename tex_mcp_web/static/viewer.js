// The review page of a LaTeX paper. Its own parts are modules in page/, each of which says at its
// head what it does and what it takes and gives; the parts the review pages share are in
// shared/. This file wires them: it creates the page's parts and then the shared ones, each given
// the hooks it takes, and starts them in order once the page has loaded.

import { createSettings } from "./shared/settings.js";
import { $, showMacKeyNames } from "./shared/dom.js";
import { createCommentPanel } from "./shared/comment-panel.js";
import { createTabStrip } from "./shared/tab-strip.js";
import { createHelp } from "./shared/help.js";
import { createUpdate } from "./shared/update.js";
import { holdZoomOnFields, holdPinchOutside } from "./shared/page-zoom.js";
import { attachPanelGrips } from "./shared/panel-grips.js";
import { responseError } from "./page/response-error.js";
import { attachCopyGuard, attachEscapeKey } from "./page/page-keys.js";
import { attachTopBar } from "./page/top-bar.js";
import { attachSidebarToggle } from "./page/sidebar.js";
import { createSocket } from "./page/socket.js";
import { createPaper } from "./page/paper.js";
import { createWorkspace } from "./page/workspace.js";
import { createPdfView } from "./page/pdf-view.js";
import { createSourceEditor } from "./page/source-editor.js";
import { createProblems } from "./page/problems.js";
import { createBuild } from "./page/build.js";
import { createSections } from "./page/sections.js";
import { createTranslation } from "./page/translation.js";
import { anchorLabel, commentQuote } from "./page/comment-places.js";
import { createPlaces } from "./page/places.js";
import { createOverleafProject } from "./page/overleaf-project.js";
import { createOverleafSettings } from "./page/overleaf-settings.js";
import { createOverleafGit } from "./page/overleaf-git.js";
import { createOverleafSync } from "./page/overleaf-sync.js";
import { createOverleafLive } from "./page/overleaf-live.js";
import { createOverleafThreads } from "./page/overleaf-threads.js";
import { createOverleafReplies } from "./page/overleaf-replies.js";
import { createSuggestedEdits } from "./page/suggested-edits.js";

// The page's waits of a second or more, in milliseconds. Tests put shorter ones in
// window.texMcpWaits before the page loads.
const WAITS = {
  reconnect: 2000,     // before trying again to reach a server that has gone
  overleafGit: 4000,   // between reads of the folder's git state while comments wait on it
  gitAfterEdit: 1000,  // before reading the folder's git state after a file changed here
  calledWord: 2000,    // how long the word a press leaves beside the bell stays
  translation: 3000,   // before asking again for paragraphs still being translated
  ...window.texMcpWaits,
};

// What the browser keeps this page's choices under, named once, in index.html.
const PREFIX = document.querySelector("script[data-prefix]").dataset.prefix;

// What the comment panel shares with the page's parts.
const state = {
  // The comment panel sets the comments it lists, those opened and picked, the ringed one and
  // the comment tab shown here too (shared/comment-panel.js).
  //
  // The open comments of the comment tab shown, which the paper and the editor mark.
  markedComments: [],
};

// What the page knows of the paper's Overleaf project as the server says it, which several of its
// parts read. Each other fact of Overleaf has one part that writes it too: whether Overleaf turned
// the cookie away, Settings (overleaf-settings.js); where the folder stands against Overleaf,
// overleaf-git.js; and the reviewer's own name there, overleaf-live.js.
const overleaf = createOverleafProject();

// The comment panel as the Preview and the editor reach it: the open comments of the comment tab
// shown, which they mark, the ringed one and whether a card is open, and a comment shown in the
// panel from its mark.
const panelComments = {
  marked: () => state.markedComments,
  ringed: () => state.focusedCommentId,
  opened: (id) => state.expanded.has(id),
  showTab: () => strip.show(state.commentTab),
  show: (id) => panel.showComment(id),
};

// The named functions of a part, each calling through to the part when called: the parts reach
// each other both ways, so a part is handed another's functions before that one exists.
function reach(part, ...names) {
  return Object.fromEntries(names.map((name) => [name, (...args) => part()[name](...args)]));
}

// ----- The page's own parts -----

const socket = createSocket({
  reconnect: WAITS.reconnect,
  opened: (again) => {
    // A server that comes back may be a new version's: the page asks which runs now.
    update.read().catch((error) => console.error(error));
    // What changed while the page could not hear the server is read again. Before it first
    // could, only the comments can have changed unheard: the socket opens after their first
    // reading and the PDF viewer, and the server's first message brings the build. An agent's
    // reply written in between was never shown.
    if (again) catchUp().catch((error) => console.error(error));
    else panel.refreshComments().catch((error) => console.error(error));
    live.tellCursor();
  },
  heard: handleWebSocketMessage,
  gone: (gone) => panel.bell.show({ waiters: 0, gone }),
});

const paper = createPaper({
  read: (now) => {
    translation.applyLanguage(now.translation_language ?? null);
    sections.render(now);
  },
});

const workspace = createWorkspace({
  source: reach(() => source, "init", "resize"),
  pdf: reach(() => pdf, "zoomMode", "refit"),
  translationShown: () => translation.shown(),
});

const pdf = createPdfView({
  digest: () => paper.digest(),
  comments: panelComments,
  openCompose: (...args) => panel.openCompose(...args),
  showWhereWritten: (selection) => places.showSelectionInSource(selection),
  scheduleTranslation: (delay) => translation.schedule(delay),
});

const source = createSourceEditor({
  panelComments,
  project: overleaf.project,
  suggested: overleaf.suggested,
  showWherePrinted: (selection) => places.showSelectionInPreview(selection),
  openCompose: (...args) => panel.openCompose(...args),
  refreshComments: () => panel.refreshComments(),
  openSuggestedEdit: (id) => threads.openSuggestedEdit(id),
  compileFromKeys: () => build.compileFromKeys(),
  followedOnOverleaf: (path) => live.followed(path),
  renderOverleafCursors: () => live.renderCursors(),
  tellCursor: () => live.tellCursor(),
  followSourceCursor: () => translation.followCursor(),
  followFile: () => translation.followFile(),
  cursorAt: () => settings.cursorAt(),
});

// Where a place of the paper is shown, the one part that holds the Preview, the Source and the
// view together.
const places = createPlaces({ state, digest: () => paper.digest(), workspace, pdf, source });

const problems = createProblems({ showLayoutLine: places.showLayoutLine });

const build = createBuild({
  paper,
  pdf: reach(() => pdf, "view", "open", "shown"),
  problems,
  overleaf,
  source: reach(() => source, "dirty", "save"),
  refreshComments: () => panel.refreshComments(),
  scheduleTranslation: (delay) => translation.schedule(delay),
  showTab: (name) => strip.show(name),
});

const sections = createSections({
  showLine: places.showLine,
  panel: reach(() => panel, "openCompose", "actionButton"),
});

const translation = createTranslation({
  wait: WAITS.translation,
  workspace: reach(() => workspace, "view", "apply"),
  source: reach(() => source, "file", "started", "cursorRow"),
  pdf: reach(() => pdf, "scrolls", "currentPage"),
  showInSource: places.showInSource,
  digest: () => paper.digest(),
  failed: () => build.failed(),
  showProblems: () => build.showProblems(),
});

const overleafSettings = createOverleafSettings({
  overleaf,
  openSettings: (options) => settings.open(options),
  relinkGit: () => git.relinked(),
  readOverleafGitSoon: (delay) => git.readSoon(delay),
  renderOverleafThreadSync: () => sync.render(),
  renderPickedActions: () => panel.renderPickedActions(),
  renderNoPaper: () => build.renderNoPaper(),
  overleafCursorShown: () => live.cursorShown(),
  chooseOverleafCursor: (choice) => live.chooseCursor(choice),
});

const git = createOverleafGit({
  overleaf,
  gitAfterEdit: WAITS.gitAfterEdit,
  showSettings: (status) => overleafSettings.show(status),
  renderOverleafThreadSync: () => sync.render(),
  overleafFailed: (error) => overleafSettings.failed(error),
  readSuggestedEdits: () => suggestedEdits.read(),
});

const sync = createOverleafSync({
  overleaf,
  commentTab: () => state.commentTab,
  gitWait: WAITS.overleafGit,
  gitStanding: git.standing,
  gitTrouble: git.trouble,
  filesByHand: git.byHand,
  readOverleafGitState: () => git.readState(),
  showSettings: (status) => overleafSettings.show(status),
  refreshComments: () => panel.refreshComments(),
  cookieHeard: (refused) => overleafSettings.cookieHeard(refused),
  loadOverleafStatus: () => overleafSettings.load(),
  overleafFailed: (error) => overleafSettings.failed(error),
});

const live = createOverleafLive({
  source: reach(() => source, "ace", "file", "resize"),
  showInSource: places.showInSource,
  socket: reach(() => socket, "open", "send"),
  refreshComments: () => panel.refreshComments(),
  byHand: git.byHand,
  heardByHand: (now) => git.heardByHand(now),
  readOverleafGitSoon: (delay) => git.readSoon(delay),
  cookieHeard: (refused) => overleafSettings.cookieHeard(refused),
  renderOverleafThreadSync: () => sync.render(),
});

const suggestedEdits = createSuggestedEdits({
  state,
  overleaf,
  panel: reach(() => panel, "renderComments", "refreshComments", "pickBox", "actionButton"),
  rangeIds: () => panel.keys.rangeIds(),
  jumpToComment: (id) => places.showComment(id),
});

const outgoing = createOverleafReplies({
  state,
  overleaf,
  panel: reach(() => panel, "renderComments", "refreshComments", "renderPickedActions", "actionButton", "mutate"),
  keyHint: (name) => panel.keys.hint(name),
  draftedThread: (comment) => threads.drafted(comment),
});

const threads = createOverleafThreads({
  state,
  overleaf,
  me: live.me,
  panel: reach(() => panel, "unanswered", "replyButton", "setStatus", "setView", "showTab", "refreshComments",
    "showComment"),
  keyHint: (name) => panel.keys.hint(name),
  outgoing,
  suggestedEdits,
  showTab: (name) => strip.show(name),
  applySourceLeaf: () => source.applyLeaf(),
  renderOverleafThreadSync: () => sync.render(),
});

// ----- The parts shared with the other review pages -----

// The theme, the text size and the key that sends a comment, as Settings keeps them. The
// page's own parts and the viewer's follow the theme; the PDF's pages are drawn as compiled,
// white in every theme.
const settings = createSettings({
  prefix: PREFIX,
  onTheme: (theme) => {
    source.setTheme();
    pdf.setTheme(theme);
  },
  onLetters: (shown) => shown || panel.keys.showLetters(false),
  onCursor: () => source.holdCursorLine(),
});

// The list of comments, the row above it, its search, its cards and its keys are shared with
// the other review pages, in shared/comment-panel.js. What is this page's own goes in by the
// hooks below: the two comment tabs, Overleaf's threads, drafts and suggested edits, the hidden
// threads, the PDF and the editor.
const panel = createCommentPanel({
  state,
  prefix: PREFIX,
  settings,
  label: anchorLabel,
  quote: commentQuote,
  showPlace: (id) => places.showComment(id),
  enterPlace: (comment) => places.enterComment(comment),
  marked: (comments) => {
    state.markedComments = comments;
    pdf.markComments();
    source.markComments();
  },
  whole: () => $("#paper-comment-btn").click(),
  help: (page) => help.show(page),
  // The bell (shared/agent-bell.js). A press posts what callAgent sends, and the note after a
  // press no agent heard says how an agent comes to listen to this paper.
  bell: {
    call: callAgent,
    words: {
      queued: "No agent is listening yet, so the call is queued. Claude Code listens when started in the paper's "
        + "folder. In Codex, send “do tex listen”.",
      gone: "The review page's server cannot be reached",
    },
    wordStays: WAITS.calledWord,
  },
  // A comment shown from its mark on the paper or in the editor: its card opens and flashes.
  bringIntoView: (id, card) => {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("cmt-flash");
    setTimeout(() => card.classList.remove("cmt-flash"), 1600);
  },
  labels: {
    document: "paper", reply: "Reply privately", replyBox: "Private reply, for you and the agent…",
    firstSort: ["paper", "paper order"], composeTitle: "New private comment",
    composeBox: "leave a private comment for the coding agent…",
  },
  // The views each comment tab's list offers, as [value, name], the first being the one the tab
  // opens on. What is set aside but still open comes before what is finished. Overleaf has no
  // archive, and only its threads have drafts to send or are hidden on the page.
  tabs: {
    comments: {
      count: "#comments-tab-count",
      hint: "To comment, select words in the Preview or the Source, or draw a box with the box tool.",
    },
    "overleaf-comments": threads.tab,
  },
  tabOf: threads.tabOf,
  awaitsReviewer: threads.awaitsReviewer,
  along: threads.along,
  status: threads.status,
  search: threads.search,
  card: threads.card,
  row: threads.row,
  compose: { ...threads.compose, ...pdf.compose },
  keys: threads.keys,
});

// The sidebar's tabs. The two comment tabs share the Comments pane, which the panel fills.
const strip = createTabStrip({ shown: (name, clicked) => panel.showTab(name, clicked) });

// Help, with the paper's own Start here, search story and Overleaf page, and its own keys.
const help = createHelp({ prefix: PREFIX, pages: [["overleaf", "Overleaf"]], keys: panel.keys });

// The ⋯ menu's Update and the version line at its end, and the band that says what is left once
// an update is installed, above the band that says the server cannot be reached.
const update = createUpdate({
  name: "tex-mcp-web", server: "tex-mcp", menu: $("#more-menu"), before: $("#server-alert"),
  how: "https://github.com/MiiKiyoshi/tex-mcp-web/blob/main/docs/reviewing.md#updating",
});

// A press of the bell, for the session listening or the agent picked beside the bell. The
// picked comments go with a press for the session: with overleaf_picked, a thread from
// Overleaf reaches the agent with its Overleaf words only when picked.
async function callAgent(lane) {
  const response = await fetch("/review-request", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(lane ?? { picked: [...state.picked] }),
  });
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
}

// What changed while the page could not hear the server: the PDF, the comments and Overleaf.
async function catchUp() {
  const shown = paper.digest();
  await paper.refresh();
  if (paper.digest() && paper.digest() !== shown) await pdf.open(pdf.view());
  await panel.refreshComments();
  await overleafSettings.load();
}

async function handleWebSocketMessage(message) {
  switch (message.type) {
    case "compiling":
      build.heardCompiling(message);
      break;
    case "compile_requested":
      build.heardRequested(message);
      break;
    case "compiled":
      await build.heardCompiled(message);
      translation.schedule(1500);
      source.scheduleCompletions(1500);
      // A build may bring in or drop source files.
      if (source.started()) await source.refreshFiles();
      break;
    case "comment_added":
    case "comment_updated":
    case "comment_deleted":
    case "comments_changed":
      await panel.refreshComments();
      break;
    case "source_changed":
      git.readStateSoon();
      build.heardSourceChanged(message);
      await source.sourceChanged(message);
      break;
    case "config_reloaded":
      build.configReloaded();
      await paper.refresh();
      if (source.started()) await source.refreshFiles();
      await overleafSettings.loadStanding();
      break;
    case "config_error":
      build.configFailed(message);
      break;
    case "state":
      build.heardState(message);
      panel.bell.show({ lanes: message.lanes, waiters: message.review_waiters });
      // A build that ended after the page first read the paper and before its socket opened
      // left a PDF the page has not shown. A page that reconnects catches up as it opens.
      if (socket.closes() === 0 && message.pdf_digest !== paper.digest()) await catchUp();
      break;
    case "review_waiters":
      panel.bell.show({ waiters: message.waiters });
      break;
    case "lanes":
      panel.bell.show({ lanes: message.lanes });
      break;
    case "goto":
      pdf.showTarget(message);
      break;
    case "overleaf_live":
      live.render(message);
      break;
    case "overleaf_waiting":
      overleaf.heardWaiting(message.count);
      sync.render();
      break;
  }
}

async function init() {
  holdZoomOnFields();
  // Over the Preview the viewer zooms the PDF with a pinch.
  holdPinchOutside("#pdf-viewer");
  attachCopyGuard();
  attachTopBar({ showSettings: () => overleafSettings.show(), digest: () => paper.digest() });
  overleafSettings.attach();
  build.attachCompileKey();
  attachEscapeKey();
  sync.attach();
  git.attach();
  attachSidebarToggle();
  attachPanelGrips({
    prefix: PREFIX,
    width: "--tex-mcp-sidebar-width",
    height: "--tex-mcp-panel-height",
    split: () => workspace.split(),
    resizing: () => source.resize(),
    resized: (size) => {
      source.resize();
      if (size === "width") pdf.refitSoon();
    },
  });
  workspace.attach();
  pdf.attach();
  build.attach();
  translation.attach();
  source.attach();
  panel.attach();
  suggestedEdits.attach();
  outgoing.attach();
  $("#paper-comment-btn").addEventListener("click", () =>
    panel.openCompose({ kind: "paper" }, "Paper-level comment"));

  await paper.refresh();
  build.start();
  await panel.refreshComments();
  // A git clone of the Overleaf project: where it stands shows in the top bar from the start.
  overleafSettings.loadStanding().catch((error) => console.error(error));
  if (paper.digest()) await pdf.open(null);
  await workspace.restore();
  socket.connect();
}

document.addEventListener("DOMContentLoaded", () => {
  // On a Mac the keys go by the Mac's names, in Help and the notes above the lists too.
  showMacKeyNames("#help-dialog, .panel-note, #update-alert");
  init().catch((error) => {
    console.error(error);
    alert(`tex-mcp-web failed to start: ${error.message}`);
  });
});
