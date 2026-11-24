// The comment panel of the review pages: each tab's comments in the view and the order chosen,
// the search above them, the row that changes the picked ones, the line that undoes the last
// change, the cards with their threads, replies, edits and suggestions, the dialog that writes
// a new comment, and the keys that work the list. The panel draws itself into two places the
// page gives, #tab-comments and #compose-dialog, and takes the page's hooks DEFAULTS lists. Its
// look is comment-panel.css.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, $$, h, clear, placeholder } from "./dom.js";
import { meetsSearch, createCommentSearch } from "./comment-search.js";
import { createCommentKeys } from "./comment-keys.js";
import { createCommentApi } from "./comment-api.js";

function escaped(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

// The panel's markup. Its row has four groups, a thin line between each two: what to look at,
// picking, what acts on the picked comments, and fetching and calling. The buttons are icons,
// with the words as title and aria-label. Each button shows the one drawing that fits its
// state; a count rides beside the drawing where the action has one. A button with nothing to
// do is greyed out, not taken away: the row keeps one shape. Each <slot> is where the page's
// own parts go (drawPanel). With Help, the search's conditions end with a link to its page.
function paneMarkup(labels, helped) {
  const [sortValue, sortName] = labels.firstSort.map(escaped);
  return `<div class="filter-row">
  <span class="row-group">
    <!-- The shown tab's views (renderCommentFilter). In a row short of room even for the
         shortest view's name it is its icon alone, with the list lying clear over it. -->
    <span class="pick view-pick"><span class="icon-btn view-icon" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h12l-4.5 5.5V13l-3-1.5V8.5z"/></svg>
      </span><span id="comment-filter-name" class="pick-name" aria-hidden="true">open</span><select id="comment-filter" aria-label="View">
      <option value="open" selected>open</option>
    </select></span>
    <!-- The order of the list, kept for each comment tab. In a row short of room it is its
         icon alone, with the list lying clear over it. -->
    <span id="comment-sort-pick" class="pick sort-pick"><span class="icon-btn sort-icon" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4h6M2 8h4.5M2 12h3M12 3v10M9.5 10.5 12 13l2.5-2.5"/></svg>
      </span><span id="comment-sort-name" class="pick-name" aria-hidden="true">${sortName}</span><select id="comment-sort" aria-label="Sort by">
      <option value="${sortValue}" selected>${sortName}</option>
      <option value="latest">latest first</option>
      <option value="oldest">oldest first</option>
    </select></span>
    <button id="fold-all-btn" class="icon-btn" type="button" aria-label="Expand all" title="Expand all">
      <svg class="icon-expand" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6l4-4 4 4M4 10l4 4 4-4"/></svg>
      <svg class="icon-collapse hidden" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 2l4 4 4-4M4 14l4-4 4 4"/></svg>
    </button>
    <!-- Shown when the row is too narrow: the buttons the row has no room for wait
         behind the dots, each with its words. -->
    <span id="comment-rare" class="comment-rare">
      <button id="comment-rare-btn" class="icon-btn" type="button" aria-haspopup="true" aria-expanded="false" aria-label="More" title="More">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="12.5" cy="8" r="1.3"/></svg>
      </button>
      <span class="comment-rare-group"></span>
    </span>
  </span>
  <span class="row-group">
    <input id="pick-all-btn" class="comment-pick" type="checkbox" aria-label="Select all" title="Select all">
  </span>
  <span class="row-group">
    <!-- Resolve while a picked comment is open, Reopen while every picked one is closed. -->
    <button id="resolve-picked-btn" class="icon-btn" type="button" disabled data-status="resolved" aria-label="Resolve" title="Resolve the selected comments">
      <svg class="icon-resolve" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M5 8l2 2 4-4"/></svg>
      <svg class="icon-reopen hidden" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.5-3.5"/><path d="M13 3v3h-3"/></svg>
      <span class="wide-label">Resolve</span><span class="count"></span>
    </button>
    <button id="archive-picked-btn" class="icon-btn" type="button" disabled data-status="archived" aria-label="Archive" title="Archive the selected comments">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 3h12v3H2z"/><path d="M3 6v7h10V6M6 9h4"/></svg><span class="count"></span>
    </button>
    <slot name="picked"></slot>
  </span>
  <span id="comment-fetch" class="row-group">
    <slot name="fetch"></slot>
    <!-- The bell, which calls the agent to the comments. -->
    <button id="call-agent-btn" class="icon-btn agent-offline" type="button" aria-label="Queue call for agent" title="No agent is listening. The call will be queued until one connects (Ctrl+Alt+Enter)">
      <span id="agent-waiting-dot" class="agent-dot"></span>
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 11V7a4 4 0 0 1 8 0v4l1.5 1.5h-11z"/><path d="M6.5 14a1.5 1.5 0 0 0 3 0"/></svg>
      <span id="call-agent-word" class="count"></span>
    </button>
  </span>
</div>
<!-- Lists the tab's comments that meet what is typed, whatever their status. -->
<input id="comment-search" class="comment-search" type="search" autocomplete="off" spellcheck="false"
       aria-label="Search comments" placeholder="Search words, a file or a name…">
<!-- Shown while the search box has the focus: a click puts the condition into the box at
     the cursor. The list under them offers the tab's writers for from: and its files for file:. -->
<div id="search-terms" class="search-terms" hidden>
  <button type="button" tabindex="-1" data-insert="from:" title="Show comments with a message from a person. Type a name or part of one, or use from:me or from:agent.">from:</button>
  <button type="button" tabindex="-1" data-insert="file:" title="Show comments on a file. Type its name or part of it.">file:</button>
  <button type="button" tabindex="-1" data-insert='""' title="Match the exact phrase">"phrase"</button>
  <button type="button" tabindex="-1" data-insert="-" title="Exclude comments that contain the next word">-not</button>
  <button type="button" tabindex="-1" data-insert="OR" title="Match either the word before or the word after">OR</button>
  <button type="button" tabindex="-1" data-insert="after:" title="Show comments written on or after a date, such as 11-13, 2026-11-13 or 11-13T09:30">after:</button>
  <button type="button" tabindex="-1" data-insert="before:" title="Show comments written before a date, such as 11-13, 2026-11-13 or 11-13T09:30">before:</button>
  ${helped ? '<button id="search-help-btn" class="search-help" type="button" tabindex="-1" title="Search help">more…</button>' : ""}
  <div id="search-pick" class="search-pick" role="listbox" aria-label="Suggestions" hidden></div>
</div>
<slot name="notes"></slot>
<!-- Said after comments change status from the page, with Undo, until the next change. -->
<p id="undo-line" class="panel-note undo-line" role="status" hidden>
  <span id="undo-text"></span>
  <button id="undo-btn" class="panel-note-go undo-go" type="button">Undo</button>
  <button id="undo-close" class="undo-close" type="button" aria-label="Close" title="Close">
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>
  </button>
</p>
<div id="comments-list" class="comments-list" tabindex="-1"></div>`;
}

function composeMarkup(labels) {
  return `<form method="dialog" id="compose-form">
  <h3 id="compose-title">${escaped(labels.composeTitle)}</h3>
  <p id="compose-anchor" class="compose-anchor"></p>
  <slot name="compose"></slot>
  <textarea id="compose-text" rows="6" required placeholder="${escaped(labels.composeBox)}"></textarea>
  <div class="dialog-actions">
    <button type="button" id="compose-cancel">Cancel</button>
    <button type="submit" id="compose-submit">Post</button>
  </div>
</form>`;
}

// The markup drawn into *place*, with the page's own parts that wait in it, each marked
// data-slot with the name of the <slot> it takes the place of. They go there in their order,
// as the slot's own children: the row's groups, its fitting and its key letters know their
// buttons as children of the group.
function drawPanel(place, markup) {
  const parts = [...place.querySelectorAll(":scope > [data-slot]")];
  place.innerHTML = markup;
  for (const slot of place.querySelectorAll("slot")) {
    const own = parts.filter((part) => part.dataset.slot === slot.name);
    for (const part of own) part.removeAttribute("data-slot");
    slot.replaceWith(...own);
  }
}

// How many of the comments a search finds are drawn.
const SEARCH_SHOWN = 100;

const STATUS_DONE = { resolved: "Resolved", open: "Reopened", archived: "Archived" };

// The views a server's list gives as they are; any other view lists some of the open comments.
const LISTED = ["resolved", "archived", "all"];

// How the row gives way, one step at a time while it would spill over: the sort list shrinks
// to its icon, the words beside the other icons go, then Resolve's, the sort list waits behind
// the dots as the gaps narrow, then Expand all, and the view list gives up the room its longest
// view needs. Last the view list shrinks to its icon, with the list lying over it. Resolve and
// Archive stay in the row, which takes two lines only when even that is too wide. Each step
// names what then waits behind the dots. The page's own steps come in after the step they name.
const ROW_STEPS = [["sort-shrunk"], ["wordless"], ["resolve-wordless"], ["narrow", "#comment-sort-pick"],
  ["foldless", "#fold-all-btn"], ["squeezed"], ["view-shrunk"], ["wrapped"]];

// The hooks the page gives createCommentPanel, each with what the panel does without it, so a
// page gives only what it changes. state, prefix and settings are required. A hook is called
// each time it is needed, so what it answers may change, as the document shown does. A name
// the panel does not know is refused.
const DEFAULTS = {
  // What the panel shares with the page and the keys. It sets `comments`, the comments the list
  // shows, in its order; `expanded`, those opened; `picked`; `focusedCommentId`, the one the
  // ring is on; and `commentTab`, the comment tab shown.
  state: null,
  // What the browser keeps the order of each tab's list under, before "CommentSort-<tab>".
  prefix: null,
  // The page's Settings (createSettings): sendOn, sendKeyName and keyLetters.
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
  // Shows where a comment is: a card opened from the list or by Space, the keys staying there.
  showPlace: () => {},
  // K: the keys taken to a comment's words, as in the source.
  enterPlace: () => {},
  // Given the shown tab's open comments after each reading, to mark where they are.
  marked: () => {},
  // c: a comment on the whole document.
  whole: () => {},
  // Help, opened on a page or on the one shown last: ? and the search's "more…". Without it
  // neither is offered.
  help: null,
  // A card opened by showComment, the comment shown from its mark, brought into view.
  bringIntoView: (id, card) => card.scrollIntoView({ block: "nearest" }),
  // The words the panel says: `document`, what is reviewed, as the keys name it; `reply`, the
  // reply button's word; `replyBox`, what the reply box says; `firstSort`, [value, name] of
  // the sort list's first order; and `composeTitle` and `composeBox`, the new comment dialog's
  // heading and what its box says.
  labels: { document: "document", reply: "Reply", replyBox: "Reply for the agent…", firstSort: ["page", "page order"],
            composeTitle: "New comment", composeBox: "Leave feedback for the agent" },
  // { name: tab } for the tabs that list comments, which share one pane, the first shown first.
  // Each takes the fields TAB lists.
  tabs: { comments: {} },
  // The tab that lists a comment; one named by none goes in the first.
  tabOf: () => undefined,
  // Whether an open comment waits for the reviewer's response, given whether the agent wrote last.
  awaitsReviewer: (comment, agentWroteLast) => agentWroteLast,

  // The page's own parts, in groups. What the page reads beside each list, applied once that
  // reading is the latest.
  along: { read: async () => null, apply: () => {} },
  // Told whether some comment waits for the agent, and that a comment or a reply was posted.
  agent: { awaited: () => {}, posted: () => {} },
  // of(comment), the status a comment is listed under; said(status, before, what), what the
  // Undo line says of `what`, as "2 comments", sent to `status` from the statuses in `before`,
  // or null for the panel's own words.
  status: { of: (comment) => comment.status, said: () => null },
  // What the search reads of a comment beside its own words (meetsSearch): me(), words(comment),
  // files(comment) and said(comment).
  search: { me: () => null, words: () => [], files: (comment) => [comment.anchor?.file], said: (comment) => comment.thread },
  // A card's own parts: note(comment, expanded), under its head; reply(comment, expanded), under
  // its thread and suggestion; actions(comment, buttons), the buttons an opened card shows,
  // given the panel's; entryMark(entry), beside an entry; mayChange(entry, comment), whether the
  // reviewer may change an entry they wrote; deleteQuestion(entry, index, question), what
  // deleting one asks, given the panel's question. Null for nothing.
  card: { note: () => null, reply: () => null, actions: (comment, buttons) => buttons, entryMark: () => null,
          mayChange: () => true, deleteQuestion: (entry, index, question) => question },
  // The page's buttons in the row: render(picked), drawn for the picked comments, answering
  // { resolve: false } to hide Resolve; steps, the row's steps that send them behind the dots,
  // by the step they come after; confirm(button, ids, status), whether a press goes on; and
  // settles(comment), whether Resolve, Reopen and Archive act on a picked one.
  row: { render: () => ({}), steps: {}, confirm: () => true, settles: () => true },
  // The new comment dialog's own parts: opening(fields), to ready them once the panel has set
  // its own; filled(fields), what they add to the comment; refused(answer, body), what to say
  // of a refusal, { say, keep }, `keep` to close the dialog and keep what was written for the
  // next one, or null for the panel's words; posted(comment, body), what the page does once a
  // comment is posted, before the list is read again; closed().
  compose: { opening: () => {}, filled: () => ({}), refused: () => null, posted: () => {}, closed: () => {} },
  // The page's own keys (createCommentKeys): rows(press), and rests(event).
  keys: { rows: () => [], rests: () => false },
};

// What a comment tab gives: `views`, [value, name] with the first the one it opens on, a name
// given as a function when it changes; `noun`, what it calls one of its comments; `count`, the
// selector of its count of what waits; `hint`, what it says while it has no comment at all;
// `empty`, what a view of its own says when it lists nothing, by view; `archives: false` when
// its comments cannot be archived; `list(status, comments, searching)`, what each of its views
// lists from its comments read; `items()`, what it lists beside its comments, which waits for
// the reviewer too; `itemCard(item)`, the card of one of those, or null for a comment's own;
// `group(status, comment)`, the group a comment sorts in, lower first; and `chosen(view)`, told
// of a view the reader chose.
const TAB = {
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
  for (const name of ["state", "prefix", "settings"]) {
    if (given[name] == null) throw new Error(`createCommentPanel: ${name} is required`);
  }
  const host = { ...DEFAULTS, ...given };
  for (const group of ["labels", "along", "agent", "status", "search", "card", "row", "compose", "keys"]) {
    known(Object.keys(given[group] ?? {}), DEFAULTS[group], `${group}.`);
    host[group] = { ...DEFAULTS[group], ...given[group] };
  }
  host.tabs = Object.fromEntries(Object.entries(host.tabs).map(([name, tab]) => {
    known(Object.keys(tab), TAB, `tabs.${name}.`);
    return [name, { ...TAB, ...tab }];
  }));
  return host;
}

function counted(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

// When something was written, on this browser's clock, as MM-DD HH:MM: the clock that after:
// and before: in the search name their days and minutes on. "" for a time that cannot be read.
export function shortTime(at) {
  const moment = new Date(at ?? "");
  if (Number.isNaN(moment.getTime())) return "";
  const two = (value) => String(value).padStart(2, "0");
  return `${two(moment.getMonth() + 1)}-${two(moment.getDate())} ${two(moment.getHours())}:${two(moment.getMinutes())}`;
}

// An entry that carries its writer's name goes by it, as where it was written.
function writer(entry) {
  return entry.name ?? entry.author;
}

// When a thread was last written in, which latest first and oldest first order by: its last
// entry, or for one with none, as a suggested edit, when it was made.
function lastWritten(comment) {
  return Date.parse(comment.thread.at(-1)?.at ?? comment.at ?? comment.created);
}

// Two places, as the `place` hook gives them, compared item by item; 0 when either is null.
function byPlace(first, second) {
  if (first === null || second === null) return 0;
  for (let at = 0; at < Math.min(first.length, second.length); at += 1) {
    if (first[at] !== second[at]) return first[at] < second[at] ? -1 : 1;
  }
  return first.length - second.length;
}

// The panel, drawn at once into the page's #tab-comments and #compose-dialog, with the page's
// own parts that wait in them (drawPanel): its buttons for the picked comments,
// data-slot="picked", which follow Archive; what goes before the bell, "fetch"; its lines over
// the Undo line, "notes"; and its boxes over the comment's own, "compose". `given` is the
// page's hooks, over DEFAULTS.
export function createCommentPanel(given) {
  const host = withDefaults(given);
  const { state } = host;
  Object.assign(state, {
    comments: [],
    expanded: new Set(),
    picked: new Set(),
    focusedCommentId: null,
    commentTab: Object.keys(host.tabs)[0],
  });
  // What the panel keeps to itself. The box a pick last started from, so shift can run from it
  // to the next one.
  let pickedFrom = null;
  // The reply box open on a card, and the entry being edited, each with what is typed in it.
  let activeForm = null;
  let editingEntry = null;
  // How many comments a search found beyond those drawn.
  let searchMore = 0;
  // The order each comment tab's list is in, as its sort list was last set: the list's first
  // order, latest or oldest.
  const commentSorts = {};
  // How many things wait for the reviewer's response in each comment tab, as the unanswered
  // view lists them, with what a tab lists beside its comments.
  let unansweredCounts = {};
  // The comment tab and status the list shows, so a list drawn again knows it is the same view.
  let commentsView = null;
  // How many times the list was asked for, so only the latest answer is drawn, and the
  // reading under way.
  let commentsAsked = 0;
  let commentsReading = null;
  // The shown comment tab has no comment at all, in any view.
  let tabEmpty = false;
  // The last change of status made on the page, which the line above the list offers to undo:
  // what the line says, the status each comment it changed had before and when one resolved
  // was resolved, why some could not be put back, and how many no longer exist. Null when
  // there is nothing to undo.
  let undo = null;
  // The view the list showed before a search, which it shows again once the search is cleared.
  let viewBeforeSearch = "open";
  // Why applying a thread's suggestion was refused, with the suggestion it was refused for.
  const unfitSuggestions = new Map();
  // What a new comment is on, what the page posts with it, whether it is being posted, and what
  // was written in one the server turned away for now, for the next comment box opened.
  let pendingAnchor = null;
  let composeFields = {};
  let composeSubmitting = false;
  let keptComment = "";
  // Counted up each time the panel lets go of what it held for one document (reset), so a
  // change still on its way for that one stops when it comes back.
  let resets = 0;
  // The page's elements the row sent behind the dots, each with where it stood, in the order
  // they went, so the row puts them back before it is fitted again.
  let moved = [];

  drawPanel($("#tab-comments"), paneMarkup(host.labels, host.help !== null));
  drawPanel($("#compose-dialog"), composeMarkup(host.labels));
  const api = createCommentApi(() => host.base());
  const keys = createCommentKeys({
    state,
    labels: { document: host.labels.document, firstSort: host.labels.firstSort[1] },
    panel: () => panel,
    rows: (press) => host.keys.rows(press),
    showPlace: (id) => host.showPlace(id),
    enterPlace: (comment) => host.enterPlace(comment),
    whole: () => host.whole(),
    help: () => host.help?.(),
    lettersShown: () => host.settings.keyLetters(),
    rests: (event) => host.keys.rests(event),
  });
  const { searchQuery, openCommentSearch, closeCommentSearch, attachCommentSearch } = createCommentSearch({
    refresh: () => refreshComments(),
    tabComments: async () => (await readAll()).filter((comment) => tabOf(comment) === state.commentTab),
    page: host.search,
  });

  function tab() {
    return host.tabs[state.commentTab];
  }

  function tabOf(comment) {
    return host.tabOf(comment) ?? Object.keys(host.tabs)[0];
  }

  function statusOf(comment) {
    return host.status.of(comment);
  }

  // Every comment, whatever its status.
  async function readAll() {
    const answer = await api.list("all");
    if (!answer.ok) throw new Error(answer.error);
    return answer.data.comments;
  }

  // The list read again. A reading overtaken by a later one ends when that one does, so
  // whoever waits for it finds the list as the latest reading left it.
  function refreshComments() {
    const reading = readComments();
    commentsReading = reading;
    return reading;
  }

  // One reading of the list: the comments read for the view, with what the page reads beside
  // them, counted, listed for the shown tab in its order, and drawn. A list asked for again
  // while one is on its way, as when the view changes, is the one that stands: an answer that
  // comes back after a later one is dropped.
  async function readComments() {
    const asked = ++commentsAsked;
    // A search looks through every comment of the tab, and the list atop says so.
    const query = searchQuery();
    const searching = query.length > 0;
    showSearchView(searching);
    const status = searching ? "all" : $("#comment-filter").value;
    const [answer, along] = await Promise.all([api.list(LISTED.includes(status) ? status : "open"), host.along.read()]);
    if (!answer.ok) throw new Error(answer.error);
    if (asked !== commentsAsked) return commentsReading;
    host.along.apply(along);
    // What the page read may have shown another tab, which reads its own list.
    if (asked !== commentsAsked) return commentsReading;
    const read = answer.data.comments;
    countWaiting(read, status);
    const marked = read.filter((comment) => tabOf(comment) === state.commentTab && statusOf(comment) === "open");
    renderCommentTabs();
    const before = state.comments;
    state.comments = ordered(listed(read, status, query), status, searching);
    // A search most comments meet finds hundreds, which take a second to draw at every key: the
    // first ones show, with how many more there are.
    searchMore = searching ? Math.max(0, state.comments.length - SEARCH_SHOWN) : 0;
    if (searchMore > 0) state.comments = state.comments.slice(0, SEARCH_SHOWN);
    const handed = handFocus(before, `${state.commentTab} ${status}`);
    // A tab with no comment at all, in any view, says how to make one.
    let every = status === "all" ? read : null;
    if (state.comments.length === 0 && !searching && every === null) {
      const all = await api.list("all");
      if (asked !== commentsAsked) return commentsReading;
      every = all.ok ? all.data.comments : [];
    }
    tabEmpty = state.comments.length === 0 && !searching
      && !every.some((comment) => tabOf(comment) === state.commentTab) && !(tab().items().length > 0);
    renderComments();
    if (handed) keys.placeRing();
    host.marked(marked);
  }

  // Each tab counts what is unanswered in it, with what it lists beside its comments. A view of
  // closed comments holds no open one, and leaves the counts of what waits as they were.
  function countWaiting(read, status) {
    if (["resolved", "archived"].includes(status)) return;
    unansweredCounts = Object.fromEntries(Object.entries(host.tabs).map(([name, each]) =>
      [name, read.filter((comment) => tabOf(comment) === name && unanswered(comment)).length + each.items().length]));
    host.agent.awaited(read.some((comment) => comment.awaits_agent));
  }

  // What the view lists of the shown tab's comments read: what the tab's own views list, the
  // unanswered ones in that view, and in a search those that meet it.
  function listed(read, status, query) {
    const own = read.filter((comment) => tabOf(comment) === state.commentTab);
    const comments = tab().list ? tab().list(status, own, query.length > 0)
      : status === "unanswered" ? own.filter(unanswered) : own;
    return query.length === 0 ? comments : comments.filter((comment) => meetsSearch(comment, query, host.search));
  }

  // The order the list shows its comments in. In the sort list's first order, the open and
  // unanswered views, the views a tab adds and a search keep the order the tab lists them in,
  // each group in turn: by their places where the page gives them, else the server's. The
  // resolved view leads with the comment closed last, the one the reader has just closed and
  // looks for. The mixed view leads with the one written last, by when it was written and not
  // by when it was last touched: closing a comment touches it, so by that measure every closed
  // comment stood above every open one. The archive view leads with the thread touched last:
  // what was kept to be read again is read again when something is added to it. Latest first
  // and oldest first order every view, and a search, by when each thread was last written in,
  // which j, k, v and Shift+click then follow as they follow the list, each group in the order
  // chosen.
  function ordered(comments, status, searching) {
    const sort = commentSort();
    const group = (comment) => tab().group(status, comment);
    if (sort === "latest" || sort === "oldest") {
      return [...comments].sort((first, second) => group(first) - group(second)
        || ((lastWritten(first) - lastWritten(second)) || 0) * (sort === "latest" ? -1 : 1));
    }
    if (!searching && ["resolved", "archived", "all"].includes(status)) {
      const moment = status === "resolved" ? (comment) => comment.resolved ?? comment.created
        : status === "archived" ? (comment) => comment.updated
        : (comment) => comment.created;
      return [...comments].sort((first, second) => (moment(first) < moment(second) ? 1
        : moment(first) > moment(second) ? -1 : 0));
    }
    return [...comments].sort((first, second) => group(first) - group(second)
      || byPlace(host.place(first), host.place(second)));
  }

  // A focused comment that leaves the view, as one resolved in the open view or one whose reply
  // is sent in the unanswered view does, hands the focus to the comment now in its place, ringed
  // where the keys keep the ring, so the keys go on from there as after j. Whether it did.
  function handFocus(before, view) {
    const at = before.findIndex((comment) => comment.id === state.focusedCommentId);
    const handed = view === commentsView && at !== -1 && state.comments.length > 0
        && !state.comments.some((comment) => comment.id === state.focusedCommentId);
    if (handed) state.focusedCommentId = state.comments[Math.min(at, state.comments.length - 1)].id;
    commentsView = view;
    return handed;
  }

  // Each tab's number is how many of its comments are unanswered, with what it lists beside them.
  function renderCommentTabs() {
    for (const [name, each] of Object.entries(host.tabs)) {
      if (each.count === null) continue;
      const badge = $(each.count);
      const count = unansweredCounts[name] ?? 0;
      badge.textContent = String(count);
      badge.classList.toggle("hidden", count === 0);
    }
    renderCommentFilter();
  }

  // Whether the last message of an open thread is not the reviewer's: the agent wrote the last
  // entry, or as the page judges a thread of its own (awaitsReviewer). A comment the agent has
  // not answered yet is only open, and one listed under another status is not unanswered.
  function unanswered(comment) {
    return statusOf(comment) === "open" && host.awaitsReviewer(comment, comment.thread.at(-1)?.author === "agent");
  }

  // A view's name, which may change as what it lists does.
  function viewName(name) {
    return typeof name === "function" ? name() : name;
  }

  // The row above the list holds what the shown tab can do, from the moment the tab is chosen.
  // The list holds the tab's views and no others, since Safari still lists a hidden option.
  function renderCommentFilter() {
    const filter = $("#comment-filter");
    const { views } = tab();
    if ([...filter.options].map((option) => option.value).join(" ") !== views.map(([value]) => value).join(" ")) {
      const chosen = filter.value;
      filter.replaceChildren(...views.map(([value, name]) => h("option", { value, text: viewName(name) })));
      setView(views.some(([value]) => value === chosen) ? chosen : views[0][0]);
    }
    // A view whose name changes, as one that says how many it lists, is named afresh.
    for (const [value, name] of views) {
      if (typeof name !== "function") continue;
      [...filter.options].find((option) => option.value === value).textContent = name();
      if (filter.value === value) showViewName();
    }
    showCommentSort();
    $("#archive-picked-btn").hidden = !tab().archives;
  }

  function renderComments() {
    const list = $("#comments-list");
    const scrollTop = list.scrollTop;
    const activeInput = document.activeElement?.classList.contains("cmt-form-input")
      ? document.activeElement
      : null;
    const inputState = activeInput ? {
      commentId: activeInput.closest(".cmt")?.dataset.commentId,
      box: boxOf(activeInput),
      selectionStart: activeInput.selectionStart,
      selectionEnd: activeInput.selectionEnd,
      selectionDirection: activeInput.selectionDirection,
      scrollTop: activeInput.scrollTop,
    } : null;
    clear(list);
    if (state.comments.length === 0) {
      const view = $("#comment-filter").value;
      list.appendChild(placeholder(searchQuery().length > 0 ? "No comments match your search."
        : tab().empty[view] ?? "No comments in this view."));
      if (tabEmpty) list.appendChild(placeholder(tab().hint));
    } else {
      for (const comment of state.comments) {
        list.appendChild(tab().itemCard(comment) ?? renderCommentItem(comment));
      }
      if (searchMore > 0) {
        list.appendChild(placeholder(`${searchMore} more match. Add words to narrow the search.`));
      }
    }
    list.scrollTop = scrollTop;
    if (inputState?.commentId) {
      const commentNode = Array.from(list.children).find(
        (node) => node.dataset.commentId === inputState.commentId,
      );
      const input = commentNode?.querySelector(inputState.box);
      if (input) {
        input.focus({ preventScroll: true });
        input.setSelectionRange(
          inputState.selectionStart,
          inputState.selectionEnd,
          inputState.selectionDirection,
        );
        input.scrollTop = inputState.scrollTop;
      }
    }
    renderPickedActions();
    renderFoldAction();
    keys.mark();
  }

  // What the picked comments can be sent to, which is decided by the state they are in:
  // open ones close, unarchived ones archive, and closed ones reopen. The counts say
  // exactly what each press will touch.
  function renderPickedActions() {
    const picked = state.comments.filter((comment) => state.picked.has(comment.id) && host.row.settles(comment));
    const open = picked.filter((comment) => comment.status === "open").map((comment) => comment.id);
    const archivable = !tab().archives ? []
      : picked.filter((comment) => comment.status !== "archived").map((comment) => comment.id);
    const closed = picked.filter((comment) => comment.status !== "open").map((comment) => comment.id);
    const noun = tab().noun;
    // The box is checked when every comment listed is picked, and half checked when some are.
    const shown = state.comments.length;
    const pickedShown = state.comments.filter((comment) => state.picked.has(comment.id)).length;
    const all = $("#pick-all-btn");
    all.disabled = shown === 0;
    const clearing = shown > 0 && pickedShown === shown;
    all.checked = clearing;
    all.indeterminate = pickedShown > 0 && !clearing;
    all.setAttribute("aria-label", clearing ? "Clear selection" : "Select all");
    all.title = clearing ? "Clear selection" : `Select all ${noun}s in this view`;
    // A button with nothing to do stays in place, greyed out: buttons that came and went
    // moved everything beside them. The count shows only when there is one.
    // One button resolves or reopens, as R does: it resolves the open ones while any picked
    // comment is open, and reopens the picked ones once none is. With none picked it offers
    // what the view's comments take: Reopen in the resolved and archived views.
    const reopening = open.length === 0
      && (closed.length > 0 || ["resolved", "archived"].includes($("#comment-filter").value));
    const word = reopening ? "Reopen" : "Resolve";
    const settled = reopening ? closed : open;
    const resolve = $("#resolve-picked-btn");
    resolve.disabled = settled.length === 0;
    resolve.querySelector(".icon-resolve").classList.toggle("hidden", reopening);
    resolve.querySelector(".icon-reopen").classList.toggle("hidden", !reopening);
    resolve.querySelector(".wide-label").textContent = word;
    resolve.querySelector(".count").textContent = settled.length > 0 ? String(settled.length) : "";
    resolve.setAttribute("aria-label", settled.length > 0 ? `${word} ${settled.length}` : word);
    resolve.title = `${word} the selected ${noun}s`;
    resolve.dataset.status = reopening ? "open" : "resolved";
    resolve.dataset.ids = settled.join(" ");
    const archive = $("#archive-picked-btn");
    archive.disabled = archivable.length === 0;
    archive.querySelector(".count").textContent = archivable.length > 0 ? String(archivable.length) : "";
    archive.setAttribute("aria-label", archivable.length > 0 ? `Archive ${archivable.length}` : "Archive");
    archive.dataset.ids = archivable.join(" ");
    resolve.hidden = host.row.render(picked)?.resolve === false;
    fitCommentRow();
  }

  // The row fitted to its width (ROW_STEPS). What it sent behind the dots before goes back
  // first, the last sent first, so each finds the place it left; one already there stays put,
  // keeping the focus.
  function fitCommentRow() {
    const row = $(".filter-row");
    for (const { element, parent, next } of moved.reverse()) {
      if (element.parentNode !== parent || element.nextSibling !== next) {
        parent.insertBefore(element, next?.parentNode === parent ? next : null);
      }
    }
    moved = [];
    const steps = ROW_STEPS.flatMap((step) => [step, ...(host.row.steps[step[0]] ?? [])]);
    row.classList.remove(...steps.map(([step]) => step));
    for (const [step, behind] of steps) {
      if (row.scrollWidth <= row.clientWidth) break;
      row.classList.add(step);
      if (!behind) continue;
      for (const element of document.querySelectorAll(behind)) {
        moved.push({ element, parent: element.parentNode, next: element.nextSibling });
        $(".comment-rare-group").append(element);
      }
    }
    if (!row.classList.contains("narrow")) closeCommentRare();
  }

  function closeCommentRare() {
    $("#comment-rare").classList.remove("open");
    $("#comment-rare-btn").setAttribute("aria-expanded", "false");
  }

  // Every card in the view opened, or every one closed: which of the two the button does
  // is read off the cards, so it always offers the one that changes something.
  function renderFoldAction() {
    const shown = state.comments.map((comment) => comment.id);
    const button = $("#fold-all-btn");
    button.disabled = shown.length === 0;
    const folding = shown.length > 0 && shown.every((id) => state.expanded.has(id));
    button.querySelector(".icon-expand").classList.toggle("hidden", folding);
    button.querySelector(".icon-collapse").classList.toggle("hidden", !folding);
    button.setAttribute("aria-label", folding ? "Collapse all" : "Expand all");
    button.title = `${folding ? "Collapse" : "Expand"} all ${tab().noun}s in this view`;
  }

  function foldAll() {
    foldEvery(!state.comments.every((comment) => state.expanded.has(comment.id)));
  }

  function foldEvery(open) {
    for (const comment of state.comments) {
      if (open) state.expanded.add(comment.id);
      else state.expanded.delete(comment.id);
    }
    renderComments();
  }

  function pickAll() {
    const shown = state.comments.map((comment) => comment.id);
    const already = shown.every((id) => state.picked.has(id));
    for (const id of shown) {
      if (already) state.picked.delete(id);
      else state.picked.add(id);
    }
    renderComments();
  }

  // Comments sent to *status* in one call, the call the row, the cards, the keys and Undo all make.
  // It gives the ids that took it: all of them, or after a refusal those the server now has at
  // that status, since a server that passes the change on elsewhere may take some and stop at
  // the first it is refused. *why* is the refusal, or "". *resolved* gives comments resolved
  // again the moments they were resolved at before, by id, as Undo does.
  async function postStatus(ids, status, resolved = {}) {
    const answer = await api.setStatus(ids, status, resolved);
    if (answer.ok) return { took: ids, why: "" };
    const now = await api.list("all");
    if (!now.ok) throw new Error(answer.error);
    return { took: now.data.comments.filter((comment) => ids.includes(comment.id) && statusOf(comment) === status)
      .map((comment) => comment.id), why: answer.error };
  }

  // A change of status made on the page, from the row, a card or a key, and the list drawn again
  // once. The comments it changed are kept for Undo, each with the status it had in the list
  // just before and, if resolved, when, and the line above the list says what was done. A change
  // that changed nothing leaves the line as it was. The picked comments are let go once every
  // one has changed.
  async function setStatus(ids, status) {
    const changing = state.comments.filter((comment) => ids.includes(comment.id) && statusOf(comment) !== status);
    const before = new Map(changing.map((comment) => [comment.id, statusOf(comment)]));
    const resolved = Object.fromEntries(changing.filter((comment) => comment.status === "resolved" && comment.resolved)
      .map((comment) => [comment.id, comment.resolved]));
    const noun = tab().noun;
    const from = resets;
    try {
      const { took, why } = await postStatus(ids, status);
      if (from !== resets) return;
      const changed = took.filter((id) => before.has(id));
      if (changed.length > 0) {
        const what = counted(changed.length, noun);
        const said = host.status.said(status, changed.map((id) => before.get(id)), what) ?? `${STATUS_DONE[status]} ${what}.`;
        undo = { said, noun, why: "", gone: 0, before: new Map(changed.map((id) => [id, before.get(id)])), resolved };
      }
      if (!why) for (const id of ids) state.picked.delete(id);
      renderUndo();
      await refreshComments();
      if (why) throw new Error(why);
    } catch (error) {
      alert(`Could not save: ${error.message}`);
    }
  }

  // Undo puts each comment of the last change back to the status it had, by the same call, so a
  // thread kept elsewhere too goes back there as well, and one resolved gets back the moment it
  // was resolved at. It only restores, so it asks nothing. A comment deleted since cannot be put
  // back, and the line says how many. Those the server refuses stay on the line, which says how
  // many and why, and Undo tries them again.
  async function undoStatus() {
    const last = undo;
    if (last === null || last.before.size === 0) return;
    undo = null;
    renderUndo();
    const left = new Map(last.before);
    let why = "";
    let gone = 0;
    const from = resets;
    try {
      const answer = await api.list("all");
      if (from !== resets) return;
      if (answer.ok) {
        const present = new Set(answer.data.comments.map((comment) => comment.id));
        for (const id of [...left.keys()].filter((id) => !present.has(id))) {
          left.delete(id);
          gone += 1;
        }
      }
    } catch { /* the calls below say why they fail */ }
    for (const status of new Set(left.values())) {
      if (from !== resets) return;
      const ids = [...left.keys()].filter((id) => left.get(id) === status);
      const resolved = status === "resolved"
        ? Object.fromEntries(ids.filter((id) => last.resolved[id]).map((id) => [id, last.resolved[id]])) : {};
      try {
        const answer = await postStatus(ids, status, resolved);
        for (const id of answer.took) left.delete(id);
        why ||= answer.why;
      } catch (error) {
        why ||= error.message;
      }
    }
    if (from !== resets) return;
    // A change made meanwhile has the line.
    if ((left.size > 0 || gone > 0) && undo === null) undo = { ...last, before: left, why, gone };
    renderUndo();
    await refreshComments();
  }

  // The line above the list after a change of status: what was done, or how many Undo could not
  // put back and why, with Undo and a close button. It stays through views and tabs until the next
  // change replaces it, and Undo, the close button or a reload takes it away.
  function renderUndo() {
    $("#undo-line").hidden = undo === null;
    if (undo === null) return;
    const gone = undo.gone ? `${counted(undo.gone, undo.noun)} could not be restored because `
      + `${undo.gone === 1 ? "it no longer exists" : "they no longer exist"}.` : "";
    $("#undo-text").textContent = undo.why
      ? `${counted(undo.before.size, undo.noun)} could not be undone: ${undo.why}${gone ? ` ${gone}` : ""}`
      : gone || undo.said;
    // With nothing left to put back, the line only says what could not be.
    $("#undo-btn").hidden = undo.before.size === 0;
  }

  // The box that picks a card, in its head, which opens the card: picking is not opening.
  function pickBox(comment) {
    const noun = tab().noun;
    const box = h("input", { class: "comment-pick", type: "checkbox",
      title: `Select this ${noun}. Shift+click selects or clears every ${noun} from the last one you clicked.` });
    box.checked = state.picked.has(comment.id);
    // A checkbox has already taken its new value by the time the click arrives, so the
    // whole decision is made here and nothing listens for the change.
    box.addEventListener("click", (event) => {
      event.stopPropagation();
      const shown = state.comments.map((item) => item.id);
      const from = shown.indexOf(pickedFrom);
      const to = shown.indexOf(comment.id);
      // Shift runs from the box the last pick started at to this one, both ends included,
      // and gives the whole run the value this box just took.
      const run = event.shiftKey && from !== -1 && to !== -1
        ? shown.slice(Math.min(from, to), Math.max(from, to) + 1)
        : [comment.id];
      for (const id of run) {
        if (box.checked) state.picked.add(id);
        else state.picked.delete(id);
      }
      pickedFrom = comment.id;
      renderComments();
    });
    return box;
  }

  // The words a comment is on, quoted on its card once opened, cut to a few lines.
  function renderQuote(comment) {
    const quote = host.quote(comment);
    return quote ? h("div", { class: "anchor-quote", text: quote.length > 180 ? `${quote.slice(0, 177)}…` : quote }) : null;
  }

  function renderCommentItem(comment) {
    const expanded = state.expanded.has(comment.id);
    const stale = host.stale(comment);
    const replies = comment.thread.length - 1;
    const box = pickBox(comment);
    const toggle = (event) => {
      event.stopPropagation();
      if (expanded) state.expanded.delete(comment.id);
      else state.expanded.add(comment.id);
      renderComments();
      host.showPlace(comment.id);
    };
    const head = h("div", {
      class: "cmt-head",
      title: expanded ? "Collapse thread" : "Expand thread",
      onclick: toggle,
    },
    box,
    h("span", {
      class: "cmt-toggle",
      text: expanded ? "▾" : replies > 0 ? `▸ ${replies} repl${replies > 1 ? "ies" : "y"}` : "▸",
    }),
    h("span", { class: "cmt-id", text: comment.id }),
    h("span", { class: "cmt-status", text: `[${statusOf(comment)}]` }),
    // Outside the unanswered view, an unanswered card says so, so it stands out in the others.
    unanswered(comment) && $("#comment-filter").value !== "unanswered"
      ? h("span", { class: "cmt-unanswered", text: "unanswered", title: "Waiting for your response" }) : null,
    stale ? h("span", { class: "stale", text: "STALE", title: "The text this comment is on has changed or can no longer be found" }) : null,
    !stale && comment.reanchored
      ? h("span", { class: "reanchored", text: "EDITED", title: "The commented text was edited; the highlight follows the edited text" })
      : null,
    h("span", { class: "cmt-anchor", text: host.label(comment.anchor) }));

    const children = [head, expanded ? renderQuote(comment) : null, host.card.note(comment, expanded)];
    if (!expanded && comment.thread.length > 0) {
      // A thread whose first entry carries its writer's name names who opened it, since it may
      // be anyone.
      const first = comment.thread[0];
      children.push(h("div", { class: "cmt-preview",
                               text: first.name !== undefined ? `${first.name}  ${first.text}` : first.text }));
      // The request, then whatever was last said about it: a thread someone has answered
      // must not read like one nobody has touched, and who answered has to be visible.
      // A long answer is cut to three lines here and opens in full on a click, so nothing
      // it says is only half shown with no way to reach the rest.
      const latest = comment.thread[comment.thread.length - 1];
      if (comment.thread.length > 1) {
        children.push(h("div", { class: `cmt-latest author-${latest.author}`,
                                 title: "Expand thread",
                                 onclick: toggle,
                                 text: `${writer(latest)}  ${latest.text}` }));
      }
    }
    if (expanded) {
      // The thread reads downward, oldest first, the way it was written.
      children.push(
        h("div", { class: "cmt-thread" },
          ...comment.thread.map((entry, index) => renderThreadEntry(entry, comment, index))));
    }
    // The proposal sits under the conversation, where the card ends. A thread carries one,
    // so this is never a list. So does a reply box the page adds.
    if (comment.suggestion) children.push(renderSuggestion(comment));
    children.push(host.card.reply(comment, expanded));
    if (expanded) {
      children.push(h("div", { class: "cmt-actions" }, ...actionButtons(comment)));
      children.push(renderReplyForm(comment));
    }
    const focused = state.focusedCommentId === comment.id;
    const ranged = keys.rangeIds().includes(comment.id);
    return h("div", {
      class: `cmt status-${statusOf(comment)}${focused ? " is-focused" : ""}${ranged ? " in-range" : ""}`,
      data: { commentId: comment.id },
    }, ...children);
  }

  function renderSuggestion(comment) {
    const changes = comment.suggestion?.changes ?? [];
    // The proposal names its own file, so it applies whatever the comment points at.
    let applyButton = null;
    let rejectButton = null;
    if (changes.length > 0 && comment.status === "open") {
      // Rejected, the proposal leaves the card and the thread says what was turned down,
      // which the agent hears once. Why is said in a reply.
      rejectButton = actionButton("sugg-reject", "Reject", async () => {
        rejectButton.disabled = true;
        if (!await mutate(comment.id, "reject-suggestion", { updated: comment.updated })) rejectButton.disabled = false;
      });
      applyButton = actionButton("sugg-apply", "Apply suggestion", async () => {
        applyButton.disabled = true;
        applyButton.textContent = "Applying…";
        // One the file no longer fits says so on the card, under the proposal.
        const refused = (why) => {
          unfitSuggestions.set(comment.id, { suggestion: JSON.stringify(comment.suggestion), why });
          renderComments();
        };
        if (await mutate(comment.id, "apply-suggestion", { updated: comment.updated }, { refused })) return;
        applyButton.disabled = false;
        applyButton.textContent = "Apply suggestion";
      });
    }
    // A refusal stays with the suggestion it was for: a new one from the agent is tried afresh.
    const unfit = unfitSuggestions.get(comment.id);
    // One pair per piece the proposal changes; the text it leaves alone is not shown,
    // because it is not part of the proposal.
    return h("div", { class: "cmt-suggestion" },
      ...changes.flatMap((change) => [
        h("div", { class: "sugg-old", title: "current text" },
          h("span", { class: "sugg-marker", text: "−" }),
          h("span", { class: "sugg-text", text: change.old })),
        h("div", { class: "sugg-new", title: "suggested replacement" },
          h("span", { class: "sugg-marker", text: "+" }),
          h("span", { class: "sugg-text", text: change.new })),
      ]),
      unfit?.suggestion === JSON.stringify(comment.suggestion)
        ? h("p", { class: "sugg-unfit", role: "status", text: "The file has changed since this was suggested, so it no longer applies. "
          + `Reject it, or reply to ask the agent for a new one. (${unfit.why})` })
        : null,
      applyButton ? h("div", { class: "sugg-actions" }, rejectButton, applyButton) : null,
    );
  }

  // What the reviewer wrote and may change: not the records of applying and rejecting, and of
  // the rest what the page says they may.
  function ownEntry(entry, comment) {
    if (entry.author !== "human" || entry.applied || entry.rejected) return false;
    return host.card.mayChange(entry, comment);
  }

  function renderThreadEntry(entry, comment, index) {
    const commentId = comment.id;
    const editing = editingEntry?.commentId === commentId && editingEntry.index === index;
    const children = [
      h("div", { class: "thread-meta" },
        h("span", { text: `${writer(entry)} · ${shortTime(entry.at)}` }),
        host.card.entryMark(entry),
        // What the reviewer wrote carries Edit and Delete. A thread's first message goes with
        // its thread. The records of applying and rejecting stay.
        !editing && ownEntry(entry, comment)
          ? h("span", { class: "entry-actions" },
            actionButton("cmt-edit", "Edit", () => startEntryEdit(commentId, index, entry.text)),
            actionButton("entry-delete", "Delete", async () => {
              const asked = host.card.deleteQuestion(entry, index,
                index === 0 ? `Permanently delete ${commentId}?` : "Delete this message?");
              if (confirm(asked)) await mutate(commentId, "delete-entry", { index });
            }))
          : null),
    ];
    if (editing) children.push(renderEntryEditor(commentId, index));
    else if (entry.text) children.push(h("div", { class: "thread-text", text: entry.text }));
    if (entry.edits?.length) {
      children.push(h("div", { class: "thread-edits" },
        ...entry.edits.map((edit) => h("span", { class: "edit", text: edit }))));
    }
    return h("div", { class: `thread-entry author-${entry.author}` }, ...children);
  }

  function startEntryEdit(commentId, index, text) {
    editingEntry = { commentId, index, draft: text };
    renderComments();
    const node = Array.from($("#comments-list").children).find(
      (child) => child.dataset.commentId === commentId,
    );
    node?.querySelector(".entry-edit-input")?.focus({ preventScroll: true });
  }

  // Editing rewrites the entry in place: the author and time stay, so a typo fix
  // does not read as a new message in the thread.
  function renderEntryEditor(commentId, index) {
    const editing = editingEntry;
    const textarea = h("textarea", { class: "cmt-form-input entry-edit-input", rows: 3 });
    textarea.value = editing.draft;
    textarea.addEventListener("input", () => {
      if (editingEntry === editing) editing.draft = textarea.value;
    });
    const save = async () => {
      const text = textarea.value.trim();
      if (!text) return;
      textarea.disabled = true;
      if (await mutate(commentId, "edit", { index, text }, { saved: () => { editingEntry = null; } })) return;
      textarea.disabled = false;
      textarea.focus();
    };
    const cancel = () => {
      editingEntry = null;
      renderComments();
    };
    host.settings.sendOn(textarea, save);
    textarea.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      cancel();
      $("#comments-list").focus({ preventScroll: true });
    });
    return h("div", { class: "cmt-form mode-edit" }, textarea,
      h("div", { class: "cmt-form-actions" },
        actionButton("cmt-form-cancel", "Cancel", cancel),
        actionButton("cmt-form-submit", "Save", save)));
  }

  function actionButton(className, label, onclick) {
    const hint = keys.hint(className);
    return h("button", { class: className, type: "button", text: label, onclick, title: hint ? `${label}${hint}` : null });
  }

  // The button that opens a card's reply box.
  function replyButton(comment) {
    return actionButton("cmt-reply", host.labels.reply, () => openReply(comment.id));
  }

  function actionButtons(comment) {
    const deleteButton = actionButton("cmt-delete", "Delete", () => {
      if (confirm(`Permanently delete ${comment.id}?`)) mutate(comment.id, "delete", null);
    });
    // Every status flip is one click, made as the row's buttons make it: the thread already holds
    // what was said, so the status flips without adding an entry. An archived thread still
    // takes replies, and goes back to open or resolved from the same row.
    const buttons = [];
    if (comment.status !== "resolved") buttons.push(replyButton(comment));
    if (comment.status !== "open") {
      buttons.push(actionButton("cmt-reopen", "Reopen", () => setStatus([comment.id], "open")));
    }
    if (comment.status !== "resolved") {
      buttons.push(actionButton("cmt-resolve", "Resolve", () => closeComment(comment.id)));
    }
    if (comment.status !== "archived") {
      buttons.push(actionButton("cmt-archive", "Archive", () => setStatus([comment.id], "archived")));
    }
    buttons.push(deleteButton);
    return host.card.actions(comment, buttons);
  }

  // A card can hold several boxes, one the page adds above the one being written in a form
  // among them, so a box is found again by which it is, never as the card's first: one in a
  // form by the form's mode, any other by its own class.
  function boxOf(input) {
    const mode = [...(input.closest(".cmt-form")?.classList ?? [])].find((name) => name.startsWith("mode-"));
    return mode ? `.cmt-form.${mode} .cmt-form-input` : `.${[...input.classList].find((name) => name !== "cmt-form-input")}`;
  }

  // The reply box opened on a comment's card, which takes the keys.
  function openReply(commentId) {
    activeForm = { commentId, draft: "" };
    state.expanded.add(commentId);
    renderComments();
    const commentNode = Array.from($("#comments-list").children).find(
      (node) => node.dataset.commentId === commentId,
    );
    const input = commentNode?.querySelector(".cmt-form.mode-reply .cmt-form-input");
    if (!input) return;
    input.closest(".cmt-form").scrollIntoView({ block: "nearest", behavior: "smooth" });
    input.focus({ preventScroll: true });
  }

  // The reply box closed with what was typed in it. Whether one was open.
  function closeReply() {
    if (activeForm === null) return false;
    activeForm = null;
    renderComments();
    return true;
  }

  async function closeComment(commentId) {
    if (activeForm?.commentId === commentId) activeForm = null;
    await setStatus([commentId], "resolved");
  }

  function renderReplyForm(comment) {
    if (activeForm?.commentId !== comment.id) return null;
    const form = activeForm;
    const textarea = h("textarea", { class: "cmt-form-input", rows: 3, placeholder: host.labels.replyBox });
    textarea.value = form.draft;
    textarea.addEventListener("input", () => {
      if (activeForm === form) form.draft = textarea.value;
    });
    let submitting = false;
    const submit = async () => {
      const text = textarea.value.trim();
      if (!text || submitting) return;
      submitting = true;
      textarea.disabled = true;
      submitButton.disabled = true;
      submitButton.textContent = "Saving…";
      const saved = () => {
        activeForm = null;
        host.agent.posted();
      };
      if (await mutate(comment.id, "reply", { text }, { saved })) {
        // The box is gone with what was typed in it: the keys go back to the list.
        $("#comments-list").focus({ preventScroll: true });
        return;
      }
      submitting = false;
      textarea.disabled = false;
      submitButton.disabled = false;
      submitButton.textContent = "Post reply";
      textarea.focus();
    };
    host.settings.sendOn(textarea, submit);
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeReply();
        $("#comments-list").focus({ preventScroll: true });
      }
    });
    const submitButton = h("button", { class: "cmt-form-submit", type: "button", text: "Post reply",
                                       title: `Post reply (${host.settings.sendKeyName()})`, onclick: submit });
    return h("div", { class: "cmt-form mode-reply" },
      h("div", { class: "cmt-form-title", text: host.labels.replyBox }),
      textarea,
      h("div", { class: "cmt-form-actions" }, actionButton("cmt-form-cancel", "Cancel", closeReply), submitButton));
  }

  // A 409, the server refusing because something changed since, goes with the server's reason
  // to *refused* when it is given. Every other failure is said in an alert.
  async function doMutation(commentId, action, body, refused = null) {
    let answer;
    try {
      answer = await api.mutate(commentId, action, body);
    } catch (error) {
      alert(`Action failed: ${error.message}`);
      return false;
    }
    if (!answer.ok) {
      if (refused && answer.conflict) refused(answer.error);
      else alert(`Action failed: ${answer.error}`);
      return false;
    }
    return true;
  }

  // A change to one comment, and the list read again once the server took it: what every card
  // button that changes a comment does. `saved` is done once it took it, before the list is
  // read; `refused` is as doMutation's. A list that cannot be read again is said in an alert.
  // Whether the change was saved.
  async function mutate(commentId, action, body, { refused = null, saved = () => {} } = {}) {
    if (!await doMutation(commentId, action, body, refused)) return false;
    saved();
    try {
      await refreshComments();
    } catch (error) {
      alert(`Saved, but comments could not be refreshed: ${error.message}`);
    }
    return true;
  }

  // The order the shown tab's list is in: latest first, oldest first, or the list's first order.
  function commentSort() {
    const sort = commentSorts[state.commentTab];
    return sort === "latest" || sort === "oldest" ? sort : $("#comment-sort").options[0].value;
  }

  // The order shown in the list's words, and in its title for the icon it shrinks to.
  function showCommentSort() {
    const select = $("#comment-sort");
    select.value = commentSort();
    $("#comment-sort-name").textContent = select.selectedOptions[0].textContent;
    select.title = `Sort by ${select.selectedOptions[0].textContent}${keys.hint("comment-sort")}`;
  }

  // The order chosen for the shown tab, which this browser keeps for it.
  function setCommentSort(sort) {
    commentSorts[state.commentTab] = sort;
    try { localStorage.setItem(`${host.prefix}CommentSort-${state.commentTab}`, sort); } catch { /* the choice lasts this visit */ }
    showCommentSort();
  }

  function cycleSort() {
    const select = $("#comment-sort");
    select.value = select.options[(select.selectedIndex + 1) % select.options.length].value;
    select.dispatchEvent(new Event("change"));
  }

  // The view the list shows, which the next reading lists. A view chosen ends what a search
  // showed in the list.
  function setView(value) {
    const filter = $("#comment-filter");
    filter.value = value;
    filter.classList.remove("searching");
    showViewName();
  }

  // The view's name, in the list and in its title, which names it while the list is its icon.
  function showViewName() {
    const filter = $("#comment-filter");
    $("#comment-filter-name").textContent = filter.selectedOptions[0].textContent;
    filter.title = filter.classList.contains("searching")
      ? `The search covers all ${tab().noun}s. Choose a view to clear it.`
      : `Showing ${filter.selectedOptions[0].textContent}`;
  }

  // The view the reader chose, which the list reads again once a search is cleared.
  function chosenView() {
    const filter = $("#comment-filter");
    return filter.classList.contains("searching") ? viewBeforeSearch : filter.value;
  }

  // While words are in the search box the list covers every status, so the view list reads
  // "all", greyed, and goes back to the view chosen before once the search is cleared.
  function showSearchView(searching) {
    const filter = $("#comment-filter");
    if (searching === filter.classList.contains("searching")) return;
    if (searching) {
      viewBeforeSearch = filter.value;
      filter.value = "all";
    } else {
      filter.value = viewBeforeSearch;
    }
    filter.classList.toggle("searching", searching);
    showViewName();
  }

  // A comment tab shown, as the page's tabs show it: the list shows its comments, on its open
  // view. A tab chosen by a click gives the keys to the list, since Safari focuses no button it
  // clicks. Any other tab is the page's own.
  function showTab(name, focus = false) {
    if (!(name in host.tabs)) return;
    if (state.commentTab !== name) {
      state.commentTab = name;
      state.picked.clear();
      renderCommentFilter();
      setView("open");
      refreshComments().catch((error) => console.error(error));
    }
    if (focus) $("#comments-list").focus({ preventScroll: true });
  }

  // A comment shown from its mark on the page: opened and ringed in the list, in the open view of
  // the tab shown when the view leaves it out, as the unanswered view leaves out a comment the
  // agent has not answered yet, and brought into view (bringIntoView). One the open view does
  // not list either stays as the list has it.
  async function showComment(id) {
    if (!state.comments.some((comment) => comment.id === id)) {
      closeCommentSearch();
      setView("open");
      await refreshComments();
      if (!state.comments.some((comment) => comment.id === id)) return;
    }
    state.focusedCommentId = id;
    state.expanded.add(id);
    renderComments();
    const card = $(`#comments-list [data-comment-id="${CSS.escape(id)}"]`);
    if (card) host.bringIntoView(id, card);
  }

  // The dialog for a new comment on *anchor*, which *label* names, posted with *fields*, what the
  // page posts beside it. The panel sets its own words, and the page readies its own parts of the
  // dialog after. What a refused post left in the box comes back in it, and the first box shown
  // takes the keys.
  function openCompose(anchor, label, fields = {}) {
    pendingAnchor = anchor;
    composeFields = fields;
    const text = $("#compose-text");
    $("#compose-title").textContent = host.labels.composeTitle;
    text.placeholder = host.labels.composeBox;
    text.rows = 6;
    text.required = true;
    host.compose.opening(fields);
    $("#compose-anchor").textContent = label;
    $("#compose-submit").title = `Post (${host.settings.sendKeyName()})`;
    text.value = keptComment;
    keptComment = "";
    $("#compose-dialog").showModal();
    for (const box of $$("#compose-form textarea")) fitComposeBox(box);
    setTimeout(() => $$("#compose-form textarea").find((box) => !box.closest("[hidden]")).focus(), 50);
  }

  // A comment box as tall as what is typed in it, and never shorter than its rows: the
  // stylesheet caps the height, and past that the box scrolls.
  function fitComposeBox(box) {
    box.style.height = "";
    if (box.scrollHeight > box.clientHeight) {
      box.style.height = `${box.scrollHeight + box.offsetHeight - box.clientHeight}px`;
    }
  }

  function applyComposeSubmitting(submitting) {
    composeSubmitting = submitting;
    const dialog = $("#compose-dialog");
    dialog.setAttribute("aria-busy", String(submitting));
    for (const control of $$("#compose-form textarea, #compose-form button")) {
      control.disabled = submitting;
    }
    $("#compose-submit").textContent = submitting ? "Posting…" : "Post";
  }

  // A comment posts what its box holds, or what the page's own boxes add when that box is empty.
  // A refusal the page has words for says them, and may keep what was written for the next box.
  async function submitCompose(event) {
    event.preventDefault();
    if (composeSubmitting) return;
    const text = $("#compose-text").value.trim();
    const filled = host.compose.filled(composeFields);
    if (!(text || Object.keys(filled).length > 0) || !pendingAnchor) return;
    const body = { anchor: pendingAnchor, text, ...composeFields, ...filled };

    applyComposeSubmitting(true);
    let started;
    try {
      const answer = await api.create(body);
      if (!answer.ok) {
        const told = host.compose.refused(answer, body);
        if (told?.keep) {
          keptComment = $("#compose-text").value;
          $("#compose-dialog").close();
        }
        alert(told?.say ?? `Could not save comment: ${answer.error}`);
        return;
      }
      started = answer.data;
      $("#compose-dialog").close();
    } finally {
      applyComposeSubmitting(false);
    }
    // A note for the agent waits for it, and the line above the list may say how to call it.
    if (text) host.agent.posted();
    await host.compose.posted(started, body);
    await refreshComments();
  }

  // The panel's controls wired: the row, Undo, the dialog, the search, the views and the orders.
  function attach() {
    $("#fold-all-btn").addEventListener("click", foldAll);
    $("#pick-all-btn").addEventListener("click", pickAll);
    // The row is fitted again whenever it, or one of its groups, changes width.
    const row = $(".filter-row");
    const refit = new ResizeObserver(() => fitCommentRow());
    refit.observe(row);
    for (const group of row.children) refit.observe(group);
    $("#comment-rare-btn").addEventListener("click", (event) => {
      event.stopPropagation();
      const open = !$("#comment-rare").classList.contains("open");
      $("#comment-rare").classList.toggle("open", open);
      $("#comment-rare-btn").setAttribute("aria-expanded", String(open));
    });
    // A choice behind the dots closes them, and so does a click anywhere else.
    document.addEventListener("click", (event) => {
      if (!event.target.closest("#comment-rare") || event.target.closest(".comment-rare-group button")) closeCommentRare();
    });
    // The buttons carry the ids they were drawn with and the status they set, so a press acts on
    // what its label counted. Once done, each is drawn for what is picked then.
    for (const button of $$(".filter-row button[data-status]")) {
      button.addEventListener("click", async () => {
        const ids = button.dataset.ids ? button.dataset.ids.split(" ") : [];
        const status = button.dataset.status;
        if (ids.length === 0 || !host.row.confirm(button, ids, status)) return;
        button.disabled = true;
        await setStatus(ids, status);
        renderPickedActions();
      });
    }
    $("#undo-btn").title = `Undo the last status change${keys.hint("undo-go")}`;
    $("#comment-search").title = `Search the comments of this tab${keys.hint("comment-search")}`;
    $("#undo-btn").addEventListener("click", () => undoStatus().catch((error) => console.error(error)));
    $("#undo-close").addEventListener("click", () => {
      undo = null;
      renderUndo();
    });
    $("#search-help-btn")?.addEventListener("click", () => host.help("search"));
    $("#compose-form").addEventListener("submit", (event) => {
      submitCompose(event).catch((error) => alert(error.message));
    });
    for (const box of $$("#compose-form textarea")) {
      host.settings.sendOn(box, () => $("#compose-form").requestSubmit());
      box.addEventListener("input", () => fitComposeBox(box));
    }
    // However the dialog closes, by Post, Cancel, Escape or Ctrl+[, what it was opened on goes,
    // and the page lets go of it too. The close event comes after the dialog has closed, by which
    // time it may be open again, on what it is opened on now, which stays.
    $("#compose-dialog").addEventListener("close", () => {
      if ($("#compose-dialog").open) return;
      pendingAnchor = null;
      host.compose.closed();
    });
    $("#compose-cancel").addEventListener("click", (event) => {
      event.preventDefault();
      $("#compose-dialog").close();
    });
    for (const name of Object.keys(host.tabs)) {
      try { commentSorts[name] = localStorage.getItem(`${host.prefix}CommentSort-${name}`); } catch { /* the first order */ }
    }
    renderCommentFilter();
    attachCommentSearch();
    // A view chosen in the list puts the search away, since the list shows that view, and gives
    // the comment keys back to the comments: the list would keep them, and Safari focuses no
    // button it clicks. An order chosen does too, but keeps the search, which it orders as well,
    // and closes the dots it may wait behind.
    $("#comment-filter").addEventListener("change", () => {
      closeCommentSearch();
      setView($("#comment-filter").value);
      tab().chosen($("#comment-filter").value);
      $("#comments-list").focus({ preventScroll: true });
      refreshComments().catch((error) => console.error(error));
    });
    $("#comment-sort").addEventListener("change", () => {
      setCommentSort($("#comment-sort").value);
      closeCommentRare();
      $("#comments-list").focus({ preventScroll: true });
      refreshComments().catch((error) => console.error(error));
    });
  }

  // What the panel holds for the document shown let go, for a page that shows another in its
  // place: its comments, those opened and picked, the ring and a held range, a box being written
  // in or an entry being edited, and the change Undo would put back, which was made to the
  // other's comments. A change still on its way for that one stops when it comes back.
  function reset() {
    resets += 1;
    state.comments = [];
    state.expanded.clear();
    state.picked.clear();
    pickedFrom = null;
    state.focusedCommentId = null;
    activeForm = null;
    editingEntry = null;
    unfitSuggestions.clear();
    commentsView = null;
    undo = null;
    keys.reset();
    renderUndo();
  }

  const panel = {
    attach, keys, refreshComments, renderComments, renderPickedActions, fitCommentRow, foldAll, setStatus, undoStatus,
    pickBox, actionButton, replyButton, mutate, closeReply, unanswered, setView, chosenView, showTab,
    showComment, cycleSort, openCompose, openCommentSearch, reset,
  };
  return panel;
}
