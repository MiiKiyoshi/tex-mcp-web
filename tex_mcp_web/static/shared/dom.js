// What the review pages' scripts build their parts with: finding elements, and making them.
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
