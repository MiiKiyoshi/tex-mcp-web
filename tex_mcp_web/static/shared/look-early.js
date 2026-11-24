// The theme is set before the page is drawn, so a dark page does not flash light: the
// reader's last choice, else the system's. The page names the key the choice is kept under:
// <script src="/static/shared/look-early.js" data-key="…Theme"></script> in its head.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.
try {
  const chosen = localStorage.getItem(document.currentScript.dataset.key);
  document.documentElement.dataset.theme = ["light", "dark", "warm"].includes(chosen) ? chosen
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
} catch {
  document.documentElement.dataset.theme = "light";
}
