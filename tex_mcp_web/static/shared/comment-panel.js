// The comment panel of the review pages: each tab's comments in the view and the order chosen,
// the search above them, the row that changes the picked ones, the line that undoes the last
// change, the cards with their threads, replies, edits and suggestions, and the dialog that
// writes a new comment. The panel draws itself into two places the page gives, #tab-comments
// and #compose-dialog, and the page gives the hooks createCommentPanel describes. Its look is
// comment-panel.css.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, $$, h, clear, placeholder } from "./dom.js";
import { meetsSearch, createCommentSearch } from "./comment-search.js";

function escaped(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

// The panel's markup. Its row has four groups, a thin line between each two: what to look at,
// picking, what acts on the picked comments, and fetching and calling. The buttons are icons,
// with the words as title and aria-label. Each button shows the one drawing that fits its
// state; a count rides beside the drawing where the action has one. A button with nothing to
// do is greyed out, not taken away: the row keeps one shape. Each <slot> is where the page's
// own parts go (drawPanel).
function paneMarkup(labels) {
  const [sortValue, sortName] = labels.firstSort.map(escaped);
  return `<div class="filter-row">
  <span class="row-group">
    <!-- The page fills the list with the shown tab's views. In a row short of room even
         for the shortest view's name it is its icon alone, with the list lying clear over it. -->
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
  <slot name="search"></slot>
  <div id="search-pick" class="search-pick" role="listbox" aria-label="Suggestions" hidden></div>
</div>
<slot name="notes"></slot>
<!-- Said after comments change status from the page, with Undo, until the next change. -->
<p id="undo-line" class="sync-why undo-line" role="status" hidden>
  <span id="undo-text"></span>
  <button id="undo-btn" class="sync-why-go undo-go" type="button">Undo</button>
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

const STATUS_DONE = { resolved: "Resolved", open: "Reopened", archived: "Archived", hidden: "Hid" };

// How the row gives way, one step at a time while it would spill over: the sort list shrinks
// to its icon, the words beside the other icons go, then Resolve's, the sort list waits behind
// the dots as the gaps narrow, then Expand all, and the view list gives up the room its longest
// view needs. Last the view list shrinks to its icon, with the list lying over it. Resolve and
// Archive stay in the row, which takes two lines only when even that is too wide. Each step
// names what then waits behind the dots. The page's own steps come in after the step they name.
const ROW_STEPS = [["sort-shrunk"], ["wordless"], ["resolve-wordless"], ["narrow", "#comment-sort-pick"],
  ["foldless", "#fold-all-btn"], ["squeezed"], ["view-shrunk"], ["wrapped"]];

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

// The status a comment is listed under: "hidden" for an open one the reviewer hid on the page,
// else its own.
export function statusOf(comment) {
  return comment.hidden && comment.status === "open" ? "hidden" : comment.status;
}

// The panel, kept on the page's `state`, which it adds its own fields to, and drawn at once into
// the page's #tab-comments and #compose-dialog, with the page's own parts that wait in them
// (drawPanel): its buttons for the picked comments, data-slot="picked", which follow Archive;
// what goes before the bell, "fetch"; what follows the search's conditions, "search"; its
// lines over the Undo line, "notes"; and its boxes over the comment's own, "compose". The page
// gives:
//
//   prefix       what the browser keeps the order of each tab's list under, before "CommentSort-"
//   api          the server's comment calls: list(status) with "open", "resolved", "archived"
//                or "all"; create(body); setStatus(ids, status, resolved), `resolved` giving
//                comments resolved again the moments they were resolved at, by id; and
//                mutate(id, action, body) for "reply", "edit", "delete-entry",
//                "apply-suggestion", "reject-suggestion" and "delete". Each answers
//                { ok: true, data } or { ok: false, error, reason, conflict }: the refusal in
//                words, the kind of reason the server names, and whether something changed since.
//   tabs         { name: tab } for the tabs that list comments, which share one pane, the first
//                shown first. A tab has `views`, [value, name] with the first the one it opens
//                on, a name given as a function when it changes; `noun`, what it calls one of
//                its comments; `count`, the selector of its count of what waits; and `hint`,
//                what it says while it has no comment at all. A tab may have `empty`, what a
//                view of its own says when it lists nothing, by view; `archives: false` when its
//                comments cannot be archived; `list(status, comments, searching)`, what each of
//                its views lists from its comments read; `items()`, what it lists beside its
//                comments, which waits for the reviewer too; and `group(status, comment)`, the
//                group a comment sorts in, lower first.
//   labels       `reply`, the reply button's word; `replyBox`, what the reply box says;
//                `firstSort`, [value, name] of the sort list's first order, which keeps the
//                order the tab lists its comments in; and `composeTitle` and `composeBox`, the
//                new comment dialog's heading and what its box says
//   sendOn, sendKeyName   the key that sends what is written, as Settings chooses it
//   keys         the comment keys: hint(button), the key that presses a card's button, or works
//                "comment-search" or "comment-sort", for its title; rangeIds(), the comments a
//                held range covers; mark(), to mark the ringed card's buttons with their keys;
//                placeRing(), to scroll the ringed card into place
//   tabOf(comment)              the tab that lists a comment
//   readAlong(), applyAlong(read)   what the page reads beside each list, applied once that
//                               reading is the latest
//   showTabs()                  shows the page's tabs as each reading leaves them
//   agentAwaited(awaited), posted()   whether some comment waits for the agent, and that a
//                               comment or a reply was posted
//   me(), searchWords(comment)  the reviewer's own name on entries that carry their writer's
//                               name, and the words a comment holds beside its own for a search
//   unanswered(comment, byAgent)   whether an open comment waits for the reviewer, given
//                               whether the agent wrote last
//   settles(comment)            whether the row's Resolve, Reopen and Archive act on a picked one
//   anchorLabel(anchor), showPlace(id), markPlaces()   a comment's place: named on its card,
//                               shown, and the shown tab's open comments marked where they are
//   stale(comment)              whether what a comment is on can no longer be found
//   suggestedCard(item)         the card of a suggested edit the list holds
//   cardNote(comment, expanded), cardReply(comment, expanded)   what the page adds under a
//                               card's head, and under its thread and suggestion; null for nothing
//   cardActions(comment, buttons)   the buttons an opened card shows, given the ones it would
//   entryMark(entry), mayChange(entry, comment), deleteQuestion(entry, index, question)
//                               a mark beside an entry, whether the reviewer may change an entry
//                               they wrote, and what deleting one asks, given the question
//   renderRow(picked), restoreRow(), rowSteps   the page's own buttons in the row: drawn for
//                               the picked comments, put back in their places before the row is
//                               fitted, and the steps that send them behind the dots, by the
//                               step they come after
//   confirmRow(button, ids, status)   whether a press of a row button goes on
//   viewChosen(view)            told of a view the reader chose
//   compose      the dialog's own parts: opening(fields), to ready them; filled(fields), what
//                they add to the comment; refused(answer, body), true when the page said why
//                itself; posted(comment, body), what follows a comment posted; closed()
export function createCommentPanel(host) {
  const { state, api, tabs, keys, compose, labels } = host;
  Object.assign(state, {
    // The comments the list shows, in its order, those opened, those picked, and the one the
    // ring is on.
    comments: [],
    expanded: new Set(),
    picked: new Set(),
    // The box a pick last started from, so shift can run from it to the next one.
    pickedFrom: null,
    focusedCommentId: null,
    // The reply box open on a card, and the entry being edited, each with what is typed in it.
    activeForm: null,
    editingEntry: null,
    // Which comment tab is shown. The list, and the page's marks, show that tab's comments.
    commentTab: Object.keys(tabs)[0],
    // What is typed in the comment search, which lists the tab's comments that meet it, and how
    // many it found beyond those drawn.
    commentSearch: "",
    searchMore: 0,
    // The order each comment tab's list is in, as its sort list was last set: the list's first
    // order, latest or oldest.
    commentSorts: {},
    // How many things wait for the reviewer's response in each comment tab, as the unanswered
    // view lists them, with what a tab lists beside its comments.
    unansweredCounts: {},
    // The open comments of the tab shown that are not hidden, whatever its view lists: the page
    // marks these where they are.
    markedComments: [],
    // The comment tab and status the list shows, so a list drawn again knows it is the same view.
    commentsView: null,
    // How many times the list was asked for, so only the latest answer is drawn, and the
    // reading under way.
    commentsAsked: 0,
    commentsReading: null,
    // The shown comment tab has no comment at all, in any view.
    tabEmpty: false,
    // The last change of status made on the page, which the line above the list offers to undo:
    // what the line says, the status each comment it changed had before and when one resolved
    // was resolved, why some could not be put back, and how many no longer exist. Null when
    // there is nothing to undo.
    undo: null,
    // The view the list showed before a search, which it shows again once the search is cleared.
    viewBeforeSearch: "open",
    // Why applying a thread's suggestion was refused, with the suggestion it was refused for.
    unfitSuggestions: new Map(),
    // What a new comment is on, and what the page posts with it.
    pendingAnchor: null,
    composeFields: {},
    // What was written in a new comment the server turned away for now, for the next comment
    // box opened.
    keptComment: "",
    composeSubmitting: false,
  });
  drawPanel($("#tab-comments"), paneMarkup(labels));
  drawPanel($("#compose-dialog"), composeMarkup(labels));
  // Counted up each time the panel lets go of what it held for one document (reset), so a
  // change still on its way for that one stops when it comes back.
  let resets = 0;
  const steps = ROW_STEPS.flatMap((step) => [step, ...(host.rowSteps[step[0]] ?? [])]);
  const FORM_CONFIG = {
    reply: { placeholder: labels.replyBox, key: "text", label: "Post reply" },
  };
  const { searchQuery, openCommentSearch, closeCommentSearch, attachCommentSearch } = createCommentSearch({
    state,
    refresh: () => refreshComments(),
    tabComments: async () => (await readAll()).filter((comment) => host.tabOf(comment) === state.commentTab),
    me: host.me,
  });

  function tab() {
    return tabs[state.commentTab];
  }

  // What the shown tab calls one of its entries.
  function tabNoun() {
    return tab().noun;
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
    state.commentsReading = reading;
    return reading;
  }

  async function readComments() {
    // A list asked for again while one is on its way, as when the view changes, is the one that
    // stands: an answer that comes back after a later one is dropped.
    const asked = ++state.commentsAsked;
    // A search looks through every comment of the tab, and the list atop says so.
    const query = searchQuery();
    showSearchView(query.length > 0);
    const status = query.length > 0 ? "all" : $("#comment-filter").value;
    // A view of its own, as the unanswered one, lists some of the open comments.
    const [answer, along] = await Promise.all([
      api.list(["resolved", "archived", "all"].includes(status) ? status : "open"), host.readAlong()]);
    if (!answer.ok) throw new Error(answer.error);
    if (asked !== state.commentsAsked) return state.commentsReading;
    host.applyAlong(along);
    const read = answer.data.comments;
    // The other tabs' comments are left out here, once, so the list and the page's marks agree
    // without each asking. Each tab counts what is unanswered in it.
    const inTab = (name) => read.filter((comment) => host.tabOf(comment) === name);
    // A view of closed comments holds no open one, and leaves the counts of what waits as they were.
    if (!["resolved", "archived"].includes(status)) {
      state.unansweredCounts = Object.fromEntries(Object.entries(tabs).map(([name, each]) =>
        [name, inTab(name).filter(unanswered).length + (each.items?.().length ?? 0)]));
      host.agentAwaited(read.some((comment) => comment.awaits_agent));
    }
    state.markedComments = inTab(state.commentTab).filter((comment) => statusOf(comment) === "open");
    renderCommentTabs();
    const shown = tab();
    const listed = inTab(state.commentTab);
    let comments = shown.list ? shown.list(status, listed, query.length > 0)
      : status === "unanswered" ? listed.filter(unanswered) : listed;
    if (query.length > 0) {
      comments = comments.filter((comment) => meetsSearch(comment, query, host.me(), host.searchWords(comment)));
    }
    // In the sort list's first order, the open and unanswered views, and the views a tab adds,
    // keep the order the tab lists them in, the server's, each group in turn. The resolved
    // view leads with the comment closed last, the one the reader has just closed and looks
    // for. The mixed view leads with the one written last, by when it was written and not by
    // when it was last touched: closing a comment touches it, so by that measure every closed
    // comment stood above every open one.
    // The archive view leads with the thread touched last: what was kept to be read
    // again is read again when something is added to it. A search keeps the server's order, as
    // the open view does.
    // Latest first and oldest first order every view, and a search, by when each thread was
    // last written in, which j, k, v and Shift+click then follow as they follow the list, each
    // group in the order chosen.
    const moment = status === "resolved"
      ? (comment) => comment.resolved ?? comment.created
      : status === "archived" ? (comment) => comment.updated
      : (comment) => comment.created;
    const before = state.comments;
    const sort = commentSort();
    const group = (comment) => shown.group?.(status, comment) ?? 0;
    state.comments = sort === "latest" || sort === "oldest"
      ? [...comments].sort((first, second) => group(first) - group(second)
        || ((lastWritten(first) - lastWritten(second)) || 0) * (sort === "latest" ? -1 : 1))
      : query.length === 0 && ["resolved", "archived", "all"].includes(status)
        ? [...comments].sort((first, second) => (moment(first) < moment(second) ? 1
          : moment(first) > moment(second) ? -1 : 0))
      : [...comments].sort((first, second) => group(first) - group(second));
    // A search most comments meet finds hundreds, which take a second to draw at every key: the
    // first ones show, with how many more there are.
    state.searchMore = 0;
    if (query.length > 0 && state.comments.length > SEARCH_SHOWN) {
      state.searchMore = state.comments.length - SEARCH_SHOWN;
      state.comments = state.comments.slice(0, SEARCH_SHOWN);
    }
    // A focused comment that leaves the view, as one resolved in the open view or one whose
    // reply is sent in the unanswered view does, hands the focus to the comment now in its place, ringed
    // where the keys keep the ring, so the keys go on from there as after j.
    const view = `${state.commentTab} ${status}`;
    const at = before.findIndex((comment) => comment.id === state.focusedCommentId);
    const handed = view === state.commentsView && at !== -1 && state.comments.length > 0
        && !state.comments.some((comment) => comment.id === state.focusedCommentId);
    if (handed) state.focusedCommentId = state.comments[Math.min(at, state.comments.length - 1)].id;
    state.commentsView = view;
    // A tab with no comment at all, in any view, says how to make one.
    let every = status === "all" ? read : null;
    if (state.comments.length === 0 && query.length === 0 && every === null) {
      const all = await api.list("all");
      if (asked !== state.commentsAsked) return state.commentsReading;
      every = all.ok ? all.data.comments : [];
    }
    state.tabEmpty = state.comments.length === 0 && query.length === 0
      && !every.some((comment) => host.tabOf(comment) === state.commentTab) && !(shown.items?.().length > 0);
    renderComments();
    if (handed) keys.placeRing();
    host.markPlaces();
  }

  // Each tab's number is how many of its comments are unanswered, with what it lists beside them.
  function renderCommentTabs() {
    host.showTabs();
    for (const [name, each] of Object.entries(tabs)) {
      const badge = $(each.count);
      const count = state.unansweredCounts[name] ?? 0;
      badge.textContent = String(count);
      badge.classList.toggle("hidden", count === 0);
    }
    renderCommentFilter();
  }

  // Whether the last message of an open thread is not the reviewer's: the agent wrote the last
  // entry, or as the page judges a thread of its own. A comment the agent has not answered yet
  // is only open, and a hidden one is not unanswered.
  function unanswered(comment) {
    return statusOf(comment) === "open" && host.unanswered(comment, comment.thread.at(-1)?.author === "agent");
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
      setCommentFilter(views.some(([value]) => value === chosen) ? chosen : views[0][0]);
    }
    // A view whose name changes, as one that says how many it lists, is named afresh.
    for (const [value, name] of views) {
      if (typeof name !== "function") continue;
      [...filter.options].find((option) => option.value === value).textContent = name();
      if (filter.value === value) showViewName();
    }
    showCommentSort();
    $("#archive-picked-btn").hidden = tab().archives === false;
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
        : tab().empty?.[view] ?? "No comments in this view."));
      if (state.tabEmpty) list.appendChild(placeholder(tab().hint));
    } else {
      for (const comment of state.comments) {
        list.appendChild(comment.suggested ? host.suggestedCard(comment) : renderCommentItem(comment));
      }
      if (state.searchMore > 0) {
        list.appendChild(placeholder(`${state.searchMore} more match. Add words to narrow the search.`));
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
    const picked = state.comments.filter((comment) => state.picked.has(comment.id) && host.settles(comment));
    const open = picked.filter((comment) => comment.status === "open").map((comment) => comment.id);
    const archivable = tab().archives === false ? []
      : picked.filter((comment) => comment.status !== "archived").map((comment) => comment.id);
    const closed = picked.filter((comment) => comment.status !== "open").map((comment) => comment.id);
    // The box is checked when every comment listed is picked, and half checked when some are.
    const shown = state.comments.length;
    const pickedShown = state.comments.filter((comment) => state.picked.has(comment.id)).length;
    const all = $("#pick-all-btn");
    all.disabled = shown === 0;
    const clearing = shown > 0 && pickedShown === shown;
    all.checked = clearing;
    all.indeterminate = pickedShown > 0 && !clearing;
    all.setAttribute("aria-label", clearing ? "Clear selection" : "Select all");
    all.title = clearing ? "Clear selection" : `Select all ${tabNoun()}s in this view`;
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
    resolve.title = `${word} the selected ${tabNoun()}s`;
    resolve.dataset.status = reopening ? "open" : "resolved";
    resolve.dataset.ids = settled.join(" ");
    const archive = $("#archive-picked-btn");
    archive.disabled = archivable.length === 0;
    archive.querySelector(".count").textContent = archivable.length > 0 ? String(archivable.length) : "";
    archive.setAttribute("aria-label", archivable.length > 0 ? `Archive ${archivable.length}` : "Archive");
    archive.dataset.ids = archivable.join(" ");
    host.renderRow(picked);
    fitCommentRow();
  }

  function fitCommentRow() {
    const row = $(".filter-row");
    // Each button back in its place in the row. One already there stays put, keeping the focus.
    const [sort, fold] = [$("#comment-sort-pick"), $("#fold-all-btn")];
    host.restoreRow();
    if (fold.nextElementSibling !== $("#comment-rare")) $("#comment-rare").before(fold);
    if (sort.nextElementSibling !== fold) fold.before(sort);
    row.classList.remove(...steps.map(([step]) => step));
    for (const [step, behind] of steps) {
      if (row.scrollWidth <= row.clientWidth) break;
      row.classList.add(step);
      if (behind) $(".comment-rare-group").append(...document.querySelectorAll(behind));
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
    button.title = `${folding ? "Collapse" : "Expand"} all ${tabNoun()}s in this view`;
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
    const noun = tabNoun();
    const from = resets;
    try {
      const { took, why } = await postStatus(ids, status);
      if (from !== resets) return;
      const changed = took.filter((id) => before.has(id));
      if (changed.length > 0) {
        // Hidden threads opened are shown again, not reopened.
        const said = status === "open" && changed.every((id) => before.get(id) === "hidden")
          ? `Showed ${counted(changed.length, noun)} again.` : `${STATUS_DONE[status]} ${counted(changed.length, noun)}.`;
        state.undo = { said, noun, why: "", gone: 0,
                       before: new Map(changed.map((id) => [id, before.get(id)])), resolved };
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
    const undo = state.undo;
    if (undo === null || undo.before.size === 0) return;
    state.undo = null;
    renderUndo();
    const left = new Map(undo.before);
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
        ? Object.fromEntries(ids.filter((id) => undo.resolved[id]).map((id) => [id, undo.resolved[id]])) : {};
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
    if ((left.size > 0 || gone > 0) && state.undo === null) state.undo = { ...undo, before: left, why, gone };
    renderUndo();
    await refreshComments();
  }

  // The line above the list after a change of status: what was done, or how many Undo could not
  // put back and why, with Undo and a close button. It stays through views and tabs until the next
  // change replaces it, and Undo, the close button or a reload takes it away.
  function renderUndo() {
    const undo = state.undo;
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
    const noun = tabNoun();
    const box = h("input", { class: "comment-pick", type: "checkbox",
      title: `Select this ${noun}. Shift+click selects or clears every ${noun} from the last one you clicked.` });
    box.checked = state.picked.has(comment.id);
    // A checkbox has already taken its new value by the time the click arrives, so the
    // whole decision is made here and nothing listens for the change.
    box.addEventListener("click", (event) => {
      event.stopPropagation();
      const shown = state.comments.map((item) => item.id);
      const from = shown.indexOf(state.pickedFrom);
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
      state.pickedFrom = comment.id;
      renderComments();
      renderPickedActions();
    });
    return box;
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
    h("span", { class: "cmt-anchor", text: host.anchorLabel(comment.anchor) }));

    const children = [head, host.cardNote(comment, expanded)];
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
    children.push(host.cardReply(comment, expanded));
    if (expanded) {
      children.push(h("div", { class: "cmt-actions" }, ...actionButtons(comment)));
      const form = renderActiveForm(comment);
      if (form) children.push(form);
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
        if (await doMutation(comment.id, "reject-suggestion", { updated: comment.updated })) {
          await refreshComments();
          return;
        }
        rejectButton.disabled = false;
      });
      applyButton = actionButton("sugg-apply", "Apply suggestion", async () => {
        applyButton.disabled = true;
        applyButton.textContent = "Applying…";
        // One the file no longer fits says so on the card, under the proposal.
        const refused = (why) => {
          state.unfitSuggestions.set(comment.id, { suggestion: JSON.stringify(comment.suggestion), why });
          renderComments();
        };
        if (await doMutation(comment.id, "apply-suggestion", { updated: comment.updated }, refused)) {
          try {
            await refreshComments();
          } catch (error) {
            alert(`Applied, but comments could not be refreshed: ${error.message}`);
          }
          return;
        }
        applyButton.disabled = false;
        applyButton.textContent = "Apply suggestion";
      });
    }
    // A refusal stays with the suggestion it was for: a new one from the agent is tried afresh.
    const unfit = state.unfitSuggestions.get(comment.id);
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
    return host.mayChange(entry, comment);
  }

  function renderThreadEntry(entry, comment, index) {
    const commentId = comment.id;
    const editing = state.editingEntry?.commentId === commentId
      && state.editingEntry.index === index;
    const children = [
      h("div", { class: "thread-meta" },
        h("span", { text: `${writer(entry)} · ${shortTime(entry.at)}` }),
        host.entryMark(entry),
        // What the reviewer wrote carries Edit and Delete. A thread's first message goes with
        // its thread. The records of applying and rejecting stay.
        !editing && ownEntry(entry, comment)
          ? h("span", { class: "entry-actions" },
            actionButton("cmt-edit", "Edit", () => startEntryEdit(commentId, index, entry.text)),
            actionButton("entry-delete", "Delete", async () => {
              const asked = host.deleteQuestion(entry, index,
                index === 0 ? `Permanently delete ${commentId}?` : "Delete this message?");
              if (!confirm(asked)) return;
              if (await doMutation(commentId, "delete-entry", { index })) await refreshComments();
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
    state.editingEntry = { commentId, index, draft: text };
    renderComments();
    const node = Array.from($("#comments-list").children).find(
      (child) => child.dataset.commentId === commentId,
    );
    node?.querySelector(".entry-edit-input")?.focus({ preventScroll: true });
  }

  // Editing rewrites the entry in place: the author and time stay, so a typo fix
  // does not read as a new message in the thread.
  function renderEntryEditor(commentId, index) {
    const editing = state.editingEntry;
    const textarea = h("textarea", { class: "cmt-form-input entry-edit-input", rows: 3 });
    textarea.value = editing.draft;
    textarea.addEventListener("input", () => {
      if (state.editingEntry === editing) editing.draft = textarea.value;
    });
    const save = async () => {
      const text = textarea.value.trim();
      if (!text) return;
      textarea.disabled = true;
      if (!await doMutation(commentId, "edit", { index, text })) {
        textarea.disabled = false;
        textarea.focus();
        return;
      }
      state.editingEntry = null;
      try {
        await refreshComments();
      } catch (error) {
        alert(`Saved, but comments could not be refreshed: ${error.message}`);
      }
    };
    const cancel = () => {
      state.editingEntry = null;
      renderComments();
    };
    host.sendOn(textarea, save);
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

  function actionButtons(comment) {
    const deleteButton = actionButton("cmt-delete", "Delete", () => {
      if (confirm(`Permanently delete ${comment.id}?`)) {
        mutateAndRefresh(comment.id, "delete", null);
      }
    });
    // Every status flip is one click, made as the row's buttons make it: the thread already holds
    // what was said, so the status flips without adding an entry. An archived thread still
    // takes replies, and goes back to open or resolved from the same row.
    const buttons = [];
    if (comment.status !== "resolved") {
      buttons.push(actionButton("cmt-reply", labels.reply, () => setActiveForm(comment.id, "reply")));
    }
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
    return host.cardActions(comment, buttons);
  }

  // A card can hold several boxes, one the page adds above the one being written in a form
  // among them, so a box is found again by which it is, never as the card's first: one in a
  // form by the form's mode, any other by its own class.
  function boxOf(input) {
    const mode = [...(input.closest(".cmt-form")?.classList ?? [])].find((name) => name.startsWith("mode-"));
    return mode ? `.cmt-form.${mode} .cmt-form-input` : `.${[...input.classList].find((name) => name !== "cmt-form-input")}`;
  }

  function setActiveForm(commentId, mode) {
    state.activeForm = { commentId, mode, draft: "" };
    state.expanded.add(commentId);
    renderComments();
    const commentNode = Array.from($("#comments-list").children).find(
      (node) => node.dataset.commentId === commentId,
    );
    const input = commentNode?.querySelector(`.cmt-form.mode-${mode} .cmt-form-input`);
    if (!input) return;
    input.closest(".cmt-form").scrollIntoView({ block: "nearest", behavior: "smooth" });
    input.focus({ preventScroll: true });
  }

  async function closeComment(commentId) {
    if (state.activeForm?.commentId === commentId) state.activeForm = null;
    await setStatus([commentId], "resolved");
  }

  function renderActiveForm(comment) {
    if (!state.activeForm || state.activeForm.commentId !== comment.id) return null;
    const mode = state.activeForm.mode;
    const config = FORM_CONFIG[mode];
    const textarea = h("textarea", {
      class: "cmt-form-input",
      rows: 3,
      placeholder: config.placeholder,
    });
    textarea.value = state.activeForm.draft;
    textarea.addEventListener("input", () => {
      if (
        state.activeForm?.commentId === comment.id
        && state.activeForm.mode === mode
      ) {
        state.activeForm.draft = textarea.value;
      }
    });
    let submitting = false;
    let submitButton = null;
    const submit = async () => {
      const text = textarea.value.trim();
      if (!text || submitting) return;
      submitting = true;
      textarea.disabled = true;
      submitButton.disabled = true;
      submitButton.textContent = "Saving…";
      const saved = await doMutation(comment.id, mode, { [config.key]: text });
      if (saved) {
        state.activeForm = null;
        host.posted();
        try {
          await refreshComments();
        } catch (error) {
          alert(`Saved, but comments could not be refreshed: ${error.message}`);
        }
        // The box is gone with what was typed in it: the keys go back to the list.
        $("#comments-list").focus({ preventScroll: true });
        return;
      }
      submitting = false;
      textarea.disabled = false;
      submitButton.disabled = false;
      submitButton.textContent = config.label;
      textarea.focus();
    };
    host.sendOn(textarea, submit);
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        state.activeForm = null;
        renderComments();
        $("#comments-list").focus({ preventScroll: true });
      }
    });
    submitButton = actionButton("cmt-form-submit", config.label, submit);
    submitButton.title = `${config.label} (${host.sendKeyName()})`;
    const form = h("div", { class: `cmt-form mode-${mode}` },
      h("div", { class: "cmt-form-title", text: config.placeholder }),
      textarea,
      h("div", { class: "cmt-form-actions" },
        actionButton("cmt-form-cancel", "Cancel", () => {
          state.activeForm = null;
          renderComments();
        }),
        submitButton));
    return form;
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

  async function mutateAndRefresh(commentId, action, body) {
    if (!await doMutation(commentId, action, body)) return;
    try {
      await refreshComments();
    } catch (error) {
      alert(`Saved, but comments could not be refreshed: ${error.message}`);
    }
  }

  // The order the shown tab's list is in: latest first, oldest first, or the list's first order.
  function commentSort() {
    const sort = state.commentSorts[state.commentTab];
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
    state.commentSorts[state.commentTab] = sort;
    try { localStorage.setItem(`${host.prefix}CommentSort-${state.commentTab}`, sort); } catch { /* the choice lasts this visit */ }
    showCommentSort();
  }

  function cycleSort() {
    const select = $("#comment-sort");
    select.value = select.options[(select.selectedIndex + 1) % select.options.length].value;
    select.dispatchEvent(new Event("change"));
  }

  // The comment tabs share one pane, which shows the chosen tab's comments. A view chosen
  // ends what a search showed in the list.
  function setCommentFilter(value) {
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
      ? `The search covers all ${tabNoun()}s. Choose a view to clear it.`
      : `Showing ${filter.selectedOptions[0].textContent}`;
  }

  // The view the reader chose, which the list reads again once a search is cleared.
  function chosenView() {
    const filter = $("#comment-filter");
    return filter.classList.contains("searching") ? state.viewBeforeSearch : filter.value;
  }

  // While words are in the search box the list covers every status, so the view list reads
  // "all", greyed, and goes back to the view chosen before once the search is cleared.
  function showSearchView(searching) {
    const filter = $("#comment-filter");
    if (searching === filter.classList.contains("searching")) return;
    if (searching) {
      state.viewBeforeSearch = filter.value;
      filter.value = "all";
    } else {
      filter.value = state.viewBeforeSearch;
    }
    filter.classList.toggle("searching", searching);
    showViewName();
  }

  function switchTab(name) {
    for (const button of $$(".tab-btn")) button.classList.toggle("active", button.dataset.tab === name);
    const pane = name in tabs ? "comments" : name;
    for (const element of $$(".tab-pane")) element.classList.toggle("active", element.id === `tab-${pane}`);
    if (pane !== "comments" || state.commentTab === name) return;
    state.commentTab = name;
    state.picked.clear();
    renderCommentFilter();
    // Each comment tab opens on its open view.
    setCommentFilter("open");
    refreshComments().catch((error) => console.error(error));
  }

  // The dialog for a new comment on *anchor*, which *label* names, posted with *fields*, what the
  // page posts beside it. The page readies its own parts of the dialog first. What a refused
  // post left in the box comes back in it, and the first box shown takes the keys.
  function openCompose(anchor, label, fields = {}) {
    state.pendingAnchor = anchor;
    state.composeFields = fields;
    compose.opening(fields);
    $("#compose-anchor").textContent = label;
    $("#compose-submit").title = `Post (${host.sendKeyName()})`;
    $("#compose-text").value = state.keptComment;
    state.keptComment = "";
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
    state.composeSubmitting = submitting;
    const dialog = $("#compose-dialog");
    dialog.setAttribute("aria-busy", String(submitting));
    for (const control of $$("#compose-form textarea, #compose-form button")) {
      control.disabled = submitting;
    }
    $("#compose-submit").textContent = submitting ? "Posting…" : "Post";
  }

  // A comment posts what its box holds, or what the page's own boxes add when that box is empty.
  async function submitCompose(event) {
    event.preventDefault();
    if (state.composeSubmitting) return;
    const text = $("#compose-text").value.trim();
    const filled = compose.filled(state.composeFields);
    if (!(text || Object.keys(filled).length > 0) || !state.pendingAnchor) return;
    const body = { anchor: state.pendingAnchor, text, ...state.composeFields, ...filled };

    applyComposeSubmitting(true);
    let started;
    try {
      const answer = await api.create(body);
      if (!answer.ok) {
        if (!compose.refused(answer, body)) alert(`Could not save comment: ${answer.error}`);
        return;
      }
      started = answer.data;
      $("#compose-dialog").close();
    } finally {
      applyComposeSubmitting(false);
    }
    // A note for the agent waits for it, and the line above the list may say how to call it.
    if (text) host.posted();
    await compose.posted(started, body);
  }

  // The panel's controls wired: the row, Undo, the dialog, the views, the tabs and the orders.
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
        if (ids.length === 0 || !host.confirmRow(button, ids, status)) return;
        button.disabled = true;
        await setStatus(ids, status);
        renderPickedActions();
      });
    }
    $("#undo-btn").title = `Undo the last status change${keys.hint("undo-go")}`;
    $("#comment-search").title = `Search the comments of this tab${keys.hint("comment-search")}`;
    $("#undo-btn").addEventListener("click", () => undoStatus().catch((error) => console.error(error)));
    $("#undo-close").addEventListener("click", () => {
      state.undo = null;
      renderUndo();
    });
    $("#compose-form").addEventListener("submit", (event) => {
      submitCompose(event).catch((error) => alert(error.message));
    });
    for (const box of $$("#compose-form textarea")) {
      host.sendOn(box, () => $("#compose-form").requestSubmit());
      box.addEventListener("input", () => fitComposeBox(box));
    }
    // However the dialog closes, by Post, Cancel, Escape or Ctrl+[, what it was opened on goes,
    // and the page lets go of it too. The close event comes after the dialog has closed, by which
    // time it may be open again, on what it is opened on now, which stays.
    $("#compose-dialog").addEventListener("close", () => {
      if ($("#compose-dialog").open) return;
      state.pendingAnchor = null;
      compose.closed();
    });
    $("#compose-cancel").addEventListener("click", (event) => {
      event.preventDefault();
      $("#compose-dialog").close();
    });
    for (const name of Object.keys(tabs)) {
      try { state.commentSorts[name] = localStorage.getItem(`${host.prefix}CommentSort-${name}`); } catch { /* the first order */ }
    }
    renderCommentFilter();
    // A view chosen in the list, and a comment tab chosen, give the comment keys back to the
    // comments: the list would keep them, and Safari focuses no button it clicks.
    attachCommentSearch();
    $("#comment-filter").addEventListener("change", () => {
      // A view chosen puts the search away: the list shows that view.
      closeCommentSearch();
      setCommentFilter($("#comment-filter").value);
      host.viewChosen($("#comment-filter").value);
      $("#comments-list").focus({ preventScroll: true });
      refreshComments().catch((error) => console.error(error));
    });
    for (const button of $$(".tab-btn")) {
      button.addEventListener("click", () => {
        switchTab(button.dataset.tab);
        if ($("#tab-comments").classList.contains("active")) $("#comments-list").focus({ preventScroll: true });
      });
    }
    // An order chosen gives the keys back to the comments too. It keeps the search, which it
    // orders as well, and closes the dots it may wait behind.
    $("#comment-sort").addEventListener("change", () => {
      setCommentSort($("#comment-sort").value);
      closeCommentRare();
      $("#comments-list").focus({ preventScroll: true });
      refreshComments().catch((error) => console.error(error));
    });
  }

  // What the panel holds for the document shown let go, for a page that shows another in its
  // place: its comments, those opened and picked, the ring, a box being written in or an entry
  // being edited, and the change Undo would put back, which was made to the other's comments.
  // A change still on its way for that one stops when it comes back.
  function reset() {
    resets += 1;
    state.comments = [];
    state.expanded.clear();
    state.picked.clear();
    state.pickedFrom = null;
    state.focusedCommentId = null;
    state.activeForm = null;
    state.editingEntry = null;
    state.unfitSuggestions.clear();
    state.commentsView = null;
    state.undo = null;
    renderUndo();
  }

  return {
    attach, refreshComments, renderComments, renderPickedActions, fitCommentRow, foldAll, setStatus,
    undoStatus, pickBox, actionButton, doMutation, setActiveForm, unanswered, setCommentFilter, chosenView,
    switchTab, cycleSort, openCompose, openCommentSearch, closeCommentSearch, reset,
  };
}
