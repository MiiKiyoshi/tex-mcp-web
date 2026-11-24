// The theme is set before the page is drawn, so a dark page does not flash light: the
// reader's last choice, else the system's. The page names the prefix its choices are kept
// under, which its own script reads from here too:
// <script src="/static/shared/look-early.js" data-prefix="…"></script> in its head. This runs
// before any module and imports nothing, so the themes are named here as in settings.js.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.
try {
  const chosen = localStorage.getItem(`${document.currentScript.dataset.prefix}Theme`);
  document.documentElement.dataset.theme = ["light", "dark", "warm"].includes(chosen) ? chosen
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
} catch {
  document.documentElement.dataset.theme = "light";
}
