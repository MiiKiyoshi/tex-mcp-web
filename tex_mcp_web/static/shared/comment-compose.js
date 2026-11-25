// The comment panel's dialog for a new comment (comment-panel.js): opened on what the comment is
// on, the page's own boxes readied and the panel's words set as the page changes them, each box
// as tall as what is typed in it, and the comment posted, or its refusal said.
//
// createCommentCompose takes state (comment-state.js); host, the page's hooks; api
// (comment-api.js); bell, the bell or null; and of the list, refreshComments(). It gives
// openCompose(anchor, label, fields, words) and attach().
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, $$ } from "./dom.js";

export function createCommentCompose({ state, host, api, bell, refreshComments }) {
  // What a new comment is on, what the page posts with it, whether it is being posted, and what
  // was written in one the server turned away for now, for the next comment box opened.
  let pendingAnchor = null;
  let composeFields = {};
  let composeSubmitting = false;
  let keptComment = "";

  // The dialog for a new comment on *anchor*, which *label* names, posted with *fields*, what the
  // page posts beside it. The page readies its own parts of the dialog (compose.opening), and the
  // panel sets its own words, with what the page changes of them. The box starts with *words*,
  // unless a refused post left words in it, which come back instead, and the first box shown
  // takes the keys.
  function openCompose(anchor, label, fields = {}, words = "") {
    pendingAnchor = anchor;
    composeFields = fields;
    const text = $("#compose-text");
    const own = host.compose.opening(fields) ?? {};
    const box = { placeholder: host.labels.composeBox, rows: 6, required: true, ...own.box };
    $("#compose-title").textContent = own.title ?? host.labels.composeTitle;
    text.placeholder = box.placeholder;
    text.rows = box.rows;
    text.required = box.required;
    $("#compose-anchor").textContent = label;
    $("#compose-submit").title = `Post (${host.settings.sendKeyName()})`;
    text.value = keptComment || words;
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
  // An answer that comes back after the panel was reset closes the dialog it was posted from, or
  // says the refusal, and marks nothing on the document shown now.
  async function submitCompose(event) {
    event.preventDefault();
    if (composeSubmitting) return;
    const text = $("#compose-text").value.trim();
    const filled = host.compose.filled(composeFields);
    if (!(text || Object.keys(filled).length > 0) || !pendingAnchor) return;
    const body = { anchor: pendingAnchor, text, ...composeFields, ...filled };

    applyComposeSubmitting(true);
    const from = state.generation();
    let started;
    try {
      const answer = await api.create(body);
      if (!answer.ok) {
        const told = from === state.generation() ? host.compose.refused(answer, body) : null;
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
    if (from !== state.generation()) return;
    // A note for the agent waits for it, and the line above the list may say how to call it.
    if (text) bell?.posted();
    await host.compose.posted(started, body);
    await refreshComments();
  }

  // The dialog's boxes and buttons wired.
  function attach() {
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
  }

  return { openCompose, attach };
}
