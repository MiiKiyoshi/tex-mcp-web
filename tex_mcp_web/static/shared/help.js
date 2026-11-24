// Help, in pages chosen by tabs: the page's own "Start here", Search, the page's own pages, and
// Keys, which lists the comment keys from their table and then the page's own keys. Help draws
// itself into #help-dialog, with the page's parts that wait in it as <template data-slot="…">:
//
//   start            the first page
//   search-example   the Search page's worked example: a heading, a story and its steps, each
//                    step's search a <code class="help-typed">, which the tests type
//   search-order     the sentence on what the sort list's first order keeps
//   search-rows      the rows of the search terms table for from: and file:
//   search-writers   the sentence on the names from: takes
//   search-words     what the search looks for words in
//   search-thread    a search two entries meet, which the sentence on whole threads names
//   page-<name>      each page of the page's own, named in `pages`
//   keys             the page's own keys, after the comments': <h4> for each part of the page,
//                    <h5> under it, and each key as <span class="keys-of"><kbd>…</kbd></span>
//                    followed by a <span> of what it does
//
// Its look is help.css.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, $$, h, clear } from "./dom.js";
import { KEY_NAMES } from "./comment-keys.js";

function helpMarkup(all) {
  const tabs = all.map(([page, name], at) => `<button type="button" role="tab" id="help-tab-${page}" data-page="${page}" `
    + `aria-controls="help-${page}" aria-selected="${at === 0}"${at === 0 ? "" : ' tabindex="-1"'}>${name}</button>`);
  const own = all.slice(2, -1).map(([page]) => `<section id="help-${page}" class="help-page" role="tabpanel" `
    + `aria-labelledby="help-tab-${page}" hidden><template data-place="page-${page}"></template></section>`);
  return `<form method="dialog" id="help-form">
  <h3 id="help-title">Help</h3>
  <div id="help-tabs" class="help-tabs" role="tablist" aria-label="Help pages">
    ${tabs.join("\n    ")}
  </div>
  <div id="help-pages" class="help-pages">
    <section id="help-start" class="help-page" role="tabpanel" aria-labelledby="help-tab-start"><template data-place="start"></template></section>
    <section id="help-search" class="help-page" role="tabpanel" aria-labelledby="help-tab-search" hidden>
      <p>The search box above the comment list finds comments in the tab that is open, in any
        status. Press <kbd>/</kbd> to go to it.</p>
      <template data-place="search-example"></template>
      <p>While you search, the view list shows <b>all</b>, because the search covers threads in
        every status. Press Enter to move to the results and go through them with the keys.
        Press Escape in the search box to clear the search and return to the view you had.</p>
      <p>The sort list orders the results as it does in any view. <template data-place="search-order"></template> <b>latest first</b> and <b>oldest first</b> order them by each
        thread's most recent message.</p>
      <h4>Search terms</h4>
      <table id="help-search-terms" class="help-table">
        <thead><tr><th>Search</th><th>Finds threads that</th></tr></thead>
        <tbody>
          <tr><td><code>caption width</code></td><td>contain both words, in any order</td></tr>
          <tr><td><code>figure OR table</code></td><td>contain either word</td></tr>
          <tr><td><code>"base case"</code></td><td>contain the exact phrase</td></tr>
          <tr><td><code>-appendix</code></td><td>don't contain the word</td></tr>
          <template data-place="search-rows"></template>
          <tr><td><code>after:11-12</code></td><td>have a message written on or after November&nbsp;12</td></tr>
          <tr><td><code>before:11-14</code></td><td>have a message written before November&nbsp;14</td></tr>
        </tbody>
      </table>
      <p>Write OR in capitals. It splits the search into alternatives, so
        <code>caption width OR table</code> finds threads that contain both caption and width,
        or that contain table. A minus also works before a phrase or a term with a colon, as in
        <code>-from:agent</code>. <template data-place="search-writers"></template></p>
      <h4>Dates and times</h4>
      <table id="help-search-dates" class="help-table">
        <thead><tr><th>Date</th><th>Means</th></tr></thead>
        <tbody>
          <tr><td><code>11-12</code></td><td>November&nbsp;12 of this year</td></tr>
          <tr><td><code>2026-11-12</code></td><td>November&nbsp;12, 2026</td></tr>
          <tr><td><code>11-12T09</code></td><td>9:00 on November&nbsp;12</td></tr>
          <tr><td><code>11-12T09:30</code></td><td>9:30 on November&nbsp;12</td></tr>
          <tr><td><code>"11-12 09:30"</code></td><td>9:30 on November&nbsp;12, written as the comment cards show it</td></tr>
        </tbody>
      </table>
      <p>Dates and times are in your computer's time zone, as on the comment cards. A date
        without a time means the start of that day, so <code>before:11-14</code> still includes
        November&nbsp;13.</p>
      <p>The search looks for words in <template data-place="search-words"></template>. Capital letters don't
        matter. Each term is checked against the whole thread rather than a single message, so
        <template data-place="search-thread"></template>. A word with a colon that
        isn't one of these terms, such as <code>note:</code>, is searched for as an ordinary
        word.</p>
    </section>
    ${own.join("\n    ")}
    <section id="help-keys" class="help-page" role="tabpanel" aria-labelledby="help-tab-keys" hidden>
      <div class="keys-grid">
        <h4>Comments</h4>
        <p>These shortcuts act on the current comment when the Comments tab is open and you are
          not typing. For a doubled letter such as dd, press the key twice.</p>
        <div id="keys-list"></div>
        <p id="keys-picked"></p>
        <template data-place="keys"></template>
      </div>
    </section>
  </div>
  <div class="dialog-actions">
    <button type="submit">Close</button>
  </div>
</form>`;
}

// Help drawn into #help-dialog, with the page's parts in their places, and its tabs wired.
// `prefix` is what the browser keeps the page shown last under, before "HelpPage"; `pages` the
// page's own pages, [name, tab], which come between Search and Keys; and `keys` the comment
// keys createCommentKeys made.
export function createHelp({ prefix, pages, keys }) {
  const dialog = $("#help-dialog");
  const parts = new Map([...dialog.querySelectorAll(":scope > template[data-slot]")]
    .map((part) => [part.dataset.slot, part.content]));
  const all = [["start", "Start here"], ["search", "Search"], ...pages, ["keys", "Keys"]];
  const order = all.map(([page]) => page);
  dialog.innerHTML = helpMarkup(all);
  for (const place of dialog.querySelectorAll("template[data-place]")) {
    place.replaceWith(parts.get(place.dataset.place) ?? "");
  }

  // Shows one page and selects and focuses its tab, so the arrow keys work from there. The page
  // opens scrolled to its top, which Safari applies only a frame later.
  function showPage(page) {
    for (const tab of $$("#help-tabs [role=tab]")) {
      const chosen = tab.dataset.page === page;
      tab.setAttribute("aria-selected", String(chosen));
      tab.tabIndex = chosen ? 0 : -1;
      $(`#${tab.getAttribute("aria-controls")}`).hidden = !chosen;
      if (chosen) tab.focus();
    }
    try { localStorage.setItem(`${prefix}HelpPage`, page); } catch { /* remembered for this visit only */ }
    const shown = $("#help-pages");
    shown.scrollTop = 0;
    requestAnimationFrame(() => { shown.scrollTop = 0; });
  }

  // Lists the comment keys on the Keys page, built from their table.
  function listCommentKeys() {
    const list = $("#keys-list");
    clear(list);
    // One grid for every group, so the words line up down the whole list.
    for (const [group, entries] of keys.table) {
      list.append(h("h5", { text: group }), ...entries.flatMap((entry) => [
        h("span", { class: "keys-of" }, ...entry.keys.map((names) => h("kbd", { text: KEY_NAMES[names] ?? names }))),
        h("span", { text: entry.does }),
      ]));
    }
    const picked = keys.table.flatMap(([, group]) => group).filter((entry) => entry.picked).map((entry) => entry.keys[0]);
    $("#keys-picked").textContent = `With comments selected, ${picked.slice(0, -1).join(", ")} and ${picked.at(-1)} act on the selection.`;
  }

  // Opens Help on the given page, or else on the page shown last in this browser.
  function show(page) {
    let shown = page;
    if (shown === undefined) {
      try { shown = localStorage.getItem(`${prefix}HelpPage`); } catch { /* the first page */ }
    }
    listCommentKeys();
    dialog.showModal();
    showPage(order.includes(shown) ? shown : order[0]);
  }

  for (const tab of $$("#help-tabs [role=tab]")) {
    tab.addEventListener("click", () => showPage(tab.dataset.page));
  }
  // Left and Right move to the previous and next tab, wrapping around at the ends, and Home and
  // End move to the first and last.
  $("#help-tabs").addEventListener("keydown", (event) => {
    const at = order.indexOf(event.target.dataset?.page);
    const to = { ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: order.length - 1 }[event.key];
    if (at === -1 || to === undefined || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    showPage(order[(to + order.length) % order.length]);
  });
  $("#help-btn").addEventListener("click", () => show());
  $("#search-help-btn")?.addEventListener("click", () => show("search"));
  return { show };
}
