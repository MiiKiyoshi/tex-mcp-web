// The Sections tab: the paper's headings, each shown in the source and in the Preview by a click
// on it, with a button that comments on its section. createSections takes workspace's view(),
// source's init() and open(file, line), pdf's showLine(file, line), and the comment panel's
// openCompose and actionButton. It gives render(paper), which lists the paper's headings.

import { $, h, clear, placeholder } from "../shared/dom.js";

export function createSections({ workspace, source, pdf, panel }) {
  function renderSections(paper) {
    const host = $("#sections-list");
    clear(host);
    if (!paper) {
      host.appendChild(placeholder("Loading…"));
      return;
    }
    const list = h("ul", { class: "paper-sections" });
    for (const section of paper.sections) {
      list.appendChild(h("li", { class: `paper-section level-${section.level}` },
        h("span", {
          class: "paper-section-title",
          title: `Show this heading in the source, at ${section.file}:${section.line}`,
          onclick: () => jumpToSource(section.file, section.line),
        },
        section.number ? h("span", { class: "section-number", text: section.number }) : null,
        h("span", { text: section.title })),
        // A section comment names the heading it was pressed on too, since headings can share a title.
        panel.actionButton("comment-section-btn", "+ comment", () =>
          panel.openCompose({ kind: "section", title: section.title, label: section.label }, `Section: ${section.title}`,
            { heading: { file: section.file, line: section.line } }))));
    }
    host.appendChild(list);
  }

  async function jumpToSource(file, line) {
    if (workspace.view() !== "pdf") {
      await source.init();
      await source.open(file, line);
    }
    if (workspace.view() !== "source") await pdf.showLine(file, line);
  }

  return { render: renderSections };
}
