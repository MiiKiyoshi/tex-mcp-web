// What the page's Overleaf parts share: the leaf that marks Overleaf, each collaborator's colour,
// the sentence that asks for a cookie, what Settings says of how a connected folder syncs, and
// the counted words their sentences use.

// Overleaf is marked by a leaf wherever the page speaks of it.
const LEAF_ICON = '<svg class="leaf-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
  + 'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<path d="M3 13C3 7 7 3 13 3c0 6-4 10-10 10z"/><path d="M3 13l6-6"/></svg>';

export function leafIcon() {
  const template = document.createElement("template");
  template.innerHTML = LEAF_ICON;
  return template.content.firstChild;
}

export function overleafHue(id) {
  let hash = 0;
  for (const char of String(id)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 360;
}

export function overleafColor(id) {
  return `hsl(${overleafHue(id)} 62% 42%)`;
}

// What stands in the way of comments while the project has no cookie, said in the leaf tab
// and on a thread's reply for Overleaf.
export const OVERLEAF_COOKIE_WANTED = "Save your Overleaf cookie in Settings to sync comments and send replies.";

// What Settings says of how a connected folder syncs: a git clone, live or not, or a folder
// synced by hand with Get and Send.
export const OVERLEAF_BY_GIT = "This folder is a git clone of the Overleaf project.";
export const OVERLEAF_BY_HAND = "This folder syncs with Overleaf by hand, with Get and Send.";

export function commits(count) {
  return `${count} commit${count === 1 ? "" : "s"}`;
}

export function replies(count) {
  return `${count} repl${count === 1 ? "y" : "ies"}`;
}

export function fileCount(count) {
  return `${count} file${count === 1 ? "" : "s"}`;
}
