// The comment search: what a search asks for, which comments meet it, and the box above the
// list it is typed in, with the conditions it offers and the names and files it completes.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, h } from "./dom.js";

// What a search asks for, in the conventions of mail and code search:
//   query = group ("OR" group)*      OR, in capitals, binds looser than the spaces
//   group = term*                     a comment meets a group when it meets every term
//   term  = ["-"] [field ":"] value   "-" asks for the comments that do not meet it
//   value = '"' text '"' | text       quotes hold words together, spaces and all
//   field = from | file | after | before
// after: and before: take YYYY-MM-DD, or MM-DD in `year`, and may go on to a minute as
// T09, T09:30 or, quoted, "11-13 09:30", in local time. A field followed by anything else,
// and any other word with a colon, is an ordinary word. A term with nothing to look for, as
// a field not yet typed, and a group with no term are left out. Each term comes as
// { not, field, value }: the field is "" for a word or a phrase, whose value is in lower case,
// and the value of after: and before: is the moment they name, in milliseconds.
export function parseSearch(text, year) {
  const groups = [[]];
  for (const [, not, field = "", quoted, bare] of text.matchAll(/(-?)(?:(from|file|after|before):)?(?:"([^"]*)"?|([^\s"]+))?/g)) {
    const raw = (quoted ?? bare ?? "").replace(/\s+/g, " ").trim();
    if (raw === "") continue;
    if (raw === "OR" && bare !== undefined && !not && !field) {
      groups.push([]);
      continue;
    }
    const moment = field === "after" || field === "before" ? searchMoment(raw, year) : undefined;
    groups.at(-1).push(moment === null ? { not: Boolean(not), field: "", value: `${field}:${raw}`.toLowerCase() }
      : { not: Boolean(not), field, value: moment ?? raw.toLowerCase() });
  }
  return groups.filter((group) => group.length > 0);
}

// The first moment of a day, or of a minute in it, in local time, in milliseconds, or null
// when the words name none.
function searchMoment(text, year) {
  const read = /^(?:(\d{4})-)?(\d{1,2})-(\d{1,2})(?:[Tt ](\d{1,2})(?::(\d{2}))?)?$/.exec(text);
  if (read === null) return null;
  const [y, month, day, hour, minute] = [read[1] ?? year, read[2], read[3], read[4] ?? 0, read[5] ?? 0].map(Number);
  const moment = new Date(y, month - 1, day, hour, minute);
  return moment.getMonth() === month - 1 && moment.getDate() === day && hour < 24 && minute < 60
    ? moment.getTime() : null;
}

// Whether a comment meets a search parseSearch read: every term of one of its groups. A word
// or a phrase is held in what was said in it and by whom, in the words it is on, its file,
// its id, its suggestion, or `more`, the words the page keeps with it beside these, or in the
// words a suggested edit listed among the comments inserts or deletes and who suggested it.
// from: is someone who wrote in the thread or suggested the edit, file: the file the comment
// is on, after: an entry written at that moment or later, and before: one written earlier, an
// edit's being when it was suggested. Each term is met by the thread on its own, so from: and
// after: may be met by two entries. `me` is the reviewer's own name on the entries that carry
// their writer's name.
export function meetsSearch(comment, query, me, more) {
  let held = null;
  const said = comment.suggested ? [{ name: comment.name ?? "", at: comment.at }] : comment.thread;
  const meets = ({ field, value }) => field === "from"
    ? said.some((entry) => writerNames(entry, me).some((name) => name.toLowerCase().includes(value)))
    : field === "file" ? [comment.anchor?.file, comment.resolved_source?.file].some((file) => file?.toLowerCase().includes(value))
    : field === "after" ? said.some((entry) => Date.parse(entry.at) >= value)
    : field === "before" ? said.some((entry) => Date.parse(entry.at) < value)
    : (held ??= [
      comment.id, comment.anchor?.quote, comment.anchor?.file, comment.anchor?.title, comment.resolved_source?.file,
      comment.source_selector?.exact, ...more,
      ...(comment.suggested ? [comment.inserted, comment.deleted, comment.name] : []),
      ...comment.thread.flatMap((entry) => [entry.text, entry.name]),
      ...(comment.suggestion?.changes ?? []).flatMap((change) => [change.old, change.new]),
    ].filter(Boolean).map((words) => words.replace(/\s+/g, " ")).join("\n").toLowerCase()).includes(value);
  return query.some((group) => group.every((term) => meets(term) !== term.not));
}

// The names from: knows an entry's writer by: the name the entry carries, "agent" for the
// agent, and "me" for the reviewer, whose entries here carry no name and who goes by `me` on
// the entries that do.
function writerNames(entry, me) {
  if (entry.name !== undefined) return entry.name === me ? [entry.name, "me"] : [entry.name];
  return [entry.author === "agent" ? "agent" : "me"];
}

// The search box above the list, the conditions under it and the list of names and files under
// those. What is typed is kept in `state.commentSearch`. `refresh` lists what the search finds,
// `tabComments` reads the shown tab's comments of every status, and `me` is the reviewer's own
// name on the entries that carry their writer's name.
export function createCommentSearch({ state, refresh, tabComments, me }) {
  // The writers and the files of the tab's comments, whatever their status, which the list
  // under the search box offers for a from: or a file: being typed. Read when the list opens,
  // and null while it is closed. The one the arrows are on.
  let searchChoices = null;
  let choiceAt = 0;

  // What the search box asks for, its dates without a year in this year.
  function searchQuery() {
    return parseSearch(state.commentSearch, new Date().getFullYear());
  }

  async function readSearchChoices() {
    const comments = await tabComments();
    const listed = (values) => [...new Set(values.filter(Boolean))].sort((first, second) => first.localeCompare(second));
    return { from: listed(comments.flatMap((comment) => comment.thread.map((entry) => writerNames(entry, me())[0]))),
             file: listed(comments.map((comment) => comment.resolved_source?.file ?? comment.anchor?.file)) };
  }

  // The from: or the file: the cursor is in: its field, what is typed after the colon, and where
  // its value starts and ends in the box, a closing quote included.
  function conditionAtCursor(box) {
    if (box.selectionStart !== box.selectionEnd) return null;
    const typed = /(?:^|\s)-?(from|file):(?:"([^"]*)|([^\s"]*))$/.exec(box.value.slice(0, box.selectionStart));
    if (typed === null) return null;
    const rest = box.value.slice(box.selectionStart);
    return { field: typed[1], value: (typed[2] ?? typed[3]).toLowerCase(),
             start: typed.index + typed[0].indexOf(":") + 1,
             end: box.selectionStart + (typed[2] !== undefined ? /^[^"]*"?/ : /^[^\s"]*/).exec(rest)[0].length };
  }

  // The list under the box, open while the cursor is in a from: or a file:, with the names or
  // the files that hold what is typed after the colon.
  function offerChoices() {
    const box = $("#comment-search");
    const pick = $("#search-pick");
    const condition = document.activeElement === box ? conditionAtCursor(box) : null;
    if (condition === null) {
      closeChoices();
      return;
    }
    if (searchChoices === null) {
      searchChoices = { from: [], file: [] };
      readSearchChoices().then((read) => {
        if (searchChoices === null) return;
        searchChoices = read;
        offerChoices();
      }).catch((error) => console.error(error));
    }
    const shown = searchChoices[condition.field].filter((choice) => choice.toLowerCase().includes(condition.value));
    choiceAt = Math.max(0, Math.min(choiceAt, shown.length - 1));
    pick.replaceChildren(...shown.map((choice, index) => h("div", {
      class: "search-choice", role: "option", "aria-selected": String(index === choiceAt), text: choice,
      onclick: () => takeChoice(choice) })));
    pick.hidden = shown.length === 0;
    pick.children[choiceAt]?.scrollIntoView({ block: "nearest" });
  }

  function closeChoices() {
    $("#search-pick").hidden = true;
    searchChoices = null;
    choiceAt = 0;
  }

  // A name or a file picked completes the from: or the file: at the cursor, in quotes when it
  // has a space, and the cursor goes on after it.
  function takeChoice(choice) {
    const box = $("#comment-search");
    const condition = conditionAtCursor(box);
    if (condition === null) return;
    const after = /\s/.test(box.value.charAt(condition.end)) ? "" : " ";
    box.setRangeText(`${/\s/.test(choice) ? `"${choice}"` : choice}${after}`, condition.start, condition.end, "end");
    box.dispatchEvent(new Event("input"));
  }

  // A condition put into the box at the cursor, apart from the word before it, with the cursor
  // where its value goes: after the colon or the -, between the quotes, or after OR.
  function insertCondition(box, text) {
    const joins = (text === "OR" ? /\S$/ : /[^\s:-]$/).test(box.value.slice(0, box.selectionStart));
    box.setRangeText(`${joins ? " " : ""}${text}${text === "OR" ? " " : ""}`, box.selectionStart, box.selectionEnd, "end");
    if (text === '""') box.setSelectionRange(box.selectionStart - 1, box.selectionStart - 1);
    box.dispatchEvent(new Event("input"));
  }

  function openCommentSearch() {
    const box = $("#comment-search");
    box.focus();
    box.select();
  }

  // The search put away: the list shows its view again. Whether there was one.
  function closeCommentSearch() {
    const searched = state.commentSearch !== "";
    $("#comment-search").value = "";
    state.commentSearch = "";
    return searched;
  }

  function attachCommentSearch() {
    const box = $("#comment-search");
    const terms = $("#search-terms");
    let timer = null;
    const list = () => refresh().catch((error) => console.error(error));
    box.addEventListener("input", () => {
      state.commentSearch = box.value;
      clearTimeout(timer);
      timer = setTimeout(list, 150);
      choiceAt = 0;
      offerChoices();
    });
    // A click in the box may put the cursor in a from: or a file:, or take it out of one.
    box.addEventListener("click", () => offerChoices());
    // The conditions show while the box has the focus, which a press on them or on the list
    // under them leaves in the box.
    box.addEventListener("focus", () => { terms.hidden = false; });
    box.addEventListener("blur", () => {
      terms.hidden = true;
      closeChoices();
    });
    terms.addEventListener("mousedown", (event) => event.preventDefault());
    for (const button of terms.querySelectorAll("[data-insert]")) {
      button.addEventListener("click", () => insertCondition(box, button.dataset.insert));
    }
    // Enter goes to the comments found, for the keys to go on, and Escape puts the search away.
    // While the list of names or files is open, the arrows go through it, Enter picks the one
    // they are on, and Escape closes the list first.
    box.addEventListener("keydown", (event) => {
      if (!$("#search-pick").hidden && ["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key)) {
        event.preventDefault();
        event.stopPropagation();
        if (event.key === "Escape") closeChoices();
        else if (event.key === "Enter") $("#search-pick").children[choiceAt]?.click();
        else {
          choiceAt = (choiceAt + (event.key === "ArrowDown" ? 1 : -1) + $("#search-pick").children.length)
            % $("#search-pick").children.length;
          offerChoices();
        }
        return;
      }
      if (event.key !== "Escape" && event.key !== "Enter") return;
      event.preventDefault();
      event.stopPropagation();
      clearTimeout(timer);
      if (event.key === "Escape") closeCommentSearch();
      else state.commentSearch = box.value;
      list();
      $("#comments-list").focus({ preventScroll: true });
    });
  }

  return { searchQuery, openCommentSearch, closeCommentSearch, attachCommentSearch };
}
