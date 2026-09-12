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
from marionette_driver.marionette import ActionSequence


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


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox is required")
def test_highlight_badges_leave_pdf_text_selectable(tmp_path: Path) -> None:
    import fitz

    sentence = "The quick brown fox jumps over the lazy dog."
    (tmp_path / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        f"{sentence}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), sentence)
        selections = [
            (quote, list(page.search_for(quote)[0]))
            for quote in ("quick brown fox", "lazy dog")
        ]
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(
        f"main: paper.tex\nauto_compile: false\nport: {port}\n", encoding="utf-8"
    )
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_highlight_")
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
        wait_until(lambda: shared.server.pdf_digest is not None)
        digest = shared.server.pdf_digest
        ids = []
        for quote, bbox in selections:
            comment = post_json(f"{base}/comments", {
                "anchor": {
                    "kind": "text_selection",
                    "quote": quote,
                    "selection": {"page": 1, "bbox": bbox, "rects": [bbox]},
                    "pdf_digest": digest,
                },
                "text": quote,
            })
            ids.append(comment["id"])
        store_path = tmp_path / ".tex-mcp-web" / "comments.json"
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        stored["comments"][1]["stale"] = True
        store_path.write_text(json.dumps(stored), encoding="utf-8")

        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile,
             "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        browser = marionette.Marionette(
            host="127.0.0.1", port=marionette_port, startup_timeout=30
        )
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1200, height=800)
        browser.navigate(base)
        wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container")?.shadowRoot;
          return root?.querySelectorAll(".tex-comment-badge").length === 2;
        '''))
        marks = browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return {
            badges: Array.from(root.querySelectorAll(".tex-comment-badge"))
              .map((badge) => ({text: badge.textContent, stale: badge.classList.contains("stale")})),
            pointers: Array.from(root.querySelectorAll(".tex-comment-segment"))
              .map((segment) => getComputedStyle(segment).pointerEvents),
            badgeWidth: root.querySelector(".tex-comment-badge").getBoundingClientRect().width,
            segmentWidth: root.querySelector(".tex-comment-segment").getBoundingClientRect().width,
          };
        ''')
        assert marks["badges"] == [
            {"text": "1", "stale": False}, {"text": "2", "stale": True},
        ]
        assert marks["pointers"] == ["none", "none"]
        browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          root.querySelector('button[aria-label="Zoom In"]').click();
          root.querySelector('button[aria-label="Zoom In"]').click();
        ''')
        zoomed = wait_until(lambda: browser.execute_script(f'''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const badge = root.querySelector(".tex-comment-badge");
          const segment = root.querySelector(".tex-comment-segment");
          if (!badge || !segment) return false;
          const badgeWidth = badge.getBoundingClientRect().width;
          const segmentWidth = segment.getBoundingClientRect().width;
          return segmentWidth > {marks["segmentWidth"] * 1.1}
            ? {{badgeWidth, segmentWidth}} : false;
        '''))
        assert abs(zoomed["badgeWidth"] - marks["badgeWidth"]) < 0.5

        geometry = browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return Array.from(root.querySelectorAll(".tex-comment-segment")).map((segment) => {
            const rect = segment.getBoundingClientRect();
            return {left: rect.left, right: rect.right, y: rect.top + rect.height / 2};
          });
        ''')
        first, second = geometry
        ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
            .pointer_move(int(first["left"] + 5), int(first["y"])).pointer_down() \
            .pointer_move(int(first["right"] + 70), int(first["y"])).pointer_up().perform()
        wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return Array.from(root.querySelectorAll("button")).some((button) =>
            /comment/i.test(button.textContent) && button.getBoundingClientRect().width > 0);
        '''))
        ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
            .pointer_move(int(first["right"] + 250), int(first["y"] + 260)) \
            .pointer_down().pointer_up() \
            .perform()
        wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return !Array.from(root.querySelectorAll("button")).some((button) =>
            /comment/i.test(button.textContent) && button.getBoundingClientRect().width > 0);
        '''))
        ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
            .pointer_move(int(second["left"] - 40), int(second["y"])).pointer_down() \
            .pointer_move(int(second["left"] - 20), int(second["y"])) \
            .pointer_move(int(second["left"] + 5), int(second["y"])).pointer_up().perform()
        wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return Array.from(root.querySelectorAll("button")).some((button) =>
            /comment/i.test(button.textContent) && button.getBoundingClientRect().width > 0);
        '''))

        browser.execute_script('''
          document.querySelector(".layout").classList.add("sidebar-collapsed");
          const root = document.querySelector("embedpdf-container").shadowRoot;
          root.querySelectorAll(".tex-comment-badge")[1].click();
        ''')
        wait_until(lambda: browser.execute_script(f'''
          return !document.querySelector(".layout").classList.contains("sidebar-collapsed")
            && document.querySelector('[data-comment-id="{ids[1]}"]').classList.contains("is-focused");
        '''))
        browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          root.querySelectorAll(".tex-comment-badge")[1].click();
        ''')
        wait_until(lambda: browser.execute_script('''
          return document.querySelector(".layout").classList.contains("sidebar-collapsed");
        '''))
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
