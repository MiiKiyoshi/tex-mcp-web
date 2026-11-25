// A comment's card in the panel's list (comment-panel.js): its head, the words it is on, its
// thread, drawn as markdown (markdown.js), with Edit and Delete on what the reviewer wrote, its
// suggestion with Apply and Reject, its buttons, its reply box, and the change a button makes to
// the comment (mutate). It exports shortTime(at), when something was written as a card says it.
//
// createCommentCard takes state (comment-state.js); host, the page's hooks; api
// (comment-api.js); the panel's statusOf(comment) and unanswered(comment); keys, the comment
// keys' hint(button); bell, the bell or null; of the views, view(); of the list,
// renderComments() and refreshComments(); of the row, pickBox(comment); and of the status,
// setStatus(ids, status). It gives card(comment, { open, ringed, inRange }); actionButton(class,
// label, onclick); replyButton(comment); mutate(id, action, body, { refused, saved });
// closeReply(), whether a reply box was open; boxOf(box), what finds a box again in its card;
// and reset().
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, h } from "./dom.js";
import { renderMarkdown, markdownToText } from "./markdown.js";

// When something was written, on this browser's clock, as MM-DD HH:MM: the clock that after:
// and before: in the search name their days and minutes on. "" for a time that cannot be read.
export function shortTime(at) {
  const moment = new Date(at ?? "");
  if (Number.isNaN(moment.getTime())) return "";
  const two = (value) => String(value).padStart(2, "0");
  return `${two(moment.getMonth() + 1)}-${two(moment.getDate())} ${two(moment.getHours())}:${two(moment.getMinutes())}`;
}

// An entry that carries its writer's name goes by it, as where it was written.
function writer(entry) {
  return entry.name ?? entry.author;
}

// What an entry says, drawn as its markdown, and as the words alone for a closed card. The
// records of applying and rejecting a suggestion quote the file's own text, and stay as stored.
function drawnWords(entry) {
  return entry.applied || entry.rejected ? entry.text : renderMarkdown(entry.text);
}

function plainWords(entry) {
  return entry.applied || entry.rejected ? entry.text : markdownToText(entry.text);
}

export function createCommentCard({
  state, host, api, statusOf, unanswered, keys, bell, view, renderComments, refreshComments, pickBox, setStatus,
}) {
  // The reply box open on a card, and the entry being edited, each with what is typed in it.
  let activeForm = null;
  let editingEntry = null;
  // Why applying a thread's suggestion was refused, with the suggestion it was refused for.
  const unfitSuggestions = new Map();

  // The words a comment is on, quoted on its card once opened, cut to a few lines.
  function renderQuote(comment) {
    const quote = host.quote(comment);
    return quote ? h("div", { class: "anchor-quote", text: quote.length > 180 ? `${quote.slice(0, 177)}…` : quote }) : null;
  }

  // A comment's card, given whether it is open, ringed and in the keys' range.
  function renderCommentItem(comment, { open: expanded, ringed, inRange }) {
    const stale = host.stale(comment);
    const replies = comment.thread.length - 1;
    const box = pickBox(comment);
    const toggle = (event) => {
      event.stopPropagation();
      state.setOpen(comment.id, !expanded);
      renderComments();
      host.showPlace(comment);
    };
    const head = h("div", {
      class: "cmt-head",
      title: expanded ? "Collapse thread" : "Expand thread",
      onclick: toggle,
    },
    box,
    h("span", {
      class: "cmt-toggle",
      text: expanded ? "▾" : replies > 0 ? `▸ ${replies} repl${replies > 1 ? "ies" : "y"}` : "▸",
    }),
    h("span", { class: "cmt-id", text: comment.id }),
    h("span", { class: "cmt-status", text: `[${statusOf(comment)}]` }),
    // Outside the unanswered view, an unanswered card says so, so it stands out in the others.
    unanswered(comment) && view() !== "unanswered"
      ? h("span", { class: "cmt-unanswered", text: "unanswered", title: "Waiting for your response" }) : null,
    stale ? h("span", { class: "stale", text: "STALE", title: "The text this comment is on has changed or can no longer be found" }) : null,
    !stale && comment.reanchored
      ? h("span", { class: "reanchored", text: "EDITED", title: "The commented text was edited; the highlight follows the edited text" })
      : null,
    h("span", { class: "cmt-anchor", text: host.label(comment.anchor) }));

    const children = [head, expanded ? renderQuote(comment) : null, host.card.note(comment, expanded)];
    if (!expanded && comment.thread.length > 0) {
      // A thread whose first entry carries its writer's name names who opened it, since it may
      // be anyone.
      const first = comment.thread[0];
      children.push(h("div", { class: "cmt-preview",
                               text: first.name !== undefined ? `${first.name}  ${plainWords(first)}` : plainWords(first) }));
      // The request, then whatever was last said about it: a thread someone has answered
      // must not read like one nobody has touched, and who answered has to be visible.
      // A long answer is cut to three lines here and opens in full on a click, so nothing
      // it says is only half shown with no way to reach the rest.
      const latest = comment.thread[comment.thread.length - 1];
      if (comment.thread.length > 1) {
        children.push(h("div", { class: `cmt-latest author-${latest.author}`,
                                 title: "Expand thread",
                                 onclick: toggle,
                                 text: `${writer(latest)}  ${plainWords(latest)}` }));
      }
    }
    if (expanded) {
      // The thread reads downward, oldest first, the way it was written.
      children.push(
        h("div", { class: "cmt-thread" },
          ...comment.thread.map((entry, index) => renderThreadEntry(entry, comment, index))));
    }
    // The proposal sits under the conversation, where the card ends. A thread carries one,
    // so this is never a list. So does a reply box the page adds.
    if (comment.suggestion) children.push(renderSuggestion(comment));
    children.push(host.card.reply(comment, expanded));
    if (expanded) {
      children.push(h("div", { class: "cmt-actions" }, ...actionButtons(comment)));
      children.push(renderReplyForm(comment));
    }
    return h("div", {
      class: `cmt status-${statusOf(comment)}${ringed ? " is-focused" : ""}${inRange ? " in-range" : ""}`,
      data: { commentId: comment.id },
    }, ...children);
  }

  function renderSuggestion(comment) {
    const changes = comment.suggestion?.changes ?? [];
    // The proposal names its own file, so it applies whatever the comment points at.
    let applyButton = null;
    let rejectButton = null;
    if (changes.length > 0 && comment.status === "open") {
      // Rejected, the proposal leaves the card and the thread says what was turned down,
      // which the agent hears once. Why is said in a reply.
      rejectButton = actionButton("sugg-reject", "Reject", async () => {
        rejectButton.disabled = true;
        if (!await mutate(comment.id, "reject-suggestion", { updated: comment.updated })) rejectButton.disabled = false;
      });
      applyButton = actionButton("sugg-apply", "Apply suggestion", async () => {
        applyButton.disabled = true;
        applyButton.textContent = "Applying…";
        // One the file no longer fits says so on the card, under the proposal.
        const refused = (why) => {
          unfitSuggestions.set(comment.id, { suggestion: JSON.stringify(comment.suggestion), why });
          renderComments();
        };
        if (await mutate(comment.id, "apply-suggestion", { updated: comment.updated }, { refused })) return;
        applyButton.disabled = false;
        applyButton.textContent = "Apply suggestion";
      });
    }
    // A refusal stays with the suggestion it was for: a new one from the agent is tried afresh.
    const unfit = unfitSuggestions.get(comment.id);
    // One pair per piece the proposal changes; the text it leaves alone is not shown,
    // because it is not part of the proposal.
    return h("div", { class: "cmt-suggestion" },
      ...changes.flatMap((change) => [
        h("div", { class: "sugg-old", title: "current text" },
          h("span", { class: "sugg-marker", text: "−" }),
          h("span", { class: "sugg-text", text: change.old })),
        h("div", { class: "sugg-new", title: "suggested replacement" },
          h("span", { class: "sugg-marker", text: "+" }),
          h("span", { class: "sugg-text", text: change.new })),
      ]),
      unfit?.suggestion === JSON.stringify(comment.suggestion)
        ? h("p", { class: "sugg-unfit", role: "status", text: "The file has changed since this was suggested, so it no longer applies. "
          + `Reject it, or reply to ask the agent for a new one. (${unfit.why})` })
        : null,
      applyButton ? h("div", { class: "sugg-actions" }, rejectButton, applyButton) : null,
    );
  }

  // What the reviewer wrote and may change: not the records of applying and rejecting, and of
  // the rest what the page says they may.
  function ownEntry(entry, comment) {
    if (entry.author !== "human" || entry.applied || entry.rejected) return false;
    return host.card.mayChange(entry, comment);
  }

  function renderThreadEntry(entry, comment, index) {
    const commentId = comment.id;
    const editing = editingEntry?.commentId === commentId && editingEntry.index === index;
    const children = [
      h("div", { class: "thread-meta" },
        h("span", { text: `${writer(entry)} · ${shortTime(entry.at)}` }),
        host.card.entryMark(entry),
        // What the reviewer wrote carries Edit and Delete. A thread's first message goes with
        // its thread. The records of applying and rejecting stay.
        !editing && ownEntry(entry, comment)
          ? h("span", { class: "entry-actions" },
            actionButton("cmt-edit", "Edit", () => startEntryEdit(commentId, index, entry.text)),
            actionButton("entry-delete", "Delete", async () => {
              const asked = host.card.deleteQuestion(entry, index,
                index === 0 ? `Permanently delete ${commentId}?` : "Delete this message?");
              if (confirm(asked)) await mutate(commentId, "delete-entry", { index });
            }))
          : null),
    ];
    if (editing) children.push(renderEntryEditor(commentId, index));
    else if (entry.text) children.push(h("div", { class: "thread-text" }, drawnWords(entry)));
    if (entry.edits?.length) {
      children.push(h("div", { class: "thread-edits" },
        ...entry.edits.map((edit) => h("span", { class: "edit", text: edit }))));
    }
    return h("div", { class: `thread-entry author-${entry.author}` }, ...children);
  }

  function startEntryEdit(commentId, index, text) {
    editingEntry = { commentId, index, draft: text };
    renderComments();
    const node = Array.from($("#comments-list").children).find(
      (child) => child.dataset.commentId === commentId,
    );
    node?.querySelector(".entry-edit-input")?.focus({ preventScroll: true });
  }

  // Editing rewrites the entry in place: the author and time stay, so a typo fix
  // does not read as a new message in the thread.
  function renderEntryEditor(commentId, index) {
    const editing = editingEntry;
    const textarea = h("textarea", { class: "cmt-form-input entry-edit-input", rows: 3 });
    textarea.value = editing.draft;
    textarea.addEventListener("input", () => {
      if (editingEntry === editing) editing.draft = textarea.value;
    });
    const save = async () => {
      const text = textarea.value.trim();
      if (!text) return;
      textarea.disabled = true;
      if (await mutate(commentId, "edit", { index, text }, { saved: () => { editingEntry = null; } })) return;
      textarea.disabled = false;
      textarea.focus();
    };
    const cancel = () => {
      editingEntry = null;
      renderComments();
    };
    host.settings.sendOn(textarea, save);
    textarea.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      cancel();
      $("#comments-list").focus({ preventScroll: true });
    });
    return h("div", { class: "cmt-form mode-edit" }, textarea,
      h("div", { class: "cmt-form-actions" },
        actionButton("cmt-form-cancel", "Cancel", cancel),
        actionButton("cmt-form-submit", "Save", save)));
  }

  function actionButton(className, label, onclick) {
    const hint = keys.hint(className);
    return h("button", { class: className, type: "button", text: label, onclick, title: hint ? `${label}${hint}` : null });
  }

  // The button that opens a card's reply box.
  function replyButton(comment) {
    return actionButton("cmt-reply", host.labels.reply, () => openReply(comment.id));
  }

  function actionButtons(comment) {
    const deleteButton = actionButton("cmt-delete", "Delete", () => {
      if (confirm(`Permanently delete ${comment.id}?`)) mutate(comment.id, "delete", null);
    });
    // Every status flip is one click, made as the row's buttons make it: the thread already holds
    // what was said, so the status flips without adding an entry. An archived thread still
    // takes replies, and goes back to open or resolved from the same row.
    const buttons = [];
    if (comment.status !== "resolved") buttons.push(replyButton(comment));
    if (comment.status !== "open") {
      buttons.push(actionButton("cmt-reopen", "Reopen", () => setStatus([comment.id], "open")));
    }
    if (comment.status !== "resolved") {
      buttons.push(actionButton("cmt-resolve", "Resolve", () => closeComment(comment.id)));
    }
    if (comment.status !== "archived") {
      buttons.push(actionButton("cmt-archive", "Archive", () => setStatus([comment.id], "archived")));
    }
    buttons.push(deleteButton);
    return host.card.actions(comment, buttons);
  }

  // A card can hold several boxes, one the page adds above the one being written in a form
  // among them, so a box is found again by which it is, never as the card's first: one in a
  // form by the form's mode, any other by its own class.
  function boxOf(input) {
    const mode = [...(input.closest(".cmt-form")?.classList ?? [])].find((name) => name.startsWith("mode-"));
    return mode ? `.cmt-form.${mode} .cmt-form-input` : `.${[...input.classList].find((name) => name !== "cmt-form-input")}`;
  }

  // The reply box opened on a comment's card, which takes the keys.
  function openReply(commentId) {
    activeForm = { commentId, draft: "" };
    state.setOpen(commentId, true);
    renderComments();
    const commentNode = Array.from($("#comments-list").children).find(
      (node) => node.dataset.commentId === commentId,
    );
    const input = commentNode?.querySelector(".cmt-form.mode-reply .cmt-form-input");
    if (!input) return;
    input.closest(".cmt-form").scrollIntoView({ block: "nearest", behavior: "smooth" });
    input.focus({ preventScroll: true });
  }

  // The reply box closed with what was typed in it. Whether one was open.
  function closeReply() {
    if (activeForm === null) return false;
    activeForm = null;
    renderComments();
    return true;
  }

  async function closeComment(commentId) {
    if (activeForm?.commentId === commentId) activeForm = null;
    await setStatus([commentId], "resolved");
  }

  function renderReplyForm(comment) {
    if (activeForm?.commentId !== comment.id) return null;
    const form = activeForm;
    const textarea = h("textarea", { class: "cmt-form-input", rows: 3, placeholder: host.labels.replyBox });
    textarea.value = form.draft;
    textarea.addEventListener("input", () => {
      if (activeForm === form) form.draft = textarea.value;
    });
    let submitting = false;
    const submit = async () => {
      const text = textarea.value.trim();
      if (!text || submitting) return;
      submitting = true;
      textarea.disabled = true;
      submitButton.disabled = true;
      submitButton.textContent = "Saving…";
      const saved = () => {
        activeForm = null;
        bell?.posted();
      };
      if (await mutate(comment.id, "reply", { text }, { saved })) {
        // The box is gone with what was typed in it: the keys go back to the list.
        $("#comments-list").focus({ preventScroll: true });
        return;
      }
      submitting = false;
      textarea.disabled = false;
      submitButton.disabled = false;
      submitButton.textContent = "Post reply";
      textarea.focus();
    };
    host.settings.sendOn(textarea, submit);
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeReply();
        $("#comments-list").focus({ preventScroll: true });
      }
    });
    const submitButton = h("button", { class: "cmt-form-submit", type: "button", text: "Post reply",
                                       title: `Post reply (${host.settings.sendKeyName()})`, onclick: submit });
    return h("div", { class: "cmt-form mode-reply" },
      h("div", { class: "cmt-form-title", text: host.labels.replyBox }),
      textarea,
      h("div", { class: "cmt-form-actions" }, actionButton("cmt-form-cancel", "Cancel", closeReply), submitButton));
  }

  // A change to one comment, and the list read again once the server took it: what every card
  // button that changes a comment does. `saved` is done once it took it, before the list is
  // read. A 409, the server refusing because something changed since, goes with the server's
  // reason to `refused` when it is given; every other failure, and a list that cannot be read
  // again, is said in an alert. An answer that comes back after the panel was reset (reset) is
  // for the document shown before, and does nothing more. Whether the change was saved for what
  // the panel shows.
  async function mutate(commentId, action, body, { refused = null, saved = () => {} } = {}) {
    const from = state.generation();
    let answer;
    try {
      answer = await api.mutate(commentId, action, body);
    } catch (error) {
      if (from === state.generation()) alert(`Action failed: ${error.message}`);
      return false;
    }
    if (from !== state.generation()) return false;
    if (!answer.ok) {
      if (refused && answer.conflict) refused(answer.error);
      else alert(`Action failed: ${answer.error}`);
      return false;
    }
    saved();
    try {
      await refreshComments();
    } catch (error) {
      alert(`Saved, but comments could not be refreshed: ${error.message}`);
    }
    return true;
  }

  // The box being written in, the entry being edited and the refusals of suggestions let go of,
  // for a panel that shows another document.
  function reset() {
    activeForm = null;
    editingEntry = null;
    unfitSuggestions.clear();
  }

  return { card: renderCommentItem, actionButton, replyButton, mutate, closeReply, boxOf, reset };
}
