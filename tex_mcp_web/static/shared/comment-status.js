// The comment panel's changes of status (comment-panel.js): comments sent to a status in one
// call, as the row, the cards, the keys and Undo all send them, and the line above the list that
// says what the last change did and offers to undo it.
//
// createCommentStatus takes state (comment-state.js); host, the page's hooks; api
// (comment-api.js); the panel's tab() and statusOf(comment); keys, the comment keys'
// hint(button); and of the list, refreshComments(). It gives setStatus(ids, status),
// undoStatus(), attach() and reset().
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $ } from "./dom.js";

const STATUS_DONE = { resolved: "Resolved", open: "Reopened", archived: "Archived" };

function counted(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export function createCommentStatus({ state, host, api, tab, statusOf, keys, refreshComments }) {
  // The last change of status made on the page, which the line above the list offers to undo:
  // what the line says, the status each comment it changed had before and when one resolved
  // was resolved, why some could not be put back, and how many no longer exist. Null when
  // there is nothing to undo.
  let undo = null;

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
    const changing = state.listed().filter((comment) => ids.includes(comment.id) && statusOf(comment) !== status);
    const before = new Map(changing.map((comment) => [comment.id, statusOf(comment)]));
    const resolved = Object.fromEntries(changing.filter((comment) => comment.status === "resolved" && comment.resolved)
      .map((comment) => [comment.id, comment.resolved]));
    const noun = tab().noun;
    const from = state.generation();
    try {
      const { took, why } = await postStatus(ids, status);
      if (from !== state.generation()) return;
      const changed = took.filter((id) => before.has(id));
      if (changed.length > 0) {
        const what = counted(changed.length, noun);
        const said = host.status.said(status, changed.map((id) => before.get(id)), what) ?? `${STATUS_DONE[status]} ${what}.`;
        undo = { said, noun, why: "", gone: 0, before: new Map(changed.map((id) => [id, before.get(id)])), resolved };
      }
      if (!why) state.pick(ids, false);
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
    const from = state.generation();
    try {
      const answer = await api.list("all");
      if (from !== state.generation()) return;
      if (answer.ok) {
        const present = new Set(answer.data.comments.map((comment) => comment.id));
        for (const id of [...left.keys()].filter((id) => !present.has(id))) {
          left.delete(id);
          gone += 1;
        }
      }
    } catch { /* the calls below say why they fail */ }
    for (const status of new Set(left.values())) {
      if (from !== state.generation()) return;
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
    if (from !== state.generation()) return;
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

  // Undo and the line's close button wired.
  function attach() {
    $("#undo-btn").title = `Undo the last status change${keys.hint("undo-go")}`;
    $("#undo-btn").addEventListener("click", () => undoStatus().catch((error) => console.error(error)));
    $("#undo-close").addEventListener("click", () => {
      undo = null;
      renderUndo();
    });
  }

  // The last change let go of, for a panel that shows another document: it was made to the
  // comments of the one before.
  function reset() {
    undo = null;
    renderUndo();
  }

  return { setStatus, undoStatus, attach, reset };
}
