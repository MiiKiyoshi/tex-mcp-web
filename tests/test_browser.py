"""Drive the review page in a real browser.

The comment sidebar is plain DOM code that the HTTP tests never execute, so the
one-click close and the in-place edit are exercised through actual clicks here.
"""

import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from tex_mcp_web.config import load_config
from tex_mcp_web.mcp_client import SharedProjectServer


marionette = pytest.importorskip("marionette_driver.marionette")


PAPER = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section{Introduction}\\label{sec:intro}\n"
    "Hello world.\n"
    "\\end{document}\n"
)


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_until(check, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except Exception as error:
            last = error
        time.sleep(0.2)
    raise AssertionError(f"condition was not met: {last}")


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox is required")
def test_browser_comment_actions(tmp_path: Path) -> None:
    import fitz

    (tmp_path / "paper.tex").write_text(PAPER, encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(
        f"main: paper.tex\nauto_compile: false\nport: {port}\n", encoding="utf-8"
    )

    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_browser_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8"
    )
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        comment = post_json(
            f"{base}/comments", {"anchor": {"kind": "paper"}, "text": "typo herre"}
        )
        cid = comment["id"]

        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1500, height=1000)
        browser.navigate(base)

        wait_until(lambda: browser.execute_script(
            'return document.querySelectorAll("[data-comment-id]").length === 1'))
        browser.execute_script(f'''
          const card = Array.from(document.querySelectorAll("[data-comment-id]"))
            .find((node) => node.dataset.commentId === "{cid}");
          card.querySelector(".cmt-preview, .cmt-id").click();
        ''')
        wait_until(lambda: browser.execute_script(
            f'return document.querySelector("[data-comment-id=\\"{cid}\\"] .cmt-edit") !== null'))

        wait_until(lambda: browser.execute_script('''
          const viewer = document.querySelector("embedpdf-container");
          return viewer && !viewer.dispatchEvent(new KeyboardEvent("keydown", {
            key: "c", metaKey: true, bubbles: true, cancelable: true, composed: true,
          }));
        '''))
        assert browser.execute_script('''
          const text = document.querySelector(".thread-text");
          const range = document.createRange();
          range.selectNodeContents(text);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          const allowed = (target, modifier) => target.dispatchEvent(new KeyboardEvent(
            "keydown", { key: "c", [modifier]: true, bubbles: true,
              cancelable: true, composed: true }));
          const result = {
            selected: selection.toString(),
            mac: allowed(text, "metaKey"),
            ctrl: allowed(document.body, "ctrlKey"),
          };
          selection.removeAllRanges();
          result.pdf = allowed(document.querySelector("embedpdf-container"), "metaKey");
          return result;
        ''') == {"selected": "typo herre", "mac": True, "ctrl": True, "pdf": False}

        browser.execute_script(f'''
          const findCard = () => Array.from(document.querySelectorAll("[data-comment-id]"))
            .find((node) => node.dataset.commentId === "{cid}");
          findCard().querySelector(".cmt-edit").click();
          // The click re-renders the card, so the editor is looked up on the new node.
          const card = findCard();
          const input = card.querySelector(".entry-edit-input");
          input.value = "typo here";
          input.dispatchEvent(new Event("input", {{bubbles: true}}));
          Array.from(card.querySelectorAll("button")).find((b) => b.textContent === "Save").click();
        ''')
        wait_until(lambda: get_json(f"{base}/comments/{cid}")["thread"][0]["text"] == "typo here")
        assert len(get_json(f"{base}/comments/{cid}")["thread"]) == 1

        browser.execute_script(f'''
          const card = Array.from(document.querySelectorAll("[data-comment-id]"))
            .find((node) => node.dataset.commentId === "{cid}");
          Array.from(card.querySelectorAll("button")).find((b) => b.textContent === "Resolve").click();
        ''')
        wait_until(lambda: get_json(f"{base}/comments/{cid}")["status"] == "resolved")
        assert len(get_json(f"{base}/comments/{cid}")["thread"]) == 1
    finally:
        if browser is not None:
            try:
                browser.delete_session()
            except Exception:
                pass
        if browser_process is not None:
            browser_process.terminate()
            try:
                browser_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser_process.kill()
        shutil.rmtree(profile, ignore_errors=True)
        shared.stop()
