// A thread's reply to Overleaf, and a new thread's first message: the box on its card that holds
// it, the agent's draft and the reviewer's words over it, and its send, from the card or for the
// picked threads from the row.
//
// createOverleafReplies takes state, the comment panel's (comments, picked and expanded);
// overleaf, the page's knowledge of the project, of which it reads cookie; panel
// (renderComments(), refreshComments(), renderPickedActions(), actionButton(name, text, onClick),
// mutate(id, action, body, options)); keyHint(name); and draftedThread(comment). It gives
// attach(), sending, the threads whose replies are on their way, text(comment), what a reply box
// holds, render(comment) and send(id, text, from).

import { $, h } from "../shared/dom.js";
import { leafIcon, replies, OVERLEAF_COOKIE_WANTED } from "./leaf.js";

export function createOverleafReplies({ state, overleaf, panel, keyHint, draftedThread }) {
  // The threads whose replies are on their way to Overleaf: their buttons show the work under way
  // until the list shows what it did.
  const beingSent = new Set();
  // What the reviewer has typed into a thread's Overleaf reply, kept across renders, with
  // the draft it started from and the reason a send was refused.
  const overleafReplies = new Map();

  // A reply sent to its thread on Overleaf: null once sent, or why not, with the files its
  // draft names that are not the same here as on Overleaf when that is the only reason.
  async function sendOverleafReply(id, text, anyway = false) {
    const response = await fetch(`/comments/${id}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, overleaf: true, anyway }),
    });
    if (response.ok) return null;
    const raw = await response.text();
    try {
      const { error, apart = [] } = JSON.parse(raw);
      return { error, apart };
    } catch (error) {
      return { error: `${response.status}: ${raw}`, apart: [] };
    }
  }

  // A thread's reply, or a new thread's first message, sent to Overleaf, from its card or from
  // the dialog that starts the thread. The
  // thread leaves the unanswered view at once, and elsewhere its card shows the
  // reply on its way until the list shows it sent, or why not: refused, the card keeps *text*
  // in its box, opened, over the draft *from*.
  async function sendToOverleaf(id, text, from) {
    beingSent.add(id);
    panel.renderComments();
    panel.refreshComments().catch((error) => console.error(error));
    try {
      let answer = await sendOverleafReply(id, text.trim());
      if (answer?.apart.length && confirm(`${answer.error} Send it anyway?`)) {
        answer = await sendOverleafReply(id, text.trim(), true);
      }
      if (answer === null) {
        overleafReplies.delete(id);
      } else {
        overleafReplies.set(id, { text, from, error: answer.error });
        state.expanded.add(id);
      }
    } catch (error) {
      overleafReplies.set(id, { text, from, error: `Could not send: ${error.message}` });
      state.expanded.add(id);
    } finally {
      beingSent.delete(id);
      await panel.refreshComments();
    }
  }

  // Each picked reply goes to its own thread on Overleaf, one after another. Those whose
  // files are not the same here as on Overleaf wait for one question at the end and go
  // anyway when it is answered yes. One that is refused keeps its words and says why on
  // its card, opened, and the rest still go.
  async function sendPickedToOverleaf(ids) {
    const waiting = [];
    const refused = (id, text, from, error) => {
      overleafReplies.set(id, { text, from, error });
      state.expanded.add(id);
    };
    for (const id of ids) {
      const comment = state.comments.find((item) => item.id === id);
      const text = comment ? overleafReplyText(comment) : "";
      if (!text.trim()) continue;
      const answer = await sendOverleafReply(id, text.trim());
      if (answer === null) {
        overleafReplies.delete(id);
        state.picked.delete(id);
      } else if (answer.apart.length) {
        waiting.push({ id, text, from: comment.overleaf_draft?.text, ...answer });
      } else {
        refused(id, text, comment.overleaf_draft?.text, answer.error);
      }
    }
    const files = [...new Set(waiting.flatMap((reply) => reply.apart))];
    if (waiting.length && confirm(`${files.join(", ")} ${files.length === 1 ? "is" : "are"} not the same here as on `
      + `Overleaf, so ${waiting.length === 1 ? "a reply" : replies(waiting.length)} may describe an edit Overleaf `
      + `does not have yet. Send ${waiting.length === 1 ? "it" : "them"} anyway?`)) {
      for (const reply of waiting) {
        const answer = await sendOverleafReply(reply.id, reply.text.trim(), true);
        if (answer === null) {
          overleafReplies.delete(reply.id);
          state.picked.delete(reply.id);
        } else {
          refused(reply.id, reply.text, reply.from, answer.error);
        }
      }
    } else {
      for (const reply of waiting) refused(reply.id, reply.text, reply.from, reply.error);
    }
    await panel.refreshComments();
  }


  // The thread's Overleaf reply: the agent's draft, or the first message the reviewer wrote,
  // which the reviewer edits and sends. The server sends it only once the words its draft
  // describes are on Overleaf, and says why not.
  // What a thread's reply box for Overleaf holds: what the reviewer typed, else the draft. What
  // they typed stays when the agent writes a new draft, which the card then offers beside it.
  function overleafReplyText(comment) {
    const kept = overleafReplies.get(comment.id);
    if (kept && kept.from !== comment.overleaf_draft?.text && kept.text === kept.from) overleafReplies.delete(comment.id);
    return overleafReplies.get(comment.id)?.text ?? comment.overleaf_draft?.text ?? "";
  }

  function renderOverleafReply(comment) {
    const draft = comment.overleaf_draft;
    const textarea = h("textarea", {
      class: "cmt-form-input overleaf-reply-input", rows: 3,
      placeholder: draftedThread(comment) ? "The thread's first public message on Overleaf, for everyone in the project…"
        : "Public reply on Overleaf, for everyone in the project…",
    });
    textarea.value = overleafReplyText(comment);
    const kept = overleafReplies.get(comment.id);
    // A draft the agent wrote after the reviewer began typing, which the box does not show.
    const newer = kept && draft && kept.from !== draft.text ? draft.text : null;
    textarea.addEventListener("input", () => {
      overleafReplies.set(comment.id, { text: textarea.value, from: overleafReplies.get(comment.id)?.from ?? draft?.text });
      panel.renderPickedActions();
    });
    // Escape leaves the box with its words in it, for the comment keys to go on.
    textarea.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      $("#comments-list").focus({ preventScroll: true });
    });
    const sending = beingSent.has(comment.id);
    textarea.disabled = sending;
    // Send waits for a cookie, which the note says when nothing else is to be said.
    const note = h("div", { class: "overleaf-reply-note", role: "status",
      text: sending ? "Sending…" : kept?.error ?? (overleaf.cookie ? "" : OVERLEAF_COOKIE_WANTED) });
    const send = h("button", {
      class: `overleaf-send${sending ? " busy" : ""}`, type: "button",
      title: overleaf.cookie ? `Send to Overleaf as a public reply, for everyone in the project${keyHint("overleaf-send")}`
        : OVERLEAF_COOKIE_WANTED,
    }, "Send to ", leafIcon());
    send.disabled = !overleaf.cookie || sending;
    send.addEventListener("click", () => {
      if (textarea.value.trim()) sendToOverleaf(comment.id, textarea.value, draft?.text);
    });
    // The agent's draft, turned down: it leaves the card with what was typed over it, and
    // the thread says what it was. Nothing goes to Overleaf, where it never was. A new thread
    // a send left half opened there is finished by sending. A first message the reviewer wrote
    // is theirs, not a draft to reject.
    let reject = null;
    if (draft && !draft.mine && !draft.thread && comment.status === "open") {
      reject = panel.actionButton("overleaf-reject", "Reject draft", async () => {
        reject.disabled = true;
        const saved = () => overleafReplies.delete(comment.id);
        if (!await panel.mutate(comment.id, "reject-draft", { updated: comment.updated }, { saved })) reject.disabled = false;
      });
      reject.disabled = sending;
    }
    return h("div", { class: "cmt-overleaf" },
      h("div", { class: "cmt-overleaf-head" }, leafIcon(),
        h("span", { text: draftedThread(comment) ? "New public thread on Overleaf"
          : draft ? "Draft of a public reply" : "Public reply on Overleaf" })),
      textarea,
      newer ? h("div", { class: "overleaf-newer" },
        h("p", { text: "The agent has written a new draft:" }),
        h("blockquote", { text: newer }),
        panel.actionButton("overleaf-use", "Use new draft", () => {
          overleafReplies.delete(comment.id);
          panel.renderComments();
        })) : null,
      draft?.ranges?.length
        ? h("div", { class: "thread-edits" }, ...draft.ranges.map(draftRange))
        : null,
      note,
      h("div", { class: "cmt-form-actions" }, reject, send));
  }

  // The lines a draft describes, where they are now as a comment's are. A range not found has
  // no line to name, and one whose lines were edited after the draft was written says so.
  function draftRange(range) {
    return h("span", { class: "edit", text: range.range },
      range.stale ? h("span", { class: "stale", text: "STALE", title: "These lines are not found in the file any more" }) : null,
      range.edited ? h("span", { class: "reanchored", text: "EDITED", title: "These lines were edited after the draft was written" }) : null);
  }

  function attach() {
    $("#send-picked-btn").addEventListener("click", async () => {
      const button = $("#send-picked-btn");
      const ids = button.dataset.ids ? button.dataset.ids.split(" ") : [];
      if (ids.length === 0 || !confirm(`Send ${replies(ids.length)} to Overleaf, for everyone in the project?`)) return;
      for (const id of ids) beingSent.add(id);
      panel.renderComments();
      panel.refreshComments().catch((error) => console.error(error));
      try {
        await sendPickedToOverleaf(ids);
      } catch (error) {
        alert(`Could not send: ${error.message}`);
      } finally {
        for (const id of ids) beingSent.delete(id);
        await panel.refreshComments();
      }
    });
  }

  return { attach, sending: beingSent, text: overleafReplyText, render: renderOverleafReply, send: sendToOverleaf };
}
