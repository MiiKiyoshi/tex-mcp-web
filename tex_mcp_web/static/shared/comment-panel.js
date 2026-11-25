// The comment panel of the review pages: each tab's comments in the view and the order chosen,
// the search above them, the row that changes the picked ones, the line that undoes the last
// change, the cards with their threads, replies, edits and suggestions, the dialog that writes
// a new comment, the keys that work the list, and the bell that calls the agent to the comments
// (agent-bell.js). The panel draws itself into two places the page gives, #tab-comments and
// #compose-dialog, and takes the page's hooks DEFAULTS lists. Its look is comment-panel.css.
//
// This file makes the panel from its parts, each a file of its own: what it holds of the comments
// it shows (comment-state.js), its markup (comment-markup.js), the list (comment-list.js), the
// views and orders (comment-views.js), the row (comment-row.js), the changes of status and Undo
// (comment-status.js), the cards (comment-card.js) and the new comment dialog
// (comment-compose.js), with the search (comment-search.js) and the keys (comment-keys.js). A
// page holds none of what the panel holds: it reads it, and changes it, through the panel
// createCommentPanel gives.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $ } from "./dom.js";
import { meetsSearch, createCommentSearch } from "./comment-search.js";
import { createCommentKeys } from "./comment-keys.js";
import { createCommentApi } from "./comment-api.js";
import { createAgentBell, BELL_STEPS } from "./agent-bell.js";
import { createCommentState } from "./comment-state.js";
import { paneMarkup, composeMarkup, drawPanel } from "./comment-markup.js";
import { createCommentList } from "./comment-list.js";
import { createCommentViews } from "./comment-views.js";
import { createCommentRow } from "./comment-row.js";
import { createCommentStatus } from "./comment-status.js";
import { createCommentCard } from "./comment-card.js";
import { createCommentCompose } from "./comment-compose.js";

// The hooks the page gives createCommentPanel, each with what the panel does without it, so a
// page gives only what it changes. settings is required. A hook is called each time it is
// needed, so what it answers may change, as the document shown does. A name the panel does not
// know is refused.
const DEFAULTS = {
  // The page's Settings (createSettings): sendOn, sendKeyName, keyLetters, and prefix, what the
  // browser keeps the order of each tab's list under, before "CommentSort-<tab>".
  settings: null,
  // The route base of the comment calls (createCommentApi): "" for the page's own routes, or
  // null while nothing is shown to comment on, when the list is empty.
  base: () => "",
  // A comment's place as its card names it, from its anchor.
  label: (anchor) => anchor.kind,
  // The words a comment is on, which its opened card quotes; "" for none.
  quote: (comment) => comment.anchor?.quote ?? "",
  // Whether what a comment is on can no longer be found.
  stale: (comment) => Boolean(comment.stale),
  // Where a comment is, for the sort list's first order: an array compared item by item, such
  // as [page, y, x], or null to keep the order the server lists it in.
  place: () => null,
  // Shows where a comment is, given the comment: a card opened from the list or by Space, the
  // keys staying there.
  showPlace: () => {},
  // K: the keys taken to a comment's words, as in the source. Without it there is no K.
  enterPlace: null,
  // Given the shown tab's open comments after each reading, to mark where they are.
  marked: () => {},
  // The bell's hooks, as agent-bell.js lists them; without them there is no bell. The page tells
  // the bell what it hears with panel.bell.show.
  bell: null,
  // c: a comment on the whole document. Without it there is no c.
  whole: null,
  // Help, opened on a page or on the one shown last: ? and the search's "more…". Without it
  // neither is offered.
  help: null,
  // The files a comment is on, which the search's file: looks in. A suggestion is a change to a
  // file, which the reviewer applies or rejects on its card with aa and nn. Null where comments
  // are on no file: the search then has no file:, and there are no aa and nn.
  files: (comment) => [comment.anchor?.file],
  // A card opened by showComment, the comment shown from its mark, brought into view: scrolled
  // to the middle of the list, smoothly, and flashed.
  bringIntoView: (id, card) => {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("cmt-flash");
    setTimeout(() => card.classList.remove("cmt-flash"), 1600);
  },
  // The words the panel says: `document`, what is reviewed, as the keys name it; `reply`, the
  // reply button's word; `replyBox`, what the reply box says; `firstSort`, [value, name] of
  // the sort list's first order; and `composeTitle` and `composeBox`, the new comment dialog's
  // heading and what its box says.
  labels: { document: "document", reply: "Reply", replyBox: "Reply for the agent…", firstSort: ["page", "page order"],
            composeTitle: "New comment", composeBox: "Leave feedback for the agent" },
  // { name: tab } for the tabs that list comments, which share one pane, the first shown first.
  // Each takes the fields TAB lists.
  tabs: { comments: {} },

  // The page's own parts, in groups. What the page reads beside each list, applied once that
  // reading is the latest.
  along: { read: async () => null, apply: () => {} },
  // of(comment), the status a comment is listed under; said(status, before, what), what the
  // Undo line says of `what`, as "2 comments", sent to `status` from the statuses in `before`,
  // or null for the panel's own words; and waits(comment, agentWroteLast), whether an open
  // comment waits for the reviewer's response, given whether the agent wrote last.
  status: { of: (comment) => comment.status, said: () => null, waits: (comment, agentWroteLast) => agentWroteLast },
  // What the search reads of a comment beside its own words and its files (meetsSearch): me(),
  // words(comment) and said(comment).
  search: { me: () => null, words: () => [], said: (comment) => comment.thread },
  // A card's own parts: note(comment, expanded), under its head; reply(comment, expanded), under
  // its thread and suggestion; actions(comment, buttons), the buttons an opened card shows,
  // given the panel's; entryMark(entry), beside an entry; mayChange(entry, comment), whether the
  // reviewer may change an entry they wrote; deleteQuestion(entry, index, question), what
  // deleting one asks, given the panel's question. Null for nothing.
  card: { note: () => null, reply: () => null, actions: (comment, buttons) => buttons, entryMark: () => null,
          mayChange: () => true, deleteQuestion: (entry, index, question) => question },
  // The page's buttons in the row: render(settled, { picked, listed, view, tab }), drawn for
  // the picked comments Resolve, Reopen and Archive act on, given every picked comment the list
  // shows, the comments it shows, the view it shows ("all" during a search) and the comment tab
  // shown, answering { resolve: false } to hide Resolve; steps, the row's steps that send them
  // behind the dots, by the step they come after; confirm(button, comments, status), whether a
  // press on the listed comments goes on; and settles(comment), whether Resolve, Reopen and
  // Archive act on a picked one.
  row: { render: () => ({}), steps: {}, confirm: () => true, settles: () => true },
  // The new comment dialog's own parts: opening(fields), which readies them and answers what it
  // changes of the panel's own, { title, box: { placeholder, rows, required } }, or nothing;
  // filled(fields), what they add to the comment; refused(answer, body), what to say of a
  // refusal, { say, keep }, `keep` to close the dialog and keep what was written for the next
  // one, or null for the panel's words; posted(comment, body), what the page does once a comment
  // is posted, before the list is read again; closed().
  compose: { opening: () => ({}), filled: () => ({}), refused: () => null, posted: () => {}, closed: () => {} },
  // The page's own keys (createCommentKeys): rows(press), and rests(event).
  keys: { rows: () => [], rests: () => false },
};

// What a comment tab gives: `holds(comment)`, whether it lists a comment, a comment no tab holds
// going in the first; `views`, [value, name] with the first the one it opens on, a name given as
// a function when it changes; `noun`, what it calls one of its comments; `count`, the selector of
// its count of what waits; `hint`, what it says while it has no comment at all; `empty`, what a
// view of its own says when it lists nothing, by view; `archives: false` when its comments cannot
// be archived; `list(status, comments, searching, unanswered)`, what each of its views lists from
// its comments read, given unanswered(comment); `items()`, what it lists beside its comments,
// which waits for the reviewer too; `itemCard(item, { open, ringed, inRange })`, the card of one
// of those, given whether it is open, ringed and in the keys' range, or null for a comment's own;
// `group(status, comment)`, the group a comment sorts in, lower first; and `chosen(view)`, told
// of a view the reader chose.
const TAB = {
  holds: () => false,
  views: [["open", "open"], ["unanswered", "unanswered"], ["archived", "archived"], ["resolved", "resolved"], ["all", "all"]],
  noun: "comment", count: null, hint: "", empty: {}, archives: true, list: null, items: () => [], itemCard: () => null,
  group: () => 0, chosen: () => {},
};

// The hooks the page gives over the defaults, a group's over its own. A hook the panel does not
// know is refused, so a misspelt or retired name does not pass unseen.
function withDefaults(given) {
  const known = (names, defaults, where) => {
    for (const name of names) if (!(name in defaults)) throw new Error(`createCommentPanel: no hook ${where}${name}`);
  };
  known(Object.keys(given), DEFAULTS, "");
  if (given.settings == null) throw new Error("createCommentPanel: settings is required");
  const host = { ...DEFAULTS, ...given };
  for (const group of ["labels", "along", "status", "search", "card", "row", "compose", "keys"]) {
    known(Object.keys(given[group] ?? {}), DEFAULTS[group], `${group}.`);
    host[group] = { ...DEFAULTS[group], ...given[group] };
  }
  host.tabs = Object.fromEntries(Object.entries(host.tabs).map(([name, tab]) => {
    known(Object.keys(tab), TAB, `tabs.${name}.`);
    return [name, { ...TAB, ...tab }];
  }));
  return host;
}

// The panel, drawn at once into the page's #tab-comments and #compose-dialog, with the page's
// own parts that wait in them (drawPanel): its buttons for the picked comments,
// data-slot="picked", which follow Archive; what goes before the bell, "fetch"; its lines over
// the Undo line, "notes", which the bell's note follows; and its boxes over the comment's own,
// "compose". `given` is the page's hooks, over DEFAULTS.
//
// It gives the panel. Its readers each give the value at the moment they are called, an array
// as a copy: listed(), the comments the list shows, in order; picked(), the picked ids; ringed(),
// the ringed comment's id, or null; isOpen(id), whether its card is open; and tab(), the comment
// tab shown. keys gives the keys' table, hint(button) and showLetters(shown), and bell, when
// there is one, show(heard). Its commands: attach(), refreshComments(), renderComments(),
// renderPickedActions(), openCompose(anchor, label, fields, words), showComment(id),
// showTab(name, focus), setView(view), setStatus(ids, status), mutate(id, action, body,
// options), reset(); expand(id, open) and ring(id), each drawn at once, and unpick(ids), drawn by
// the reading that follows; and, for a page's own cards, pickBox(item), actionButton(class,
// label, onclick) and replyButton(comment).
export function createCommentPanel(given) {
  const host = withDefaults(given);
  const state = createCommentState(Object.keys(host.tabs)[0]);
  drawPanel($("#tab-comments"), paneMarkup(host.labels, host.help !== null, host.files !== null));
  drawPanel($("#compose-dialog"), composeMarkup(host.labels));
  const api = createCommentApi(() => host.base());

  // The parts, which call each other both ways: each is handed another's functions, each calling
  // through to that part when called, before that part is made.
  const parts = {};
  const call = (part, name) => (...args) => parts[part][name](...args);

  // The keys, which attach() sets listening, and what they call of the panel.
  const { attach: attachKeys, ...keys } = createCommentKeys({
    state,
    labels: { document: host.labels.document, firstSort: host.labels.firstSort[1] },
    panel: () => ({
      renderComments: call("list", "renderComments"), renderPickedActions: call("row", "renderPickedActions"),
      foldAll: call("row", "foldAll"), undoStatus: call("status", "undoStatus"), closeReply: call("card", "closeReply"),
      chosenView: call("views", "chosenView"), cycleSort: call("views", "cycleSort"), openCommentSearch,
    }),
    rows: (press) => host.keys.rows(press),
    showPlace: (comment) => host.showPlace(comment),
    enterPlace: host.enterPlace,
    whole: host.whole,
    help: host.help,
    suggestions: host.files !== null,
    lettersShown: () => host.settings.keyLetters(),
    rests: (event) => host.keys.rests(event),
  });
  // What the search reads of a comment (meetsSearch): the page's search hooks and its files.
  const searchHooks = { ...host.search, files: host.files };
  const { searchQuery, openCommentSearch, closeCommentSearch, attachCommentSearch } = createCommentSearch({
    refresh: call("list", "refreshComments"),
    tabComments: async () => (await parts.list.readAll()).filter((comment) => tabOf(comment) === state.tab()),
    page: searchHooks,
  });
  // The bell, at the end of the row and over the Undo line, which attach() sets listening.
  const bell = host.bell && createAgentBell(host.bell, {
    prefix: host.settings.prefix, row: $("#comment-fetch"), noteBefore: $("#undo-line"), fit: call("row", "fit"),
  });

  // The shown comment tab's hooks.
  function tab() {
    return host.tabs[state.tab()];
  }

  // The tab that lists a comment: the first that holds it, else the first.
  function tabOf(comment) {
    return Object.entries(host.tabs).find(([, each]) => each.holds(comment))?.[0] ?? Object.keys(host.tabs)[0];
  }

  function statusOf(comment) {
    return host.status.of(comment);
  }

  // Whether the last message of an open thread is not the reviewer's: the agent wrote the last
  // entry, or as the page judges a thread of its own (status.waits). A comment the agent has
  // not answered yet is only open, and one listed under another status is not unanswered.
  function unanswered(comment) {
    return statusOf(comment) === "open" && host.status.waits(comment, comment.thread.at(-1)?.author === "agent");
  }

  const shared = { state, host, tab };
  parts.list = createCommentList({
    ...shared, api, tabOf, statusOf, unanswered, keys, bell, searchQuery, closeCommentSearch,
    meets: (comment, query) => meetsSearch(comment, query, searchHooks),
    view: call("views", "view"), commentSort: call("views", "commentSort"), setView: call("views", "setView"),
    showSearchView: call("views", "showSearchView"), renderTabs: call("views", "renderTabs"),
    card: call("card", "card"), boxOf: call("card", "boxOf"),
    renderPickedActions: call("row", "renderPickedActions"), renderFold: call("row", "renderFold"),
  });
  parts.views = createCommentViews({
    ...shared, keys, closeCommentSearch, closeCommentRare: call("row", "closeCommentRare"),
    refreshComments: parts.list.refreshComments, renderComments: parts.list.renderComments,
  });
  parts.row = createCommentRow({
    ...shared, bellSteps: bell ? BELL_STEPS : {}, view: parts.views.view, renderComments: parts.list.renderComments,
    setStatus: call("status", "setStatus"),
  });
  parts.status = createCommentStatus({ ...shared, api, statusOf, keys, refreshComments: parts.list.refreshComments });
  parts.card = createCommentCard({
    ...shared, api, statusOf, unanswered, keys, bell, view: parts.views.view, renderComments: parts.list.renderComments,
    refreshComments: parts.list.refreshComments, pickBox: parts.row.pickBox, setStatus: parts.status.setStatus,
  });
  parts.compose = createCommentCompose({ ...shared, api, bell, refreshComments: parts.list.refreshComments });
  const { list, views, row, status, card, compose } = parts;

  // The panel's controls wired: the row, Undo, the search, the dialog, the views, the orders and
  // the keys.
  function attach() {
    bell?.attach();
    row.attach();
    status.attach();
    $("#comment-search").title = `Search the comments of this tab${keys.hint("comment-search")}`;
    $("#search-help-btn")?.addEventListener("click", () => host.help("search"));
    compose.attach();
    views.attach();
    attachCommentSearch();
    attachKeys();
  }

  // What the panel holds for the document shown let go, for a page that shows another in its
  // place: its comments, those opened and picked, the ring and a held range, a box being written
  // in or an entry being edited, and the change Undo would put back, which was made to the
  // other's comments. A reading, a change or a comment still on its way for that one does
  // nothing more when it comes back.
  function reset() {
    state.bump();
    state.clear();
    list.reset();
    card.reset();
    status.reset();
    bell?.posted(false);
    keys.reset();
  }

  return {
    listed: state.listed,
    picked: state.picked,
    ringed: state.ringed,
    isOpen: state.isOpen,
    tab: state.tab,
    keys: { table: keys.table, hint: keys.hint, showLetters: keys.showLetters },
    bell: bell && { show: bell.show },
    attach,
    refreshComments: list.refreshComments,
    renderComments: list.renderComments,
    renderPickedActions: row.renderPickedActions,
    openCompose: compose.openCompose,
    showComment: list.showComment,
    showTab: views.showTab,
    setView: views.setView,
    setStatus: status.setStatus,
    mutate: card.mutate,
    reset,
    expand(id, open = true) {
      state.setOpen(id, open);
      list.renderComments();
    },
    ring(id) {
      state.ring(id);
      list.renderComments();
    },
    unpick(ids) {
      state.pick(ids, false);
    },
    pickBox: row.pickBox,
    actionButton: card.actionButton,
    replyButton: card.replyButton,
  };
}
