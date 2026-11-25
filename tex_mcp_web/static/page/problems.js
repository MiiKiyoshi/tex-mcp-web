// The Problems tab, and the count beside its name. createProblems takes showLayoutLine(at), which
// shows a line of the layout where it is printed and in the source (places.js). It gives
// show(result), which lists what a build's result says.

import { $, h, clear, placeholder } from "../shared/dom.js";

export function createProblems({ showLayoutLine }) {
  const state = {
    errors: [],
    warnings: [],
    // Where the body ends and the paragraphs ending in a short line, as the last build left them.
    layout: {},
  };

  function show(result) {
    state.errors = result.errors;
    state.warnings = result.warnings;
    state.layout = result.layout ?? {};
    const badge = $("#compile-tab-count");
    const total = state.errors.length + state.warnings.length + (state.layout.short_lines?.length ?? 0)
      + (state.layout.more_short_lines ?? 0);
    badge.textContent = String(total);
    badge.classList.toggle("hidden", total === 0);
    badge.classList.toggle("has-errors", state.errors.length > 0);
    renderErrorBanner();
  }

  // The Problems tab: what the build said, then where the paper stands against a page limit,
  // which the agent's compile() reports in the same words. A line of the layout is a place on
  // a printed page, so a click points at it in the Preview, and moves the editor's cursor to
  // its source only where the editor already shows.
  function renderErrorBanner() {
    const banner = $("#error-banner");
    clear(banner);
    const layout = state.layout ?? {};
    const short = layout.short_lines ?? [];
    if (state.errors.length === 0 && state.warnings.length === 0 && !layout.body_end && short.length === 0) {
      banner.appendChild(placeholder("No errors or warnings."));
      return;
    }
    const items = [
      ...state.errors.map((error) => ({ ...error, level: "error" })),
      ...state.warnings.map((warning) => ({ ...warning, level: "warning" })),
    ];
    for (const item of items) {
      const children = [
        h("div", { class: "err-loc" },
          h("span", { class: `err-level err-level-${item.level}`, text: item.level.toUpperCase() }),
          h("span", { text: `${item.file ?? ""}${item.line ? `:${item.line}` : ""}` })),
        h("div", { class: "err-msg", text: item.message ?? "" }),
      ];
      if (item.context?.length) children.push(h("pre", { class: "err-context", text: item.context.join("\n") }));
      banner.appendChild(h("div", { class: `err-item err-${item.level}` }, ...children));
    }
    const place = (at) => `page ${at.page}, column ${at.column}`;
    const item = (level, word, at, message) => {
      const node = h("div", { class: `err-item err-${level}${at.bbox ? " err-goes" : ""}` },
        h("div", { class: "err-loc" },
          h("span", { class: `err-level err-level-${level}`, text: word }),
          h("span", { text: at.source ?? "" })),
        h("div", { class: "err-msg", text: message }));
      if (at.bbox) {
        node.addEventListener("click", () => showLayoutLine(at));
        node.title = "Show in the Preview";
      }
      banner.appendChild(node);
    };
    if (layout.body_end) {
      const end = layout.body_end;
      const lines = (count) => `${count} line${count === 1 ? "" : "s"}`;
      item("info", "BODY END", end,
        `The body ends on ${place(end)}${end.room_lines === undefined ? "."
          : `, with room for ${lines(end.room_lines)} under it.`}`);
    }
    for (const line of short) {
      item("warning", "SHORT LINE", line,
        `A paragraph on ${place(line)} ends with a short line (${line.width} of the column width): “${line.text}”`);
    }
    if (layout.more_short_lines) {
      banner.appendChild(placeholder(`${layout.more_short_lines} more short lines.`));
    }
  }

  return { show };
}
