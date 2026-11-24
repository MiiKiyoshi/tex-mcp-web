// The theme and the text size are set before the page is drawn, so a dark page does not flash
// light and a page kept at a larger size is not drawn at 100% first: the reader's last choice,
// else the system's theme at 100%. The page names the prefix its choices are kept under, which
// its own script reads from here too:
// <script src="/static/shared/look-early.js" data-prefix="…"></script> in its head. This runs
// before any module and imports nothing, so the themes and the sizes are named here as in
// settings.js.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.
try {
  const { prefix } = document.currentScript.dataset;
  const chosen = localStorage.getItem(`${prefix}Theme`);
  document.documentElement.dataset.theme = ["light", "dark", "warm"].includes(chosen) ? chosen
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const size = localStorage.getItem(`${prefix}TextSize`);
  if (["90", "100", "115", "130", "150"].includes(size)) {
    document.documentElement.style.setProperty("--text-scale", String(Number(size) / 100));
  }
} catch {
  document.documentElement.dataset.theme = "light";
}
