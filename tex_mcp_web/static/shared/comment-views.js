// The comment panel's views (comment-panel.js): each comment tab's count of what waits in it, the
// list of the shown tab's views, the order its list is in, which this browser keeps for each tab,
// the view a search shows in place of the one chosen, and a comment tab shown.
//
// createCommentViews takes state (comment-state.js); host, the page's hooks; the panel's tab();
// keys, the comment keys' hint(button); of the search, closeCommentSearch(); of the row,
// closeCommentRare(); and of the list, refreshComments() and renderComments(). It gives view(),
// the view the list shows; chosenView(), the one the reader chose; commentSort(), the order the
// shown tab's list is in; setView(view); cycleSort(); showSearchView(searching);
// renderTabs(counts); showTab(name, focus); and attach().
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, h, placeholder } from "./dom.js";

export function createCommentViews({
  state, host, tab, keys, closeCommentSearch, closeCommentRare, refreshComments, renderComments,
}) {
  // The order each comment tab's list is in, as its sort list was last set: the list's first
  // order, latest or oldest.
  const commentSorts = {};
  // The view the list showed before a search, which it shows again once the search is cleared.
  let viewBeforeSearch = "open";

  // The view the list shows: the one chosen, or "all" during a search.
  function view() {
    return $("#comment-filter").value;
  }

  // Each tab's number, counts[tab], is how many of its comments are unanswered, with what it
  // lists beside them.
  function renderTabs(counts) {
    for (const [name, each] of Object.entries(host.tabs)) {
      if (each.count === null) continue;
      const badge = $(each.count);
      const count = counts[name] ?? 0;
      badge.textContent = String(count);
      badge.classList.toggle("hidden", count === 0);
    }
    renderCommentFilter();
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

  // The order the shown tab's list is in: latest first, oldest first, or the list's first order.
  function commentSort() {
    const sort = commentSorts[state.tab()];
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
    commentSorts[state.tab()] = sort;
    try { localStorage.setItem(`${host.settings.prefix}CommentSort-${state.tab()}`, sort); } catch { /* the choice lasts this visit */ }
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
    if (state.tab() !== name) {
      state.setTab(name);
      state.clearPicks();
      // The tab left takes its cards with it at once: shown until the new tab's list came,
      // they were picked and resolved by a press meant for the new tab.
      state.setListed([]);
      renderCommentFilter();
      setView("open");
      renderComments();
      $("#comments-list").replaceChildren(placeholder("Loading…"));
      refreshComments().catch((error) => console.error(error));
    }
    if (focus) $("#comments-list").focus({ preventScroll: true });
  }

  // The view and order lists wired, with the order this browser kept for each tab.
  function attach() {
    for (const name of Object.keys(host.tabs)) {
      try { commentSorts[name] = localStorage.getItem(`${host.settings.prefix}CommentSort-${name}`); } catch { /* the first order */ }
    }
    renderCommentFilter();
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

  return { view, chosenView, commentSort, setView, cycleSort, showSearchView, renderTabs, showTab, attach };
}
