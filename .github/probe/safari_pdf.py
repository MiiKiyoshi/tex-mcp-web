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

IMAGES = 'return document.querySelector("embedpdf-container")?.shadowRoot?.querySelectorAll("img").length ?? -1;'
CANVAS = '''
const done = arguments[arguments.length - 1];
const within = (promise, ms) => Promise.race([promise, new Promise((ok) => setTimeout(() => ok("no answer in " + ms + " ms"), ms))]);
const encode = (w, h) => within(new Promise((ok) => { const t = performance.now();
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  const g = c.getContext("2d"); g.fillStyle = "#c33"; g.fillRect(0, 0, w, h);
  g.putImageData(g.createImageData(Math.min(w, 50), Math.min(h, 50)), 0, 0);
  c.toBlob((blob) => ok((blob ? blob.size + " bytes" : "null") + " in " + Math.round(performance.now() - t) + " ms")); }), 8000);
const bitmap = within(createImageBitmap(new ImageData(40, 40)).then(() => "ok", (e) => "fails: " + e), 8000);
const c = document.createElement("canvas"); c.width = 40; c.height = 40;
const g = c.getContext("2d"); g.fillStyle = "#c33"; g.fillRect(0, 0, 40, 40);
let url; try { url = c.toDataURL("image/png"); } catch (e) { url = "throws: " + e; }
const pixel = Array.from(g.getImageData(5, 5, 1, 1).data).join(",");
const offscreen = within((async () => { const o = new OffscreenCanvas(40, 40); const og = o.getContext("2d");
  og.fillStyle = "#c33"; og.fillRect(0, 0, 40, 40); const b = await o.convertToBlob(); return b ? b.size + " bytes" : "null"; })()
  .catch((e) => "fails: " + e), 8000);
Promise.all([encode(40, 40), encode(1224, 1584), bitmap, offscreen]).then(([small, page, bitmap, offscreen]) =>
  done(JSON.stringify({small, page, bitmap, offscreen, dataUrl: url.slice(0, 40) + " (" + url.length + ")", pixel,
    jpeg: (() => { try { return c.toDataURL("image/jpeg").length; } catch (e) { return String(e); } })(),
    agent: navigator.userAgent})));
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
try:
    for trial in range(2):
        browser = chrome.launch()
        try:
            browser.set_window_rect(x=0, y=0, width=1200, height=800)
            browser.navigate(f"http://127.0.0.1:{port}")
            time.sleep(5)
            print("trial", trial, "images", browser.execute_script(IMAGES), "canvas", browser.driver.execute_async_script(CANVAS), flush=True)
            print("   images after", browser.execute_script(IMAGES), flush=True)
        finally:
            browser.quit()
finally:
    shared.stop()
