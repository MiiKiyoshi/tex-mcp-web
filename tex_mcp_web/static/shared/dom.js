// What the review pages' scripts build their parts with: finding elements, making them, and
// reading the keys pressed on them.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

export const $ = (selector) => document.querySelector(selector);
export const $$ = (selector) => Array.from(document.querySelectorAll(selector));

export function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key === "data") {
      for (const [name, dataValue] of Object.entries(value)) element.dataset[name] = dataValue;
    } else if (key === "style") Object.assign(element.style, value);
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) element.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    element.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return element;
}

export function clear(node) {
  node.replaceChildren();
}

export function placeholder(text) {
  return h("p", { class: "placeholder", text });
}

// The element a key or focus event reached. A part drawn in a shadow root of its own, as a
// viewer's search box, seems from outside it to send its events from its host element, so a
// key typed in such a box would not read as typed in a box.
export function eventOrigin(event) {
  return event.composedPath?.()[0] ?? event.target;
}

// The letters a Korean input method composes. With one on, a letter key reaches the page as
// the method's own event and then as the letter it composed.
export const COMPOSED = /[ᄀ-ᇿ㄰-㆏가-힯]/;

// A letter key as the letter where it sits on the keyboard, whatever an input method made of it.
export function keyByPosition(event) {
  const letter = /^Key([A-Z])$/.exec(event.code)?.[1];
  return letter === undefined ? undefined : event.shiftKey ? letter : letter.toLowerCase();
}

// A Mac holds the page's shortcuts with Cmd, and its Option key is the Alt of other keyboards.
// The page's texts name the keys as Windows and Linux have them, and on a Mac every tooltip
// and label, and the texts the page names, are shown with the Mac's names. Ctrl+[, Ctrl+O and
// Ctrl+T are Ctrl on a Mac too.
export function macKeyNames(text) {
  return text.replace(/Ctrl\+Alt\+/g, "Cmd+Option+").replace(/Ctrl\+(?=Enter\b|S\b|double-click\b)/g, "Cmd+");
}

// On a Mac, the Mac's names in every title and aria-label, and in the text of what *texts*
// selects, now and as the page changes.
export function showMacKeyNames(texts) {
  if (!/Mac|iPhone|iPad|iPod/.test(navigator.platform)) return;
  const named = (node) => {
    for (const attribute of ["title", "aria-label"]) {
      const text = node.getAttribute?.(attribute);
      if (text && text !== macKeyNames(text)) node.setAttribute(attribute, macKeyNames(text));
    }
  };
  const written = (node) => {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (text.data !== macKeyNames(text.data)) text.data = macKeyNames(text.data);
    }
  };
  const seen = (node) => {
    if (node.nodeType !== Node.ELEMENT_NODE) {
      if (node.nodeType === Node.TEXT_NODE && node.parentElement?.closest(texts)) written(node.parentElement);
      return;
    }
    named(node);
    node.querySelectorAll("[title], [aria-label]").forEach(named);
    if (node.closest(texts)) written(node);
    else node.querySelectorAll(texts).forEach(written);
  };
  seen(document.body);
  new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === "attributes") named(change.target);
      else if (change.type === "characterData") seen(change.target);
      else change.addedNodes.forEach(seen);
    }
  }).observe(document.body, { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ["title", "aria-label"] });
}
