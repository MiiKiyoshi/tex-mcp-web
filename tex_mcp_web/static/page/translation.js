// One source file's paragraphs in the language `translation: language:` names in the
// configuration, with the ones typeset on the current PDF page marked. The server
// translates a paragraph only the first time its text is seen, so paging, switching files
// and recompiling cost nothing except for changed paragraphs. The file is the one chosen
// in the panel, else the editor's file (Source/Split), else the file SyncTeX places most on
// the current page. The ⋯ menu's item opens the panel with or without a configured
// language; without one the item has no check mark, the panel translates nothing, and the
// Settings choice stays hidden. The language is undefined until the paper is read.
//
// createTranslation takes wait, the milliseconds before it asks again for paragraphs still being
// translated; workspace's view() and apply(), of the layout the pane is in; source (file(),
// started(), cursorRow()) and pdf (loaded(), currentPage()), which it follows; showInSource(file,
// line), which shows a paragraph's lines in the Source (places.js); digest(), the PDF's digest;
// failed(), whether the last build failed; and showProblems(). It gives attach(),
// applyLanguage(language), shown(), whether the pane shows, schedule(delay), followCursor(), for
// the editor's cursor, and followFile(), for a file the editor opens.

import { $, h, placeholder } from "../shared/dom.js";
import { responseError } from "../shared/comment-api.js";

// Codes of the languages whose lines break differently from English, by the name the
// configuration gives a language.
const TRANSLATION_LANGS = { korean: "ko", japanese: "ja", chinese: "zh" };

export function createTranslation({ wait, workspace, source, pdf, showInSource, digest, failed, showProblems }) {
  const translation = { on: false, language: undefined, page: 0, file: null, timer: null };

  function setTranslation(on) {
    translation.on = on;
    try { localStorage.setItem("texMcpTranslation", on ? "1" : "0"); } catch { /* per-viewer only */ }
    showTranslation();
  }

  function applyTranslationLanguage(language) {
    if (language === translation.language) return;
    translation.language = language;
    translation.file = null;
    $("#translation-btn").textContent = language ? `Translation: ${language}` : "Translation…";
    $("#translation-btn").setAttribute("role", language ? "menuitemcheckbox" : "menuitem");
    $("#translation-place").hidden = !language;
    $("#translation-language").textContent = language || "Translation";
    showTranslation();
  }

  function translationShown() {
    return translation.on;
  }

  function translating() {
    return translation.on && Boolean(translation.language);
  }

  function showTranslation() {
    const item = $("#translation-btn");
    if (translation.language) item.setAttribute("aria-checked", String(translationShown()));
    else item.removeAttribute("aria-checked");
    workspace.apply();
    if (translating()) {
      syncTranslationFiles().catch((error) => console.error(error));
      scheduleTranslation(0);
    } else if (translationShown() && translation.language === null) {
      fetch("/translations").then((response) => response.json()).then(showTranslationHelp)
        .catch((error) => console.error(error));
    }
  }

  // What the pane says while there is no PDF to follow: that it waits for one, or after a
  // failed build, that a build has to succeed first.
  function showTranslationWait() {
    for (const id of ["#translation-page", "#translation-status", "#translation-tool"]) $(id).textContent = "";
    translation.file = null;
    $("#translation-body").replaceChildren(failed()
      ? h("p", { class: "placeholder" }, "The build failed. The translation will appear after a successful build. ",
        h("button", { type: "button", onclick: showProblems }, "Show Problems"))
      : placeholder("The translation will appear when the PDF is ready."));
  }

  // What the pane says while translation is not set up, in the words /translations gives.
  function showTranslationHelp(data) {
    for (const id of ["#translation-page", "#translation-status", "#translation-tool"]) $(id).textContent = "";
    $("#translation-body").replaceChildren(...data.help.map((line) => h("p", { text: line })));
  }

  async function syncTranslationFiles() {
    const response = await fetch("/sources");
    if (!response.ok) return;
    const files = (await response.json()).files.filter((file) => file.endsWith(".tex"));
    const select = $("#translation-file");
    const chosen = select.value;
    select.replaceChildren(h("option", { value: "", text: "Auto" }),
      ...files.map((file) => h("option", { value: file, text: file })));
    select.value = files.includes(chosen) ? chosen : "";
  }

  function scheduleTranslation(delay = 800) {
    if (!translating()) return;
    clearTimeout(translation.timer);
    translation.timer = setTimeout(() => loadTranslation().catch((error) => console.error(error)), delay);
  }


  async function loadTranslation() {
    if (!translating()) return;
    const chosen = $("#translation-file").value || (workspace.view() !== "pdf" ? source.file() : null);
    // The Preview's page marks its paragraphs, and with no file chosen, picks the file. With
    // no PDF yet a chosen file is translated without marks, and with none chosen the pane
    // says what it waits for. A PDF the Preview is still loading asks again once laid out.
    if (!pdf.loaded()) {
      if (digest()) return;
      if (!chosen) {
        showTranslationWait();
        return;
      }
    }
    const page = pdf.loaded() ? pdf.currentPage() : null;
    const params = new URLSearchParams();
    if (page !== null) params.set("page", String(page));
    if (chosen) params.set("file", chosen);
    const request = params.toString();
    translation.request = request;
    const response = await fetch(`/translations?${request}`);
    if (translation.request !== request) return;
    if (!response.ok) {
      $("#translation-status").textContent = await responseError(response);
      return;
    }
    const data = await response.json();
    if (data.help) {
      translation.file = null;
      showTranslationHelp(data);
      return;
    }
    $("#translation-tool").textContent = data.translator;
    $("#translation-tool").title = data.command;
    const body = $("#translation-body");
    // The language's code lets the browser break its lines as that language does: Korean
    // between words, not inside them.
    body.lang = TRANSLATION_LANGS[data.language.toLowerCase()] ?? "";
    const sameFile = data.file === translation.file;
    const pageChanged = page !== translation.page;
    const keepScroll = body.scrollTop;
    translation.file = data.file;
    translation.page = page;
    $("#translation-page").textContent = page === null ? "" : `p. ${page}`;
    // The head's one line: what runs, or why the command failed, the translations kept showing.
    const status = $("#translation-status");
    status.textContent = data.status === "pending" ? "Translating…"
      : data.status === "error" ? `Translation failed: ${data.error}`
      : data.error ? `The translation command failed: ${data.error}. Showing saved translations.` : "";
    status.title = status.textContent;
    const items = data.paragraphs.map((paragraph) => h("p", {
      class: ["translation-para", paragraph.text ? "" : "pending", paragraph.on_page ? "on-page" : ""].join(" ").trim(),
      title: `Open ${data.file}:${paragraph.line_start}-${paragraph.line_end} in the editor`,
      data: { start: paragraph.line_start, end: paragraph.line_end },
      onclick: () => showInSource(data.file, paragraph.line_start),
    }, h("span", {
      class: "translation-line",
      text: paragraph.line_start === paragraph.line_end
        ? String(paragraph.line_start) : `${paragraph.line_start}–${paragraph.line_end}`,
    }), h("span", { class: "translation-text", text: paragraph.text ?? "Translating…" })));
    body.replaceChildren(...items);
    markTranslationCursor();
    const first = items.find((item) => item.classList.contains("on-page"));
    if ((!sameFile || pageChanged) && first) body.scrollTop = Math.max(0, first.offsetTop - 8);
    else if (sameFile) body.scrollTop = keepScroll;
    else body.scrollTop = 0;
    if (data.status === "pending") translation.timer = setTimeout(() => scheduleTranslation(0), wait);
    // A command that failed is tried again now and then, and the line goes once it works.
    else if (data.error) translation.timer = setTimeout(() => scheduleTranslation(0), 60000);
  }

  // The paragraph holding the editor's cursor, while the two show the same file.
  function markTranslationCursor() {
    const line = source.started() && translation.file === source.file()
      ? source.cursorRow() + 1 : null;
    let marked = null;
    for (const item of $("#translation-body").children) {
      const here = line !== null && Number(item.dataset.start) <= line && line <= Number(item.dataset.end);
      item.classList.toggle("at-cursor", here);
      if (here) marked = item;
    }
    return marked;
  }

  // A file the editor opened, which the translation follows while the Source shows it.
  function followSourceFile() {
    if (workspace.view() !== "pdf") scheduleTranslation(300);
  }

  function followSourceCursor() {
    if (!translating()) return;
    const item = markTranslationCursor();
    if (item === null) return;
    const body = $("#translation-body");
    const above = item.offsetTop < body.scrollTop;
    const below = item.offsetTop + item.offsetHeight > body.scrollTop + body.clientHeight;
    if (above || below) body.scrollTop = Math.max(0, item.offsetTop - 8);
  }

  // The translation's type size, kept for next time, stepped as the editor's is.
  function translationFontSize() {
    let stored = 0;
    try { stored = Number(localStorage.getItem("texMcpTranslationFontSize")); } catch { /* the default */ }
    return stored >= 10 && stored <= 28 ? stored : 14;
  }

  function stepTranslationFontSize(step) {
    const size = Math.min(28, Math.max(10, translationFontSize() + step));
    try { localStorage.setItem("texMcpTranslationFontSize", String(size)); } catch { /* per-viewer only */ }
    $("#translation-body").style.fontSize = `${size}px`;
  }

  function attach() {
    $("#translation-btn").addEventListener("click", () => setTranslation(!translation.on));
    $("#translation-file").addEventListener("change", () => scheduleTranslation(0));
    $("#translation-body").style.fontSize = `${translationFontSize()}px`;
    $("#translation-font-smaller-btn").addEventListener("click", () => stepTranslationFontSize(-1));
    $("#translation-font-larger-btn").addEventListener("click", () => stepTranslationFontSize(1));
    try { if (localStorage.getItem("texMcpTranslation") === "1") setTranslation(true); } catch { /* per-viewer only */ }
  }

  return {
    attach,
    applyLanguage: applyTranslationLanguage,
    shown: translationShown,
    schedule: scheduleTranslation,
    followCursor: followSourceCursor,
    followFile: followSourceFile,
  };
}
