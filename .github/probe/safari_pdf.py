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
const viewer = document.querySelector("embedpdf-container");
const root = viewer?.shadowRoot;
const within = (promise, ms) => Promise.race([promise, new Promise((ok) => setTimeout(() => ok("timeout"), ms))]);
const registry = within(Promise.resolve(viewer?.registry).then((r) => r ? "ready: " + Object.keys(r).join(",").slice(0, 200) : "none",
  (error) => "rejected: " + error), 5000);
const wasm = within(fetch("/static/embedpdf/pdfium.wasm").then((r) => r.arrayBuffer())
  .then((bytes) => WebAssembly.compile(bytes)).then(() => "compiles", (error) => "fails: " + error), 20000);
const worker = within(new Promise((ok) => { try {
  const w = new Worker(URL.createObjectURL(new Blob(["postMessage(typeof WebAssembly + ' ' + typeof OffscreenCanvas)"], {type: "text/javascript"})));
  w.onmessage = (e) => ok("worker: " + e.data); w.onerror = (e) => ok("worker error: " + e.message);
} catch (error) { ok("no worker: " + error); } }), 5000);
Promise.all([registry, wasm, worker]).then(([registry, wasm, worker]) => done(JSON.stringify({
  screen: [screen.width, screen.height], inner: [innerWidth, innerHeight],
  registry, wasm, worker,
  resources: performance.getEntriesByType("resource").filter((e) => /wasm|pdf|embedpdf/.test(e.name))
    .map((e) => [e.name.replace(location.origin, ""), e.responseStatus, Math.round(e.duration), e.transferSize]),
  imgs: root?.querySelectorAll("img").length, canvases: root?.querySelectorAll("canvas").length,
  parts: root ? [...root.querySelectorAll("*")].filter((e) => !["STYLE", "SCRIPT"].includes(e.tagName))
    .map((e) => e.tagName.toLowerCase() + (e.className && typeof e.className === "string" ? "." + e.className.split(" ")[0] : "")).slice(0, 80).join(" ") : null,
  words: root ? [...root.querySelectorAll("*")].filter((e) => !["STYLE", "SCRIPT"].includes(e.tagName) && e.children.length === 0)
    .map((e) => e.textContent.trim()).filter(Boolean).slice(0, 40) : null,
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
    for width, height in ((1200, 800), (900, 600)):
        browser.set_window_rect(x=0, y=0, width=width, height=height)
        if width == 1200:
            browser.navigate(f"http://127.0.0.1:{port}")
        for wait in (3, 15):
            time.sleep(wait if wait == 3 else wait - 3)
            browser.driver.set_script_timeout(60)
            print(width, height, f"after {wait}s", browser.driver.execute_async_script(STATE), flush=True)
        print("window", json.dumps(browser.driver.get_window_rect()), flush=True)
        (out / f"safari-{width}-{len(list(out.iterdir()))}.png").write_bytes(browser.screenshot("binary"))
finally:
    browser.quit()
    shared.stop()
