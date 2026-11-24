// The parts of Settings the review pages share: the theme, the size of the page's own text,
// and the key that sends a comment. Each choice is kept in the browser under the page's own
// prefix, so each page keeps its own.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

const THEMES = ["light", "dark", "warm"];

// Each choice as Settings shows it: its heading, what it is to a screen reader, the data
// attribute its buttons carry, and its buttons, the default first.
const CHOICES = [
  ["Theme", "Theme", "theme",
    [["system", "System"], ["light", "Light"], ["dark", "Dark"], ["warm", "Warm"]]],
  ["Text size of the top bar, panels and dialogs",
    "Size of the text in the top bar, the panels and the dialogs", "text",
    [["90", "90%"], ["100", "100%"], ["115", "115%"], ["130", "130%"], ["150", "150%"]]],
  ["Send a comment with", "Key that sends a comment", "send", [["enter", "Enter"], ["shift", "Shift+Enter"]]],
];

function choiceGroup([heading, label, name, buttons]) {
  const title = document.createElement("h4");
  title.textContent = heading;
  const group = document.createElement("div");
  group.className = "theme-choice";
  group.setAttribute("role", "radiogroup");
  group.setAttribute("aria-label", label);
  for (const [value, text] of buttons) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.setAttribute("aria-checked", "false");
    button.dataset[`${name}Choice`] = value;
    button.textContent = text;
    group.append(button);
  }
  return [title, group];
}

function check(name, value) {
  for (const button of document.querySelectorAll(`[data-${name}-choice]`)) {
    button.setAttribute("aria-checked", String(button.dataset[`${name}Choice`] === value));
  }
}

// Fills #settings-look with the shared choices and applies what was kept. The page is told of
// the theme each time it changes, to restyle what CSS does not reach (an editor, a viewer, a
// frame), and of the key that sends, to name it on its buttons.
export function createSettings({ prefix, onTheme = () => {}, onSendKey = () => {} }) {
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
  const applySendKey = () => {
    check("send", enterSends() ? "enter" : "shift");
    onSendKey(sendKeyName());
  };
  for (const button of document.querySelectorAll("[data-send-choice]")) {
    button.addEventListener("click", () => {
      sendKey = button.dataset.sendChoice;
      keep("SendKey", sendKey);
      applySendKey();
    });
  }
  applySendKey();

  return {
    sendKeyName,
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
