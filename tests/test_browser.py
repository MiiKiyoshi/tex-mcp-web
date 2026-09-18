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
from concurrent.futures import ThreadPoolExecutor
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
        comment = post_json(f"{base}/comments", {
            "anchor": {"kind": "paper"},
            "text": "typo herre",
            "suggestion": {"old": "typo herre", "new": "typo here"},
        })
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
        assert browser.execute_script(
            'return document.querySelectorAll(".sugg-apply").length') == 0
        pdf_zoom = wait_until(lambda: browser.execute_script('''
          const input = document.querySelector("embedpdf-container")?.shadowRoot
            ?.querySelector('input[name="zoom"]');
          const value = Number(input?.value);
          return value > 0 ? value : false;
        '''))

        browser.execute_script('document.querySelector("[data-view=source]").click()')
        wait_until(lambda: browser.execute_script(
            'return document.querySelector(".layout").classList.contains("view-source")'))
        wait_until(lambda: browser.execute_script(
            'return Boolean((window.wrappedJSObject || window).ace)'))
        wait_until(lambda: browser.execute_script('''
          const page = window.wrappedJSObject || window;
          const editor = page.ace.edit("source-editor");
          return editor.getValue().includes("Hello world.")
            && editor.session.getMode().$id === "ace/mode/latex";
        '''))
        assert browser.execute_script('''
          return [document.querySelector("#source-file").value,
            getComputedStyle(document.querySelector("#pdf-pane")).display,
            getComputedStyle(document.querySelector("#source-pane")).display];
        ''') == ["paper.tex", "none", "flex"]
        browser.execute_script('''
          const page = window.wrappedJSObject || window;
          const editor = page.ace.edit("source-editor");
          const Range = page.ace.require("ace/range").Range;
          editor.selection.setRange(new Range(3, 0, 3, 12));
          document.querySelector("#source-comment-btn").click();
        ''')
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("#compose-dialog").open'))
        assert browser.execute_script('''
          return [document.querySelector("#compose-anchor").textContent,
            document.querySelector("#compose-suggestion-old").value];
        ''') == ["Source: paper.tex:4-4", "Hello world."]
        browser.execute_script('''
          const input = document.querySelector("#compose-text");
          input.value = "Clarify this source sentence.";
          input.dispatchEvent(new Event("input", {bubbles: true}));
          document.querySelector("#compose-suggestion-new").value = "Hello applied.";
          document.querySelector("#compose-form").requestSubmit();
        ''')
        source_comment = wait_until(lambda: next(
            (item for item in get_json(f"{base}/comments")["comments"]
             if item["anchor"]["kind"] == "source_range"),
            None,
        ))
        source_cid = source_comment["id"]
        assert source_comment["anchor"] == {
            "kind": "source_range", "file": "paper.tex", "line_start": 4, "line_end": 4,
            "column_start": 0, "column_end": 12,
        }
        assert source_comment["source_selector"]["exact"] == "Hello world."
        assert source_comment["suggestion"] == {
            "old": "Hello world.", "new": "Hello applied.",
        }
        wait_until(lambda: browser.execute_script(f'''
          const cards = Array.from(document.querySelectorAll("[data-comment-id]"));
          const source = cards.find((node) => node.dataset.commentId === "{source_cid}");
          return document.querySelectorAll(".sugg-apply").length === 1
            && source?.querySelector(".sugg-apply")?.textContent === "Apply suggestion";
        '''))
        browser.execute_script(f'''
          const source = Array.from(document.querySelectorAll("[data-comment-id]"))
            .find((node) => node.dataset.commentId === "{source_cid}");
          source.querySelector(".sugg-apply").click();
        ''')
        applied = wait_until(lambda: (
            current
            if (current := get_json(f"{base}/comments/{source_cid}")).get("suggestion_applied")
            else None
        ))
        assert applied["status"] == "open"
        assert applied["thread"][-1]["edits"] == ["paper.tex:4-4"]
        wait_until(lambda: browser.execute_script(f'''
          const source = Array.from(document.querySelectorAll("[data-comment-id]"))
            .find((node) => node.dataset.commentId === "{source_cid}");
          const button = source?.querySelector(".sugg-apply");
          return button?.textContent === "Applied" && button.disabled;
        '''))
        wait_until(lambda: browser.execute_script('''
          const page = window.wrappedJSObject || window;
          return page.ace.edit("source-editor").getValue().includes("Hello applied.");
        '''))
        refreshed = get_json(f"{base}/comments/{source_cid}")
        assert refreshed["status"] == "open"
        assert "stale" not in refreshed
        wait_until(lambda: browser.execute_script('''
          return document.querySelectorAll("#source-editor .source-comment-highlight").length === 1
            && document.querySelectorAll("#source-editor .source-comment-line").length === 1;
        '''))
        browser.execute_script('''
          const page = window.wrappedJSObject || window;
          const editor = page.ace.edit("source-editor");
          editor.setValue(editor.getValue().replace("Hello applied.", "Hello editor."), -1);
          document.querySelector("#source-save-btn").click();
        ''')
        wait_until(lambda: "Hello editor." in (tmp_path / "paper.tex").read_text(encoding="utf-8"))
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("#source-status").textContent === "Saved"'))

        browser.execute_script('document.querySelector("[data-view=split]").click()')
        wait_until(lambda: browser.execute_script('''
          return document.querySelector(".layout").classList.contains("view-split")
            && getComputedStyle(document.querySelector("#pdf-pane")).display !== "none"
            && getComputedStyle(document.querySelector("#source-pane")).display === "flex";
        '''))
        split_zoom = wait_until(lambda: browser.execute_script('''
          const input = document.querySelector("embedpdf-container")?.shadowRoot
            ?.querySelector('input[name="zoom"]');
          const value = Number(input?.value);
          return value > 0 && value < arguments[0] ? value : false;
        ''', script_args=[pdf_zoom]))
        assert split_zoom < pdf_zoom
        browser.execute_script('document.querySelector("[data-view=pdf]").click()')
        wait_until(lambda: browser.execute_script(
            'return document.querySelector(".layout").classList.contains("view-pdf")'))
        browser.execute_script(f'''
          const card = Array.from(document.querySelectorAll("[data-comment-id]"))
            .find((node) => node.dataset.commentId === "{source_cid}");
          card.querySelector(".cmt-preview, .cmt-id").click();
        ''')
        wait_until(lambda: browser.execute_script('''
          const page = window.wrappedJSObject || window;
          return document.querySelector(".layout").classList.contains("view-source")
            && page.ace.edit("source-editor").getCursorPosition().row === 3;
        '''))
        browser.execute_script('document.querySelector("[data-view=pdf]").click()')
        wait_until(lambda: browser.execute_script(
            'return document.querySelector(".layout").classList.contains("view-pdf")'))

        assert browser.execute_script('''
          const button = document.querySelector("#call-agent-btn");
          return [button.classList.contains("agent-offline"), button.disabled,
            button.getAttribute("aria-label"), button.title, getComputedStyle(button).color];
        ''') == [True, False, "Queue call for agent",
                 "Agent is not waiting; queue this call until it reconnects", "rgb(180, 74, 67)"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiter = pool.submit(lambda: urllib.request.urlopen(
                f"{base}/wait-review", timeout=10).read())
            wait_until(lambda: browser.execute_script('''
              const button = document.querySelector("#call-agent-btn");
              return button.classList.contains("agent-ready")
                && button.getAttribute("aria-label") === "Call agent"
                && button.title === "Call the waiting agent now"
                && getComputedStyle(button).color === "rgb(79, 119, 88)";
            '''))
            browser.execute_script('document.querySelector("#call-agent-btn").click()')
            wait_until(lambda: browser.execute_script(
                'return document.querySelector("#call-agent-word").textContent === "Called"'))
            assert waiter.result(timeout=10).startswith(b"[review]")
        wait_until(lambda: browser.execute_script('''
          const button = document.querySelector("#call-agent-btn");
          return button.classList.contains("agent-offline") && !button.disabled;
        '''))
        browser.execute_script('document.querySelector("#call-agent-btn").click()')
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("#call-agent-word").textContent === "Queued"'))
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

        browser.set_window_rect(width=500, height=900)
        wait_until(lambda: browser.execute_script("return window.innerWidth <= 560"))
        mobile = browser.execute_script('''
          const workspace = document.querySelector("#workspace").getBoundingClientRect();
          const sidebar = document.querySelector("#sidebar").getBoundingClientRect();
          const grip = document.querySelector("#sidebar-grip");
          const topbar = document.querySelector(".topbar");
          return {workspaceWidth: workspace.width, workspaceBottom: workspace.bottom,
            sidebarWidth: sidebar.width, sidebarTop: sidebar.top, sidebarHeight: sidebar.height,
            gripDisplay: getComputedStyle(grip).display,
            topbarOverflow: topbar.scrollWidth - topbar.clientWidth};
        ''')
        assert mobile["sidebarTop"] >= mobile["workspaceBottom"] - 2
        assert abs(mobile["sidebarWidth"] - mobile["workspaceWidth"]) <= 2
        assert mobile["gripDisplay"] == "flex"
        assert mobile["topbarOverflow"] <= 1

        grip_box = browser.execute_script('''
          const box = document.querySelector("#sidebar-grip").getBoundingClientRect();
          return {x: Math.round(box.left + box.width / 2),
            y: Math.round(box.top + box.height / 2)};
        ''')
        drag = browser.actions.sequence("pointer", "mouse", {"pointerType": "mouse"})
        drag.pointer_move(grip_box["x"], grip_box["y"]).pointer_down()
        drag.pointer_move(grip_box["x"], grip_box["y"] - 80, duration=100)
        drag.pointer_up().perform()
        wait_until(lambda: browser.execute_script(
            'return Number(localStorage.getItem("texMcpPanelHeight"))')
            > mobile["sidebarHeight"] + 40)
        dragged_height = browser.execute_script(
            'return document.querySelector("#sidebar").getBoundingClientRect().height')
        assert dragged_height > mobile["sidebarHeight"] + 40
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

        def badge_center(index):
            return browser.execute_script(f'''
              const root = document.querySelector("embedpdf-container").shadowRoot;
              const badge = root.querySelectorAll(".tex-comment-badge")[{index}];
              const rect = badge.getBoundingClientRect();
              const x = rect.left + rect.width / 2;
              const y = rect.top + rect.height / 2;
              const hit = root.elementFromPoint(x, y);
              return hit === badge ? {{x, y}} : false;
            ''')

        def click_badge(index):
            # Collapsing the sidebar widens the viewer, which re-fits the page a moment
            # later and moves the badges with it: a click aimed before that lands beside
            # the badge. The badge is clicked once it has stayed put.
            def settled():
                before = badge_center(index)
                if not before:
                    return False
                time.sleep(0.3)
                after = badge_center(index)
                return after if after == before else False
            target = wait_until(settled)
            ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
                .pointer_move(int(target["x"]), int(target["y"])) \
                .pointer_down().pointer_up().perform()

        browser.execute_script('''
          document.querySelector(".layout").classList.add("sidebar-collapsed");
        ''')
        click_badge(1)
        wait_until(lambda: browser.execute_script(f'''
          return !document.querySelector(".layout").classList.contains("sidebar-collapsed")
            && document.querySelector('[data-comment-id="{ids[1]}"]').classList.contains("is-focused");
        '''))
        click_badge(1)
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


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox is required")
def test_a_finger_held_on_a_word_selects_it(tmp_path: Path) -> None:
    """The viewer selects by a drag or a double click; a tablet asks with a finger held
    still. The hold selects the word under it, a highlight in between or not, the
    browser's long-press menu is kept off the touch, and a drag, a tap, a cancelled
    touch and a mouse do what they did."""
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
        bbox = list(page.search_for("quick brown fox")[0])
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(
        f"main: paper.tex\nauto_compile: false\nport: {port}\n", encoding="utf-8"
    )
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_hold_")
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
        post_json(f"{base}/comments", {
            "anchor": {
                "kind": "text_selection",
                "quote": "quick brown fox",
                "selection": {"page": 1, "bbox": bbox, "rects": [bbox]},
                "pdf_digest": shared.server.pdf_digest,
            },
            "text": "quick brown fox",
        })

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
        browser.set_window_rect(x=0, y=0, width=800, height=1100)
        browser.navigate(base)
        wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container")?.shadowRoot;
          return root?.querySelectorAll(".tex-comment-segment").length === 1;
        '''))
        highlight = browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const rect = root.querySelector(".tex-comment-segment").getBoundingClientRect();
          return {left: rect.left, right: rect.right, y: rect.top + rect.height / 2};
        ''')
        y = int(highlight["y"])
        menu_shown = '''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return Array.from(root.querySelectorAll("button")).some((button) =>
            /comment/i.test(button.textContent) && button.getBoundingClientRect().width > 0);
        '''

        def finger():
            return ActionSequence(browser, "pointer", "finger", {"pointerType": "touch"})

        def quoted():
            wait_until(lambda: browser.execute_script(menu_shown))
            browser.execute_script('''
              const root = document.querySelector("embedpdf-container").shadowRoot;
              Array.from(root.querySelectorAll("button")).find((button) =>
                /comment/i.test(button.textContent) && button.getBoundingClientRect().width > 0).click();
            ''')
            wait_until(lambda: browser.execute_script(
                'return document.querySelector("#compose-dialog").open'))
            anchor = browser.execute_script(
                'return document.querySelector("#compose-anchor").textContent')
            browser.execute_script('document.querySelector("#compose-cancel").click()')
            wait_until(lambda: not browser.execute_script(
                'return document.querySelector("#compose-dialog").open'))
            ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
                .pointer_move(int(highlight["right"] + 200), y + 300) \
                .pointer_down().pointer_up().perform()
            wait_until(lambda: not browser.execute_script(menu_shown))
            return anchor

        # Held still on a bare word, the finger selects that word.
        bare_x = int(highlight["right"] + 12)                 # inside "jumps"
        finger().pointer_move(bare_x, y).pointer_down().pause(700).pointer_up().perform()
        assert quoted() == 'PDF text: "jumps"'

        # Held on a comment's highlight, it selects the word under the highlight and
        # does not open the comment.
        finger().pointer_move(int(highlight["left"] + 5), y).pointer_down().pause(700) \
            .pointer_up().perform()
        assert quoted() == 'PDF text: "quick"'
        assert not browser.execute_script(
            'return Boolean(document.querySelector("[data-comment-id].is-focused"))')

        # Dragged on after the hold, it selects from the word onward.
        finger().pointer_move(int(highlight["left"] + 5), y).pointer_down().pause(700) \
            .pointer_move(int(highlight["right"] + 60), y).pointer_up().perform()
        assert "brown fox jumps" in quoted()

        # Dragged at once, it selects the range as before.
        finger().pointer_move(int(highlight["left"] + 5), y).pointer_down() \
            .pointer_move(int(highlight["left"] + 40), y) \
            .pointer_move(int(highlight["right"] + 60), y).pointer_up().perform()
        assert "brown fox jumps" in quoted()

        # A tap selects nothing.
        finger().pointer_move(bare_x, y).pointer_down().pause(80).pointer_up().perform()
        time.sleep(0.8)
        assert not browser.execute_script(menu_shown)

        # While the finger is held, the browser's long-press menu is refused; a mouse
        # keeps its menu.
        contextmenu = f'''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return root.elementFromPoint({bare_x}, {y}).dispatchEvent(new PointerEvent("contextmenu",
            {{bubbles: true, cancelable: true, composed: true, clientX: {bare_x}, clientY: {y},
              pointerType: arguments[0]}}));
        '''
        assert browser.execute_script(contextmenu, script_args=["mouse"]) is True
        finger().pointer_move(bare_x, y).pointer_down().perform()
        assert browser.execute_script(contextmenu, script_args=["touch"]) is False
        finger().pause(700).pointer_up().perform()
        assert quoted() == 'PDF text: "jumps"'

        # A touch the browser cancels before the hold is up selects nothing.
        finger().pointer_move(bare_x, y).pointer_down().perform()
        browser.execute_script(f'''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          root.elementFromPoint({bare_x}, {y}).dispatchEvent(new PointerEvent("pointercancel",
            {{bubbles: true, composed: true, pointerType: "touch", isPrimary: true}}));
        ''')
        finger().pause(700).pointer_up().perform()
        time.sleep(0.8)
        assert not browser.execute_script(menu_shown)
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
def test_browser_source_selection_and_split_resize(tmp_path: Path) -> None:
    import fitz

    (tmp_path / "paper.tex").write_text(PAPER.replace("Hello world.", "prefix " * 15 + "SELECTED " * 10 + " suffix" * 30), encoding="utf-8")
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

        browser.execute_script('document.querySelector("[data-view=split]").click()')
        wait_until(lambda: browser.execute_script('''
          const page = window.wrappedJSObject || window;
          return page.ace?.edit("source-editor").getValue().includes("SELECTED");
        '''))
        coords = browser.execute_script('''
          const editor = (window.wrappedJSObject || window).ace.edit("source-editor");
          return [90, 175].map(column => {
            const point = editor.renderer.textToScreenCoordinates(3, column);
            return {x: point.pageX, y: point.pageY + editor.renderer.lineHeight / 2};
          });
        ''')
        assert coords[1]["y"] > coords[0]["y"]  # One source line, several visual lines.
        ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
            .pointer_move(round(coords[0]["x"]), round(coords[0]["y"])) \
            .pointer_down().pointer_move(round(coords[1]["x"]), round(coords[1]["y"]), duration=300) \
            .pointer_up().perform()
        selection = browser.execute_script('''
          const editor = (window.wrappedJSObject || window).ace.edit("source-editor");
          return {text: editor.getSelectedText(), start: editor.getSelectionRange().start,
            end: editor.getSelectionRange().end};
        ''')
        assert selection["start"] == {"row": 3, "column": 90}
        assert selection["end"] == {"row": 3, "column": 175}
        browser.execute_script('''
          document.querySelector("#source-comment-btn").click();
          const input = document.querySelector("#compose-text");
          input.value = "Only these characters";
          input.dispatchEvent(new Event("input", {bubbles: true}));
          document.querySelector("#compose-form").requestSubmit();
        ''')
        comment = wait_until(lambda: next((item for item in get_json(f"{base}/comments")["comments"]
            if item["anchor"]["kind"] == "source_range"), None))
        assert comment["source_selector"]["exact"] == selection["text"]
        assert (comment["anchor"]["column_start"], comment["anchor"]["column_end"]) == (90, 175)
        coverage = '''
          const editor = (window.wrappedJSObject || window).ace.edit("source-editor");
          const rects = Array.from(document.querySelectorAll(".source-comment-highlight"), n => n.getBoundingClientRect());
          return [89, 90, 130, 174, 175].map(column => {
            const point = editor.renderer.textToScreenCoordinates(3, column);
            const x = point.pageX + editor.renderer.characterWidth / 2;
            const y = point.pageY + editor.renderer.lineHeight / 2;
            return rects.some(r => x >= r.left && x < r.right && y >= r.top && y < r.bottom);
          });
        '''
        wait_until(lambda: browser.execute_script(coverage) == [False, True, True, True, False])
        grip = browser.execute_script('''
          const r = document.querySelector("#split-grip").getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2,
            before: document.querySelector("#pdf-pane").getBoundingClientRect().width};
        ''')
        ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
            .pointer_move(round(grip["x"]), round(grip["y"])).pointer_down() \
            .pointer_move(round(grip["x"] - 130), round(grip["y"]), duration=300).pointer_up().perform()
        wait_until(lambda: browser.execute_script('return document.querySelector("#pdf-pane").getBoundingClientRect().width') < grip["before"] - 110)
        wait_until(lambda: browser.execute_script(coverage) == [False, True, True, True, False])
        ratio = browser.execute_script('return document.querySelector("#split-grip").getAttribute("aria-valuenow")')
        browser.refresh()
        wait_until(lambda: browser.execute_script('return document.querySelector("#split-grip").getAttribute("aria-valuenow")') == ratio)
        wait_until(lambda: browser.execute_script(coverage) == [False, True, True, True, False])
        import base64
        (tmp_path / "split-selection.png").write_bytes(base64.b64decode(browser.screenshot()))
        browser.set_window_rect(width=800, height=1000)
        wait_until(lambda: browser.execute_script('return document.querySelector("#split-grip").getAttribute("aria-orientation")') == "horizontal")
        grip = browser.execute_script('''
          const r = document.querySelector("#split-grip").getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2,
            before: document.querySelector("#pdf-pane").getBoundingClientRect().height};
        ''')
        ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
            .pointer_move(round(grip["x"]), round(grip["y"])).pointer_down() \
            .pointer_move(round(grip["x"]), round(grip["y"] + 65), duration=300).pointer_up().perform()
        wait_until(lambda: browser.execute_script('return document.querySelector("#pdf-pane").getBoundingClientRect().height') > grip["before"] + 45)

        # Upright, the comments sit under the stacked panes, and their bar changes the height
        # those panes share: the split's own bar was carried up the screen with it. It stays
        # where it was, and the ratio that keeps it there is what the split remembers.
        wait_until(lambda: browser.execute_script(
            'return getComputedStyle(document.querySelector("#sidebar-grip")).display') == "flex")
        split_center = '''
          const r = document.querySelector("#split-grip").getBoundingClientRect();
          return r.top + r.height / 2;'''
        split_ratio = 'return document.querySelector("#split-grip").getAttribute("aria-valuenow")'
        held = browser.execute_script(split_center)
        ratio_before = browser.execute_script(split_ratio)
        side_before = browser.execute_script(
            'return document.querySelector("#sidebar").getBoundingClientRect().height')
        box = browser.execute_script('''
          const r = document.querySelector("#sidebar-grip").getBoundingClientRect();
          return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)};
        ''')
        drag = ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"})
        drag.pointer_move(box["x"], box["y"]).pointer_down()
        for step in range(1, 5):
            drag.pointer_move(box["x"], box["y"] - 40 * step, duration=30)
        drag.pointer_up().perform()
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("#sidebar").getBoundingClientRect().height') > side_before + 120)
        assert abs(browser.execute_script(split_center) - held) <= 2
        ratio_after = browser.execute_script(split_ratio)
        assert ratio_after != ratio_before
        stored = browser.execute_script('return Number(localStorage.getItem("texMcpSplitRatio"))')
        assert round(stored * 100) == int(ratio_after)
        # The two bars are drawn alike, in bands of the same thickness.
        bars = browser.execute_script('''
          const split = getComputedStyle(document.querySelector("#split-grip"), "::after");
          const grip = document.querySelector("#sidebar-grip");
          const side = getComputedStyle(grip, "::before");
          return {split: [split.width, split.height], side: [side.width, side.height],
                  splitBand: document.querySelector("#split-grip").getBoundingClientRect().height,
                  band: grip.getBoundingClientRect().height};
        ''')
        assert bars["split"] == bars["side"] == ["32px", "3px"], bars
        assert abs(bars["band"] - bars["splitBand"]) <= 1, bars

        # The keys move the bar as the split's do, and the height they reach is kept.
        keyed = browser.execute_script('''
          const grip = document.querySelector("#sidebar-grip");
          const before = document.querySelector("#sidebar").getBoundingClientRect().height;
          grip.focus();
          grip.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowDown", bubbles: true}));
          const after = document.querySelector("#sidebar").getBoundingClientRect().height;
          return {before, after, stored: Number(localStorage.getItem("texMcpPanelHeight"))};
        ''')
        assert keyed["after"] < keyed["before"] - 20, keyed
        assert abs(keyed["stored"] - keyed["after"]) <= 2, keyed
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
def test_archived_threads_have_their_own_view_and_picked_archive_action(tmp_path: Path) -> None:
    """Archive is the third visible status: its view lists kept threads apart, and the
    archive-box action moves only picked threads there."""
    import fitz

    (tmp_path / "paper.tex").write_text(PAPER, encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nauto_compile: false\nport: {port}\n", encoding="utf-8")

    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_reference_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        kept = post_json(f"{base}/comments", {"anchor": {"kind": "paper"}, "text": "keep me"})["id"]
        working = post_json(f"{base}/comments", {"anchor": {"kind": "paper"}, "text": "work on me"})["id"]
        untouched = post_json(f"{base}/comments", {"anchor": {"kind": "paper"}, "text": "leave me"})["id"]
        post_json(f"{base}/comments/{kept}/archive", {})

        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1500, height=1000)
        browser.navigate(base)

        def shown(status, expected):
            # The view is asked for and then waited for by its content: the cards of the
            # view before it stand until the new ones arrive, and both views may hold one.
            browser.execute_script(f"""
              const filter = document.querySelector("#comment-filter");
              filter.value = "{status}";
              filter.dispatchEvent(new Event("change", {{bubbles: true}}));
            """)
            return wait_until(lambda: browser.execute_script(
                'const ids = Array.from(document.querySelectorAll("[data-comment-id]"))'
                '  .map((card) => card.dataset.commentId);'
                f'return JSON.stringify([...ids].sort()) === {json.dumps(json.dumps(sorted(expected), separators=(",", ":")))} ? ids : false;'))

        assert browser.execute_script(
            'return Array.from(document.querySelectorAll("#comment-filter option")).map((o) => o.value);'
        ) == ["open", "resolved", "archived", "all"]
        assert browser.execute_script(
            'return Array.from(document.querySelectorAll("#comment-filter option")).map((o) => o.textContent);'
        ) == ["open", "resolved", "archived", "all"]
        assert shown("open", [working, untouched]) == [working, untouched]
        assert shown("archived", [kept]) == [kept]

        def expanded_actions(comment_id):
            browser.execute_script(f'document.querySelector("[data-comment-id=\'{comment_id}\'] .cmt-head").click();')
            return wait_until(lambda: browser.execute_script(f'''
              const card = document.querySelector("[data-comment-id='{comment_id}']");
              const buttons = Array.from(card.querySelectorAll(".cmt-actions button")).map((b) => b.textContent);
              return buttons.length ? [card.querySelector(".cmt-status").textContent, buttons] : false;
            '''))

        assert expanded_actions(kept) == ["[archived]", ["Reply", "Reopen", "Resolve", "Delete"]]

        # An open card calls the action Archive, and the archive-box button moves only the
        # picked thread while leaving the other open thread alone.
        assert shown("open", [working, untouched]) == [working, untouched]
        assert expanded_actions(working) == ["[open]", ["Reply", "Resolve", "Archive", "Delete"]]
        browser.execute_script(f'''
          document.querySelector("[data-comment-id='{working}'] .comment-pick").click();
        ''')
        assert browser.execute_script('''
          const button = document.querySelector("#archive-picked-btn");
          return [button.disabled, button.getAttribute("aria-label"), button.title];
        ''') == [False, "Archive 1", "Archive the picked comments"]
        browser.find_element("css selector", "#archive-picked-btn").click()
        wait_until(lambda: get_json(f"{base}/comments/{working}")["status"] == "archived")
        wait_until(lambda: browser.execute_script(
            f'return document.querySelectorAll("[data-comment-id]").length === 1'
            f' && document.querySelector("[data-comment-id=\'{untouched}\']") !== null;'))
        assert get_json(f"{base}/comments/{untouched}")["status"] == "open"
        assert set(shown("archived", [working, kept])) == {working, kept}
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
