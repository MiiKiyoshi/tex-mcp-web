// The edits suggested on Overleaf, with track changes on, in the leaf Comments tab: each as the list
// holds it (suggestedItem), its card, accepted or rejected on Overleaf from it or for the picked
// ones from the row, and read again now and then.
//
// createSuggestedEdits takes overleaf, the page's knowledge of the project (overleaf-project.js),
// of which it reads project(); panel, the comment panel (listed(), picked(), renderComments(),
// refreshComments(), expand(id, open), ring(id), unpick(ids), pickBox(item), actionButton(name,
// text, onClick)); and jumpToComment(item), which shows an edit in the source. It gives attach(),
// reviewing, the edits being accepted or rejected now, render(item, { open, ringed, inRange }),
// the card of an edit, and read().

import { $, h } from "../shared/dom.js";
import { shortTime } from "../shared/comment-card.js";
import { responseError } from "../shared/comment-api.js";
import { leafIcon, overleafColor } from "./leaf.js";

// A suggested edit as the list holds it, beside the comments: open until accepted or rejected,
// with nothing said in it, and its place in the source, or its line on Overleaf when the text
// around it is not in the file here.
export function suggestedItem(edit) {
  const place = edit.here ?? { line_start: edit.line, line_end: edit.line, column_start: 0, column_end: 0 };
  return { ...edit, suggested: true, status: "open", thread: [], anchor: { kind: "suggested" },
           resolved_source: { file: edit.file, ...place } };
}

export function createSuggestedEdits({ overleaf, panel, jumpToComment }) {
  // The suggested edits being accepted or rejected now, and when they were last read.
  const reviewingEdits = new Set();
  let suggestedRead = 0;

  // A suggested edit's card, given whether it is open, ringed and in the keys' range: who
  // suggested it and when, its words as a proposal's − and + lines show them, and Accept and
  // Reject, which act on Overleaf for everyone in the project. A click shows it in the source, and
  // opens long words in full.
  function renderSuggestedEdit(item, { open: expanded, ringed, inRange }) {
    const show = (event) => {
      event.stopPropagation();
      panel.expand(item.id, !expanded);
      panel.ring(item.id);
      jumpToComment(item).catch((error) => console.error(error));
    };
    const where = `${item.file}:${item.here ? item.here.line_start : item.line}`;
    const head = h("div", { class: "cmt-head", title: "Show it in the source", onclick: show },
      panel.pickBox(item),
      h("span", { class: "suggested-by", style: { borderColor: overleafColor(item.user_id) }, text: item.name || "Someone" }),
      shortTime(item.at) ? h("span", { class: "cmt-when", text: shortTime(item.at) }) : null,
      h("span", { class: "cmt-anchor", text: where }));
    const line = (kind, marker, text) => h("div", { class: kind },
      h("span", { class: "sugg-marker", text: marker }), h("span", { class: "sugg-text", text }));
    const words = h("div", { class: `cmt-suggestion suggested-words${expanded ? "" : " clamped"}`, onclick: show },
      item.deleted ? line("sugg-old", "−", item.deleted) : null,
      item.inserted ? line("sugg-new", "+", item.inserted) : null);
    const busy = reviewingEdits.has(item.id);
    const press = (action) => () => reviewSuggestedEdits(action, [item]).catch((error) => alert(error.message));
    const accept = panel.actionButton("change-accept", "Accept", press("accept"));
    const reject = panel.actionButton("change-reject", "Reject", press("reject"));
    for (const button of [accept, reject]) {
      button.disabled = busy;
      button.classList.toggle("busy", busy);
    }
    return h("div", {
      class: `cmt suggested-edit${ringed ? " is-focused" : ""}${inRange ? " in-range" : ""}`,
      data: { commentId: item.id },
    }, head, words,
    item.here ? null : h("div", { class: "cmt-unplaced" }, leafIcon(), h("span", {
      text: `On Overleaf at ${item.file}:${item.line}. The text around it is not in the file here yet.`,
    })),
    h("div", { class: "cmt-actions" }, accept, reject));
  }

  // Overleaf's suggested edits read again when the reviewer comes back to the page or opens their
  // view, at most every ten seconds: an edit suggested in a file the page does not follow live
  // is not told to it. The list changes once the reading finds something new.
  function readSuggestedEdits() {
    if (!overleaf.project() || Date.now() - suggestedRead < 10000) return;
    suggestedRead = Date.now();
    fetch("/overleaf/changes/read", { method: "POST" }).catch((error) => console.error(error));
  }

  // Accept or reject suggested edits on Overleaf, the edits of one file in one request.
  async function reviewSuggestedEdits(action, items) {
    const docs = new Map();
    for (const item of items) docs.set(item.doc, [...(docs.get(item.doc) ?? []), item]);
    for (const item of items) reviewingEdits.add(item.id);
    panel.renderComments();
    try {
      for (const [doc, edits] of docs) {
        const response = await fetch(`/overleaf/changes/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ doc, ids: edits.flatMap((item) => item.ids) }),
        });
        if (!response.ok) throw new Error(await responseError(response));
        panel.unpick(edits.map((item) => item.id));
      }
    } finally {
      for (const item of items) reviewingEdits.delete(item.id);
      await panel.refreshComments();
    }
  }

  function attach() {
    for (const [id, action, word] of [["#accept-picked-btn", "accept", "Accept"], ["#reject-picked-btn", "reject", "Reject"]]) {
      $(id).addEventListener("click", () => {
        const picked = panel.picked();
        const edits = panel.listed().filter((item) => item.suggested && picked.includes(item.id));
        if (edits.length === 0 || !confirm(`${word} ${edits.length} suggested edit${edits.length === 1 ? "" : "s"} `
          + "on Overleaf, for everyone in the project?")) return;
        reviewSuggestedEdits(action, edits).catch((error) => alert(error.message));
      });
    }
  }

  return { attach, reviewing: reviewingEdits, render: renderSuggestedEdit, read: readSuggestedEdits };
}
