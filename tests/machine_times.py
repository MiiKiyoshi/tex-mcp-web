"""How long this machine takes for what most browser tests do first: start Chrome, build a
small paper, and start the review server. Run by the CI jobs, whose logs then say where a slow
system spends a test's time. Usage: python -m tests.machine_times"""
import subprocess
import tempfile
import time
from pathlib import Path

from tests import chrome
from tex_mcp_web.config import load_config
from tex_mcp_web.mcp_client import SharedProjectServer


def timed(label: str, action) -> None:
    start = time.perf_counter()
    action()
    print(f"{label}: {time.perf_counter() - start:.2f}s", flush=True)


def main() -> None:
    folder = Path(tempfile.mkdtemp())
    (folder / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nA line.\n\\end{document}\n", encoding="utf-8")
    (folder / ".tex-mcp-web.yaml").write_text("main: paper.tex\nport: 8791\n", encoding="utf-8")
    for turn in range(3):
        browser = []
        timed(f"Chrome start {turn + 1}", lambda: browser.append(chrome.launch()))
        timed(f"Chrome quit {turn + 1}", browser[0].quit)
    for turn in range(3):
        timed(f"latexmk {turn + 1}", lambda: subprocess.run(
            ["latexmk", "-pdf", "-synctex=1", "-interaction=nonstopmode", f"-jobname=turn{turn}", "paper.tex"],
            cwd=folder, capture_output=True, check=False))
    shared = SharedProjectServer(load_config(folder / ".tex-mcp-web.yaml"))
    timed("server start", shared.ensure)
    deadline = time.monotonic() + 60
    start = time.perf_counter()
    while (shared.server.compiling or shared.server.last_result is None) and time.monotonic() < deadline:
        time.sleep(0.02)
    print(f"server's first build: {time.perf_counter() - start:.2f}s", flush=True)
    timed("server stop", shared.stop)


if __name__ == "__main__":
    main()
