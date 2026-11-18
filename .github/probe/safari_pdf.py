"""Says what Safari shows of the PDF at two window sizes, to find why a page image is missing."""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests import chrome  # noqa: E402
from tests.test_browser import available_port, wait_for_build  # noqa: E402
from tex_mcp_web.config import load_config  # noqa: E402
from tex_mcp_web.mcp_client import SharedProjectServer  # noqa: E402

STATE = '''
const done = arguments[arguments.length - 1];
const root = document.querySelector("embedpdf-container")?.shadowRoot;
const frames = new Promise((ok) => { let n = 0; const t = performance.now();
  const tick = () => { n += 1; performance.now() - t < 1000 ? requestAnimationFrame(tick) : ok(n); };
  requestAnimationFrame(tick); setTimeout(() => ok(n), 1500); });
frames.then((n) => done(JSON.stringify({
  screen: [screen.width, screen.height, screen.availWidth, screen.availHeight, devicePixelRatio],
  inner: [innerWidth, innerHeight], outer: [outerWidth, outerHeight, screenX, screenY],
  visibility: document.visibilityState, focus: document.hasFocus(), frames: n,
  status: document.querySelector("#compile-status")?.textContent,
  viewer: (() => { const r = document.querySelector("#pdf-viewer")?.getBoundingClientRect(); return r && [r.left, r.top, r.width, r.height]; })(),
  root: Boolean(root), elements: root?.querySelectorAll("*").length,
  imgs: root ? [...root.querySelectorAll("img")].map((i) => { const r = i.getBoundingClientRect(); return [r.width, r.height, i.complete, i.naturalWidth, i.src.slice(0, 30)]; }) : null,
  canvases: root?.querySelectorAll("canvas").length,
  text: root?.textContent.replace(/\\s+/g, " ").slice(0, 300),
})));
'''

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
folder = Path(tempfile.mkdtemp())
(folder / "paper.tex").write_text(
    "\\documentclass{article}\n\\begin{document}\nThe quick brown fox jumps over the lazy dog.\n\\end{document}\n",
    encoding="utf-8")
port = available_port()
(folder / ".tex-mcp-web.yaml").write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")
shared = SharedProjectServer(load_config(folder / ".tex-mcp-web.yaml"))
shared.ensure()
wait_for_build(shared)
browser = chrome.launch()
try:
    for width, height in ((1200, 800), (1500, 1000), (1200, 800)):
        browser.set_window_rect(x=0, y=0, width=width, height=height)
        if width == 1200:
            browser.navigate(f"http://127.0.0.1:{port}")
        for wait in (3, 10, 20):
            time.sleep(wait if wait == 3 else wait - 3)
            print(width, height, f"after {wait}s", browser.driver.execute_async_script(STATE), flush=True)
        print("window", json.dumps(browser.driver.get_window_rect()), flush=True)
        (out / f"safari-{width}-{len(list(out.iterdir()))}.png").write_bytes(browser.screenshot("binary"))
finally:
    browser.quit()
    shared.stop()
