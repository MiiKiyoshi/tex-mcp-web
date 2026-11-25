// Go to definition, and back. createDefinitions takes ace(), the Ace editor; sourcePath(), the
// open file; openSource(path, line); and setSourceStatus(text, kind). It gives go(), back(), and
// attachBack(editor), for Ctrl+O with Vim keys.

import { $ } from "../shared/dom.js";
import { vimCommandMode } from "./vim-keys.js";

// gd with Vim keys, or F12, goes from what the cursor is on to where it is defined: from
// \input (or \include, \subfile) to that file, from \ref and its kin to the \label it
// names, from \cite and its kin to the entry in a .bib file. In a list such as
// \cite{a,b} it is the name under the cursor. The open file is searched first.
const DEFINED = /\\(input|include|subfile|ref|cref|Cref|eqref|autoref|pageref|nameref|cite|citep|citet|parencite|textcite)\*?(?:\[[^\]]*\])*\{([^}]*)\}/g;

export function createDefinitions({ ace, sourcePath, openSource, setSourceStatus }) {

  // Each jump to a definition leaves its starting place here, and Ctrl+T or Ctrl+O with Vim
  // keys, or Shift+F12, goes back to the last one, as Vim's tag stack does.
  const definitionTrail = [];

  // Windows browsers keep Ctrl+T for a new tab, so with Vim keys Ctrl+O, Vim's way back to
  // the last place, goes back from a definition too. With no such jump left it is Vim's own.
  function attachDefinitionBack(editor) {
    editor.container.addEventListener("keydown", (event) => {
      if (!vimCommandMode(editor) || definitionTrail.length === 0 || event.code !== "KeyO"
        || !event.ctrlKey || event.shiftKey || event.altKey || event.metaKey) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      backFromDefinition().catch((error) => setSourceStatus(error.message, "conflict"));
    }, true);
  }

  async function backFromDefinition() {
    const back = definitionTrail.pop();
    if (back === undefined) {
      setSourceStatus("Nothing to go back to");
      return;
    }
    if (await openSource(back.path, back.row + 1)) ace().moveCursorTo(back.row, back.column);
  }

  async function jumpTo(path, line, from) {
    if (await openSource(path, line)) definitionTrail.push(from);
  }

  async function goToDefinition() {
    const editor = ace();
    if (!editor) return;
    const { row, column } = editor.getCursorPosition();
    const line = editor.session.getLine(row);
    const match = [...line.matchAll(DEFINED)].find((found) =>
      column >= found.index && column <= found.index + found[0].length);
    if (match === undefined) {
      setSourceStatus("No \\input, \\ref or \\cite under the cursor");
      return;
    }
    const [whole, command, argument] = match;
    let at = match.index + whole.length - argument.length - 1;
    let name = argument.trim();
    for (const part of argument.split(",")) {
      name = part.trim();
      if (column <= at + part.length) break;
      at += part.length + 1;
    }
    const files = Array.from($("#source-file").options, (option) => option.value);
    if (["input", "include", "subfile"].includes(command)) {
      const file = [name, `${name}.tex`].find((path) => files.includes(path));
      if (file === undefined) setSourceStatus(`${name} is not among the source files`);
      else await jumpTo(file, 1, { path: sourcePath(), row, column });
      return;
    }
    const cited = command.includes("cite");
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern = cited ? new RegExp(`@\\w+\\s*\\{\\s*${escaped}\\s*,`) : new RegExp(`\\\\label\\{\\s*${escaped}\\s*\\}`);
    const candidates = files.filter((path) => path.endsWith(cited ? ".bib" : ".tex"))
      .sort((a, b) => (b === sourcePath()) - (a === sourcePath()));
    for (const path of candidates) {
      const text = path === sourcePath()
        ? editor.getValue()
        : (await (await fetch(`/source?${new URLSearchParams({ path })}`)).json()).text;
      const index = text?.search(pattern) ?? -1;
      if (index >= 0) {
        await jumpTo(path, text.slice(0, index).split("\n").length, { path: sourcePath(), row, column });
        return;
      }
    }
    setSourceStatus(`No ${cited ? "bib entry" : "\\label"} for ${name}`);
  }

  return { go: goToDefinition, back: backFromDefinition, attachBack: attachDefinitionBack };
}
