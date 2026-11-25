// What the editor's Vim keys add to Ace's Vim: commands of the page's own (defineVimCommands), a
// held key that repeats, yanks that reach the clipboard, an input that stays at the cursor, and
// keys read by where they sit while an input method is on (attachVimInputMethod). Each takes the
// Ace editor, and defineVimCommands Ace's Vim and the editor's functions its commands call too;
// vimCommandMode(editor) says whether Vim keys are on outside Insert mode.

import { COMPOSED, keyByPosition } from "../shared/dom.js";

// Outside Insert mode Vim moves the editor's hidden input off to the editor's left, out of
// view, and Safari, bringing it into view at each key, made the text shake and flicker as
// the cursor moved. The input stays at the cursor, inside the editor, in every mode. Ace
// keeps it away from the cursor on iOS itself, which a touch screen leaves alone.
export function keepInputAtCursor(editor) {
  if (window.matchMedia("(pointer: coarse)").matches) return;
  const keep = () => {
    editor.renderer.$keepTextAreaAtCursor = true;
    editor.renderer.$moveTextAreaToCursor();
  };
  editor.state.cm.on("vim-mode-change", keep);
  keep();
}

// What Vim yanks, deletes or changes goes to the system clipboard too, so it pastes in
// another program. A browser lets a page write the clipboard only on localhost or https.
export function syncYanksToClipboard(editor) {
  const Vim = window.ace.require("ace/keyboard/vim").Vim;
  const unnamed = () => Vim.getRegisterController().getRegister('"').toString();
  let last = unnamed();
  // Vim says a command is done as it starts on it, before the text reaches the register.
  editor.state.cm.on("vim-command-done", () => setTimeout(() => {
    const text = unnamed();
    if (text === last) return;
    last = text;
    navigator.clipboard?.writeText(text).catch(() => {});
  }));
}

// macOS answers a letter held down in a text field with its accent menu rather than a
// repeat, so j held to run down the file opened the menu. Outside Insert mode the editor's
// input field is read-only, which the menu leaves alone, so a held key repeats. A touch
// screen keeps the field writable: its on-screen keyboard opens only for one.
export function holdKeysOutsideInsert(editor) {
  if (window.matchMedia("(pointer: coarse)").matches) return;
  const cm = editor.state.cm;
  const field = editor.textInput.getElement();
  const sync = () => { field.readOnly = !cm.state.vim.insertMode; };
  cm.on("vim-mode-change", sync);
  sync();
}

// :w saves, and gc comments on what a motion or the visual selection covers, as Vim's
// operators do: gcc the line, gcip the paragraph, gc on a selection the selection.
let vimCommandsDefined = false;

export function defineVimCommands(Vim, editor, does) {
  if (vimCommandsDefined) return;
  vimCommandsDefined = true;
  const {
    compileFromKeys, saveSource, commentSourceSelection, showCommentAtCursor, showSourceSelectionInPdf,
    goToDefinition, backFromDefinition, setSourceStatus,
  } = does;
  // jj typed quickly leaves Insert mode, as Escape and Ctrl+[ do.
  Vim.map("jj", "<Esc>", "insert");
  Vim.defineEx("make", "mak", () => compileFromKeys());
  Vim.defineEx("write", "w", () =>
    saveSource().catch((error) => alert(`Could not save: ${error.message}`)));
  // What an operator covers, selected in the editor; its start is where the cursor ends.
  const select = (ranges) => {
    const ends = [ranges[0].anchor, ranges[0].head].sort((a, b) => a.line - b.line || a.ch - b.ch);
    const [start] = ends;
    let [, end] = ends;
    // A line's operator ends at the start of the next line, which is not part of it.
    if (end.ch === 0 && end.line > start.line) {
      end = { line: end.line - 1, ch: editor.session.getLine(end.line - 1).length };
    }
    const Range = window.ace.require("ace/range").Range;
    editor.selection.setRange(new Range(start.line, start.ch, end.line, end.ch));
    return start;
  };
  Vim.defineOperator("sourceComment", (cm, args, ranges) => {
    const start = select(ranges);
    commentSourceSelection();
    return start;
  });
  Vim.mapCommand("gc", "operator", "sourceComment");
  Vim.defineAction("showCommentAtCursor", () => showCommentAtCursor());
  Vim.mapCommand("K", "action", "showCommentAtCursor", {}, { context: "normal" });
  // gs shows what a motion or the selection covers in the Preview, as → Preview does: gss
  // the line, gsip the paragraph.
  Vim.defineOperator("showInPreview", (cm, args, ranges) => {
    const start = select(ranges);
    showSourceSelectionInPdf().catch((error) => alert(`Could not show it in the Preview: ${error.message}`));
    return start;
  });
  Vim.mapCommand("gs", "operator", "showInPreview");
  Vim.defineAction("goToDefinition", () => {
    goToDefinition().catch((error) => setSourceStatus(error.message, "conflict"));
  });
  Vim.mapCommand("gd", "action", "goToDefinition", {}, { context: "normal" });
  Vim.mapCommand("<C-]>", "action", "goToDefinition", {}, { context: "normal" });
  Vim.defineAction("backFromDefinition", () => {
    backFromDefinition().catch((error) => setSourceStatus(error.message, "conflict"));
  });
  Vim.mapCommand("<C-t>", "action", "backFromDefinition", {}, { context: "normal" });
}

// With Vim keys and an input method on, as with Korean, a key outside Insert mode reaches
// the page as the method's own event and then as the letter it composed, and Vim knew
// neither: the command was lost and the letter went into the text. The key is read by
// where it sits on the keyboard instead, and the letter that follows is dropped.
export function vimCommandMode(editor) {
  const vim = editor?.state?.cm?.state.vim;
  return Boolean(editor?.$vimModeHandler && vim && !vim.insertMode);
}

export function attachVimInputMethod(editor) {
  editor.container.addEventListener("keydown", (event) => {
    const field = editor.textInput.getElement();
    if (!vimCommandMode(editor) || event.target !== field || event.ctrlKey || event.metaKey || event.altKey) return;
    let key = null;
    if (event.isComposing || event.key === "Process" || COMPOSED.test(event.key)) {
      key = keyByPosition(event) ?? null;
    } else if (field.readOnly && event.key.length === 1 && event.key !== " ") {
      // A read-only field (below) takes no typing, so a character key reaches Vim here,
      // where Vim otherwise reads it from the typing. Space is a named key Vim reads itself.
      key = event.key;
    }
    if (key === null) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    window.ace.require("ace/keyboard/vim").Vim.handleKey(editor.state.cm, key, "user");
  }, true);
  // Safari starts a composition at every key while a Korean method is on, and Ace answers
  // one outside Insert mode by cancelling it, which takes the focus off the editor and back:
  // the cursor and selection flickered at each key. The keys are read above, so outside
  // Insert mode the composition never reaches Ace.
  for (const kind of ["compositionstart", "compositionupdate", "compositionend"]) {
    editor.container.addEventListener(kind, (event) => {
      if (vimCommandMode(editor) && event.target === editor.textInput.getElement()) event.stopImmediatePropagation();
    }, true);
  }
  const typed = editor.onTextInput.bind(editor);
  editor.onTextInput = (text, ...rest) => {
    if (vimCommandMode(editor) && COMPOSED.test(text)) return;
    typed(text, ...rest);
  };
}
