// The comment panel's list (comment-panel.js): each reading of the shown tab's comments from the
// server, which is the one way the list is drawn from it, with what the page reads beside them;
// what the view and a search list of them, in the order chosen; the list drawn, keeping a box
// being written in; and a comment shown from its mark. Of the readings asked for while one is
// on its way, only the latest is drawn.
//
// createCommentList takes state (comment-state.js); host, the page's hooks; api
// (comment-api.js); the panel's tab(), tabOf(comment), statusOf(comment) and
// unanswered(comment); keys, the comment keys' mark(), placeRing() and rangeIds(); bell, the
// bell or null; of the search, searchQuery(), meets(comment, query) and closeCommentSearch(); of
// the views, view(), commentSort(), setView(view), showSearchView(searching) and
// renderTabs(counts); of the cards, card(comment, look) and boxOf(box); and of the row,
// renderPickedActions() and renderFold(). It gives readAll(), every comment whatever its status;
// refreshComments(); renderComments(); showComment(id); and reset().
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, clear, placeholder } from "./dom.js";

// How many of the comments a search finds are drawn.
const SEARCH_SHOWN = 100;

// The views a server's list gives as they are; any other view lists some of the open comments.
const LISTED = ["resolved", "archived", "all"];

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

export function createCommentList({
  state, host, api, tab, tabOf, statusOf, unanswered, keys, bell, searchQuery, meets, closeCommentSearch,
  view, commentSort, setView, showSearchView, renderTabs, card, boxOf, renderPickedActions, renderFold,
}) {
  // How many comments a search found beyond those drawn.
  let searchMore = 0;
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
    const status = searching ? "all" : view();
    const [answer, along] = await Promise.all([api.list(LISTED.includes(status) ? status : "open"), host.along.read()]);
    if (!answer.ok) throw new Error(answer.error);
    if (asked !== commentsAsked) return commentsReading;
    host.along.apply(along);
    // What the page read may have shown another tab, which reads its own list.
    if (asked !== commentsAsked) return commentsReading;
    const read = answer.data.comments;
    countWaiting(read, status);
    const marked = read.filter((comment) => tabOf(comment) === state.tab() && statusOf(comment) === "open");
    renderTabs(unansweredCounts);
    const before = state.listed();
    const comments = ordered(listed(read, status, query), status, searching);
    // A search most comments meet finds hundreds, which take a second to draw at every key: the
    // first ones show, with how many more there are.
    searchMore = searching ? Math.max(0, comments.length - SEARCH_SHOWN) : 0;
    state.setListed(searchMore > 0 ? comments.slice(0, SEARCH_SHOWN) : comments);
    const handed = handFocus(before, `${state.tab()} ${status}`);
    // A tab with no comment at all, in any view, says how to make one.
    let every = status === "all" ? read : null;
    if (comments.length === 0 && !searching && every === null) {
      const all = await api.list("all");
      if (asked !== commentsAsked) return commentsReading;
      every = all.ok ? all.data.comments : [];
    }
    tabEmpty = comments.length === 0 && !searching
      && !every.some((comment) => tabOf(comment) === state.tab()) && !(tab().items().length > 0);
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
    bell?.awaited(read.some((comment) => comment.awaits_agent));
  }

  // What the view lists of the shown tab's comments read: what the tab's own views list, the
  // unanswered ones in that view, and in a search those that meet it.
  function listed(read, status, query) {
    const own = read.filter((comment) => tabOf(comment) === state.tab());
    const comments = tab().list ? tab().list(status, own, query.length > 0, unanswered)
      : status === "unanswered" ? own.filter(unanswered) : own;
    return query.length === 0 ? comments : comments.filter((comment) => meets(comment, query));
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
    const now = state.listed();
    const at = before.findIndex((comment) => comment.id === state.ringed());
    const handed = view === commentsView && at !== -1 && now.length > 0
        && !now.some((comment) => comment.id === state.ringed());
    if (handed) state.ring(now[Math.min(at, now.length - 1)].id);
    commentsView = view;
    return handed;
  }

  // The list drawn as the state lists it, each comment on its tab's own card or on the panel's,
  // a box being written in keeping the keys, its selection and its scroll, and the row and the
  // ringed card's key letters drawn for it.
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
    const comments = state.listed();
    if (comments.length === 0) {
      list.appendChild(placeholder(searchQuery().length > 0 ? "No comments match your search."
        : tab().empty[view()] ?? "No comments in this view."));
      if (tabEmpty) list.appendChild(placeholder(tab().hint));
    } else {
      const range = keys.rangeIds();
      for (const comment of comments) {
        const look = { open: state.isOpen(comment.id), ringed: state.ringed() === comment.id, inRange: range.includes(comment.id) };
        list.appendChild(tab().itemCard(comment, look) ?? card(comment, look));
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
    renderFold();
    keys.mark();
  }

  // A comment shown from its mark on the page: opened and ringed in the list, in the open view of
  // the tab shown when the view leaves it out, as the unanswered view leaves out a comment the
  // agent has not answered yet, and brought into view (bringIntoView). One the open view does
  // not list either stays as the list has it.
  async function showComment(id) {
    const listedNow = () => state.listed().some((comment) => comment.id === id);
    if (!listedNow()) {
      closeCommentSearch();
      setView("open");
      await refreshComments();
      if (!listedNow()) return;
    }
    state.ring(id);
    state.setOpen(id, true);
    renderComments();
    const card = $(`#comments-list [data-comment-id="${CSS.escape(id)}"]`);
    if (card) host.bringIntoView(id, card);
  }

  // The readings on their way dropped, and the view forgotten, for a panel let go of what it held
  // (reset): what they would bring is the document shown before. The next reading draws.
  function reset() {
    commentsAsked += 1;
    commentsReading = null;
    commentsView = null;
  }

  return { readAll, refreshComments, renderComments, showComment, reset };
}
