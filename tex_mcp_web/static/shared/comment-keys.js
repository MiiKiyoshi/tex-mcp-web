// The comment keys of the review pages. The comments go by keys as a notebook's cells do: the
// list takes the keys while the Comments pane is shown and nothing is being typed in, and a
// box being written in keeps them until Escape. A key presses the ringed comment's button, so
// it does what that button does, and nothing on a comment without one. A key that changes a
// file or deletes is a letter pressed twice, as a notebook deletes a cell with dd.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, $$, eventOrigin, COMPOSED, keyByPosition } from "./dom.js";

// The names the keys without a letter of their own go by, on the Keys page and on the buttons.
export const KEY_NAMES = { ArrowDown: "↓", ArrowUp: "↑", " ": "Space" };

// Where the keys keep the ringed card's middle: a third of the way down the list, its top never
// above the list's, so the eye stays put while the comments move under it. The list's two ends
// hold it back where it cannot scroll.
export const RING_AT = 1 / 3;

// The button R presses on a comment.
function resolveOrReopen(comment) {
  return comment?.status === "open" ? ".cmt-resolve" : ".cmt-reopen";
}

// Several keys named in a sentence: "R, A and x".
function spoken(keys) {
  return `${keys.slice(0, -1).join(", ")} and ${keys.at(-1)}`;
}

// The keys, which the comment panel makes on what it holds of the comments it shows. A key for
// a part the page has not is not there: no K without enterPlace, no c without whole, no ?
// without help, and no aa or nn without suggestions. The panel gives:
//
//   state        what the panel holds of the comments it shows (comment-state.js)
//   labels       `document`, what the page calls what is reviewed, and `firstSort`, the name
//                of the sort list's first order
//   panel()      what the keys call of the panel: renderComments(), renderPickedActions(),
//                foldAll(), undoStatus(), closeReply(), chosenView(), cycleSort() and
//                openCommentSearch()
//   rows(press)  the page's own keys: an entry with `after` goes after the shared entry its
//                first key names, and one without gives its fields to the shared entry with
//                its first key. `press` holds what the shared keys press with: pressCard,
//                pressPickedOr, pressForRange and focusedCard.
//   showPlace(comment)  what Space shows of a comment it opens: where it is, the keys staying
//                       with the list
//   enterPlace(comment) what K does: the comment's words in the source, the keys going there
//   whole()             what c does: a comment on the whole document
//   help()              what ? does
//   suggestions         whether a comment may carry a suggestion, which aa applies and nn rejects
//   lettersShown()      whether Settings shows the key letters on the ringed comment's buttons
//   rests(event)        whether the keys rest for this key, as for a part of the page that is
//                       shown over the list and has keys of its own
//
// It gives table, the keys as Help lists them; hint(button), a button's key for its title;
// showLetters(shown); rangeIds(), the comments in the range v holds; mark(), the ringed card's
// controls marked with their keys; placeRing(), the list scrolled to the ring; attach(); and
// reset(), the range let go.
export function createCommentKeys(host) {
  const { state, labels } = host;
  // The comment a range started from, while v holds one.
  let rangeFrom = null;
  const panel = () => host.panel();

  // Each key as the Keys page of Help lists it. A key with a button names the controls it works,
  // for their titles. One that acts on the picked comments, when there are some, says so. While
  // v holds a range, a key with a range action does that, and of the rest only those marked
  // inRange act: the keys that move, which stretch it, and Space. The list's keys act while the
  // Comments pane is shown, the tab keys while the sidebar is, and ? anywhere. A key whose `has`
  // is false works a part the page has not, and is left out.
  const table = [
    ["Move", [
      { keys: ["j", "ArrowDown"], does: "Next comment", repeats: true, inRange: true,
        run: () => moveFocus((at, last) => Math.min(at + 1, last)) },
      { keys: ["k", "ArrowUp"], does: "Previous comment", repeats: true, inRange: true,
        run: () => moveFocus((at) => Math.max(at - 1, 0)) },
      { keys: ["gg", "Home"], does: "First comment", inRange: true, run: () => moveFocus(() => 0) },
      { keys: ["G", "End"], does: "Last comment", inRange: true, run: () => moveFocus((at, last) => last) },
      { keys: [" "], does: `Expand or collapse it; expanding also shows its place in the ${labels.document} or source`,
        inRange: true, run: () => foldFocused() },
      { keys: ["Shift+Space"], does: "Expand or collapse all comments", run: () => panel().foldAll() },
      { keys: ["K", "Ctrl+Alt+K"], does: "Jump to its text in the source and move keyboard focus there. "
        + "Press K (Ctrl+Alt+K without Vim keys) to come back", run: () => enterPlace(), has: host.enterPlace !== null },
    ]],
    ["Write", [
      { keys: ["r"], does: "Write a reply for the agent", button: ["cmt-reply"], run: () => pressCard(".cmt-reply") },
      { keys: ["e"], does: "Edit your last message in it", marks: ".cmt-edit", run: () => pressCard(".cmt-edit") },
      { keys: ["c"], does: `Comment on the whole ${labels.document}`, run: () => host.whole(), has: host.whole !== null },
      { keys: ["Escape"], does: "Leave the text box. An unsent reply or edit is discarded" },
    ]],
    ["Change", [
      { keys: ["R"], does: "Resolve it, or reopen it when it is not open", button: ["cmt-resolve", "cmt-reopen"], picked: true,
        marks: (comment) => resolveOrReopen(comment),
        run: () => pressPickedOr(["#resolve-picked-btn"], resolveOrReopen(focused())),
        range: () => pressForRange(["#resolve-picked-btn"]) },
      { keys: ["A"], does: "Archive it", button: ["cmt-archive"], picked: true,
        run: () => pressPickedOr(["#archive-picked-btn"], ".cmt-archive"),
        range: () => pressForRange(["#archive-picked-btn"]) },
      { keys: ["u"], does: "Undo the last status change, while Undo is shown above the list", button: ["undo-go"],
        run: () => panel().undoStatus().catch((error) => console.error(error)) },
      { keys: ["aa"], does: "Apply its suggestion to the file", button: ["sugg-apply"], run: () => pressCard(".sugg-apply"),
        has: host.suggestions },
      { keys: ["nn"], does: "Reject its suggestion", button: ["sugg-reject"], run: () => pressCard(".sugg-reject"),
        has: host.suggestions },
      { keys: ["dd"], does: "Delete it (after confirmation)", button: ["cmt-delete"], run: () => pressCard(".cmt-delete") },
      { keys: ["D"], does: "Delete your last message in it (after confirmation)",
        marks: ".entry-delete", run: () => pressCard(".entry-delete") },
    ]],
    ["Selection", [
      { keys: ["x"], does: "Select or deselect it, or the whole range while one is marked", marks: ".cmt-head",
        run: () => pickFocused(), range: () => pickRange() },
      { keys: ["v"], run: () => holdRange(), range: () => letRangeGo() },
      { keys: ["*"], does: "Select all comments, or clear the selection", run: () => $("#pick-all-btn").click() },
    ]],
    ["View", [
      { keys: ["/"], does: "Search this tab's comments in any status. Enter jumps to the results; Escape closes the search",
        button: ["comment-search"], run: () => panel().openCommentSearch() },
      { keys: ["f"], does: "Next view (open, unanswered, …)", run: () => cycleStatus(1) },
      { keys: ["F"], does: "Previous view", run: () => cycleStatus(-1) },
      { keys: ["o"], does: `Next sort order (${labels.firstSort}, latest first, oldest first)`, button: ["comment-sort"],
        run: () => panel().cycleSort() },
      { keys: ["t"], does: "Next tab", scope: "sidebar", run: () => cycleTab(1) },
      { keys: ["T"], does: "Previous tab", scope: "sidebar", run: () => cycleTab(-1) },
      { keys: ["?"], does: "Open Help", scope: "page", run: () => host.help(), has: host.help !== null },
    ]],
  ].map(([name, group]) => [name, group.filter((entry) => entry.has !== false)]);
  const entries = () => table.flatMap(([, group]) => group);
  for (const { after, ...own } of host.rows({ pressCard, pressPickedOr, pressForRange, focusedCard })) {
    const [, group] = table.find(([, listed]) => listed.some((entry) => entry.keys[0] === (after ?? own.keys[0])));
    const at = group.findIndex((entry) => entry.keys[0] === (after ?? own.keys[0]));
    if (after === undefined) Object.assign(group[at], own);
    else group.splice(at + 1, 0, own);
  }
  // v names the keys that act on a held range, the page's own among them.
  const ranged = entries().filter((entry) => entry.range && entry.keys[0] !== "v").map((entry) => entry.keys[0]);
  entries().find((entry) => entry.keys[0] === "v").does = "Start a range at it (or at the first comment) and extend it with "
    + `the movement keys. ${spoken(ranged)} then act on the whole range; Escape or v clears it`;

  // Each control of the ringed comment a key works, marked with that key's letter for the list
  // to show. A key that acts on the picked comments, while there are some, or on a held range,
  // works none of the card's controls, and none is marked for it.
  function mark() {
    const comment = focused();
    const card = comment && $(`#comments-list [data-comment-id="${CSS.escape(comment.id)}"]`);
    if (!card || rangeIds().length > 0) return;
    const picking = state.listed().some((item) => state.isPicked(item.id));
    for (const entry of entries()) {
      if (picking && entry.picked) continue;
      const where = typeof entry.marks === "function" ? entry.marks(comment)
        : entry.marks ?? entry.button?.map((name) => `.${name}`).join(", ");
      if (!where) continue;
      const found = card.querySelectorAll(where);
      found[found.length - 1]?.setAttribute("data-key", KEY_NAMES[entry.keys[0]] ?? entry.keys[0]);
    }
  }

  // The letters show once the keys move through the list, as the reader takes to them, and go
  // at a touch of the mouse or a box taken to type in.
  function showLetters(shown) {
    $("#comments-list").classList.toggle("show-keys", shown && host.lettersShown());
  }

  // The key that presses a comment's button, for the button's title.
  function hint(button) {
    const entry = entries().find((item) => item.button?.includes(button));
    return entry ? ` (${entry.keys.join(", ")})` : "";
  }

  function focused() {
    return state.listed().find((comment) => comment.id === state.ringed());
  }

  // The focused comment's card, opened unless asked not to, since a closed card shows none of
  // its buttons.
  function focusedCard(open = true) {
    const comment = focused();
    if (comment === undefined) return null;
    if (open && !state.isOpen(comment.id)) {
      state.setOpen(comment.id, true);
      panel().renderComments();
    }
    return $(`#comments-list [data-comment-id="${CSS.escape(comment.id)}"]`);
  }

  // The last of the focused card's buttons that match, which for Edit is the last thing written.
  function pressCard(selector) {
    const buttons = focusedCard()?.querySelectorAll(selector) ?? [];
    buttons[buttons.length - 1]?.click();
  }

  // With comments picked, the row's first button that can act on them; else the focused card's.
  function pressPickedOr(rows, card) {
    if (state.listed().some((comment) => state.isPicked(comment.id))) {
      rows.map((selector) => $(selector)).find((button) => !button.hidden && !button.disabled)?.click();
    } else {
      pressCard(card);
    }
  }

  // The keys move the ring alone: every card stays as it was, and the list scrolls at once, not
  // smoothly, to put the ring in its place.
  function moveFocus(to) {
    const comments = state.listed();
    const last = comments.length - 1;
    if (last < 0) return;
    const comment = comments[to(comments.findIndex((item) => item.id === state.ringed()), last)];
    state.ring(comment.id);
    panel().renderComments();
    placeRing();
  }

  // The list scrolled to put the ringed card in its place.
  function placeRing() {
    const card = $(`#comments-list [data-comment-id="${CSS.escape(state.ringed() ?? "")}"]`);
    const list = $("#comments-list");
    if (!card) return;
    const box = list.getBoundingClientRect();
    const it = card.getBoundingClientRect();
    list.scrollTop += it.top - box.top - Math.max(0, list.clientHeight * RING_AT - it.height / 2);
  }

  // The keys taken to the ringed comment's place, as K at a comment's words takes them to the
  // comment.
  function enterPlace() {
    const comment = focused();
    if (comment !== undefined) host.enterPlace(comment);
  }

  // A card opened shows its end, where the thread's latest words are, before its top when it is
  // taller than the list, and the page shows where the comment is. The keys stay with the list.
  function foldFocused() {
    const comment = focused();
    if (comment === undefined) return;
    const opening = !state.isOpen(comment.id);
    state.setOpen(comment.id, opening);
    panel().renderComments();
    const card = $(`#comments-list [data-comment-id="${CSS.escape(comment.id)}"]`);
    const list = $("#comments-list");
    const below = card ? card.getBoundingClientRect().bottom - list.getBoundingClientRect().bottom + 8 : 0;
    if (!opening) return;
    if (below > 0) list.scrollTop += below;
    host.showPlace(comment)?.catch?.((error) => console.error(error));
  }

  // A click on the card's box, so picking by key is picking by mouse.
  function pickFocused() {
    focusedCard(false)?.querySelector(".comment-pick")?.click();
  }

  // The comments from the one the range started at to the ringed one, both ends included, or
  // none while no range is held or its start has left the view.
  function rangeIds() {
    const ids = state.listed().map((comment) => comment.id);
    const from = ids.indexOf(rangeFrom);
    const to = ids.indexOf(state.ringed());
    return from === -1 || to === -1 ? [] : ids.slice(Math.min(from, to), Math.max(from, to) + 1);
  }

  function holdRange() {
    const comment = focused() ?? state.listed()[0];
    if (comment === undefined) return;
    rangeFrom = comment.id;
    state.ring(comment.id);
    panel().renderComments();
  }

  function letRangeGo() {
    rangeFrom = null;
    panel().renderComments();
  }

  // The range picked, or let go when all of it is picked already.
  function pickRange() {
    const ids = rangeIds();
    state.pick(ids, !ids.every((id) => state.isPicked(id)));
    letRangeGo();
  }

  // What the row's button does to the picked comments, done to the range. The comments picked
  // before stay picked: the button reads which comments it acts on as it is pressed.
  function pressForRange(rows) {
    const restore = state.swapPicked(rangeIds());
    rangeFrom = null;
    panel().renderPickedActions();
    rows.map((selector) => $(selector)).find((button) => !button.hidden && !button.disabled)?.click();
    restore();
    panel().renderComments();
  }

  function cycleStatus(step) {
    const filter = $("#comment-filter");
    const views = [...filter.options];
    const at = views.findIndex((option) => option.value === panel().chosenView());
    filter.value = views[(at + step + views.length) % views.length].value;
    filter.dispatchEvent(new Event("change"));
  }

  function cycleTab(step) {
    const tabs = $$(".tab-btn").filter((button) => !button.hidden);
    const at = tabs.findIndex((button) => button.classList.contains("active"));
    tabs[(at + step + tabs.length) % tabs.length].click();
  }

  // The keys heard, once the panel is drawn.
  function attach() {
    // A click on a comment puts the ring on it, as a notebook selects the cell clicked, and a
    // click anywhere in the list gives it the keys, from the editor too.
    const list = $("#comments-list");
    list.addEventListener("mousedown", (event) => {
      const card = event.target.closest(".cmt");
      if (card !== null) {
        state.ring(card.dataset.commentId);
        for (const node of list.querySelectorAll(".cmt.is-focused")) node.classList.remove("is-focused");
        card.classList.add("is-focused");
      }
      if (!event.target.closest("textarea, input, select, button, a, [contenteditable]")) {
        list.focus({ preventScroll: true });
      }
    });
    const ran = entries().filter((entry) => entry.run);
    const byKeys = new Map(ran.flatMap((entry) => entry.keys.map((keys) => [keys, entry])));
    // A letter pressed twice: the first waits a second for the second.
    const firsts = new Set([...byKeys.keys()].filter((keys) => keys.length === 2).map((keys) => keys[0]));
    let first = "";
    let timer = null;
    document.addEventListener("keydown", (event) => {
      // A key a control took for itself, as the arrows and Home and End on a panel's grip, is
      // not the list's too.
      if (event.defaultPrevented) return;
      const target = eventOrigin(event);
      // Ctrl+Alt+K, Cmd+Option+K on macOS, is K with the editor's own keys: read by where K sits,
      // since Option makes it another letter.
      const editorK = (event.ctrlKey || event.metaKey) && event.altKey && event.code === "KeyK";
      if ((!editorK && (event.metaKey || event.ctrlKey || event.altKey)) || document.querySelector("dialog[open]")) return;
      if (host.rests(event)) return;
      if (target.isContentEditable || target.closest?.("textarea, select, [contenteditable]")
          || (target.tagName === "INPUT" && !["checkbox", "radio"].includes(target.type))) return;
      // Space on a button or a box presses it.
      if (event.key === " " && target.closest?.("button, a, summary, input")) return;
      // A Korean input method gives a letter key as the letter it composes.
      const typed = event.isComposing || event.key === "Process" || COMPOSED.test(event.key)
        ? keyByPosition(event) : event.key;
      // Shift widens Space from the ringed card to every card.
      const key = editorK ? "Ctrl+Alt+K" : typed === " " && event.shiftKey ? "Shift+Space" : typed;
      if (key === undefined) return;
      clearTimeout(timer);
      if (key === "Escape") {
        first = "";
        if (rangeIds().length > 0) {
          event.preventDefault();
          letRangeGo();
        } else if (panel().closeReply()) {
          event.preventDefault();
        }
        return;
      }
      const keys = byKeys.has(first + key) ? first + key : key;
      first = "";
      const entry = byKeys.get(keys);
      if (entry === undefined) {
        if (firsts.has(keys)) {
          first = keys;
          timer = setTimeout(() => { first = ""; }, 1000);
        }
        return;
      }
      const sidebar = !$(".layout").classList.contains("sidebar-collapsed");
      const scope = entry.scope ?? "list";
      if (scope === "sidebar" ? !sidebar
        : scope === "list" ? !sidebar || !$("#tab-comments").classList.contains("active") : false) return;
      // Held down, only moving repeats: a held R would resolve one comment after another.
      if (event.repeat && !entry.repeats) return;
      if (rangeIds().length > 0) {
        if (entry.range === undefined && !entry.inRange) return;
        event.preventDefault();
        (entry.range ?? entry.run)();
        return;
      }
      event.preventDefault();
      entry.run();
      if (scope === "list") showLetters(true);
    });
    document.addEventListener("pointerdown", () => showLetters(false), true);
    document.addEventListener("pointermove", (event) => {
      if (event.pointerType === "mouse" && Math.abs(event.movementX) + Math.abs(event.movementY) > 2) showLetters(false);
    }, true);
    document.addEventListener("focusin", (event) => {
      if (eventOrigin(event).matches?.("textarea, select, [contenteditable], input:not([type=checkbox]):not([type=radio])")) {
        showLetters(false);
      }
    });
  }

  // The range let go, for a page that shows another document in the list's place.
  function reset() {
    rangeFrom = null;
  }

  return { table, hint, rangeIds, mark, placeRing, showLetters, attach, reset };
}
