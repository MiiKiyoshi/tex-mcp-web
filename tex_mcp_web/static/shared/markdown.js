// The words of a comment as the markdown an agent writes them: **bold**, *italic* and
// _italic_, `code`, links, fenced code blocks, lists and headings. Every line stays where it
// was written, blank lines too, and anything else is shown as written: a word with
// underscores, a*b*c, a path, a backslash, HTML, and a line of a suggestion that starts with
// − or +.
//
//   renderMarkdown(text)  the text drawn, as a DocumentFragment, for an element whose
//                         white-space is pre-wrap
//   markdownToText(text)  the same words without their marks, line by line, for a one-glance
//                         preview: a bullet as •, a numbered item with its number
//
// Text with no markdown in it comes back as it was, as one text node or the same string.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { h } from "./dom.js";

const FENCE = /^ {0,3}(`{3,}|~{3,})[^`]*$/;
const FENCE_END = /^ {0,3}(`{3,}|~{3,}) *$/;
// # to ###, drawn as a bold line. #### and a # with no space after it are words.
const HEADING = /^ {0,3}(#{1,3}) +(\S.*?)(?: +#+)? *$/;
// - or * and a space, or a number with . or ) and a space.
const ITEM = /^( *)([-*]|\d{1,9}[.)]) +(\S.*)$/;
// A line of a suggestion: − the words it replaces, + the words it puts in their place.
const SUGGESTED = /^[−+] /;
const LINK = /^\[([^[\]]+)\]\((https?:\/\/[^\s()<>]+)\)/;
const BARE_URL = /^https?:\/\/[A-Za-z0-9\-._~:/?#@!$&+,;=%()]+/;
// A letter or a digit of ASCII: inside a word or a number, where a mark stays as written, as
// in a*b*c, it`s, or `a' and `b' of LaTeX. A letter of another script is not, so a Korean
// particle may follow a closing mark.
const WORD = /[A-Za-z0-9]/;
// What may stand right before an opening * or _: a space, an opening bracket or quote, or a
// letter outside ASCII. Anything else keeps the mark as written: snake_case, src/*.js,
// $f^*$ and $x^{2}_{i}$.
const OPENS_AFTER = /[\s(["']|[^\x00-\x7f]/;

export function renderMarkdown(text) {
  const parts = [];
  blocksOf(text).forEach((block, index, blocks) => {
    // A line ends with its line break. A list, a code block or a heading ends its own line,
    // and a line break after it would show as an empty line.
    if (index > 0 && blocks[index - 1].line !== undefined) parts.push("\n");
    if (block.line !== undefined) parts.push(...(SUGGESTED.test(block.line) ? [block.line] : inline(block.line)));
    else if (block.code) parts.push(h("pre", {}, h("code", { text: block.code.join("\n") })));
    else if (block.heading !== undefined) parts.push(h("div", {}, h("strong", {}, ...inline(block.heading))));
    else parts.push(drawList(block));
  });
  const fragment = document.createDocumentFragment();
  fragment.append(...joined(parts));
  return fragment;
}

export function markdownToText(text) {
  return blocksOf(text).flatMap((block) => {
    if (block.line !== undefined) return [SUGGESTED.test(block.line) ? block.line : textOf(block.line)];
    if (block.code) return block.code;
    if (block.heading !== undefined) return [textOf(block.heading)];
    return listText(block);
  }).join("\n");
}

// The text's blocks in order: { line } for a line drawn as written, { code: lines },
// { heading }, and lists, { tag: "ul" or "ol", items }.
function blocksOf(text) {
  const lines = text.split("\n");
  const blocks = [];
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const fence = FENCE.exec(line)?.[1];
    const heading = HEADING.exec(line);
    if (fence) {
      // An unclosed fence runs to the end.
      const end = lines.findIndex((each, at) => at > index && FENCE_END.exec(each)?.[1].startsWith(fence));
      const last = end === -1 ? lines.length : end;
      blocks.push({ code: lines.slice(index + 1, last) });
      index = last;
    } else if (heading) {
      blocks.push({ heading: heading[2] });
    } else if (ITEM.test(line)) {
      // The items from here on, each with the indented lines under it that go on with it.
      const items = [];
      for (; index < lines.length; index++) {
        const item = ITEM.exec(lines[index]);
        if (item) items.push({ indent: item[1], marker: item[2], lines: [item[3]] });
        else if (/^ {2,}\S/.test(lines[index])) items.at(-1).lines.push(lines[index].trim());
        else break;
      }
      index--;
      blocks.push(...listsOf(items));
    } else {
      blocks.push({ line });
    }
  }
  return blocks;
}

// Items in lists: a list for each run of bulleted or numbered ones, and an item indented two
// spaces or more in the lists of the item above it, one level deep.
function listsOf(items) {
  const lists = [];
  const add = (siblings, item) => {
    const tag = /\d/.test(item.marker) ? "ol" : "ul";
    if (siblings.at(-1)?.tag !== tag) siblings.push({ tag, items: [] });
    siblings.at(-1).items.push({ ...item, lists: [] });
  };
  for (const item of items) {
    const parent = lists.at(-1)?.items.at(-1);
    add(item.indent.length >= 2 && parent ? parent.lists : lists, item);
  }
  return lists;
}

function drawList(list) {
  const start = Number.parseInt(list.items[0].marker, 10);
  return h(list.tag, list.tag === "ol" && start !== 1 ? { start } : {},
    ...list.items.map((item) => h("li", {},
      ...joined(item.lines.flatMap((line, index) => [index > 0 ? "\n" : "", ...inline(line)])),
      ...item.lists.map(drawList))));
}

function listText(list) {
  return list.items.flatMap((item) => [
    `${item.indent}${list.tag === "ul" ? "•" : item.marker} ${textOf(item.lines[0])}`,
    ...item.lines.slice(1).map((line) => `${item.indent}  ${textOf(line)}`),
    ...item.lists.flatMap(listText),
  ]);
}

// One line's words: strings, and the elements of its marks.
function inline(line, linking = true) {
  // Strings, elements, and { run } for an opening * ** or _ that waits for its closing one.
  const parts = [];
  let at = 0;
  while (at < line.length) {
    const rest = line.slice(at);
    const before = line[at - 1] ?? " ";
    const ticks = /^`+/.exec(rest)?.[0];
    if (ticks) {
      // Code between one backtick and the next, each at the edge of a word. `` is LaTeX's
      // opening quote, and `a' and `b' are LaTeX's single quotes.
      const end = ticks === "`" && !WORD.test(before)
        ? [...rest.slice(1).matchAll(/`+/g)].find((tick) => tick[0] === "`" && !WORD.test(rest[tick.index + 2] ?? ""))
        : undefined;
      if (end !== undefined) parts.push(h("code", { text: rest.slice(1, end.index + 1) }));
      else parts.push(ticks);
      at += end !== undefined ? end.index + 2 : ticks.length;
      continue;
    }
    const linked = linking ? LINK.exec(rest) : null;
    if (linked) {
      parts.push(link(linked[2], inline(linked[1], false)));
      at += linked[0].length;
      continue;
    }
    const address = linking && !WORD.test(before) ? trimmed(BARE_URL.exec(rest)?.[0] ?? "") : null;
    if (address) {
      parts.push(link(address, [address]));
      at += address.length;
      continue;
    }
    const run = /^(?:\*+|_+)/.exec(rest)?.[0];
    if (run) {
      const after = line[at + run.length] ?? " ";
      const marks = run === "*" || run === "**" || run === "_";
      const closes = marks && before !== "\\" && !/\s/.test(before) && !WORD.test(after);
      const opener = closes ? parts.findLastIndex((part) => part.run === run) : -1;
      if (opener !== -1) {
        const inside = parts.splice(opener).slice(1);
        parts.push(h(run === "**" ? "strong" : "em", {}, ...joined(inside)));
      } else {
        const opens = marks && before !== "\\" && !/\s/.test(after) && OPENS_AFTER.test(before);
        parts.push(opens ? { run } : run);
      }
      at += run.length;
      continue;
    }
    // The words up to the next mark or address, or a mark that starts nothing, as they are.
    const words = /^(?:[^`[*_h]|h(?!ttps?:\/\/))+/.exec(rest)?.[0] ?? rest[0];
    parts.push(words);
    at += words.length;
  }
  return joined(parts);
}

// A bare address ends before the stop or comma after it, and before a ) that closes a bracket
// opened before the address. An address with nothing after its // is none.
function trimmed(url) {
  const count = (mark) => url.split(mark).length - 1;
  while (/[.,;:!?]$/.test(url) || (url.endsWith(")") && count(")") > count("("))) url = url.slice(0, -1);
  return /^https?:\/\/./.test(url) ? url : null;
}

function link(href, words) {
  return h("a", { href, target: "_blank", rel: "noopener" }, ...words);
}

// *parts* with each mark left waiting written as it is, and each run of strings as one.
function joined(parts) {
  const out = [];
  for (const part of parts) {
    const piece = part.run ?? part;
    if (typeof piece === "string" && typeof out.at(-1) === "string") out[out.length - 1] += piece;
    else if (piece !== "") out.push(piece);
  }
  return out;
}

function textOf(line) {
  return inline(line).map((part) => (typeof part === "string" ? part : part.textContent)).join("");
}
