// Overleaf's threads in the comment panel: the leaf Comments tab and its views, which threads come
// from Overleaf or are drafted for it, the cards' parts and buttons for them, the row's Hide,
// Send, Accept and Reject, a thread started for Overleaf from the new comment dialog, and the
// keys that work them. Replies on their way to Overleaf are overleaf-replies.js, and the
// suggested edits suggested-edits.js.
//
// createOverleafThreads takes overleaf, the page's knowledge of the project (overleaf-project.js),
// which it reads and gives the answer read with each list to; me(), the reviewer's own name on
// Overleaf; panel, the comment panel (listed(), tab(), replyButton(comment), setStatus(ids,
// status), setView(view), showTab(name), refreshComments(), showComment(id)); keyHint(name);
// outgoing, the replies on their way; suggestedEdits; showTab(name), the sidebar's tab shown; and
// applySourceLeaf(). It gives the panel's hooks (tab, files, along, status, search, card, row,
// compose and keys), drafted(comment), openSuggestedEdit(id) and applied(listener), which hands
// in a function called once the list's reading of the project is taken in and the leaf tab shown
// or hidden by it.

import { $, h } from "../shared/dom.js";
import { leafIcon, replies, OVERLEAF_COOKIE_WANTED } from "./leaf.js";
import { suggestedItem } from "./suggested-edits.js";
import { createListeners } from "../shared/listeners.js";

export function createOverleafThreads({
  overleaf, me, panel, keyHint, outgoing, suggestedEdits, showTab, applySourceLeaf,
}) {
  const applied = createListeners();
  // The status a comment is listed under: "hidden" for an open thread the reviewer hid on the
  // page, else its own.
  function statusOf(comment) {
    return comment.hidden && comment.status === "open" ? "hidden" : comment.status;
  }

  // A thread drafted for Overleaf, or started for it by the reviewer, and not yet opened there:
  // it holds the draft of its first message, once the agent writes one, and what the reviewer
  // and the agent say about it here, which stays here.
  function draftedThread(comment) {
    return !overleaf.imported(comment.id) && (Boolean(comment.overleaf_draft) || Boolean(comment.for_overleaf));
  }

  // A thread in the leaf tab with something not yet sent to Overleaf, listed in the drafts view:
  // a new thread not opened there yet, or a thread from Overleaf with a reply drafted for it. One
  // hidden, or whose draft is on its way to Overleaf, is not listed.
  function hasDraft(comment) {
    return statusOf(comment) === "open" && !outgoing.sending.has(comment.id)
      && (draftedThread(comment) || Boolean(comment.overleaf_draft));
  }

  function forOverleaf(comment) {
    return overleaf.imported(comment.id) || draftedThread(comment);
  }

  // A thread from Overleaf whose last message there is someone else's: one sent from here,
  // or written there under the reviewer's own name, is the reviewer's answer. Entries that
  // stay here are not on Overleaf, and a thread drafted for it has nothing said in it yet.
  function answeredByOthers(comment) {
    const last = lastOnOverleaf(comment);
    return last !== undefined && last.overleaf === undefined && last.name !== me();
  }

  function lastOnOverleaf(comment) {
    return comment.thread.filter((entry) => entry.name !== undefined || entry.overleaf !== undefined).at(-1);
  }

  // Each entry says who reads it. A public one is on Overleaf, written there or sent there,
  // for everyone in the project. A private one stays on this page, for the reviewer and the
  // agent, as everything in a comment written here does.
  function reachMark(entry) {
    if (entry.overleaf !== undefined) {
      return h("span", {
        class: "entry-overleaf sent",
        title: `Sent to Overleaf${entry.overleaf ? ` at commit ${entry.overleaf}` : ""}, for everyone in the project`,
      }, leafIcon(), "public");
    }
    if (entry.name !== undefined) {
      return h("span", { class: "entry-overleaf", title: "Written on Overleaf, for everyone in the project" },
        leafIcon(), "public");
    }
    return h("span", { class: "entry-overleaf here", text: "private", title: "Only you and the agent can see this" });
  }

  // A suggested edit opened from the source: the leaf tab's suggested edits view, ringed there.
  async function openSuggestedEdit(id) {
    showTab("overleaf-comments");
    panel.setView("changes");
    $("#comments-list").focus({ preventScroll: true });
    await panel.refreshComments();
    if (panel.listed().some((comment) => comment.id === id)) await panel.showComment(id);
  }

  return {
    drafted: draftedThread,
    openSuggestedEdit,
    applied: applied.add,
    // The comments written here are in one tab, and Overleaf's threads, with those drafted for
    // it, in this one.
    tab: {
      holds: forOverleaf,
      views: [["open", "open"], ["unanswered", "unanswered"], ["drafts", "drafts"],
        // The suggested edits' view says how many wait.
        ["changes", () => (overleaf.suggested().length ? `suggested edits (${overleaf.suggested().length})` : "suggested edits")],
        ["hidden", "hidden"], ["resolved", "resolved"], ["all", "all"]],
      noun: "thread",
      count: "#overleaf-tab-count",
      hint: "To start a thread for Overleaf, select words in the Source with the leaf beside Comment pressed.",
      empty: { changes: "No suggested edits on Overleaf." },
      archives: false,
      // Overleaf's threads come in the order the paper reads them, as Overleaf's review panel
      // has them, with the suggested edits after them in the open view and with them in a search.
      list: (status, threads, searching, unanswered) => {
        const edits = overleaf.suggested().map(suggestedItem);
        return status === "unanswered" ? threads.filter(unanswered)
          : status === "open" ? [...threads.filter((comment) => statusOf(comment) === "open"), ...edits]
          : status === "hidden" ? threads.filter((comment) => statusOf(comment) === "hidden")
          : status === "drafts" ? threads.filter(hasDraft)
          : status === "changes" ? edits
          : searching ? [...threads, ...edits]
          : threads;
      },
      items: () => overleaf.suggested().map(suggestedItem),
      itemCard: (item, look) => (item.suggested ? suggestedEdits.render(item, look) : null),
      // The drafts view lists the new threads first and then the replies drafted.
      group: (status, comment) => (status === "drafts" && !draftedThread(comment) ? 1 : 0),
      chosen: (view) => {
        if (view === "changes") suggestedEdits.read();
      },
    },
    // A thread's files: where it is in the source, and what its anchor names.
    files: (comment) => [comment.resolved_source?.file, comment.anchor?.file],
    // Which threads came from Overleaf, and so can be answered there, read with every list. The
    // leaf tab shows once the paper names an Overleaf project or has threads from one, with the
    // button that syncs its threads and what stands in their way; hidden while shown, it gives
    // way to the comments tab.
    along: {
      read: () => overleaf.read(),
      // An answer the server did not give leaves what the page knew.
      apply: (linked) => {
        if (linked) overleaf.apply(linked);
        applySourceLeaf();
        const leafTab = $('.tab-btn[data-tab="overleaf-comments"]');
        leafTab.hidden = !overleaf.project() && overleaf.importedCount() === 0;
        if (leafTab.hidden && panel.tab() === "overleaf-comments") {
          if (leafTab.classList.contains("active")) showTab("comments");
          else panel.showTab("comments");
        }
        applied.tell();
      },
    },
    status: {
      of: statusOf,
      // Hidden threads opened are shown again, not reopened.
      said: (status, before, what) => (status === "hidden" ? `Hid ${what}.`
        : status === "open" && before.every((was) => was === "hidden") ? `Showed ${what} again.` : null),
      // In the leaf tab, someone else wrote the last message on Overleaf. A new thread not yet
      // sent, which has no message, is only open, and so is one whose reply is on its way to
      // Overleaf: it comes back, opened and saying why, only when Overleaf refuses it.
      waits: (comment, agentWroteLast) => !outgoing.sending.has(comment.id)
        && (forOverleaf(comment) ? answeredByOthers(comment) : agentWroteLast),
    },
    // A thread's draft for Overleaf, its place in the source and the words there are searched too,
    // and of a suggested edit what it inserts and deletes and who suggested it, and when.
    search: {
      me,
      words: (comment) => [comment.overleaf_draft?.text, comment.resolved_source?.file, comment.source_selector?.exact,
        ...(comment.suggested ? [comment.inserted, comment.deleted, comment.name] : [])],
      said: (comment) => (comment.suggested ? [{ name: comment.name ?? "", at: comment.at }] : comment.thread),
    },
    card: {
      note: (comment) => {
        const unplaced = overleaf.unplaced(comment.id);
        return unplaced && comment.status === "open" ? h("div", { class: "cmt-unplaced" }, leafIcon(), h("span", {
          text: `On Overleaf at ${unplaced}. That text is not in the file here yet, so the comment is shown on the whole paper for now.`,
        })) : null;
      },
      // The reply drafted for Overleaf, or a new thread's first message, shown closed or open while
      // it waits to be sent.
      reply: (comment, expanded) => (forOverleaf(comment) && comment.status === "open"
        && (expanded || comment.overleaf_draft || draftedThread(comment)) ? outgoing.render(comment) : null),
      actions: (comment, buttons) => {
        const own = (name) => buttons.find((button) => button.classList.contains(name));
        // A thread drafted for Overleaf takes replies here, about its draft, and is sent, or deleted
        // here: it is not on Overleaf to resolve.
        if (draftedThread(comment)) return [panel.replyButton(comment), own("cmt-delete")];
        if (!overleaf.imported(comment.id)) return buttons;
        // A thread from Overleaf resolves and reopens there, at once: Overleaf takes no summary,
        // has no archive, and deletes a thread only there. An open one can be hidden on this page
        // instead, which changes nothing on Overleaf.
        const hiding = statusOf(comment) === "hidden"
          ? h("button", { class: "cmt-show", type: "button", text: "Show", onclick: () => panel.setStatus([comment.id], "open"),
                          title: `Show this thread in the open view again${keyHint("cmt-show")}` })
          : h("button", { class: "cmt-hide", type: "button", text: "Hide", onclick: () => panel.setStatus([comment.id], "hidden"),
                          title: `Hide this thread here without changing it on Overleaf${keyHint("cmt-hide")}` });
        return [
          panel.replyButton(comment),
          comment.status === "open" ? own("cmt-resolve") : own("cmt-reopen"),
          comment.status === "open" ? hiding : null,
        ];
      },
      entryMark: reachMark,
      // What the reviewer wrote and may change: an entry written here, or on a thread from
      // Overleaf one sent from here or written there under the name Overleaf gives them. One on
      // Overleaf is reworded or deleted there, for everyone in the project, and others' messages
      // there are answered.
      mayChange: (entry, comment) => (entry.name === undefined && entry.overleaf === undefined)
        || (forOverleaf(comment) && (entry.overleaf !== undefined || entry.name === me())),
      deleteQuestion: (entry, index, question) => (entry.name === undefined && entry.overleaf === undefined ? question
        : index === 0 ? "Delete this thread on Overleaf, with every reply in it, for everyone in the project?"
        : "Delete this message on Overleaf, for everyone in the project?"),
    },
    row: {
      render: (settled, { picked, listed, view, tab }) => {
        // In the leaf tab, the place of Archive holds Hide, which hides the picked open threads on this page
        // only, and reads Show, as A does, once every picked thread is hidden, or in the hidden view.
        const hideable = settled.filter((comment) => statusOf(comment) === "open" && overleaf.imported(comment.id))
          .map((comment) => comment.id);
        const hidden = settled.filter((comment) => statusOf(comment) === "hidden").map((comment) => comment.id);
        const showing = hideable.length === 0 && (hidden.length > 0 || view === "hidden");
        const toggled = showing ? hidden : hideable;
        const hide = $("#hide-picked-btn");
        hide.disabled = toggled.length === 0;
        hide.querySelector(".icon-hide").classList.toggle("hidden", showing);
        hide.querySelector(".icon-show").classList.toggle("hidden", !showing);
        hide.querySelector(".count").textContent = toggled.length > 0 ? String(toggled.length) : "";
        hide.setAttribute("aria-label", `${showing ? "Show" : "Hide"}${toggled.length > 0 ? ` ${toggled.length}` : ""}`);
        hide.title = showing ? "Show the selected threads in the open view again"
          : "Hide the selected threads here without changing them on Overleaf";
        hide.dataset.status = showing ? "open" : "hidden";
        hide.dataset.ids = toggled.join(" ");
        // A picked thread from Overleaf sends what its reply box holds, each to its own thread there.
        const sendable = picked.filter((comment) => forOverleaf(comment) && comment.status === "open"
          && outgoing.text(comment).trim()).map((comment) => comment.id);
        const send = $("#send-picked-btn");
        send.hidden = !overleaf.project() || tab !== "overleaf-comments";
        send.disabled = !overleaf.cookie() || sendable.length === 0 || outgoing.sending.size > 0;
        send.classList.toggle("busy", outgoing.sending.size > 0);
        send.querySelector(".count").textContent = sendable.length > 0 ? String(sendable.length) : "";
        send.setAttribute("aria-label", sendable.length > 0 ? `Send ${replies(sendable.length)} to Overleaf` : "Send to Overleaf");
        send.title = overleaf.cookie() ? "Send the selected public replies to Overleaf, for everyone in the project"
          : OVERLEAF_COOKIE_WANTED;
        send.dataset.ids = sendable.join(" ");
        // A list with suggested edits accepts or rejects the picked ones: the suggested edits view, in
        // place of resolving, hiding and sending, and a search that finds some beside them.
        const suggesting = tab === "overleaf-comments" && view === "changes";
        const edits = picked.filter((item) => item.suggested);
        hide.hidden = tab !== "overleaf-comments" || suggesting;
        send.hidden = send.hidden || suggesting;
        for (const [id, word] of [["#accept-picked-btn", "Accept"], ["#reject-picked-btn", "Reject"]]) {
          const button = $(id);
          button.hidden = !listed.some((item) => item.suggested);
          button.disabled = edits.length === 0 || suggestedEdits.reviewing.size > 0;
          button.classList.toggle("busy", edits.some((item) => suggestedEdits.reviewing.has(item.id)));
          button.querySelector(".count").textContent = edits.length > 0 ? String(edits.length) : "";
          button.setAttribute("aria-label", edits.length > 0 ? `${word} ${edits.length}` : word);
        }
        return { resolve: !suggesting };
      },
      // Once the view list gives up its room, the sync button waits behind the dots, before the
      // bell does. In the leaf tab, Send then drops its leaf, and Accept and Reject, then Hide,
      // which each card has too, wait behind the dots. Send stays in the row.
      steps: {
        squeezed: [["unsynced", "#overleaf-sync-comments-btn"]],
        uncalled: [["leafless"], ["edits-behind", "#accept-picked-btn, #reject-picked-btn"], ["hide-behind", "#hide-picked-btn"]],
      },
      // Resolving or reopening a thread from Overleaf does so there, for everyone in the
      // project: several at one press are asked about first, with their number.
      confirm: (button, comments, status) => {
        const there = comments.filter(forOverleaf).length;
        return button.id !== "resolve-picked-btn" || there <= 1
          || confirm(`${status === "open" ? "Reopen" : "Resolve"} ${there} threads on Overleaf, for everyone in the project?`);
      },
      // A thread from Overleaf resolves and reopens there too. A thread drafted for Overleaf is only
      // sent or deleted, and a suggested edit accepted or rejected.
      settles: (comment) => !draftedThread(comment) && !comment.suggested,
    },
    compose: {
      // A comment *overleaf* starts a thread for Overleaf, in two boxes, either of which will do:
      // the first holds its first message there, posted at once, and the second a private note
      // for the agent, which drafts that message when the first box is left empty. The box for
      // the message is the larger, and the note under it starts at half its rows.
      opening: ({ overleaf }) => {
        $("#compose-public").hidden = !overleaf;
        $("#compose-message").value = "";
        return overleaf ? { title: "New public thread on Overleaf", box: { required: false, rows: 3,
          placeholder: "a private note for the agent. If you leave the message empty, the agent drafts it…" } } : {};
      },
      // The first message is kept with the thread as its draft, and sent from there.
      filled: ({ overleaf }) => {
        const message = overleaf ? $("#compose-message").value.trim() : "";
        return message ? { draft: message } : {};
      },
      // A thread started for Overleaf with only a note waits for the agent's draft among the
      // drafts. One whose first message was posted, and a comment for the agent, are open. A
      // first message goes as one sent from its card goes: refused, it stays there as the
      // thread's draft, saying why.
      posted: async (started, body) => {
        const tab = body.overleaf ? "overleaf-comments" : "comments";
        if (panel.tab() !== tab) showTab(tab);
        panel.setView(body.overleaf && !body.draft ? "drafts" : "open");
        if (body.draft) await outgoing.send(started.id, body.draft, body.draft);
      },
    },
    // The paper's own keys work its threads from Overleaf and its suggested edits.
    keys: {
      rows: ({ pressCard, pressPickedOr, pressForRange, focusedCard }) => [
        { keys: ["r"], does: "Write a private reply that the agent sees and Overleaf does not" },
        { after: "r", keys: ["i"], does: "Write its reply for Overleaf", marks: ".cmt-overleaf",
          run: () => {
            const box = focusedCard()?.querySelector(".overleaf-reply-input");
            if (!box) return;
            box.focus();
            box.setSelectionRange(box.value.length, box.value.length);
          } },
        { keys: ["e"], does: "Edit your last message in it, here or on Overleaf" },
        { keys: ["Escape"], does: "Leave the text box. An unsent private reply or edit is discarded; a reply for Overleaf is kept" },
        { keys: ["A"], does: "Archive it, or in the leaf Comments tab hide or show it", button: ["cmt-archive", "cmt-hide", "cmt-show"],
          run: () => pressPickedOr(["#archive-picked-btn", "#hide-picked-btn"], ".cmt-archive, .cmt-hide, .cmt-show"),
          range: () => pressForRange(["#archive-picked-btn", "#hide-picked-btn"]) },
        { keys: ["aa"], does: "Apply its suggestion to the file, or accept a suggested edit on Overleaf",
          button: ["sugg-apply", "change-accept"], run: () => pressCard(".sugg-apply, .change-accept") },
        { keys: ["nn"], does: "Reject its suggestion, or its Overleaf draft if there is none",
          button: ["sugg-reject", "overleaf-reject"],
          run: () => {
            const card = focusedCard();
            (card?.querySelector(".sugg-reject") ?? card?.querySelector(".overleaf-reject"))?.click();
          } },
        { after: "nn", keys: ["ss"], does: "Send its public reply to Overleaf, for everyone in the project", button: ["overleaf-send"],
          picked: true, run: () => pressPickedOr(["#send-picked-btn"], ".overleaf-send"),
          range: () => pressForRange(["#send-picked-btn"]) },
        { keys: ["dd"], does: "Delete it (after confirmation), or reject a suggested edit on Overleaf",
          button: ["cmt-delete", "change-reject"], run: () => pressCard(".cmt-delete, .change-reject") },
        { keys: ["D"], does: "Delete your last message in it, here or on Overleaf (after confirmation)" },
      ],
    },
  };
}
