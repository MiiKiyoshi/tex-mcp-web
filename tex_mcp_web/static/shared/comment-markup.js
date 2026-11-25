// The comment panel's markup (comment-panel.js): the pane it draws into #tab-comments, with the
// row, the search, the Undo line and the list, and the new comment dialog it draws into
// #compose-dialog, each with the places where the page's own parts go. It gives
// paneMarkup(labels, helped, filed), the pane, with Help's link where there is Help and with file:
// where comments are on files; composeMarkup(labels), the dialog; and drawPanel(place, markup),
// one of them drawn into its place, the page's parts in their slots.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

function escaped(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
}

// The panel's markup. Its row has four groups, a thin line between each two: what to look at,
// picking, what acts on the picked comments, and fetching and calling, which the bell ends
// (agent-bell.js). The buttons are icons, with the words as title and aria-label. Each button
// shows the one drawing that fits its state; a count rides beside the drawing where the action
// has one. A button with nothing to do is greyed out, not taken away: the row keeps one shape.
// Each <slot> is where the page's own parts go (drawPanel). With Help, the search's conditions
// end with a link to its page, and they offer file: only where comments are on files.
export function paneMarkup(labels, helped, filed) {
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
  </span>
</div>
<!-- Lists the tab's comments that meet what is typed, whatever their status. -->
<input id="comment-search" class="comment-search" type="search" autocomplete="off" spellcheck="false"
       aria-label="Search comments" placeholder="${filed ? "Search words, a file or a name…" : "Search words or a name…"}">
<!-- Shown while the search box has the focus: a click puts the condition into the box at
     the cursor. The list under them offers the tab's writers for from: and its files for file:. -->
<div id="search-terms" class="search-terms" hidden>
  <button type="button" tabindex="-1" data-insert="from:" title="Show comments with a message from a person. Type a name or part of one, or use from:me or from:agent.">from:</button>
  ${filed ? '<button type="button" tabindex="-1" data-insert="file:" title="Show comments on a file. Type its name or part of it.">file:</button>' : ""}
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

export function composeMarkup(labels) {
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
export function drawPanel(place, markup) {
  const parts = [...place.querySelectorAll(":scope > [data-slot]")];
  place.innerHTML = markup;
  for (const slot of place.querySelectorAll("slot")) {
    const own = parts.filter((part) => part.dataset.slot === slot.name);
    for (const part of own) part.removeAttribute("data-slot");
    slot.replaceWith(...own);
  }
}
