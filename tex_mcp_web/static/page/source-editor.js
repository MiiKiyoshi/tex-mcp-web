// The source editor (Ace): the file open in it, saved and reloaded, its status line, the bar
// above it, its Vim keys (vim-keys.js), completion (completion.js), go to definition
// (definitions.js), the cursor held where Settings says, and the marks on the comments' words
// (source-marks.js). A comment on the selected words, and the words shown in the Preview, start
// from here too.
//
// createSourceEditor takes panelComments, the comment panel as the editor reaches it: marked(),
// the open comments of the comment tab shown, showTab() and show(id); project() and suggested(),
// the Overleaf project and its suggested edits; showWherePrinted({ file, line_start, line_end,
// text }), which shows words selected here where the Preview prints them (places.js);
// openCompose(anchor, label, fields), refreshComments() and openSuggestedEdit(id);
// compileFromKeys(); followed(path), whether Overleaf's live session keeps a file in step; and
// cursorAt(), where Settings holds the cursor's line.
//
// It gives attach() and init(); what happens in it, each of which hands in a function called
// when it does: opened(listener), a file opened, cursorMoved(listener) and drawn(listener), the
// editor drawn again; the file open (file(), dirty(), open(path, line, force, focus),
// save(), refreshFiles(), sourceChanged(message)); the editor (started(), ace(), resize(),
// focus(), setTheme(), holdCursorLine(), cursorRow()); a place shown in it (showPlace(row,
// column), selectQuote(quote, first, last)); markComments(), applyLeaf(), for the leaf beside
// + Comment, and scheduleCompletions(delay).

import { $, h, clear } from "../shared/dom.js";
import { createListeners } from "./listeners.js";
import { RING_AT } from "../shared/comment-keys.js";
import { responseError } from "../shared/comment-api.js";
import { setSidebarCollapsed } from "../shared/sidebar.js";
import { attachMenu } from "./menu.js";
import { createSourceMarks } from "./source-marks.js";
import { createCompletion } from "./completion.js";
import { createDefinitions } from "./definitions.js";
import {
  defineVimCommands, holdKeysOutsideInsert, syncYanksToClipboard, keepInputAtCursor, attachVimInputMethod,
} from "./vim-keys.js";

function sourceMode(path) {
  if (path.endsWith(".tex")) return "ace/mode/latex";
  if (path.endsWith(".bib")) return "ace/mode/bibtex";
  return "ace/mode/text";
}

function darkTheme() {
  return document.documentElement.dataset.theme === "dark";
}

function editorTheme() {
  return darkTheme() ? "ace/theme/one_dark" : "ace/theme/textmate";
}

// The editor's type size, kept for next time. Stepped by a pixel between 10 and 28.
function sourceFontSize() {
  const stored = Number(localStorage.getItem("texMcpSourceFontSize"));
  return stored >= 10 && stored <= 28 ? stored : 13;
}

// Long lines wrap unless the viewer turned it off, and Vim keys are off unless the viewer
// turned them on, each kept for next time. With Vim keys, :w saves.
function sourceWraps() {
  return localStorage.getItem("texMcpSourceWrap") !== "0";
}

function vimKeys() {
  return localStorage.getItem("texMcpVimKeys") === "1";
}

// With the leaf pressed beside + Comment, a comment on source text starts a thread for
// Overleaf, where comments sit on source words: what the reviewer writes is for the agent,
// which drafts the thread's first message, and the reviewer sends it. Kept for next time, and
// offered once the paper names its Overleaf project.
function sourceForOverleaf() {
  try { return localStorage.getItem("texMcpSourceLeaf") === "1"; } catch { return false; }
}

export function createSourceEditor({
  panelComments, project, suggested, showWherePrinted, openCompose, refreshComments, openSuggestedEdit,
  compileFromKeys, followed, cursorAt,
}) {
  const opened = createListeners();
  const cursorMoved = createListeners();
  const drawn = createListeners();
  const state = {
    editor: null,
    sourcePath: null,
    sourceRevision: null,
    sourceDirty: false,
    sourceLoading: false,
    // What the editor's status line last said of the file, how ("", "dirty" or "conflict"), and
    // whether it was said since the cursor last moved.
    sourceStatus: { text: "Open Source to edit", kind: "", fresh: true },
    // Whether a press in the editor is held down, which moves the cursor with the text in place.
    editorPointing: false,
  };
  const marks = createSourceMarks({
    editor: () => state.editor,
    file: () => state.sourcePath,
    dirty: () => state.sourceDirty,
    comments: () => panelComments.marked(),
    suggested,
  });
  const completion = createCompletion({ sourcePath: () => state.sourcePath });
  const definitions = createDefinitions({
    ace: () => state.editor, sourcePath: () => state.sourcePath, openSource, setSourceStatus,
  });

  // The words a PDF shows and the TeX that prints them differ in spacing, in ties, in words
  // split at a line's end and in ligatures, so they are compared with all of that set aside.
  // Words a macro prints are not in the source as written; the lines are selected then.
  function selectQuoteInLines(quote, first, last) {
    const Range = window.ace.require("ace/range").Range;
    const session = state.editor.session;
    const lines = session.getLines(first - 1, last - 1);
    const kept = [];
    lines.forEach((line, row) => {
      for (let column = 0; column < line.length; column += 1) {
        if (!/[\s~]/.test(line[column])) kept.push({ character: line[column], row: first - 1 + row, column });
      }
    });
    const text = kept.map((item) => item.character).join("");
    const unligated = quote.replace(/ﬀ/g, "ff").replace(/ﬁ/g, "fi").replace(/ﬂ/g, "fl")
      .replace(/ﬃ/g, "ffi").replace(/ﬄ/g, "ffl");
    let range = null;
    for (const words of [unligated.replace(/(\p{L})-\s+(\p{L})/gu, "$1$2"), unligated]) {
      const wanted = words.replace(/\s+/g, "");
      const at = wanted === "" ? -1 : text.indexOf(wanted);
      if (at < 0) continue;
      const start = kept[at];
      const end = kept[at + wanted.length - 1];
      range = new Range(start.row, start.column, end.row, end.column + 1);
      break;
    }
    if (range === null) range = new Range(first - 1, 0, last - 1, lines[lines.length - 1].length);
    state.editor.selection.setRange(range);
    state.editor.scrollToLine(range.start.row, true, true, () => {});
    state.editor.focus();
  }

  function setSourceStatus(text, kind = "") {
    state.sourceStatus = { text, kind, fresh: true };
    showSourceStatus();
  }

  // The cursor moved: what was last said of the file has been seen.
  function sourceCursorMoved() {
    state.sourceStatus.fresh = false;
    showSourceStatus();
  }

  // The editor's status line: what was last said of the file, or, once the cursor has moved
  // into words a comment or a suggested edit is on and the file has nothing else to say, that
  // it is there and which key opens it, as a click on its mark beside the line does.
  function showSourceStatus() {
    const status = $("#source-status");
    const { text, kind, fresh } = state.sourceStatus;
    const here = !kind && !fresh && state.editor ? marks.atCursor() : undefined;
    const key = vimKeys() ? "K" : /Mac|iPhone|iPad|iPod/.test(navigator.platform) ? "Cmd+Option+K" : "Ctrl+Alt+K";
    status.textContent = here === undefined ? text
      : `${here.suggested ? "A suggested edit" : "A comment"} is here. ${key} opens it.`;
    status.classList.toggle("dirty", kind === "dirty");
    status.classList.toggle("conflict", kind === "conflict");
  }


  function setSourceDirty(dirty) {
    state.sourceDirty = dirty;
    $("#source-save-btn").disabled = !dirty;
    if (dirty) {
      setSourceStatus("Unsaved", "dirty");
      marks.clear();
    }
    updateSourceCommentButton();
  }

  function updateSourceCommentButton() {
    const button = $("#source-comment-btn");
    if (!button) return;
    const selected = Boolean(state.editor?.getSelectedText().trim());
    // With unsaved edits a press says to save first, in the bar, as the keys do.
    button.disabled = !selected;
    const where = sourceForOverleaf() && project() ? "Start a thread on Overleaf on" : "Comment on";
    button.title = state.sourceDirty
      ? "Save source before commenting"
      : selected ? `${where} the selected source text (Ctrl+Alt+M, gc with Vim keys)` : "Select source text to comment";
    button.setAttribute("aria-label", button.title);
    $("#source-pdf-btn").disabled = !selected;
  }

  // The words selected in the editor, shown where the Preview prints them. A selection that ends
  // at the start of a line ends on the line before.
  async function showSourceSelectionInPdf() {
    const text = state.editor.getSelectedText();
    if (!text.trim() || !state.sourcePath) return;
    const range = state.editor.getSelectionRange();
    const lastRow = range.end.column === 0 && range.end.row > range.start.row ? range.end.row - 1 : range.end.row;
    await showWherePrinted({ file: state.sourcePath, line_start: range.start.row + 1, line_end: lastRow + 1, text });
  }

  // The marks drawn again, and the status line, which says when the cursor is in their words.
  function syncSourceCommentMarkers() {
    if (marks.sync()) showSourceStatus();
  }

  function openSourceCommentCompose() {
    if (!state.editor || !state.sourcePath || state.sourceDirty) return;
    const text = state.editor.getSelectedText();
    const range = state.editor.getSelectionRange();
    if (!text || range.isEmpty()) return;
    // A selection that ends at the start of a line, as one of whole lines does, ends on the line
    // before: the line it ends at holds none of it.
    let end = range.end;
    while (end.column === 0 && end.row > range.start.row) {
      const row = end.row - 1;
      const column = state.editor.session.getLine(row).length;
      if (row === range.start.row && column <= range.start.column) break;
      end = { row, column };
    }
    const lineStart = range.start.row + 1;
    const lineEnd = end.row + 1;
    // A comment on the source names the revision of the file it was written on, which the server
    // checks is still the file's, and may start a thread for Overleaf.
    const fields = {};
    if (state.sourceRevision !== null) fields.source_revision = state.sourceRevision;
    if (sourceForOverleaf() && project()) fields.overleaf = true;
    // Back in the editor once the comment is sent or dropped, to go on from where it was.
    $("#compose-dialog").addEventListener("close", () => state.editor.focus(), { once: true });
    openCompose(
      {
        kind: "source_range",
        file: state.sourcePath,
        line_start: lineStart,
        line_end: lineEnd,
        column_start: range.start.column,
        column_end: end.column,
      },
      `Source: ${state.sourcePath}:${lineStart}-${lineEnd}`,
      fields,
    );
  }

  // A line given puts the cursor there and the keys in the editor, unless *focus* is false.
  async function openSource(path, line = null, force = false, focus = true) {
    if (!state.editor || !path) return false;
    if (!force && path === state.sourcePath) {
      if (line !== null) {
        state.editor.gotoLine(Math.max(1, Number(line)), 0, true);
        state.editor.focus();
      }
      return true;
    }
    if (!force && state.sourceDirty && path !== state.sourcePath
        && !confirm(`Discard unsaved changes to ${state.sourcePath}?`)) {
      showSourceFile(state.sourcePath);
      return false;
    }

    const response = await fetch(`/source?${new URLSearchParams({ path })}`);
    if (!response.ok) throw new Error(await responseError(response));
    const source = await response.json();
    state.sourceLoading = true;
    state.editor.session.setMode(sourceMode(source.path));
    state.editor.setValue(source.text, -1);
    state.sourceLoading = false;
    state.sourcePath = source.path;
    state.sourceRevision = source.revision;
    state.sourceDirty = false;
    showSourceFile(source.path);
    opened.tell();
    $("#source-save-btn").disabled = true;
    $("#source-reload-btn").disabled = false;
    setSourceStatus("Saved");
    syncSourceCommentMarkers();
    updateSourceCommentButton();
    if (line !== null) {
      state.editor.gotoLine(Math.max(1, Number(line)), 0, true);
      if (focus) state.editor.focus();
    }
    return true;
  }

  async function refreshSourceFiles() {
    const response = await fetch("/sources");
    if (!response.ok) throw new Error(await responseError(response));
    const result = await response.json();
    const select = $("#source-file");
    clear(select);
    for (const path of result.files) {
      select.appendChild(h("option", { value: path, text: path }));
    }
    if (result.files.length === 0) {
      state.editor.setReadOnly(true);
      setSourceStatus("No watched source files");
      return;
    }
    state.editor.setReadOnly(false);
    const preferred = result.files.includes(state.sourcePath)
      ? state.sourcePath
      : result.files.includes(result.main_file)
        ? result.main_file
        : result.files[0];
    showSourceFile(preferred);
    if (preferred !== state.sourcePath) await openSource(preferred);
  }

  // The file list's choice, and on a touch screen the name drawn in the bar's own type under
  // the list, which lies over it, clear, and still opens on a tap.
  function showSourceFile(path) {
    $("#source-file").value = path;
    $("#source-file-name").textContent = path.split("/").pop();
  }


  function stepSourceFontSize(step) {
    const size = Math.min(28, Math.max(10, sourceFontSize() + step));
    localStorage.setItem("texMcpSourceFontSize", String(size));
    state.editor?.setFontSize(`${size}px`);
  }


  function applySourceWrap() {
    const on = sourceWraps();
    $("#source-wrap-btn").setAttribute("aria-checked", String(on));
    state.editor?.session.setUseWrapMode(on);
  }


  function applySourceLeaf() {
    const button = $("#source-leaf-btn");
    const on = sourceForOverleaf() && Boolean(project());
    button.hidden = !project();
    button.classList.toggle("active", on);
    button.setAttribute("aria-pressed", String(on));
    button.title = on ? "Overleaf threads are on: a new comment from the Source starts a public thread on Overleaf. "
      + "Click to make new comments private."
      : "Overleaf threads are off: a new comment from the Source is a private comment. "
      + "Click to start public threads on Overleaf instead.";
    updateSourceCommentButton();
  }

  // The cursor's line held a third or a half of the way down the editor, as the comment keys
  // hold the ringed card in the list, when the viewer chose it in Settings: the keys move the
  // text under the cursor and the eyes stay where they are. A click or a drag leaves the text
  // where it is. The first lines stay at the top and the last ones at the bottom, with no room
  // made above or below the text to hold them there.
  function holdCursorLine() {
    const editor = state.editor;
    const at = cursorAt();
    if (!editor || at === undefined || state.editorPointing) return;
    const { row, column } = editor.getCursorPosition();
    const top = editor.session.documentToScreenRow(row, column) * editor.renderer.lineHeight;
    editor.session.setScrollTop(Math.max(0, top - editor.renderer.scroller.clientHeight * at));
  }

  function applyVimKeys() {
    const on = vimKeys();
    $("#source-vim-btn").setAttribute("aria-checked", String(on));
    state.editor?.setKeyboardHandler(on ? "ace/keyboard/vim" : null, () => {
      // Leaving Vim keys keeps Vim's block cursor, under which a click takes the character
      // before the one clicked.
      if (!on) {
        state.editor.renderer.$blockCursor = false;
        state.editor.textInput.getElement().readOnly = false;
        return;
      }
      defineVimCommands(window.ace.require("ace/keyboard/vim").Vim, state.editor, {
        compileFromKeys, saveSource, commentSourceSelection, showCommentAtCursor, showSourceSelectionInPdf,
        goToDefinition: definitions.go, backFromDefinition: definitions.back, setSourceStatus,
      });
      holdKeysOutsideInsert(state.editor);
      syncYanksToClipboard(state.editor);
      keepInputAtCursor(state.editor);
    });
  }

  // K with Vim keys, or Ctrl+Alt+K (Cmd+Option+K), opens in the comments the comment the
  // cursor is on, as a click on its mark beside the line does: the narrowest when several
  // cover the cursor, else one on the cursor's line. The comment keys then act on it, and K there
  // brings the cursor back to its words. Off any comment, the keys go to the list as it was.
  function showCommentAtCursor() {
    const { row } = state.editor.getCursorPosition();
    const chosen = marks.atCursor() ?? marks.onRow(row)?.[0];
    setSidebarCollapsed(false);
    state.editor.blur();
    if (chosen?.suggested) {
      openSuggestedEdit(chosen.id).catch((error) => console.error(error));
      return;
    }
    panelComments.showTab();
    if (chosen !== undefined) panelComments.show(chosen.id).catch((error) => console.error(error));
    $("#comments-list").focus({ preventScroll: true });
  }


  // The comment button, Ctrl+Alt+M (Cmd+Option+M), or gc with Vim keys. Comments wait for the
  // file to be saved, and the bar says so.
  function commentSourceSelection() {
    if (state.sourceDirty) {
      setSourceStatus("Save before commenting", "dirty");
      return;
    }
    openSourceCommentCompose();
  }

  async function initializeSourceEditor() {
    if (state.editor) return;
    if (!window.ace) throw new Error("Ace Editor did not load");
    window.ace.config.set("basePath", "/static/ace");
    state.editor = window.ace.edit("source-editor");
    state.editor.setTheme(editorTheme());
    // Ace restarts its cursor's blinking animation at every move, and Safari redrew the whole
    // editor each time, so the text flickered as the cursor moved. In Safari the cursor holds
    // steady instead. Every browser on an iPhone or iPad is Safari underneath.
    if (/iPhone|iPad|iPod/.test(navigator.userAgent)
      || /^((?!chrome|chromium|android|edg).)*safari/i.test(navigator.userAgent)) {
      state.editor.container.classList.add("steady-cursor");
    }
    state.editor.session.setMode("ace/mode/latex");
    state.editor.session.setUseWorker(false);
    state.editor.session.setUseWrapMode(sourceWraps());
    state.editor.setOptions({
      fontSize: `${sourceFontSize()}px`,
      showPrintMargin: false,
      scrollPastEnd: 0.25,
    });
    // A press in the text or beside it moves the cursor itself, before the editor does.
    for (const name of ["mousedown", "guttermousedown"]) state.editor.on(name, () => { state.editorPointing = true; });
    document.addEventListener("pointerup", () => { state.editorPointing = false; }, true);
    state.editor.selection.on("changeCursor", holdCursorLine);
    state.editor.selection.on("changeCursor", sourceCursorMoved);
    holdCursorLine();
    state.editor.on("change", () => {
      if (!state.sourceLoading) setSourceDirty(true);
    });
    state.editor.on("changeSelection", updateSourceCommentButton);
    state.editor.selection.on("changeCursor", () => cursorMoved.tell());
    state.editor.on("guttermousedown", (event) => {
      const row = event.getDocumentPosition().row;
      // A suggested edit's mark beside its line opens it in the leaf tab.
      const edit = suggested().find((item) => item.file === state.sourcePath && item.here?.line_start === row + 1);
      if (edit && event.domEvent.target.classList.contains("source-suggested-line")
          && !event.domEvent.target.classList.contains("source-comment-line")) {
        event.stop();
        setSidebarCollapsed(false);
        openSuggestedEdit(edit.id).catch((error) => console.error(error));
        return;
      }
      const comments = marks.onRow(row);
      if (!comments?.length || !event.domEvent.target.classList.contains("source-comment-line")) return;
      event.stop();
      setSidebarCollapsed(false);
      panelComments.showTab();
      panelComments.show(comments[0].id).catch((error) => console.error(error));
    });
    state.editor.commands.addCommand({
      name: "saveSource",
      bindKey: { win: "Ctrl-S", mac: "Command-S" },
      exec: () => saveSource().catch((error) => alert(`Could not save: ${error.message}`)),
    });
    state.editor.commands.addCommand({
      name: "backFromDefinition",
      bindKey: { win: "Shift-F12", mac: "Shift-F12" },
      exec: () => definitions.back().catch((error) => setSourceStatus(error.message, "conflict")),
    });
    state.editor.commands.addCommand({
      name: "goToDefinition",
      bindKey: { win: "F12", mac: "F12" },
      exec: () => definitions.go().catch((error) => setSourceStatus(error.message, "conflict")),
    });
    state.editor.commands.addCommand({
      name: "showInPreview",
      bindKey: { win: "Ctrl-Alt-P", mac: "Command-Alt-P" },
      exec: () => showSourceSelectionInPdf().catch((error) => alert(`Could not show it in the Preview: ${error.message}`)),
    });
    state.editor.commands.addCommand({
      name: "showCommentAtCursor",
      bindKey: { win: "Ctrl-Alt-K", mac: "Command-Alt-K" },
      exec: () => showCommentAtCursor(),
    });
    state.editor.commands.addCommand({
      name: "commentSource",
      bindKey: { win: "Ctrl-Alt-M", mac: "Command-Alt-M" },
      exec: commentSourceSelection,
    });
    attachVimInputMethod(state.editor);
    definitions.attachBack(state.editor);
    completion.attach(state.editor);
    state.editor.renderer.on("afterRender", () => drawn.tell());
    applyVimKeys();
    await refreshSourceFiles();
  }

  async function saveSource() {
    if (!state.editor || !state.sourcePath || !state.sourceDirty) return;
    const button = $("#source-save-btn");
    button.disabled = true;
    setSourceStatus("Saving…");
    const response = await fetch(`/source?${new URLSearchParams({ path: state.sourcePath })}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: state.editor.getValue(),
        revision: state.sourceRevision,
      }),
    });
    if (response.status === 409) {
      setSourceStatus("Changed on disk · reload", "conflict");
      button.disabled = false;
      return;
    }
    if (!response.ok) {
      button.disabled = false;
      setSourceStatus("Save failed", "conflict");
      throw new Error(await responseError(response));
    }
    const result = await response.json();
    if (typeof result.text === "string" && result.text !== state.editor.getValue()) {
      // Brought together with edits made on Overleaf meanwhile: the editor takes the result
      // where it stands.
      const cursor = state.editor.getCursorPosition();
      const top = state.editor.session.getScrollTop();
      state.sourceLoading = true;
      state.editor.session.doc.setValue(result.text);
      state.sourceLoading = false;
      state.editor.moveCursorToPosition(cursor);
      state.editor.clearSelection();
      state.editor.session.setScrollTop(top);
    }
    state.sourceRevision = result.revision;
    state.sourceDirty = false;
    button.disabled = true;
    setSourceStatus(typeof result.text === "string" ? "Saved with Overleaf's edits" : "Saved");
    completion.schedule(300);
    await refreshComments();
    updateSourceCommentButton();
  }

  async function reloadSource() {
    if (!state.sourcePath) return;
    if (state.sourceDirty && !confirm(`Discard unsaved changes to ${state.sourcePath}?`)) return;
    const line = state.editor.getCursorPosition().row + 1;
    await openSource(state.sourcePath, line, true);
  }


  async function handleSourceChanged(message) {
    if (!state.editor || message.path !== state.sourcePath
        || message.revision === state.sourceRevision) return;
    if (state.sourceDirty) {
      const inStep = followed(message.path);
      setSourceStatus(inStep ? "Edited on Overleaf · saving merges both" : "Changed on disk · reload",
        inStep ? "" : "conflict");
      return;
    }
    const line = state.editor.getCursorPosition().row + 1;
    // A file changed under the reader leaves the keys where they are, in a reply being written
    // or with the comment list.
    const focused = state.editor.isFocused();
    await refreshComments();
    await openSource(state.sourcePath, line, true, focused);
  }

  // A place shown from the comments: the cursor on it, and its line a third of the way down the
  // editor, as the ring is in the list, wherever the editor was scrolled, so the eye finds it there.
  function showSourcePlace(row, column) {
    const editor = state.editor;
    editor.selection.clearSelection();
    editor.session.unfold({ row, column });
    editor.moveCursorTo(row, column);
    const top = editor.session.documentToScreenRow(row, column) * editor.renderer.lineHeight;
    editor.session.setScrollTop(Math.max(0, top - editor.renderer.scroller.clientHeight * RING_AT));
  }

  async function sourceChanged(message) {
    // A file the list does not hold yet is new: the list is read again.
    if (state.editor && ![...$("#source-file").options].some((option) => option.value === message.path)) {
      await refreshSourceFiles();
    }
    await handleSourceChanged(message);
  }

  function setTheme() {
    state.editor?.setTheme(editorTheme());
  }

  function attach() {
    window.addEventListener("beforeunload", (event) => {
      if (!state.sourceDirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
    $("#source-save-btn").addEventListener("click", () => {
      saveSource().catch((error) => alert(`Could not save: ${error.message}`));
    });
    $("#source-reload-btn").addEventListener("click", () => {
      reloadSource().catch((error) => alert(`Could not reload: ${error.message}`));
    });
    // The editor's ⋯ menu stays open as the text size steps, and Wrap long lines or Vim keys
    // closes it.
    attachMenu($("#source-more-btn"), $("#source-more-menu"), {
      closes: (target) => target.closest('#source-more-menu [role="menuitemcheckbox"]'),
    });
    $("#source-font-smaller-btn").addEventListener("click", () => stepSourceFontSize(-1));
    $("#source-font-larger-btn").addEventListener("click", () => stepSourceFontSize(1));
    $("#source-wrap-btn").addEventListener("click", () => {
      localStorage.setItem("texMcpSourceWrap", sourceWraps() ? "0" : "1");
      applySourceWrap();
    });
    $("#source-vim-btn").addEventListener("click", () => {
      localStorage.setItem("texMcpVimKeys", vimKeys() ? "0" : "1");
      applyVimKeys();
      state.editor?.focus();
    });
    $("#source-leaf-btn").addEventListener("click", () => {
      try { localStorage.setItem("texMcpSourceLeaf", sourceForOverleaf() ? "0" : "1"); } catch { /* this visit only */ }
      applySourceLeaf();
      state.editor?.focus();
    });
    applySourceWrap();
    applyVimKeys();
    $("#source-comment-btn").addEventListener("click", commentSourceSelection);
    $("#source-pdf-btn").addEventListener("click", () => {
      showSourceSelectionInPdf().catch((error) => alert(`Could not show it in the Preview: ${error.message}`));
    });
    $("#source-file").addEventListener("change", (event) => {
      openSource(event.target.value).catch((error) => alert(`Could not open source: ${error.message}`));
    });
  }

  return {
    attach,
    init: initializeSourceEditor,
    opened: opened.add,
    cursorMoved: cursorMoved.add,
    drawn: drawn.add,
    started: () => Boolean(state.editor),
    ace: () => state.editor,
    file: () => state.sourcePath,
    dirty: () => state.sourceDirty,
    open: openSource,
    save: saveSource,
    refreshFiles: refreshSourceFiles,
    sourceChanged,
    resize: () => state.editor?.resize(),
    focus: () => state.editor?.focus(),
    setTheme,
    holdCursorLine,
    showPlace: showSourcePlace,
    selectQuote: selectQuoteInLines,
    cursorRow: () => state.editor.getCursorPosition().row,
    markComments: syncSourceCommentMarkers,
    applyLeaf: applySourceLeaf,
    scheduleCompletions: completion.schedule,
  };
}
