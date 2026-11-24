// The parts of Settings the review pages share: the theme, the size of the page's own text,
// the key that sends a comment, the key letters on the ringed comment's buttons, and where the
// source editor holds its cursor's line. Each choice is kept in the browser under the page's
// own prefix, so each page keeps its own.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { h } from "./dom.js";

// The themes a reader may choose, beside System. look-early.js names them too.
const THEMES = ["light", "dark", "warm"];

// Each choice as Settings shows it: its heading, what it is to a screen reader, the data
// attribute its buttons carry, and its buttons, the default first, each with its words and,
// where they say more, its title.
const CHOICES = [
  ["Theme", "Theme", "theme",
    [["system", "System"], ["light", "Light"], ["dark", "Dark"], ["warm", "Warm"]]],
  ["Text size of the top bar, panels and dialogs",
    "Size of the text in the top bar, the panels and the dialogs", "text",
    [["90", "90%"], ["100", "100%"], ["115", "115%"], ["130", "130%"], ["150", "150%"]]],
  ["Send a comment with", "Key that sends a comment", "send", [["enter", "Enter"], ["shift", "Shift+Enter"]]],
  ["Shortcut letters on a comment's buttons",
    "Show shortcut letters on the current comment's buttons while you navigate with the keyboard", "letters",
    [["on", "On"], ["off", "Off"]]],
  ["Editor cursor position", "Where the editor keeps the cursor line as you move through the text", "cursor",
    [["free", "Anywhere", "The cursor moves over the text, which scrolls only at the editor's edges"],
      ["third", "1/3 down", "The cursor's line stays 1/3 of the way down the editor, and the text moves under it"],
      ["half", "1/2 down", "The cursor's line stays in the middle of the editor, and the text moves under it"]]],
];

// Where the editor holds the cursor's line, as a share of its height, and what each choice is
// kept as, the third as it was kept when it was the only one.
const CURSOR_AT = { third: 1 / 3, half: 1 / 2 };
const CURSOR_KEPT = { free: "0", third: "1", half: "half" };

function choiceGroup([heading, label, name, buttons]) {
  return [h("h4", { text: heading }),
    h("div", { class: "setting-choice", role: "radiogroup", "aria-label": label },
      ...buttons.map(([value, text, title]) => h("button", {
        type: "button", role: "radio", "aria-checked": "false", data: { [`${name}Choice`]: value }, title, text })))];
}

function check(name, value) {
  for (const button of document.querySelectorAll(`[data-${name}-choice]`)) {
    button.setAttribute("aria-checked", String(button.dataset[`${name}Choice`] === value));
  }
}

// Fills #settings-look with the shared choices and applies what was kept. The page is told of
// the theme each time it changes, to restyle what CSS does not reach (an editor, a viewer, a
// frame); of the key letters turned on or off, given whether they show; and of a new place
// for the editor's cursor, which the page's own editor takes up.
export function createSettings({ prefix, onTheme = () => {}, onLetters = () => {}, onCursor = () => {} }) {
  const read = (name) => {
    try { return localStorage.getItem(prefix + name); } catch { return null; }
  };
  const keep = (name, value) => {
    try {
      if (value === null) localStorage.removeItem(prefix + name);
      else localStorage.setItem(prefix + name, value);
    } catch { /* the choice lasts this visit */ }
  };
  document.querySelector("#settings-look").replaceChildren(...CHOICES.flatMap(choiceGroup));

  // Light, dark or warm, as the reader last chose, else, under System, light or dark as the
  // system is, following it as it changes.
  let themeChosen = THEMES.includes(read("Theme")) ? read("Theme") : null;
  const system = matchMedia("(prefers-color-scheme: dark)");
  const applyTheme = () => {
    const theme = themeChosen ?? (system.matches ? "dark" : "light");
    document.documentElement.dataset.theme = theme;
    check("theme", themeChosen ?? "system");
    onTheme(theme);
  };
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.addEventListener("click", () => {
      themeChosen = THEMES.includes(button.dataset.themeChoice) ? button.dataset.themeChoice : null;
      keep("Theme", themeChosen);
      applyTheme();
    });
  }
  system.addEventListener("change", () => {
    if (themeChosen === null) applyTheme();
  });
  applyTheme();

  // The text of the top bar, the panels and the dialogs at the size chosen.
  const applyTextSize = (size) => {
    document.documentElement.style.setProperty("--text-scale", String(Number(size) / 100));
    check("text", size);
  };
  for (const button of document.querySelectorAll("[data-text-choice]")) {
    button.addEventListener("click", () => {
      keep("TextSize", button.dataset.textChoice);
      applyTextSize(button.dataset.textChoice);
    });
  }
  const size = read("TextSize");
  applyTextSize(document.querySelector(`[data-text-choice="${CSS.escape(size ?? "")}"]`) ? size : "100");

  // Enter sends, shift with it writing a new line, or shift with enter sends, Enter writing the
  // new line as in any box of text. The command or control key with enter sends either way.
  let sendKey = read("SendKey");
  const enterSends = () => sendKey !== "shift";
  const sendKeyName = () => (enterSends() ? "Enter" : "Shift+Enter");
  for (const button of document.querySelectorAll("[data-send-choice]")) {
    button.addEventListener("click", () => {
      sendKey = button.dataset.sendChoice;
      keep("SendKey", sendKey);
      check("send", enterSends() ? "enter" : "shift");
    });
  }
  check("send", enterSends() ? "enter" : "shift");

  // The key letters on the ringed comment's buttons, shown unless turned off.
  let lettersOff = read("KeyLetters") === "off";
  for (const button of document.querySelectorAll("[data-letters-choice]")) {
    button.addEventListener("click", () => {
      lettersOff = button.dataset.lettersChoice === "off";
      keep("KeyLetters", button.dataset.lettersChoice);
      check("letters", lettersOff ? "off" : "on");
      onLetters(!lettersOff);
    });
  }
  check("letters", lettersOff ? "off" : "on");

  // The cursor's line held a third or a half of the way down the editor, as the comment keys
  // hold the ringed card in the list, or left to move over the text.
  const cursorChoice = () => Object.keys(CURSOR_KEPT).find((choice) => CURSOR_KEPT[choice] === read("CursorHeld")) ?? "free";
  for (const button of document.querySelectorAll("[data-cursor-choice]")) {
    button.addEventListener("click", () => {
      keep("CursorHeld", CURSOR_KEPT[button.dataset.cursorChoice]);
      check("cursor", cursorChoice());
      onCursor();
    });
  }
  check("cursor", cursorChoice());

  return {
    sendKeyName,
    // Whether the key letters show on the ringed comment's buttons.
    keyLetters: () => !lettersOff,
    // How far down the editor its cursor's line is held, as a share of its height, or
    // undefined while the cursor moves freely.
    cursorAt: () => CURSOR_AT[cursorChoice()],
    sendOn(input, send) {
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" || event.altKey) return;
        if (!(event.metaKey || event.ctrlKey || (enterSends() ? !event.shiftKey : event.shiftKey))) return;
        // An Enter that ends a word an input method is still composing, as Korean is typed,
        // comes before the box holds that word, which is sent once composing ends.
        if (event.isComposing) {
          input.addEventListener("compositionend", () => setTimeout(send), { once: true });
          return;
        }
        event.preventDefault();
        send();
      });
    },
    // Opens Settings, at its head unless told otherwise. The focus goes to Close at the foot,
    // which scrolls a dialog taller than the window down to it, and Safari scrolls to the focus
    // after the dialog opens, so the head is set again then.
    open({ atHead = true } = {}) {
      const dialog = document.querySelector("#settings-dialog");
      if (dialog.open) return false;
      dialog.showModal();
      if (atHead) {
        dialog.scrollTop = 0;
        requestAnimationFrame(() => { dialog.scrollTop = 0; });
      }
      return true;
    },
  };
}
