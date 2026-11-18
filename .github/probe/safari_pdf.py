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
    for trial in range(10):
        variant = ("size-then-open", "open-then-size", "open-only", "size-open-1500", "size-then-open")[trial % 5]
        browser = chrome.launch()
        try:
            started = time.monotonic()
            if variant == "size-then-open":
                browser.set_window_rect(x=0, y=0, width=1200, height=800)
            if variant == "size-open-1500":
                browser.set_window_rect(x=0, y=0, width=1500, height=1000)
            browser.navigate(f"http://127.0.0.1:{port}")
            if variant == "open-then-size":
                browser.set_window_rect(x=0, y=0, width=1200, height=800)
            seen = None
            while time.monotonic() - started < 20:
                if browser.execute_script(IMAGES) > 0:
                    seen = round(time.monotonic() - started, 1)
                    break
                time.sleep(0.2)
            inner = browser.execute_script("return [innerWidth, innerHeight, document.visibilityState, document.hasFocus()]")
            print("trial", trial, variant, "image after", seen, inner, flush=True)
            if seen is None:
                browser.set_window_rect(x=0, y=0, width=1000, height=700)
                time.sleep(5)
                print("   after a resize:", browser.execute_script(IMAGES), flush=True)
                browser.refresh()
                time.sleep(8)
                print("   after a reload:", browser.execute_script(IMAGES), flush=True)
        finally:
            browser.quit()
finally:
    shared.stop()
