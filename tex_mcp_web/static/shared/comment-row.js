// The comment panel's row above the list (comment-panel.js): what acts on the picked comments,
// counted for what each press touches; the row fitted to its width, what has no room waiting
// behind the dots; Expand all and Select all; and the box on each card that picks it.
//
// createCommentRow takes state (comment-state.js); host, the page's hooks; the panel's tab();
// bellSteps, the bell's steps as the row gives way (BELL_STEPS in agent-bell.js), or none; of
// the views, view(); of the list, renderComments(); and of the status, setStatus(ids, status). It
// gives renderPickedActions(); renderFold(), Expand all drawn for the cards; fit(), the row
// fitted; closeCommentRare(), the dots closed; foldAll(); pickBox(comment); and attach().
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, $$, h } from "./dom.js";

// How the row gives way, one step at a time while it would spill over: the sort list shrinks
// to its icon, Resolve's word goes, the sort list waits behind the dots as the gaps narrow,
// then Expand all, and the view list gives up the room its longest view needs. Last the view
// list shrinks to its icon, with the list lying over it. Resolve and Archive stay in the row,
// which takes two lines only when even that is too wide. Each step names what then waits
// behind the dots. The bell's steps (BELL_STEPS) and then the page's own come in after the step
// they name.
const ROW_STEPS = [["sort-shrunk"], ["resolve-wordless"], ["narrow", "#comment-sort-pick"],
  ["foldless", "#fold-all-btn"], ["squeezed"], ["view-shrunk"], ["wrapped"]];

export function createCommentRow({ state, host, tab, bellSteps, view, renderComments, setStatus }) {
  // The row's steps, each followed by the bell's after it.
  const rowSteps = ROW_STEPS.flatMap((step) => [step, ...(bellSteps[step[0]] ?? [])]);
  // The page's elements the row sent behind the dots, each with where it stood, in the order
  // they went, so the row puts them back before it is fitted again.
  let moved = [];

  // What the picked comments can be sent to, which is decided by the state they are in:
  // open ones close, unarchived ones archive, and closed ones reopen. The counts say
  // exactly what each press will touch.
  function renderPickedActions() {
    const comments = state.listed();
    const picked = comments.filter((comment) => state.isPicked(comment.id));
    const settled = picked.filter((comment) => host.row.settles(comment));
    const open = settled.filter((comment) => comment.status === "open").map((comment) => comment.id);
    const archivable = !tab().archives ? []
      : settled.filter((comment) => comment.status !== "archived").map((comment) => comment.id);
    const closed = settled.filter((comment) => comment.status !== "open").map((comment) => comment.id);
    const noun = tab().noun;
    // The box is checked when every comment listed is picked, and half checked when some are.
    const shown = comments.length;
    const pickedShown = picked.length;
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
      && (closed.length > 0 || ["resolved", "archived"].includes(view()));
    const word = reopening ? "Reopen" : "Resolve";
    const acted = reopening ? closed : open;
    const resolve = $("#resolve-picked-btn");
    resolve.disabled = acted.length === 0;
    resolve.querySelector(".icon-resolve").classList.toggle("hidden", reopening);
    resolve.querySelector(".icon-reopen").classList.toggle("hidden", !reopening);
    resolve.querySelector(".wide-label").textContent = word;
    resolve.querySelector(".count").textContent = acted.length > 0 ? String(acted.length) : "";
    resolve.setAttribute("aria-label", acted.length > 0 ? `${word} ${acted.length}` : word);
    resolve.title = `${word} the selected ${noun}s`;
    resolve.dataset.status = reopening ? "open" : "resolved";
    resolve.dataset.ids = acted.join(" ");
    const archive = $("#archive-picked-btn");
    archive.disabled = archivable.length === 0;
    archive.querySelector(".count").textContent = archivable.length > 0 ? String(archivable.length) : "";
    archive.setAttribute("aria-label", archivable.length > 0 ? `Archive ${archivable.length}` : "Archive");
    archive.dataset.ids = archivable.join(" ");
    resolve.hidden = host.row.render(settled, { picked, listed: comments, view: view(), tab: state.tab() })
      ?.resolve === false;
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
    const steps = rowSteps.flatMap((step) => [step, ...(host.row.steps[step[0]] ?? [])]);
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
    const shown = state.listed().map((comment) => comment.id);
    const button = $("#fold-all-btn");
    button.disabled = shown.length === 0;
    const folding = shown.length > 0 && shown.every((id) => state.isOpen(id));
    button.querySelector(".icon-expand").classList.toggle("hidden", folding);
    button.querySelector(".icon-collapse").classList.toggle("hidden", !folding);
    button.setAttribute("aria-label", folding ? "Collapse all" : "Expand all");
    button.title = `${folding ? "Collapse" : "Expand"} all ${tab().noun}s in this view`;
  }

  function foldAll() {
    const comments = state.listed();
    const open = !comments.every((comment) => state.isOpen(comment.id));
    for (const comment of comments) state.setOpen(comment.id, open);
    renderComments();
  }

  function pickAll() {
    const shown = state.listed().map((comment) => comment.id);
    state.pick(shown, !shown.every((id) => state.isPicked(id)));
    renderComments();
  }

  // The box that picks a card, in its head, which opens the card: picking is not opening.
  function pickBox(comment) {
    const noun = tab().noun;
    const box = h("input", { class: "comment-pick", type: "checkbox",
      title: `Select this ${noun}. Shift+click selects or clears every ${noun} from the last one you clicked.` });
    box.checked = state.isPicked(comment.id);
    // A checkbox has already taken its new value by the time the click arrives, so the
    // whole decision is made here and nothing listens for the change.
    box.addEventListener("click", (event) => {
      event.stopPropagation();
      const shown = state.listed().map((item) => item.id);
      const from = shown.indexOf(state.pickedFrom());
      const to = shown.indexOf(comment.id);
      // Shift runs from the box the last pick started at to this one, both ends included,
      // and gives the whole run the value this box just took.
      const run = event.shiftKey && from !== -1 && to !== -1
        ? shown.slice(Math.min(from, to), Math.max(from, to) + 1)
        : [comment.id];
      state.pick(run, box.checked);
      state.pickFrom(comment.id);
      renderComments();
    });
    return box;
  }

  // The row's buttons wired, and the row fitted again whenever it, or one of its groups, changes
  // width.
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
        if (ids.length === 0
          || !host.row.confirm(button, state.listed().filter((comment) => ids.includes(comment.id)), status)) return;
        button.disabled = true;
        await setStatus(ids, status);
        renderPickedActions();
      });
    }
  }

  return { renderPickedActions, renderFold: renderFoldAction, fit: fitCommentRow, closeCommentRare, foldAll, pickBox, attach };
}
