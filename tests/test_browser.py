"""Drive the review page in a real browser.

The comment sidebar is plain DOM code that the HTTP tests never execute, so the
one-click close and the in-place edit are exercised through actual clicks here.
"""

import json
import math
import os
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


def type_keys(browser, keys):
    """Keys pressed one by one, with <C-[> for Ctrl+[ and <Enter> for Enter."""
    actions = ActionSequence(browser, "key", "keyboard")
    for key in keys:
        if key == "<C-[>":
            actions.key_down("\ue009").key_down("[").key_up("[").key_up("\ue009")
        elif key == "<Enter>":
            actions.key_down("\ue007").key_up("\ue007")
        else:
            actions.key_down(key).key_up(key)
    actions.perform()

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
        f"main: paper.tex\nport: {port}\n", encoding="utf-8"
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
            "file": "paper.tex",
            "changes": [{"old": "Hello world.", "new": "Hello applied."}],
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
            if len((current := get_json(f"{base}/comments/{source_cid}"))["thread"]) > 1
            else None
        ))
        assert applied["status"] == "open"
        assert applied["thread"][-1]["edits"] == ["paper.tex:4-4"]
        # The proposal is in the file now, so the card no longer offers it.
        assert "suggestion" not in applied
        wait_until(lambda: browser.execute_script(f'''
          const source = Array.from(document.querySelectorAll("[data-comment-id]"))
            .find((node) => node.dataset.commentId === "{source_cid}");
          return source !== undefined && source.querySelector(".sugg-apply") === null;
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
                 "Agent is not waiting; queue this call until it reconnects (Ctrl+Alt+Enter)", "rgb(180, 74, 67)"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiter = pool.submit(lambda: urllib.request.urlopen(
                f"{base}/wait-review?listener=one&pid={os.getpid()}", timeout=10).read())
            wait_until(lambda: browser.execute_script('''
              const button = document.querySelector("#call-agent-btn");
              return button.classList.contains("agent-ready")
                && button.getAttribute("aria-label") === "Call agent"
                && button.title === "Call the waiting agent now (Ctrl+Alt+Enter)"
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
        # Ctrl+Alt+Enter presses the bell.
        browser.execute_script('document.body.dispatchEvent(new KeyboardEvent("keydown",'
                               ' {key: "Enter", ctrlKey: true, altKey: true, bubbles: true}))')
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
        # Text selected outside the Preview copies natively: the sidebar, the Settings
        # dialog, the note above the editor.
        assert browser.execute_script('''
          const selection = window.getSelection();
          const select = (node) => {
            const range = document.createRange();
            range.selectNodeContents(node);
            selection.removeAllRanges();
            selection.addRange(range);
            return selection.toString();
          };
          const allowed = (target, modifier) => target.dispatchEvent(new KeyboardEvent(
            "keydown", { key: "c", [modifier]: true, bubbles: true,
              cancelable: true, composed: true }));
          const text = document.querySelector(".thread-text");
          const result = {
            selected: select(text),
            mac: allowed(text, "metaKey"),
            ctrl: allowed(document.body, "ctrlKey"),
          };
          // The editor takes its own Ctrl+C, whatever is selected elsewhere.
          const field = document.querySelector("#source-editor textarea");
          result.field = false;
          field.addEventListener("keydown", () => { result.field = true; }, { once: true });
          allowed(field, "ctrlKey");
          const dialog = document.querySelector("#settings-dialog");
          dialog.showModal();
          result.settings = [select(dialog.querySelector("h3")), allowed(dialog, "ctrlKey")];
          dialog.close();
          document.querySelector("[data-view=split]").click();
          const notice = document.querySelector("#overleaf-file-notice");
          notice.textContent = "Co Author edited this file on Overleaf.";
          notice.hidden = false;
          result.notice = [select(notice), allowed(notice, "ctrlKey")];
          notice.hidden = true;
          document.querySelector("[data-view=pdf]").click();
          selection.removeAllRanges();
          result.pdf = allowed(document.querySelector("embedpdf-container"), "metaKey");
          return result;
        ''') == {"selected": "typo herre", "mac": True, "ctrl": True, "field": True,
                 "settings": ["Settings", True],
                 "notice": ["Co Author edited this file on Overleaf.", True], "pdf": False}

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
        f"main: paper.tex\nport: {port}\n", encoding="utf-8"
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
        # Ctrl with the wheel zooms about the point under the pointer, a quarter a notch: a
        # highlight under it stays under it, across too once the page is wider than the
        # pane, which centres a narrower page. The viewer's own gesture eased from the pane's
        # middle to the pointer over a third of the pane's width, so the words slid aside.
        def wheel_on_segment(delta_y):
            return browser.execute_script('''
              const root = document.querySelector("embedpdf-container").shadowRoot;
              const box = root.querySelector(".tex-comment-segment").getBoundingClientRect();
              const x = box.left + box.width / 2, y = box.top + box.height / 2;
              root.elementFromPoint(x, y).dispatchEvent(new WheelEvent("wheel", {
                deltaY: arguments[0], deltaMode: WheelEvent.DOM_DELTA_LINE, ctrlKey: true,
                clientX: x, clientY: y, bubbles: true, cancelable: true, composed: true}));
              return [x, y, box.width];
            ''', script_args=[delta_y])

        def settled(x, y, width, exact):
            # While the gesture runs the pages are a scaled picture; it ends with the zoom.
            box = browser.execute_script('''
              const root = document.querySelector("embedpdf-container").shadowRoot;
              if (Array.from(root.querySelectorAll("div")).some((node) => /scale/.test(node.style.transform))) {
                return [NaN, NaN, NaN];
              }
              const box = root.querySelector(".tex-comment-segment").getBoundingClientRect();
              return [box.left + box.width / 2, box.top + box.height / 2, box.width];
            ''')
            # The viewer keeps a zoom to two decimals, so the size lands within a step of it.
            return box if abs(box[2] - width) < width * 0.02 and abs(box[1] - y) <= 2 \
                and (not exact or abs(box[0] - x) <= 2) else None

        # Exact across only once the column under the pointer is wide enough to fill the pane
        # wherever the pointer holds it: below that the column is kept whole, or filling the
        # pane (further on).
        for delta_y, exact in ((-3, False),) * 6 + ((-3, True), (3, True)):
            x, y, width = wheel_on_segment(delta_y)
            wait_until(lambda: settled(x, y, width * (1.25 if delta_y < 0 else 1 / 1.25), exact))
        # A paper is read a column at a time: from a page a little narrower than the pane to
        # one a little wider, a pinch on a column leaves that column, the half of the page it
        # was on, whole in the pane rather than pushing its outer edge off.
        widths = '''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const viewport = Array.from(root.querySelectorAll("div")).find((node) =>
            /(auto|scroll)/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight);
          const pages = viewport.firstElementChild;
          return /scale/.test(pages.style.transform) ? null
            : [viewport.clientWidth, pages.getBoundingClientRect().width];
        '''

        def pinch_to(share):
            pane, pages = wait_until(lambda: browser.execute_script(widths))
            delta = -100 * math.log(share * pane / pages)
            # A trackpad's steps are small; one past 40px reads as a mouse notch.
            steps = max(1, math.ceil(abs(delta) / 30))
            x, y, _ = browser.execute_script('''
              const root = document.querySelector("embedpdf-container").shadowRoot;
              const box = root.querySelector(".tex-comment-segment").getBoundingClientRect();
              const x = box.left + box.width / 2, y = box.top + box.height / 2;
              for (let step = 0; step < arguments[1]; step++) {
                root.elementFromPoint(x, y).dispatchEvent(new WheelEvent("wheel", {
                  deltaY: arguments[0] / arguments[1], deltaMode: WheelEvent.DOM_DELTA_PIXEL, ctrlKey: true,
                  clientX: x, clientY: y, bubbles: true, cancelable: true, composed: true}));
              }
              return [x, y, box.width];
            ''', script_args=[delta, steps])
            wait_until(lambda: (now := browser.execute_script(widths))
                       and abs(now[1] - share * pane) < 0.02 * pane)
            return x, y

        pinch_to(0.95)
        x, _ = pinch_to(1.12)
        assert wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const viewport = Array.from(root.querySelectorAll("div")).find((node) =>
            /(auto|scroll)/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight);
          const page = viewport.firstElementChild.firstElementChild.getBoundingClientRect();
          const view = viewport.getBoundingClientRect();
          const middle = page.left + page.width / 2;
          const [left, right] = arguments[0] < middle ? [page.left, middle] : [middle, page.right];
          return left >= view.left - 1 && right <= view.left + viewport.clientWidth + 1;
        ''', script_args=[x]))
        # On the gap between the columns, the middle of the page, no column is chosen: the
        # page zooms about the pointer, and its middle stays under it.
        middle = browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const viewport = Array.from(root.querySelectorAll("div")).find((node) =>
            /(auto|scroll)/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight);
          const page = viewport.firstElementChild.firstElementChild.getBoundingClientRect();
          const x = page.left + page.width / 2, y = viewport.getBoundingClientRect().top + 200;
          root.elementFromPoint(x, y).dispatchEvent(new WheelEvent("wheel", {
            deltaY: -10, deltaMode: WheelEvent.DOM_DELTA_PIXEL, ctrlKey: true,
            clientX: x, clientY: y, bubbles: true, cancelable: true, composed: true}));
          return [x, page.width];
        ''')
        assert wait_until(lambda: (page := browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const viewport = Array.from(root.querySelectorAll("div")).find((node) =>
            /(auto|scroll)/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight);
          const pages = viewport.firstElementChild;
          if (/scale/.test(pages.style.transform)) return null;
          const box = pages.firstElementChild.getBoundingClientRect();
          return [box.left + box.width / 2, box.width];
        ''')) and page[1] > middle[1] * 1.05 and abs(page[0] - middle[0]) <= 2)
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
        # Ctrl+C over words picked in the Preview is still the Preview's to copy.
        assert browser.execute_script('''
          return !document.querySelector("embedpdf-container").dispatchEvent(new KeyboardEvent(
            "keydown", { key: "c", ctrlKey: true, bubbles: true, cancelable: true, composed: true }));
        ''') is True
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
def test_the_area_button_draws_a_box_to_comment_on(tmp_path: Path) -> None:
    """With the viewer's Area tool on, a drag draws a box even where it starts on text, the
    box becomes an area comment in PDF points, and the next drag selects text again."""
    import fitz

    sentence = "The quick brown fox jumps over the lazy dog."
    (tmp_path / "paper.tex").write_text(
        f"\\documentclass{{article}}\n\\begin{{document}}\n{sentence}\n\\end{{document}}\n",
        encoding="utf-8",
    )
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), sentence)
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_area_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8"
    )
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: shared.server.pdf_digest is not None)
        with fitz.open(shared.server.last_result.output_file) as pdf:
            words = pdf[0].search_for("quick brown fox")[0]
            page_width = pdf[0].rect.width
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1200, height=800)
        browser.navigate(base)
        page = wait_until(lambda: browser.execute_script(f'''
          const image = document.querySelector("embedpdf-container")?.shadowRoot?.querySelector("img");
          const rect = image?.getBoundingClientRect();
          return rect && rect.width > 100 ? [rect.left, rect.top, rect.width / {page_width}] : false;
        '''))

        def at(x, y):
            return int(page[0] + x * page[2]), int(page[1] + y * page[2])

        def drag(start, end):
            ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
                .pointer_move(*start).pointer_down() \
                .pointer_move(end[0], end[1], duration=300).pointer_up().perform()

        # The tool sits with pan and pointer in the viewer's toolbar and shows pressed as they do.
        area_tool = ('document.querySelector("embedpdf-container").shadowRoot'
                     '.querySelector(\'[data-epdf-i="main-toolbar"] button[aria-label="Draw a box to comment on"]\')')
        pressed = f'return {area_tool}.classList.contains("bg-interactive-selected");'
        wait_until(lambda: browser.execute_script(f"return {area_tool} !== null"))
        browser.execute_script(f"{area_tool}.click();")
        wait_until(lambda: browser.execute_script(pressed))
        box = (words.x0 - 20, words.y0 - 20, words.x1 + 120, words.y1 + 150)
        drag(at(words.x0 + 2, (words.y0 + words.y1) / 2), at(box[2], box[3]))
        wait_until(lambda: browser.execute_script('return document.querySelector("#compose-dialog").open'))
        assert browser.execute_script(
            'return document.querySelector("#compose-anchor").textContent') == "PDF area on p1"
        assert not browser.execute_script(pressed)
        browser.execute_script('''
          document.querySelector("#compose-text").value = "this box";
          document.querySelector("#compose-form").requestSubmit();
        ''')
        comments = wait_until(lambda: get_json(f"{base}/comments")["comments"] or False)
        anchor = comments[0]["anchor"]
        assert anchor["kind"] == "area" and anchor["page"] == 1 and anchor["region"]
        drawn = (words.x0 + 2, (words.y0 + words.y1) / 2, box[2], box[3])
        assert all(abs(got - want) < 2 / page[2] for got, want in zip(anchor["bbox"], drawn)), anchor

        # With the button up again, a drag across the words selects them.
        y = (words.y0 + words.y1) / 2
        drag(at(words.x0 + 2, y), at(words.x1 - 2, y))
        wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          return Array.from(root.querySelectorAll("button")).some((button) =>
            /comment/i.test(button.textContent) && button.getBoundingClientRect().width > 0);
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
@pytest.mark.skipif(shutil.which("synctex") is None or shutil.which("latexmk") is None,
                    reason="latexmk and synctex are required")
def test_source_beside_comment_opens_the_selected_words_in_the_editor(tmp_path: Path) -> None:
    """Source on a PDF selection opens the editor beside the Preview at the lines SyncTeX
    names and selects the words there, though the PDF joined the lines into one paragraph.
    → Preview goes the other way. Either turns the view to Split."""
    import fitz

    (tmp_path / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "A first line of the paragraph.\n"
        "The quick~brown fox jumps over the lazy dog.\n"
        "A last line follows.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_source_jump_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8"
    )
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: shared.server.pdf_digest is not None)
        with fitz.open(shared.server.last_result.output_file) as pdf:
            words = pdf[0].search_for("quick brown fox")[0]
            page_width = pdf[0].rect.width
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1400, height=900)
        browser.navigate(base)
        page = wait_until(lambda: browser.execute_script(f'''
          const image = document.querySelector("embedpdf-container")?.shadowRoot?.querySelector("img");
          const rect = image?.getBoundingClientRect();
          return rect && rect.width > 100 ? [rect.left, rect.top, rect.width / {page_width}] : false;
        '''))
        def select_words():
            image = browser.execute_script('''
              const rect = document.querySelector("embedpdf-container").shadowRoot
                .querySelector("img").getBoundingClientRect();
              return [rect.left, rect.top];
            ''')
            y = image[1] + (words.y0 + words.y1) / 2 * page[2]
            ActionSequence(browser, "pointer", "mouse", {"pointerType": "mouse"}) \
                .pointer_move(int(image[0] + (words.x0 + 1) * page[2]), int(y)).pointer_down() \
                .pointer_move(int(image[0] + (words.x1 - 1) * page[2]), int(y), duration=300).pointer_up().perform()

        def menu_button(label):
            return browser.execute_script(f'''
              return Array.from(document.querySelector("embedpdf-container").shadowRoot.querySelectorAll("button"))
                .some((button) => button.textContent.trim() === "{label}" && button.getBoundingClientRect().width > 0);
            ''')

        # Pressed in the Preview alone, Source turns the view to Split and selects the words.
        select_words()
        wait_until(lambda: menu_button("Source"))
        browser.execute_script('''
          Array.from(document.querySelector("embedpdf-container").shadowRoot.querySelectorAll("button"))
            .find((button) => button.textContent.trim() === "Source").click();
        ''')
        opened = wait_until(lambda: browser.execute_script('''
          const editor = (window.wrappedJSObject || window).ace?.edit("source-editor");
          const range = editor?.getSelectionRange();
          return document.querySelector(".layout").classList.contains("view-split") && editor
            && editor.getSelectedText() !== ""
            ? {text: editor.getSelectedText(), row: range.start.row} : false;
        '''))
        assert opened == {"text": "quick~brown fox", "row": 3}

        # And back: → Preview on words selected in the editor alone turns the view to Split
        # and marks where the Preview prints them.
        browser.execute_script('document.querySelector(".view-tab[data-view=source]").click()')
        wait_until(lambda: browser.execute_script(
            'return document.querySelector(".layout").classList.contains("view-source")'))
        browser.execute_script('''
          const page = window.wrappedJSObject || window;
          const editor = page.ace.edit("source-editor");
          const Range = page.ace.require("ace/range").Range;
          editor.selection.setRange(new Range(3, 10, 3, 22));   // "brown fox ju"
          editor.focus();
        ''')
        # Its keys, Ctrl+Alt+P, do what the button does.
        ActionSequence(browser, "key", "keyboard").key_down("\ue009").key_down("\ue00a").key_down("p") \
            .key_up("p").key_up("\ue00a").key_up("\ue009").perform()
        with fitz.open(shared.server.last_result.output_file) as pdf:
            printed = pdf[0].search_for("brown fox ju")[0]
        marked = wait_until(lambda: browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const image = root.querySelector("img").getBoundingClientRect();
          const mark = Array.from(root.querySelectorAll("div"))
            .find((node) => node.style.opacity === "0.6" && node.getBoundingClientRect().width > 0);
          if (!mark) return false;
          const rect = mark.getBoundingClientRect();
          return [rect.left - image.left, rect.top - image.top, rect.right - image.left, rect.bottom - image.top,
                  image.width];
        '''))
        scale = marked[4] / page_width
        drawn = [value / scale for value in marked[:4]]
        assert all(abs(got - want) < 3 for got, want in zip(drawn, printed)), (drawn, printed)
        assert browser.execute_script('return document.querySelector(".layout").classList.contains("view-split")')
        # With Vim keys, gss shows the cursor's line in the Preview as the keys above do.
        browser.execute_script('''
          const page = window.wrappedJSObject || window;
          const fetch = page.fetch.bind(page);
          page.shown = 0;
          page.fetch = (url, ...rest) => {
            if (String(url).includes("/synctex/source-to-pdf-words")) page.shown += 1;
            return fetch(url, ...rest);
          };
          document.querySelector("#source-vim-btn").click();
        ''', sandbox=None)
        editor = '(window.wrappedJSObject || window).ace.edit("source-editor")'
        wait_until(lambda: browser.execute_script(f"return Boolean({editor}.$vimModeHandler);"))
        browser.execute_script(f"{editor}.focus(); {editor}.gotoLine(4, 0);")
        type_keys(browser, [*"gss"])
        assert wait_until(lambda: browser.execute_script("return window.shown;", sandbox=None))
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
        f"main: paper.tex\nport: {port}\n", encoding="utf-8"
    )
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_hold_")
    marionette_port = available_port()
    # A coarse pointer is what a finger is; the touch rules key off it.
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n'
        'user_pref("ui.primaryPointerCapabilities", 1);\n'
        'user_pref("dom.maxtouchpoints.testing.value", 5);\n', encoding="utf-8"
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

        # A touch screen starts in the pointer mode, where a finger dragged on the pages
        # scrolls them too: the layer the viewer selects on lets a touch pan.
        pressed = '''
          return document.querySelector("embedpdf-container").shadowRoot
            .querySelector(`button[aria-label="Toggle ${arguments[0]} Mode"]`)
            .classList.contains("bg-interactive-selected");
        '''
        assert browser.execute_script(pressed, script_args=["Pointer"])
        assert not browser.execute_script(pressed, script_args=["Pan"])
        # The editor's file list draws its name in the bar's type, not the 16px a list takes
        # on a touch screen, and lies clear over it.
        assert browser.execute_script('''
          const name = getComputedStyle(document.querySelector("#source-file-name"));
          const list = getComputedStyle(document.querySelector("#source-file"));
          return [name.display, parseFloat(name.fontSize) < 14, list.opacity];
        ''') == ["block", True, "0"]
        assert browser.execute_script('''
          const root = document.querySelector("embedpdf-container").shadowRoot;
          const layers = Array.from(root.querySelectorAll('[style*="touch-action: none"]'))
            .filter((node) => !node.matches("[data-epdf-handle], [data-epdf-vertex]"));
          return layers.length > 0 && layers.every((node) => getComputedStyle(node).touchAction === "pan-x pan-y");
        ''')

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

        # Dragged at once, it scrolls and selects nothing: the viewer never sees the press.
        finger().pointer_move(int(highlight["left"] + 5), y).pointer_down() \
            .pointer_move(int(highlight["left"] + 40), y) \
            .pointer_move(int(highlight["right"] + 60), y).pointer_up().perform()
        time.sleep(0.8)
        assert not browser.execute_script(menu_shown)

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
        f"main: paper.tex\nport: {port}\n", encoding="utf-8"
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
        # A− and A+ step the editor's type by a pixel and keep the size for the next visit.
        font_size = 'return (window.wrappedJSObject || window).ace.edit("source-editor").getFontSize();'
        assert browser.execute_script(font_size) == "13px"
        for button in ("larger", "larger", "smaller"):
            browser.execute_script(f'document.querySelector("#source-font-{button}-btn").click()')
        assert browser.execute_script(font_size) == "14px"
        assert browser.execute_script('return localStorage.getItem("texMcpSourceFontSize")') == "14"
        browser.execute_script('document.querySelector("#source-font-smaller-btn").click()')
        # The moon button turns the page dark and back, keeps the choice, and takes the
        # editor and the PDF viewer with it.
        theme = '''
          const page = window.wrappedJSObject || window;
          const viewer = document.querySelector("embedpdf-container");
          return [document.documentElement.dataset.theme, localStorage.getItem("texMcpTheme"),
                  page.ace.edit("source-editor").getTheme(), viewer.getAttribute("data-tex-theme"),
                  (viewer.wrappedJSObject || viewer).themePreference];
        '''
        pick = 'document.querySelector("[data-theme-choice=%s]").click()'
        browser.execute_script(pick % "dark")
        assert browser.execute_script(theme) == ["dark", "dark", "ace/theme/one_dark", "dark", "dark"]
        # Warm, the restful theme, is drawn on the light schemes with its own colours.
        browser.execute_script(pick % "warm")
        assert browser.execute_script(theme) == ["warm", "warm", "ace/theme/textmate", "warm", "light"]
        assert browser.execute_script(
            'return document.querySelector("[data-theme-choice=warm]").getAttribute("aria-checked")') == "true"
        assert browser.execute_script(
            'return getComputedStyle(document.body).backgroundColor') == "rgb(231, 225, 215)"
        browser.execute_script(pick % "light")
        assert browser.execute_script(theme) == ["light", "light", "ace/theme/textmate", "light", "light"]
        # Ctrl+Enter in the editor compiles, as the button does.
        browser.execute_script('''
          window.compiles = 0;
          const fetch = window.fetch.bind(window);
          window.fetch = (url, ...rest) => {
            if (String(url) === "/compile") window.compiles += 1;
            return fetch(url, ...rest);
          };
        ''', sandbox=None)
        browser.execute_script('(window.wrappedJSObject || window).ace.edit("source-editor").focus()')
        ActionSequence(browser, "key", "keyboard").key_down("\ue009").key_down("\ue007") \
            .key_up("\ue007").key_up("\ue009").perform()
        assert wait_until(lambda: browser.execute_script("return window.compiles;", sandbox=None)) == 1
        wait_until(lambda: not browser.execute_script('return document.querySelector("#compile-btn").disabled'))
        # Long lines wrap until the wrap button turns it off, and the Vim button gives the
        # editor Vim keys, with Ctrl+[ as Escape and :w saving.
        editor = '(window.wrappedJSObject || window).ace.edit("source-editor")'
        assert browser.execute_script(f"return {editor}.session.getUseWrapMode();") is True
        browser.execute_script('document.querySelector("#source-wrap-btn").click()')
        assert browser.execute_script(f"return {editor}.session.getUseWrapMode();") is False
        assert browser.execute_script('return localStorage.getItem("texMcpSourceWrap")') == "0"
        browser.execute_script('document.querySelector("#source-wrap-btn").click()')
        assert browser.execute_script(f"return {editor}.session.getUseWrapMode();") is True
        before = browser.execute_script(f"return {editor}.getValue();")
        browser.execute_script('document.querySelector("#source-vim-btn").click()')
        wait_until(lambda: browser.execute_script(f"return Boolean({editor}.$vimModeHandler);"))
        # Outside Insert mode the editor's input field is read-only, so a held key repeats
        # rather than opening macOS's accent menu; Insert mode writes again.
        # A yank goes to the system clipboard too.
        browser.execute_script('''
          navigator.clipboard.writeText = (text) => { window.copied = text; return Promise.resolve(); };
        ''', sandbox=None)
        type_keys(browser, [*"ggyy"])
        assert wait_until(lambda: browser.execute_script("return window.copied ?? null;", sandbox=None)) \
            == browser.execute_script(f"return {editor}.session.getLine(0) + '\\n';")
        field = f"{editor}.textInput.getElement().readOnly"
        assert browser.execute_script(f"return {field};") is True
        type_keys(browser, ["i"])
        assert browser.execute_script(f"return {field};") is False
        type_keys(browser, ["<C-[>"])
        assert browser.execute_script(f"return {field};") is True
        # jj typed quickly leaves Insert mode too, and leaves no j behind.
        kept = browser.execute_script(f"return {editor}.getValue();")
        type_keys(browser, ["i", "j", "j"])
        assert wait_until(lambda: browser.execute_script(f"return {field};") is True)
        assert browser.execute_script(f"return {editor}.getValue();") == kept
        # With a Korean input method on, a key outside Insert mode is read by where it sits
        # (the key that gives ㅣ is l), and the letter the method composes is dropped.
        ime = browser.execute_script(f"""
          const editor = {editor};
          editor.focus();
          editor.gotoLine(1, 0);
          const press = (code) => editor.textInput.getElement().dispatchEvent(new KeyboardEvent("keydown",
            {{key: "Process", code, bubbles: true, cancelable: true}}));
          let blurred = 0;
          editor.textInput.getElement().addEventListener("blur", () => {{ blurred += 1; }});
          editor.textInput.getElement().dispatchEvent(new CompositionEvent("compositionstart", {{bubbles: true, data: ""}}));
          editor.textInput.getElement().dispatchEvent(new CompositionEvent("compositionupdate", {{bubbles: true, data: "ㅣ"}}));
          press("KeyL");
          const column = editor.getCursorPosition().column;
          editor.onTextInput("ㅣ");
          press("KeyH");
          return [column, editor.getCursorPosition().column, editor.getValue() === arguments[0], blurred];
        """, script_args=[before], sandbox=None)
        # A composition outside Insert mode never reaches Ace, which cancelled it by taking
        # the focus off the editor and back: the cursor flickered in Safari at each key.
        assert ime == [1, 0, True, 0], ime
        type_keys(browser, [*"ggOvimtest", "<C-[>", *"dd"])
        assert browser.execute_script(f"return {editor}.getValue();") == before
        assert browser.execute_script('return document.querySelector("#source-save-btn").disabled') is False
        type_keys(browser, [*":w", "<Enter>"])
        wait_until(lambda: browser.execute_script('return document.querySelector("#source-save-btn").disabled'))
        # gc comments on what a motion covers: gcc the line the cursor is on, gcj that line
        # and the next. The editor has the keys back once the comment is dropped.
        for keys, label in (("ggjgcc", "Source: paper.tex:2-2"), ("gggcj", "Source: paper.tex:1-2")):
            type_keys(browser, [*keys])
            wait_until(lambda: browser.execute_script('return document.querySelector("#compose-dialog").open'))
            assert browser.execute_script('return document.querySelector("#compose-anchor").textContent') == label
            browser.execute_script('document.querySelector("#compose-cancel").click()')
            wait_until(lambda: browser.execute_script(
                'return document.querySelector("#source-editor").contains(document.activeElement)'))
        browser.execute_script('document.querySelector("#source-vim-btn").click()')
        wait_until(lambda: not browser.execute_script(f"return Boolean({editor}.$vimModeHandler);"))
        assert browser.execute_script(f"return {editor}.textInput.getElement().readOnly;") is False
        # Without Vim keys, Ctrl+Alt+M comments on the selection.
        browser.execute_script(f"{editor}.focus(); {editor}.selection.selectLine();")
        ActionSequence(browser, "key", "keyboard").key_down("\ue009").key_down("\ue00a").key_down("m") \
            .key_up("m").key_up("\ue00a").key_up("\ue009").perform()
        wait_until(lambda: browser.execute_script('return document.querySelector("#compose-dialog").open'))
        # Ctrl+[ drops the comment being written, as Escape does where Safari keeps Escape.
        ActionSequence(browser, "key", "keyboard").key_down("\ue009").key_down("[") \
            .key_up("[").key_up("\ue009").perform()
        wait_until(lambda: browser.execute_script('return !document.querySelector("#compose-dialog").open'))
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
        # Ctrl+Alt+K on the commented characters opens their comment in the comments.
        browser.execute_script('const e = (window.wrappedJSObject || window).ace.edit("source-editor"); e.focus(); e.moveCursorTo(3, 130);')
        ActionSequence(browser, "key", "keyboard").key_down("\ue009").key_down("\ue00a").key_down("k") \
            .key_up("k").key_up("\ue00a").key_up("\ue009").perform()
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("[data-comment-id].is-focused")?.dataset.commentId') == comment["id"])
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
def test_shift_picks_the_run_between_two_boxes(tmp_path: Path) -> None:
    """Picking six threads one box at a time is six clicks. Shift takes the run from the
    box the pick started at to the one it ends at, the way a list of files does, and the
    run takes the value the second box just took, so shift also lets a run go."""
    import fitz

    (tmp_path / "paper.tex").write_text(PAPER, encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")

    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_shift_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        ids = [post_json(f"{base}/comments",
                         {"anchor": {"kind": "paper"}, "text": f"request {n}"})["id"]
               for n in range(5)]

        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1500, height=1000)
        browser.navigate(base)
        wait_until(lambda: browser.execute_script(
            "return document.querySelectorAll('.comment-pick').length === 5 || false;"))

        def click(comment_id, shift):
            browser.execute_script(f"""
              const box = document.querySelector("[data-comment-id='{comment_id}'] .comment-pick");
              box.dispatchEvent(new MouseEvent("click",
                {{bubbles: true, cancelable: true, shiftKey: {str(shift).lower()}}}));
            """)

        def picked():
            return browser.execute_script(
                "return Array.from(document.querySelectorAll('[data-comment-id]'))"
                "  .filter((card) => card.querySelector('.comment-pick').checked)"
                "  .map((card) => card.dataset.commentId);")

        click(ids[1], shift=False)
        assert picked() == [ids[1]]
        # Two clicks take the four threads between them, ends included.
        click(ids[4], shift=True)
        assert picked() == ids[1:5]
        assert browser.execute_script(
            "return document.querySelector('#archive-picked-btn').getAttribute('aria-label');"
        ) == "Archive 4"

        # The run takes what the second box became, so the same gesture lets it go again.
        click(ids[1], shift=False)
        assert picked() == ids[2:5]
        click(ids[4], shift=True)
        assert picked() == []

        # Without shift only the one box moves.
        click(ids[0], shift=False)
        click(ids[3], shift=False)
        assert picked() == [ids[0], ids[3]]

        # A narrow panel keeps the less pressed buttons behind the dots, each with its words,
        # and a wide one has them back in the row.
        rare = '''const button = document.querySelector("#archive-picked-btn");
          return [document.querySelector(".filter-row").classList.contains("narrow"),
                  button.getBoundingClientRect().width > 0, getComputedStyle(button, "::after").content];'''
        width = lambda pixels: browser.execute_script(
            f'document.querySelector(".layout").style.setProperty("--tex-mcp-sidebar-width", "{pixels}px")')
        assert browser.execute_script(rare) == [False, True, "none"]
        width(240)
        wait_until(lambda: browser.execute_script(rare) == [True, False, '"Archive 2"'])
        browser.find_element("css selector", "#comment-rare-btn").click()
        assert browser.execute_script(rare) == [True, True, '"Archive 2"']
        browser.find_element("css selector", "#comments-list").click()
        assert browser.execute_script(rare)[1] is False
        width(700)
        wait_until(lambda: browser.execute_script(rare) == [False, True, "none"])
    finally:
        if browser is not None:
            browser.delete_session()
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
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")

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

        # The tab of comments written here offers these views, and no unanswered one.
        assert browser.execute_script(
            'return Array.from(document.querySelectorAll("#comment-filter option")).filter((o) => !o.hidden)'
            '.map((o) => [o.value, o.textContent]);'
        ) == [["open", "open"], ["resolved", "resolved"], ["archived", "archived"], ["all", "all"]]
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


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox is required")
def test_split_puts_the_translation_where_the_layout_menu_says(tmp_path: Path, monkeypatch) -> None:
    """The translation sits under the Preview in Preview, under the editor in Source, and in
    Split where the layout menu puts it; each place keeps its grip's share and the choice
    survives a reload. A narrow window stacks every pane."""
    import fitz

    monkeypatch.setattr("tex_mcp_web.translate.run_claude",
                        lambda prompt, cwd, system: json.dumps({"t": {}}))
    (tmp_path / "paper.tex").write_text(PAPER, encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(
        f"main: paper.tex\nport: {port}\ntranslation:\n  language: Korean\n", encoding="utf-8")

    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_layout_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1500, height=1000)
        browser.navigate(base)

        def boxes():
            return browser.execute_script("""
              const out = {};
              for (const [name, id] of [["preview", "pdf-pane"], ["source", "source-pane"],
                                        ["translation", "translation-pane"]]) {
                const element = document.getElementById(id);
                if (getComputedStyle(element).display === "none") continue;
                const box = element.getBoundingClientRect();
                out[name] = [box.left, box.top, box.right, box.bottom].map(Math.round);
              }
              return out;
            """)

        def choose(place):
            browser.execute_script(f"""
              const menu = document.querySelector("#layout-select");
              menu.value = "{place}";
              menu.dispatchEvent(new Event("change", {{bubbles: true}}));
            """)
            return wait_until(lambda: (lambda shown: shown if len(shown) >= 2 else False)(boxes()))

        def view(name):
            browser.execute_script(f'document.querySelector(".view-tab[data-view={name}]").click();')

        button = wait_until(lambda: browser.execute_script(
            'const b = document.querySelector("#translation-btn");'
            'return b.classList.contains("hidden") ? false : b.textContent;'))
        assert button == "Korean"
        assert set(boxes()) == {"preview"}

        browser.execute_script('document.querySelector("#translation-btn").click();')
        shown = wait_until(lambda: (lambda b: b if "translation" in b else False)(boxes()))
        assert shown["translation"][1] > shown["preview"][3]           # under the Preview
        assert shown["translation"][0] == shown["preview"][0]
        assert browser.execute_script(
            'return document.querySelector("#layout-select").classList.contains("hidden");')

        view("source")
        shown = wait_until(lambda: (lambda b: b if "source" in b else False)(boxes()))
        assert set(shown) == {"source", "translation"}
        assert shown["translation"][1] > shown["source"][3]

        view("split")
        wait_until(lambda: not browser.execute_script(
            'return document.querySelector("#layout-select").classList.contains("hidden");'))
        shown = choose("preview")
        assert shown["translation"][1] > shown["preview"][3]
        assert shown["source"][0] > shown["preview"][2] and shown["source"][0] > shown["translation"][2]
        assert shown["source"][3] == shown["translation"][3]           # Source spans both rows

        shown = choose("source")
        assert shown["translation"][1] > shown["source"][3]
        assert shown["translation"][0] == shown["source"][0] > shown["preview"][2]
        assert shown["preview"][3] == shown["translation"][3]

        shown = choose("column")
        assert shown["preview"][2] < shown["source"][0] < shown["source"][2] < shown["translation"][0]
        assert shown["preview"][1] == shown["source"][1] == shown["translation"][1]

        # The translation grip's share in this place moves by a key and stays this place's.
        before = shown["translation"][0]
        browser.execute_script("""
          const grip = document.querySelector("#translation-grip");
          grip.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowLeft", bubbles: true}));
        """)
        moved = wait_until(lambda: (lambda b: b if b["translation"][0] < before - 10 else False)(boxes()))
        assert browser.execute_script(
            'return document.querySelector("#translation-grip").getAttribute("aria-orientation");'
        ) == "vertical"

        shown = choose("replace")
        assert set(shown) == {"preview", "translation"}
        assert shown["translation"][0] > shown["preview"][2]

        choose("column")
        browser.refresh()
        shown = wait_until(lambda: (lambda b: b if len(b) == 3 else False)(boxes()))
        assert browser.execute_script('return document.querySelector("#layout-select").value;') == "column"
        assert shown["translation"][0] == moved["translation"][0]

        # A narrow window stacks the three panes in the order the place reads.
        browser.set_window_rect(x=0, y=0, width=900, height=1000)
        shown = wait_until(lambda: (lambda b: b if b["source"][1] > b["preview"][3] else False)(boxes()))
        assert shown["preview"][3] < shown["source"][1] < shown["source"][3] < shown["translation"][1]
        assert browser.execute_script(
            'return document.querySelector("#split-grip").getAttribute("aria-orientation");'
        ) == "horizontal"
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
def test_the_more_menu_opens_overleaf_and_its_comments_have_their_own_tab(tmp_path: Path) -> None:
    """The top bar and the more menu open the Overleaf dialog, which asks for the
    project's link first. Comments from Overleaf are in the leaf Comments tab, apart from
    the comments written here."""
    import fitz

    from tex_mcp_web.comments import CommentStore, PaperAnchor

    (tmp_path / "paper.tex").write_text(PAPER, encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    # Threads brought in from Overleaf earlier: their comments here, and the ledger naming
    # them. The first waits for the reviewer, who answered the second from here and the
    # third on Overleaf.
    kept = CommentStore(tmp_path / ".tex-mcp-web" / "comments.json")
    brought = kept.add(PaperAnchor(), "Say hello properly.", name="A. Reviewer")
    answered = kept.add(PaperAnchor(), "Which words?", name="A. Reviewer")
    kept.sent_to_overleaf(answered.id, "These.", "")
    answered_there = kept.add(PaperAnchor(), "Is this clear?", name="A. Reviewer")
    kept.reply(answered_there.id, "Yes, see the intro.", "human", name="Me Myself")
    (tmp_path / ".tex-mcp-web" / "overleaf-imported.json").write_text(json.dumps(
        {"t1": {"comment": brought.id, "messages": 1, "seen": ["#0"]},
         "t2": {"comment": answered.id, "entries": {}}, "t3": {"comment": answered_there.id, "entries": {}}}),
        encoding="utf-8")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_overleaf_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.navigate(base)
        wait_until(lambda: browser.execute_script('return document.querySelector("#comments-list") !== null'))
        # Unconnected, the top bar still offers Overleaf, and its button opens the dialog.
        top = 'const b = document.querySelector("#overleaf-live-btn"); return [b.hidden, b.textContent];'
        assert browser.execute_script(top) == [False, "Connect Overleaf"]
        browser.execute_script('document.querySelector("#overleaf-live-btn").click()')
        wait_until(lambda: browser.execute_script('return document.querySelector("#settings-dialog").open'))
        browser.execute_script('document.querySelector("#settings-dialog").close()')
        # On a phone the row has no room for it or for the gear, and the ⋯ menu offers Settings.
        size = browser.window_rect
        drawn = lambda selector: browser.execute_script(f'return getComputedStyle(document.querySelector("{selector}")).display')
        assert drawn("#settings-btn") != "none" and drawn("#settings-menu-btn") == "none"
        browser.set_window_rect(width=480, height=800)
        wait_until(lambda: drawn("#overleaf-live-btn") == "none" and drawn("#settings-btn") == "none")
        browser.execute_script('document.querySelector("#more-btn").click()')
        assert browser.execute_script('return document.querySelector("#more-menu").hidden') is False
        assert drawn("#settings-menu-btn") != "none"
        browser.execute_script('document.querySelector("#settings-menu-btn").click()')
        wait_until(lambda: browser.execute_script('return document.querySelector("#settings-dialog").open'))
        assert browser.execute_script('return document.querySelector("#more-menu").hidden') is True
        browser.execute_script('document.querySelector("#settings-dialog").close()')
        browser.set_window_rect(width=size["width"], height=size["height"])
        wait_until(lambda: drawn("#overleaf-live-btn") != "none" and drawn("#settings-btn") != "none")
        browser.execute_script('document.querySelector("#settings-btn").click()')
        wait_until(lambda: browser.execute_script('return document.querySelector("#settings-dialog").open'))
        # With no project named, Settings asks for its link before anything else.
        wait_until(lambda: browser.execute_script(
            'return !document.querySelector("#overleaf-link-row").hidden'
            ' && document.querySelector("#overleaf-connected").hidden'
            ' && document.querySelector("#overleaf-mode").hidden'))
        # A paper with threads from Overleaf shows their tab, though it names no project.
        leaf_tab = '.tab-btn[data-tab="overleaf-comments"]'
        wait_until(lambda: browser.execute_script(f"return !document.querySelector('{leaf_tab}').hidden"))
        browser.execute_script('document.querySelector("#settings-dialog").close()')
        post_json(f"{base}/comments", {"anchor": {"kind": "paper"}, "text": "Written here."})
        listed = 'return document.querySelector("#comments-list").textContent'
        count = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").textContent')
        wait_until(lambda: "Written here." in browser.execute_script(listed))
        assert "Say hello properly." not in browser.execute_script(listed)
        assert count("#comments-tab-count") == "1" and count("#overleaf-tab-count") == "3"
        browser.execute_script(f"document.querySelector('{leaf_tab}').click()")
        wait_until(lambda: "Say hello properly." in browser.execute_script(listed))
        assert "Written here." not in browser.execute_script(listed)
        # The tab opens on the threads whose last message on Overleaf is someone else's. Who
        # the reviewer is on Overleaf comes with the page's place in the project.
        assert browser.execute_script('const f = document.querySelector("#comment-filter");'
                                      ' return [f.value, f.options[0].value, f.options[0].hidden,'
                                      ' document.querySelector("#comment-filter-name").textContent];') \
            == ["unanswered", "unanswered", False, "unanswered"]
        assert "Which words?" not in browser.execute_script(listed)
        assert "Is this clear?" in browser.execute_script(listed)
        import asyncio
        asyncio.run_coroutine_threadsafe(shared.server.broadcast(
            {"type": "overleaf_live", "status": "off", "me": "Me Myself"}), shared.loop).result(5)
        wait_until(lambda: "Is this clear?" not in browser.execute_script(listed))
        assert "Say hello properly." in browser.execute_script(listed)
        browser.execute_script('const f = document.querySelector("#comment-filter"); f.value = "open";'
                               ' f.dispatchEvent(new Event("change"));')
        wait_until(lambda: "Which words?" in browser.execute_script(listed) and "Is this clear?" in browser.execute_script(listed))
        # Its message goes by the name Overleaf gives the writer, with the leaf, and is
        # answered rather than edited.
        theirs = f"[data-comment-id='{brought.id}']"
        assert browser.execute_script(f'return document.querySelector("{theirs} .cmt-preview").textContent') \
            == "A. Reviewer  Say hello properly."
        browser.execute_script(f'document.querySelector("{theirs} .cmt-head").click()')
        wait_until(lambda: browser.execute_script(f'return document.querySelector("{theirs} .thread-meta") !== null'))
        assert browser.execute_script(f'''
          const meta = document.querySelector("{theirs} .thread-meta");
          return [meta.firstChild.textContent.split(" · ")[0], meta.querySelector(".entry-overleaf").title,
                  meta.querySelector(".cmt-edit") === null];
        ''') == ["A. Reviewer", "Written on Overleaf", True]
        assert browser.execute_script('return document.querySelector("#tab-comments").classList.contains("active")')
        # Overleaf has no archive, so the tab offers none, and its filter has no archived view.
        assert browser.execute_script('return document.querySelector("#archive-picked-btn").hidden') is True
        assert browser.execute_script(
            'return document.querySelector(\'#comment-filter option[value="archived"]\').hidden') is True
        # A thread the agent drafted for Overleaf waits in this tab, with nothing said in it
        # yet: its first message is the draft, and it is sent or deleted.
        from tex_mcp_web.comments import OverleafDraft, ResolvedSource, SourceRangeAnchor, capture_source_selector

        store = CommentStore(tmp_path / ".tex-mcp-web" / "comments.json")
        drafted = store.add_overleaf_thread(
            SourceRangeAnchor(file="paper.tex", line_start=3, line_end=3), OverleafDraft("Feedback and answer."),
            ResolvedSource(file="paper.tex", line_start=3, line_end=3),
            capture_source_selector(tmp_path / "paper.tex", 3, 3))
        card = f"[data-comment-id='{drafted.id}']"
        wait_until(lambda: browser.execute_script(f'return document.querySelector("{card} .overleaf-reply-input")?.value')
                   == "Feedback and answer.")
        assert "New thread on Overleaf" in browser.execute_script(f'return document.querySelector("{card}").textContent')
        assert count("#overleaf-tab-count") == "4"
        browser.execute_script(f'document.querySelector("{card} .cmt-head").click()')
        wait_until(lambda: browser.execute_script(f'return document.querySelector("{card} .cmt-actions") !== null'))
        assert browser.execute_script(
            f'return [...document.querySelectorAll("{card} .cmt-actions button")].map((b) => b.textContent)') == ["Delete"]
        browser.execute_script('document.querySelector(\'.tab-btn[data-tab="comments"]\').click()')
        wait_until(lambda: "Written here." in browser.execute_script(listed))
        assert browser.execute_script(
            'return document.querySelector(\'#comment-filter option[value="unanswered"]\').hidden') is True
        assert "Feedback and answer." not in browser.execute_script(
            'return [...document.querySelectorAll(".overleaf-reply-input")].map((b) => b.value).join("|")')
        assert browser.execute_script('return document.querySelector("#archive-picked-btn").hidden') is False
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
def test_the_more_menu_downloads_the_pdf_and_a_section_comment_keeps_its_heading(tmp_path: Path) -> None:
    """Download PDF in the more menu saves the PDF, under its own name while no Overleaf
    project is named. + comment in the Sections tab lands on the heading it was pressed
    on, though another heading has the same title."""
    import fitz

    (tmp_path / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\section{Methods}\n\\subsection{Setup}\nFirst.\n"
        "\\section{Experiments}\n\\subsection{Setup}\nSecond.\n\\end{document}\n", encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_download_")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    marionette_port = available_port()
    prefs = {"marionette.port": marionette_port, "browser.download.folderList": 2,
             "browser.download.dir": str(downloads), "browser.download.useDownloadDir": True,
             "browser.download.always_ask_before_handling_new_types": False,
             "browser.helperApps.neverAsk.saveToDisk": "application/pdf", "pdfjs.disabled": True}
    (Path(profile) / "user.js").write_text(
        "".join(f"user_pref({json.dumps(key)}, {json.dumps(value)});\n" for key, value in prefs.items()),
        encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper")["pdf_digest"])
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.navigate(base)
        wait_until(lambda: browser.execute_script('return document.querySelectorAll(".paper-section").length') == 4)
        browser.execute_script('document.querySelector("#more-btn").click()')
        assert browser.execute_script('return document.querySelector("#download-pdf-btn").disabled') is False
        browser.execute_script('document.querySelector("#download-pdf-btn").click()')
        assert browser.execute_script('return document.querySelector("#more-menu").hidden') is True
        saved = downloads / "paper.pdf"
        wait_until(lambda: saved.is_file() and not list(downloads.glob("*.part")))
        with urllib.request.urlopen(f"{base}/pdf", timeout=3) as response:
            assert saved.read_bytes() == response.read()

        browser.execute_script('document.querySelectorAll(".paper-section")[3].querySelector(".comment-section-btn").click()')
        wait_until(lambda: browser.execute_script('return document.querySelector("#compose-dialog").open'))
        browser.execute_script('''
          document.querySelector("#compose-text").value = "Which setup is this?";
          document.querySelector("#compose-form").requestSubmit();
        ''')
        made = wait_until(lambda: next((c for c in get_json(f"{base}/comments")["comments"]
                                        if c["thread"][0]["text"] == "Which setup is this?"), None))
        # The second Setup, to the end of the file, not the first one at line 4.
        assert (made["resolved_source"]["line_start"], made["resolved_source"]["line_end"]) == (7, 9)
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
def test_the_overleaf_dialog_keeps_the_cookie_and_reads_overleaf_itself(tmp_path: Path, monkeypatch) -> None:
    """With the project named in the config, the dialog takes the reviewer's session cookie
    once, keeps it in their own config folder for them alone, never shows it again, and
    brings the project's comments in from Overleaf with one button. The paper's folder is a
    git clone of the project: the dialog says where it stands against Overleaf's branch,
    pulls and pushes, and downloads comments only once the folder has Overleaf's commits."""
    import fitz
    import http.server
    import stat
    import threading

    project = "0123456789abcdef01234567"
    cookie = "s%3Asecret.value"
    threads = {"t1": {"messages": [{"user": {"first_name": "A.", "last_name": "Reviewer"},
                                    "content": "Say hello properly."}]}}
    ranges = [{"id": "d1", "ranges": {"comments": [{"op": {"c": "Hello world.", "p": PAPER.index("Hello"), "t": "t1"}}]}}]

    class Overleaf(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if f"overleaf_session2={cookie}" not in self.headers.get("Cookie", ""):
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            routes = {f"/project/{project}/threads": threads, f"/project/{project}/ranges": ranges,
                      f"/project/{project}/entities": {"entities": [{"path": "/paper.tex", "type": "doc"}]}}
            if self.path in routes:
                body, kind = json.dumps(routes[self.path]).encode(), "application/json"
            elif self.path == f"/Project/{project}/doc/d1/download":
                body, kind = PAPER.encode(), "text/plain"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Disposition", 'attachment; filename="paper.tex"')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    overleaf_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Overleaf)
    threading.Thread(target=overleaf_server.serve_forever, daemon=True).start()
    config_home = tmp_path / "config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("TEX_MCP_WEB_OVERLEAF_COOKIE", raising=False)

    def git(folder, *args):
        subprocess.run(["git", "-C", str(folder), "-c", "user.name=Someone", "-c", "user.email=someone@example.com",
                        *args], check=True, capture_output=True)

    # Overleaf's repository, a co-author's clone with one commit more, and the paper's folder.
    remote = tmp_path / "git" / project
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    coauthor = tmp_path / "coauthor"
    subprocess.run(["git", "clone", "-q", str(remote), str(coauthor)], check=True, capture_output=True)
    (coauthor / "paper.tex").write_text(PAPER, encoding="utf-8")
    git(coauthor, "add", "paper.tex")
    git(coauthor, "commit", "-q", "-m", "first")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    paper = tmp_path / "paper"
    subprocess.run(["git", "clone", "-q", "-o", "overleaf", str(remote), str(paper)], check=True)
    (coauthor / "notes.tex").write_text("A note.\n", encoding="utf-8")
    git(coauthor, "add", "notes.tex")
    git(coauthor, "commit", "-q", "-m", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(paper / "paper.pdf")
    port = available_port()
    config_path = paper / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n"
                           f"overleaf: http://127.0.0.1:{overleaf_server.server_port}/project/{project}\n",
                           encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_overleaf_direct_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    shown = lambda selector: browser.execute_script(f'return !document.querySelector("{selector}").hidden')
    status = lambda: browser.execute_script('return document.querySelector("#overleaf-status").textContent')
    text = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").textContent')
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.navigate(base)
        wait_until(lambda: browser.execute_script('return document.querySelector("#comments-list") !== null'))
        browser.execute_script('document.querySelector("#settings-btn").click()')
        wait_until(lambda: shown("#overleaf-direct"))
        assert shown("#overleaf-cookie-row") and not shown("#overleaf-signed")
        # Comments are synced with the cookie, so the button waits for it.
        assert browser.execute_script('return document.querySelector("#overleaf-sync-comments-btn").disabled') is True
        assert browser.execute_script('return document.querySelector("#overleaf-project").textContent').endswith(project)
        # Named in the config, the project shows as connected.
        assert shown("#overleaf-connected") and not shown("#overleaf-link-row")
        # By hand is the way a paper follows Overleaf until live is chosen, and in a git clone it
        # says Pull and Push move the commits.
        assert shown("#overleaf-mode")
        assert browser.execute_script('return document.querySelector(\'#overleaf-mode input[value="hand"]\').checked') is True
        hand_note = browser.execute_script('return document.querySelector("#overleaf-mode-hand-note").textContent')
        assert "git pull or git push" in hand_note and "Comments tab" in hand_note
        # The note shows the sync button's drawing, the only thing that button shows, and the
        # leaf the tab it is in shows.
        assert browser.execute_script('return document.querySelectorAll("#overleaf-mode-hand-note svg").length') == 3

        field = browser.find_element("css selector", "#overleaf-cookie")
        field.send_keys(cookie)
        type_keys(browser, ["<Enter>"])
        wait_until(lambda: status() == "Cookie saved.")
        assert browser.execute_script('return document.querySelector("#settings-dialog").open') is True
        assert not shown("#overleaf-cookie-row") and shown("#overleaf-signed")
        assert browser.execute_script('return document.querySelector("#overleaf-cookie").value') == ""
        assert cookie not in browser.execute_script("return document.documentElement.outerHTML")
        kept = config_home / "tex-mcp-web" / "overleaf-cookie"
        assert kept.read_text(encoding="utf-8").strip() == f"overleaf_session2={cookie}"
        assert stat.S_IMODE(kept.stat().st_mode) == 0o600

        # Overleaf has a commit the folder lacks: Pull shows in the top bar with the count, and
        # comments wait for it.
        said = lambda: browser.execute_script('return document.querySelector("#overleaf-git-state").textContent')
        disabled = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").disabled')
        hidden = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").hidden')
        wait_until(lambda: said().startswith("Overleaf has 1 commit this folder lacks"))
        assert disabled("#overleaf-sync-comments-btn") and hidden("#git-push-btn") and not hidden("#git-pull-btn")
        assert text("#git-pull-btn") == "git pull1"
        browser.execute_script('document.querySelector("#git-pull-btn").click()')
        wait_until(lambda: said().startswith("This folder is at Overleaf's commit"))
        assert (paper / "notes.tex").exists() and status() == "git pull done."
        assert not disabled("#overleaf-sync-comments-btn") and hidden("#git-pull-btn") and hidden("#git-push-btn")

        # The sync button sits in the leaf Comments tab, with the threads it brings.
        assert hidden("#overleaf-sync-comments-btn")
        browser.execute_script('document.querySelector(\'.tab-btn[data-tab="overleaf-comments"]\').click()')
        wait_until(lambda: not hidden("#overleaf-sync-comments-btn"))
        browser.execute_script('document.querySelector("#overleaf-sync-comments-btn").click()')
        wait_until(lambda: status().startswith("Synced: 1 new comments"))
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("#comments-list").textContent.includes("Say hello properly.")'))

        # A commit made here: opening the dialog again finds it, Push shows in the top bar,
        # and it asks once before sending to everyone in the project.
        (paper / "paper.tex").write_text(PAPER + "% more\n", encoding="utf-8")
        git(paper, "commit", "-q", "-am", "here")
        browser.execute_script('document.querySelector("#settings-dialog").close()')
        browser.execute_script('document.querySelector("#settings-btn").click()')
        wait_until(lambda: said().startswith("This folder has 1 commit Overleaf lacks"))
        assert not hidden("#git-push-btn") and text("#git-push-btn") == "git push1"
        browser.execute_script("(window.wrappedJSObject || window).confirm = () => true;", sandbox=None)
        browser.execute_script('document.querySelector("#git-push-btn").click()')
        wait_until(lambda: said().startswith("This folder is at Overleaf's commit"))
        assert hidden("#git-push-btn")
        assert status() == "git push done."

        # Live may be chosen here too, asked once, and By hand chosen back: each is kept in
        # the config.
        choose = lambda mode: browser.execute_script(
            f'document.querySelector(\'#overleaf-mode input[value="{mode}"]\').click()')
        choose("live")
        wait_until(lambda: status() == "The files now follow Overleaf live.")
        assert "overleaf_sync: true" in config_path.read_text(encoding="utf-8")
        live_note = browser.execute_script('return document.querySelector("#overleaf-mode-live-note").textContent')
        assert "comes in by itself" in live_note and "goes with git push" in live_note
        choose("hand")
        wait_until(lambda: status() == "The files now follow Overleaf by hand.")
        assert "overleaf_sync" not in config_path.read_text(encoding="utf-8")
    finally:
        overleaf_server.shutdown()
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
def test_a_file_that_differs_from_overleaf_waits_for_a_choice_on_the_page(tmp_path: Path, monkeypatch) -> None:
    """With overleaf_sync, a file that differs from Overleaf's at the start is listed with
    a choice; choosing Overleaf's text writes it, and the panel says the file is in step."""
    import asyncio
    import threading
    from aiohttp.test_utils import TestServer
    from tests.test_overleaf_sync import BODY, MAIN, PROJECT_ID, FakeOverleaf
    from tex_mcp_web import overleaf

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    fake = FakeOverleaf()
    site = TestServer(fake.app, loop=loop)
    asyncio.run_coroutine_threadsafe(site.start_server(), loop).result(10)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv(overleaf.COOKIE_ENV, raising=False)
    overleaf.save_cookie(fake.cookie)
    (tmp_path / "tex").mkdir()
    (tmp_path / "main.tex").write_text(MAIN + "\n% only here", encoding="utf-8")
    (tmp_path / "tex" / "body.tex").write_text(BODY, encoding="utf-8")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: main.tex\nport: {port}\n"
                           f"overleaf: http://127.0.0.1:{site.port}/project/{PROJECT_ID}\noverleaf_sync: true\n",
                           encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_overleaf_sync_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    text = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").textContent')
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.navigate(base)
        wait_until(lambda: browser.execute_script(
            'return document.querySelector(\'#overleaf-sync-files [data-path="main.tex"]\') !== null'), timeout=20)
        assert "wait for your choice" in text("#overleaf-sync-head")
        # Files kept the same both ways bring new threads in by themselves: nothing to press.
        assert browser.execute_script('return document.querySelector("#overleaf-sync-comments-btn").hidden') is True
        assert browser.execute_script(
            'return document.querySelector("#overleaf-live-btn").classList.contains("trouble")') is True
        assert "1 waiting" in text("#overleaf-live-label")
        # The file that waits is said across the top of the page, with its choices, without
        # opening the Overleaf panel.
        assert browser.execute_script('return document.querySelector("#overleaf-sync-alert").hidden') is False
        assert browser.execute_script(
            'return document.querySelector("#overleaf-sync-alert").getBoundingClientRect().height') > 20
        assert "Not in step with Overleaf: main.tex" in text("#overleaf-sync-alert")
        assert [b for b in browser.execute_script(
            'return [...document.querySelectorAll("#overleaf-sync-alert button")].map((b) => b.textContent)')] == [
            "Open the file", "Use Overleaf's", "Send this file's"]
        assert (tmp_path / "main.tex").read_text(encoding="utf-8") == MAIN + "\n% only here"
        browser.execute_script("(window.wrappedJSObject || window).confirm = () => true;", sandbox=None)
        browser.execute_script("document.querySelector('#overleaf-sync-alert .overleaf-sync-overleaf').click()")
        wait_until(lambda: (tmp_path / "main.tex").read_text(encoding="utf-8") == MAIN, timeout=15)
        wait_until(lambda: "2 files kept in step with Overleaf" in text("#overleaf-sync-head"), timeout=15)
        assert browser.execute_script('return document.querySelector("#overleaf-sync-alert").hidden') is True
        assert "waiting" not in text("#overleaf-live-label")
        assert fake.docs["d-main"]["text"] == MAIN
        assert list((tmp_path / ".tex-mcp-web" / "overleaf-sync" / "kept").glob("main.tex.here.*"))
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
        asyncio.run_coroutine_threadsafe(site.close(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox is required")
def test_a_folder_without_git_gets_and_sends_by_hand_from_the_top_bar(tmp_path: Path, monkeypatch) -> None:
    """By hand in a folder that is no git clone, the top bar's two buttons are Get and Send,
    with the leaf, shown when there are edits to move, each counting the files it moves."""
    import asyncio
    import threading
    from aiohttp.test_utils import TestServer
    from tests.test_overleaf_sync import BODY, FIGURE, MAIN, PROJECT_ID, FakeOverleaf
    from tex_mcp_web import overleaf

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    fake = FakeOverleaf()
    site = TestServer(fake.app, loop=loop)
    asyncio.run_coroutine_threadsafe(site.start_server(), loop).result(10)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv(overleaf.COOKIE_ENV, raising=False)
    overleaf.save_cookie(fake.cookie)
    (tmp_path / "tex").mkdir()
    (tmp_path / "main.tex").write_text(MAIN, encoding="utf-8")
    (tmp_path / "tex" / "body.tex").write_text(BODY, encoding="utf-8")
    (tmp_path / "fig").mkdir()
    (tmp_path / "fig" / "plot.png").write_bytes(FIGURE)
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: main.tex\nport: {port}\n"
                           f"overleaf: http://127.0.0.1:{site.port}/project/{PROJECT_ID}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_overleaf_hand_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    text = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").textContent')
    hidden = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").hidden')
    click = lambda selector: browser.execute_script(f'document.querySelector("{selector}").click()')
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.navigate(base)
        wait_until(lambda: "hold the same files" in text("#overleaf-git-state"), timeout=20)
        assert hidden("#git-pull-btn") and hidden("#git-push-btn")
        click("#settings-btn")
        hand_note = text("#overleaf-mode-hand-note")
        assert "Get or" in hand_note and "Send, which show in the top bar" in hand_note
        assert browser.execute_script('return document.querySelectorAll("#overleaf-mode-hand-note svg").length') == 7
        browser.execute_script('document.querySelector("#settings-dialog").close()')

        asyncio.run_coroutine_threadsafe(fake.coauthor("d-body", [{"p": 0, "i": "New. "}]), loop).result(10)
        (tmp_path / "main.tex").write_text(MAIN.replace("fast", "quick"), encoding="utf-8")
        click("#settings-btn")
        wait_until(lambda: not hidden("#git-pull-btn") and not hidden("#git-push-btn"), timeout=15)
        browser.execute_script('document.querySelector("#settings-dialog").close()')
        assert text("#git-pull-btn") == "Get1" and text("#git-push-btn") == "Send1"
        assert not hidden("#git-pull-btn .by-hand-mark")
        # Comments wait until nothing is left to Get or Send, as at a git commit.
        browser.execute_script('document.querySelector(\'.tab-btn[data-tab="overleaf-comments"]\').click()')
        assert browser.execute_script('return document.querySelector("#overleaf-sync-comments-btn").disabled') is True
        click("#git-pull-btn")
        wait_until(lambda: (tmp_path / "tex" / "body.tex").read_text(encoding="utf-8") == "New. " + BODY, timeout=15)
        wait_until(lambda: hidden("#git-pull-btn"), timeout=15)
        assert fake.docs["d-main"]["text"] == MAIN
        browser.execute_script("(window.wrappedJSObject || window).confirm = () => true;", sandbox=None)
        click("#git-push-btn")
        wait_until(lambda: fake.docs["d-main"]["text"] == MAIN.replace("fast", "quick"), timeout=15)
        wait_until(lambda: hidden("#git-push-btn") and "hold the same files" in text("#overleaf-git-state"), timeout=15)
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("#overleaf-sync-comments-btn").disabled') is False, timeout=15)
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
        asyncio.run_coroutine_threadsafe(site.close(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox is required")
def test_the_page_shows_who_is_on_overleaf_where_their_cursor_is_and_what_they_changed(
        tmp_path: Path, monkeypatch) -> None:
    """With the project named and the cookie kept, an open page sits in the Overleaf project:
    a face per collaborator in the top bar, their file and line in its list, their cursor
    drawn in the editor on that file, and a note above a file they changed."""
    import asyncio
    import fitz
    import threading
    from aiohttp.test_utils import TestServer
    from tests.test_overleaf_live import FIGURE, PROJECT_ID, FakeRealtime, event
    from tex_mcp_web import overleaf

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    fake = FakeRealtime()
    realtime = TestServer(fake.app, loop=loop)
    asyncio.run_coroutine_threadsafe(realtime.start_server(), loop).result(10)
    push = lambda frame: asyncio.run_coroutine_threadsafe(fake.push(frame), loop).result(10)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.delenv(overleaf.COOKIE_ENV, raising=False)
    overleaf.save_cookie(fake.cookie)
    (tmp_path / "tex").mkdir()
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nHello world.\n\\input{tex/body}\n\\end{document}\n",
        encoding="utf-8")
    (tmp_path / "tex" / "body.tex").write_text("Body text.\nMore body text here.\n", encoding="utf-8")
    # Overleaf holds the same text and figure, so Get and Send have nothing to move.
    fake.texts = {"d-main": (tmp_path / "main.tex").read_text(encoding="utf-8"),
                  "d-body": (tmp_path / "tex" / "body.tex").read_text(encoding="utf-8")}
    (tmp_path / "a.png").write_bytes(FIGURE)
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "main.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: main.tex\nport: {port}\n"
                           f"overleaf: http://127.0.0.1:{realtime.port}/project/{PROJECT_ID}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_overleaf_live_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    text = lambda selector: browser.execute_script(f'return document.querySelector("{selector}").textContent')
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.navigate(base)
        # A face in the top bar, with no button around it; the dialog lists where each one is.
        wait_until(lambda: text("#overleaf-live-faces") == "CA")
        assert browser.execute_script('return document.querySelector("#overleaf-live-btn").hidden') is True
        assert browser.execute_script('return document.querySelector(".overleaf-face").title') == \
            "Co Author on Overleaf, at tex/body.tex:2"
        assert "Co Author" in text("#overleaf-live-users") and "tex/body.tex:2" in text("#overleaf-live-users")
        browser.execute_script('document.querySelector(".overleaf-face").click()')
        wait_until(lambda: browser.execute_script(
            'return document.querySelector(".overleaf-cursor")?.dataset.user || ""') == "Co Author")
        assert browser.execute_script("return document.querySelector('#source-file').value") == "tex/body.tex"
        caret_top = lambda: browser.execute_script("return parseFloat(document.querySelector('.overleaf-cursor').style.top)")
        first_top = caret_top()

        # The cursor moves to another file: gone from this one; back here a line lower, it follows.
        push(event("clientTracking.clientUpdated", {"id": "co-1", "user_id": "u-co", "name": "Co Author",
                                                    "doc_id": "d-main", "row": 2, "column": 0}))
        wait_until(lambda: browser.execute_script("return document.querySelector('.overleaf-cursor') === null"))
        push(event("clientTracking.clientUpdated", {"id": "co-1", "user_id": "u-co", "name": "Co Author",
                                                    "doc_id": "d-body", "row": 0, "column": 2}))
        wait_until(lambda: browser.execute_script("return document.querySelector('.overleaf-cursor') !== null"))
        assert caret_top() < first_top

        push(event("otUpdateApplied", {"doc": "d-body", "op": [{"p": 0, "i": "x"}], "v": 3,
                                       "meta": {"source": "co-1", "user_id": "u-co", "ts": 1}}))
        wait_until(lambda: not browser.execute_script('return document.querySelector("#overleaf-file-notice").hidden'))
        assert text("#overleaf-file-notice").startswith("Co Author edited this file on Overleaf")
        assert "tex/body.tex" in text("#overleaf-live-changes")

        # A comment written on Overleaf is counted on the sync button in the leaf Comments tab,
        # and comes in from it.
        browser.execute_script('document.querySelector(\'.tab-btn[data-tab="overleaf-comments"]\').click()')
        fake.threads["t1"] = {"messages": [{"user": {"first_name": "Ann"}, "content": "Came in when asked."}]}
        fake.ranges.append({"id": "c1", "op": {"c": "documentclass", "p": 1, "t": "t1"}})
        push(event("new-comment", "t1", {}))
        wait_until(lambda: browser.execute_script(
            'return document.querySelector("#overleaf-sync-comments-btn").title') == "Sync comments from Overleaf (1 new)", timeout=15)
        assert text("#overleaf-sync-comments-btn .count") == "1"
        assert not browser.execute_script('return document.body.textContent.includes("Came in when asked.")')
        # The button atop the comment list syncs them in place, with no dialog.
        click = lambda selector: browser.execute_script(f'document.querySelector("{selector}").click()')
        wait_until(lambda: not browser.execute_script('return document.querySelector("#overleaf-sync-comments-btn").disabled'))
        click("#overleaf-sync-comments-btn")
        wait_until(lambda: browser.execute_script(
            'return document.body.textContent.includes("Came in when asked.")'), timeout=15)
        assert browser.execute_script('return document.querySelector("#settings-dialog").open') is False
        assert text("#overleaf-sync-comments-btn .count") == ""

        # The agent's draft waits under the thread, even folded; a plain reply stays here.
        from tex_mcp_web.comments import CommentStore, OverleafDraft

        store = CommentStore(tmp_path / ".tex-mcp-web" / "comments.json")
        brought = store.list()[0]
        store.draft_overleaf(brought.id, brought.updated, OverleafDraft("Drafted for Ann.", [], []))
        wait_until(lambda: browser.execute_script(
            'const box = document.querySelector(".overleaf-reply-input"); return box ? box.value : ""')
            == "Drafted for Ann.", timeout=15)
        click("[data-comment-id] .cmt-head")
        write = lambda selector, words: browser.execute_script(
            f'const box = document.querySelector("{selector}"); box.value = arguments[0];'
            'box.dispatchEvent(new Event("input", {bubbles: true}));', [words])
        wait_until(lambda: browser.execute_script('return document.querySelector(".cmt-reply") !== null'))
        click(".cmt-reply")
        wait_until(lambda: browser.execute_script('return document.querySelector(".cmt-form-input:not(.overleaf-reply-input)") !== null'))
        write(".cmt-form-input:not(.overleaf-reply-input)", "Stays here.")
        click(".cmt-form-submit")
        wait_until(lambda: browser.execute_script('return document.body.textContent.includes("Stays here.")'), timeout=15)
        assert fake.written == []
        assert "here only" in text("[data-comment-id] .cmt-thread")
        # The reviewer edits the draft and sends it, to everyone in the project.
        write(".overleaf-reply-input", "Drafted for Ann, and checked.")
        click(".overleaf-send")
        wait_until(lambda: fake.written == [("reply", "t1", "Drafted for Ann, and checked.")], timeout=15)
        # Answered, it leaves the threads that wait for the reviewer, the view the tab opened
        # on, and stays among the open ones.
        wait_until(lambda: not browser.execute_script(
            'return document.querySelector("#comments-list").textContent.includes("Came in when asked.")'), timeout=15)
        browser.execute_script('const f = document.querySelector("#comment-filter"); f.value = "open";'
                               ' f.dispatchEvent(new Event("change"));')
        wait_until(lambda: "sent" in text("[data-comment-id] .cmt-thread .entry-overleaf.sent"), timeout=15)
        assert browser.execute_script('return document.querySelector(".overleaf-reply-input").value') == ""

        # Picked threads send their replies together. One whose edit Overleaf does not hold is
        # refused on its card and stays picked, while the other goes.
        fake.threads["t2"] = {"messages": [{"user": {"first_name": "Ann"}, "content": "A second one."}]}
        fake.ranges.append({"id": "c2", "op": {"c": "Hello", "p": (tmp_path / "main.tex").read_text().index("Hello"),
                                               "t": "t2"}})
        push(event("new-comment", "t2", {}))
        wait_until(lambda: text("#overleaf-sync-comments-btn .count") == "1", timeout=15)
        wait_until(lambda: not browser.execute_script('return document.querySelector("#overleaf-sync-comments-btn").disabled'))
        click("#overleaf-sync-comments-btn")
        wait_until(lambda: browser.execute_script(
            'return document.body.textContent.includes("A second one.")'), timeout=15)
        second = next(c for c in store.list() if "A second one." in c.thread[0].text)
        store.draft_overleaf(brought.id, store.get(brought.id).updated, OverleafDraft("Both go, the first.", [], []))
        store.draft_overleaf(second.id, second.updated, OverleafDraft(
            "Both go, the second.", ["main.tex:3-3"], [{"file": "main.tex", "text": "Hello world, reworded."}]))
        wait_until(lambda: browser.execute_script(
            'return [...document.querySelectorAll(".overleaf-reply-input")].map((box) => box.value).sort().join("|")')
            == "Both go, the first.|Both go, the second.", timeout=15)
        assert browser.execute_script('return document.querySelector("#send-picked-btn").disabled') is True
        for cid in (brought.id, second.id):
            click(f"[data-comment-id='{cid}'] .comment-pick")
        assert text("#send-picked-btn .count") == "2"
        browser.execute_script("(window.wrappedJSObject || window).confirm = () => true;", sandbox=None)
        click("#send-picked-btn")
        wait_until(lambda: browser.execute_script(
            f'return document.querySelector("[data-comment-id=\'{second.id}\'] .overleaf-reply-note")?.textContent || ""')
            .startswith("The edit to main.tex this reply describes is not on Overleaf"), timeout=15)
        assert fake.written[1:] == [("reply", "t1", "Both go, the first.")]
        assert text("#send-picked-btn .count") == "1"
        assert browser.execute_script(
            f'return document.querySelector("[data-comment-id=\'{second.id}\'] .overleaf-reply-input").value') \
            == "Both go, the second."

        # Resolve on a thread's card resolves it on Overleaf, for everyone, and then here.
        # Reopen brings it back the same way. Overleaf has no archive, so neither does the tab.
        assert text("#overleaf-tab-count") == "2"
        assert browser.execute_script('return document.querySelector("#archive-picked-btn").hidden') is True
        # Its card is still open from the reply written on it.
        wait_until(lambda: browser.execute_script(
            f'return document.querySelector("[data-comment-id=\'{brought.id}\'] .cmt-resolve") !== null'))
        assert browser.execute_script(
            f'return document.querySelector("[data-comment-id=\'{brought.id}\'] .cmt-archive")') is None
        click(f"[data-comment-id='{brought.id}'] .cmt-resolve")
        wait_until(lambda: fake.written[-1] == ("resolve", "t1", "d-main"), timeout=15)
        wait_until(lambda: store.get(brought.id).status == "resolved")
        wait_until(lambda: text("#overleaf-tab-count") == "1")
        browser.execute_script('const f = document.querySelector("#comment-filter"); f.value = "resolved";'
                               ' f.dispatchEvent(new Event("change"));')
        wait_until(lambda: browser.execute_script(
            f'return document.querySelector("[data-comment-id=\'{brought.id}\'] .cmt-reopen") !== null'), timeout=15)
        click(f"[data-comment-id='{brought.id}'] .cmt-reopen")
        wait_until(lambda: fake.written[-1] == ("reopen", "t1", "d-main"), timeout=15)
        wait_until(lambda: store.get(brought.id).status == "open")

        # When comments cannot be read, the page says so.
        fake.cookie = "s%3Arotated"
        push(event("new-comment", "t1", {}))
        wait_until(lambda: "not coming in" in text("#overleaf-live-status"), timeout=15)
        assert browser.execute_script(
            'return document.querySelector("#overleaf-live-btn").classList.contains("trouble")') is True
        assert browser.execute_script('return document.querySelector("#overleaf-live-btn").hidden') is False
        assert text("#overleaf-live-label") == "Comments not coming in"
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
        asyncio.run_coroutine_threadsafe(realtime.close(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)


@pytest.mark.skipif(shutil.which("firefox") is None, reason="Firefox is required")
def test_go_to_definition_follows_input_ref_and_cite(tmp_path: Path) -> None:
    """gd with Vim keys, or F12, goes from \\input to the file, from \\ref to the \\label it
    names, and from \\cite to the .bib entry, the name under the cursor in a list; Ctrl+T or
    Ctrl+O, or Shift+F12, goes back to where the jump started."""
    import fitz

    (tmp_path / "sec").mkdir()
    (tmp_path / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n\\input{sec/a}\n"
        "See \\cref{sec:other,sec:a} and \\cite{k0,k1}.\n\\bibliography{refs}\n\\end{document}\n",
        encoding="utf-8")
    (tmp_path / "sec" / "a.tex").write_text("\\section{A}\n\n\\label{sec:a}\nText.\n", encoding="utf-8")
    (tmp_path / "refs.bib").write_text("@book{k0,\n title={Zero}}\n\n@article{k1,\n title={One}}\n", encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_definition_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.navigate(base)
        browser.execute_script('document.querySelector("[data-view=source]").click()')
        editor = '(window.wrappedJSObject || window).ace.edit("source-editor")'
        wait_until(lambda: browser.execute_script(f"return {editor}.getValue().includes('\\\\input');"))
        where = f'return [document.querySelector("#source-file").value, {editor}.getCursorPosition().row + 1];'

        def go(file, row, column):
            browser.execute_script(f'''
              const page = window.wrappedJSObject || window;
              page.document.querySelector("#source-file").value = arguments[0];
              page.document.querySelector("#source-file").dispatchEvent(new Event("change"));''', script_args=[file])
            # The menu shows the file at once, and the editor gets its text when the fetch
            # returns, which puts the cursor at the top: a cursor placed before that is lost.
            text = (tmp_path / file).read_text(encoding="utf-8")
            wait_until(lambda: browser.execute_script(f"return {editor}.getValue();") == text)
            browser.execute_script(f"{editor}.focus(); {editor}.gotoLine({row}, {column});")
            ActionSequence(browser, "key", "keyboard").key_down("\ue03c").key_up("\ue03c").perform()

        go("paper.tex", 3, 3)                       # on \input{sec/a}
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["sec/a.tex", 1] and now)
        go("paper.tex", 4, 26)                      # on sec:a in \cref{sec:other,sec:a}
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["sec/a.tex", 3] and now)
        go("paper.tex", 4, 40)                      # on k1 in \cite{k0,k1}
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["refs.bib", 4] and now)
        # Shift+F12 goes back along the jumps, to where each one started.
        ActionSequence(browser, "key", "keyboard").key_down("\ue008").key_down("\ue03c") \
            .key_up("\ue03c").key_up("\ue008").perform()
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["paper.tex", 4] and now)
        assert browser.execute_script(f"return {editor}.getCursorPosition().column;") == 40
        # With Vim keys, gd goes and Ctrl+T comes back.
        browser.execute_script('document.querySelector("#source-vim-btn").click()')
        wait_until(lambda: browser.execute_script(f"return Boolean({editor}.$vimModeHandler);"))
        browser.execute_script(f"{editor}.focus(); {editor}.gotoLine(3, 3);")
        ActionSequence(browser, "key", "keyboard").key_down("g").key_up("g").key_down("d").key_up("d").perform()
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["sec/a.tex", 1] and now)
        ActionSequence(browser, "key", "keyboard").key_down("\ue009").key_down("t") \
            .key_up("t").key_up("\ue009").perform()
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["paper.tex", 3] and now)
        # Ctrl+O comes back as well, since Windows browsers keep Ctrl+T for a new tab.
        ActionSequence(browser, "key", "keyboard").key_down("g").key_up("g").key_down("d").key_up("d").perform()
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["sec/a.tex", 1] and now)
        ActionSequence(browser, "key", "keyboard").key_down("\ue009").key_down("o") \
            .key_up("o").key_up("\ue009").perform()
        assert wait_until(lambda: (now := browser.execute_script(where)) == ["paper.tex", 3] and now)
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
def test_translation_numbers_its_source_lines_and_follows_the_editor(tmp_path: Path, monkeypatch) -> None:
    """Each translated paragraph shows its source lines, the paragraph under the editor's
    cursor is marked and brought into view, and the type size steps and is kept."""
    import fitz

    def translated(prompt, cwd, system):
        blocks = json.loads(prompt.split("Paragraphs (JSON):\n", 1)[1])
        return json.dumps({"t": {pid: "T " + text for pid, text in blocks.items()}})

    monkeypatch.setattr("tex_mcp_web.translate.run_claude", translated)
    paragraphs = "\n\n".join(f"Paragraph number {n} has words\nacross two lines." for n in range(40))
    (tmp_path / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n\n" + paragraphs + "\n\n\\end{document}\n", encoding="utf-8")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(
        f"main: paper.tex\nport: {port}\ntranslation:\n  language: Korean\n", encoding="utf-8")

    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_translation_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1500, height=1000)
        browser.navigate(base)

        wait_until(lambda: not browser.execute_script(
            'return document.querySelector("#translation-btn").classList.contains("hidden");'))
        browser.execute_script("""
          document.querySelector("#translation-btn").click();
          document.querySelector(".view-tab[data-view=split]").click();
        """)
        wait_until(lambda: browser.execute_script("""
          const items = document.querySelectorAll(".translation-para");
          return items.length === 40 && !items[39].classList.contains("pending");
        """))

        labels = browser.execute_script("""
          return [...document.querySelectorAll(".translation-line")].slice(0, 2).map((x) => x.textContent);
        """)
        assert labels == ["4\u20135", "7\u20138"]
        assert browser.execute_script(
            'return document.querySelector(".translation-text").textContent;'
        ) == "T Paragraph number 0 has words\nacross two lines."

        def marked():
            return browser.execute_script("""
              const item = document.querySelector(".translation-para.at-cursor");
              if (!item) return null;
              const body = document.querySelector("#translation-body").getBoundingClientRect();
              const box = item.getBoundingClientRect();
              return [item.querySelector(".translation-line").textContent,
                      box.top >= body.top && box.bottom <= body.bottom];
            """)

        def go_to(line):
            browser.execute_script(
                f'(window.wrappedJSObject || window).ace.edit("source-editor").gotoLine({line}, 0);')

        wait_until(lambda: browser.execute_script(
            'return Boolean((window.wrappedJSObject || window).ace)'))
        go_to(119)                                                     # far down the file
        wait_until(lambda: marked() == ["118\u2013119", True])
        go_to(5)                                                       # back up
        wait_until(lambda: marked() == ["4\u20135", True])
        go_to(6)                                                       # the blank line between
        wait_until(lambda: marked() is None)

        def size():
            return browser.execute_script(
                'return parseFloat(getComputedStyle(document.querySelector(".translation-para")).fontSize);')

        before = size()
        browser.execute_script("""
          const larger = document.querySelector("#translation-font-larger-btn");
          larger.click(); larger.click();
        """)
        assert size() == before + 2
        browser.execute_script('document.querySelector("#translation-font-smaller-btn").click();')
        assert size() == before + 1
        browser.refresh()
        wait_until(lambda: browser.execute_script(
            'return document.querySelectorAll(".translation-para").length === 40;'))
        assert size() == before + 1
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
def test_the_editor_completes_labels_citations_environments_files_and_commands(tmp_path: Path) -> None:
    """Typing in the editor opens what fits there: labels after \\ref, bib entries after
    \\cite (a title's words find them), environments after \\begin and \\end, files after
    \\input and \\includegraphics, commands after a backslash, the paper's own among them.
    Enter takes the choice, but after a command typed whole it starts the next line."""
    import fitz

    (tmp_path / "paper.tex").write_text(
        "\\documentclass{article}\n\\newcommand{\\cpp}[1]{#1 CPP}\n\\begin{document}\n"
        "\\input{sec/a}\n\n\\end{document}\n", encoding="utf-8")
    (tmp_path / "sec").mkdir()
    (tmp_path / "sec" / "a.tex").write_text("\\section{A}\\label{sec:a}\nWords.\\label{fig:flow}\n", encoding="utf-8")
    (tmp_path / "refs.bib").write_text(
        "@article{kim23, title = {Fusion Cells for Placement}, author = {Kim, A. and Lee, B.}, year = 2023}\n"
        "@article{zed19, title = {Another Study}, author = {Zed, C.}, year = 2019}\n", encoding="utf-8")
    (tmp_path / "fig").mkdir()
    (tmp_path / "fig" / "flow.pdf").write_bytes(b"%PDF-1.4")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Hello world.")
        pdf.save(tmp_path / "paper.pdf")
    port = available_port()
    config_path = tmp_path / ".tex-mcp-web.yaml"
    config_path.write_text(f"main: paper.tex\nport: {port}\n", encoding="utf-8")
    shared = SharedProjectServer(load_config(config_path))
    profile = tempfile.mkdtemp(prefix="tex_mcp_complete_")
    marionette_port = available_port()
    (Path(profile) / "user.js").write_text(
        f'user_pref("marionette.port", {marionette_port});\n', encoding="utf-8")
    browser_process = None
    browser = None
    try:
        shared.ensure()
        base = f"http://127.0.0.1:{port}"
        wait_until(lambda: get_json(f"{base}/paper") is not None)
        browser_process = subprocess.Popen(
            ["firefox", "-marionette", "-headless", "-no-remote", "-profile", profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = marionette.Marionette(host="127.0.0.1", port=marionette_port, startup_timeout=30)
        browser.start_session()
        browser.set_window_rect(x=0, y=0, width=1400, height=900)
        browser.navigate(base)
        browser.execute_script('document.querySelector(".view-tab[data-view=source]").click();')
        editor = '(window.wrappedJSObject || window).ace.edit("source-editor")'
        wait_until(lambda: browser.execute_script(
            f'return Boolean((window.wrappedJSObject || window).ace) && {editor}.getValue().includes("input{{sec/a}}")'))

        def type_at_end_of(row, text):
            browser.execute_script(f"""
              const editor = {editor};
              editor.focus();
              editor.navigateTo({row}, editor.session.getLine({row}).length);
              for (const ch of arguments[0]) editor.execCommand("insertstring", ch);
            """, script_args=[text])

        def type_here(text):
            browser.execute_script(f"""
              const editor = {editor};
              for (const ch of arguments[0]) editor.execCommand("insertstring", ch);
            """, script_args=[text])

        def shown():
            return browser.execute_script(f"""
              const completer = {editor}.completer;
              if (!completer || !completer.popup || !completer.popup.isOpen) return null;
              return completer.completions.filtered.map((item) => item.caption);
            """)

        def line(row):
            return browser.execute_script(f"return {editor}.session.getLine({row});")

        def enter():
            ActionSequence(browser, "key", "keyboard").key_down("\ue007").key_up("\ue007").perform()

        def new_line_after(row):
            browser.execute_script(f"""
              const editor = {editor};
              editor.navigateTo({row}, editor.session.getLine({row}).length);
              editor.insert("\\n");
            """)
            return row + 1

        row = new_line_after(3)                                         # under \\input{sec/a}
        type_at_end_of(row, "Here \\label{sec:typed} see \\ref{")
        listed = wait_until(shown)
        assert {"sec:a", "fig:flow", "sec:typed"} <= set(listed)
        type_here("flo")
        assert wait_until(lambda: (now := shown()) and now[0] == "fig:flow" and now)
        enter()
        assert wait_until(lambda: line(row).endswith("\\ref{fig:flow}") and line(row))

        row = new_line_after(row)
        type_at_end_of(row, "\\cite{Placement")
        assert wait_until(lambda: (now := shown()) and now[0].startswith("kim23") and now)
        enter()
        assert wait_until(lambda: line(row) == "\\cite{kim23}" and line(row))

        row = new_line_after(row)
        type_at_end_of(row, "\\begin{itemi")
        assert wait_until(lambda: (now := shown()) and now[0] == "itemize" and now)
        enter()
        wait_until(lambda: line(row + 2) == "\\end{itemize}")
        assert line(row) == "\\begin{itemize}"
        assert browser.execute_script(f"return {editor}.getCursorPosition().row;") == row + 1

        type_at_end_of(row + 1, "\\item")
        enter()                                                         # a command typed whole
        wait_until(lambda: browser.execute_script(f"return {editor}.getCursorPosition().row;") == row + 2)
        assert line(row + 1).strip() == "\\item"
        type_at_end_of(row + 2, "\\end{")
        assert wait_until(lambda: (now := shown()) and now[0] == "itemize" and now)
        browser.execute_script(f"{editor}.completer.detach();")

        row = new_line_after(row + 4)
        type_at_end_of(row, "\\textb")
        assert wait_until(lambda: (now := shown()) and now[0] == "\\textbf" and now)
        enter()
        assert wait_until(lambda: line(row) == "\\textbf{}" and line(row))
        assert browser.execute_script(f"return {editor}.getCursorPosition().column;") == len("\\textbf{")

        row = new_line_after(row)
        type_at_end_of(row, "\\cp")
        assert wait_until(lambda: (now := shown()) and now[0] == "\\cpp" and now)
        enter()
        assert wait_until(lambda: line(row) == "\\cpp{}" and line(row))

        row = new_line_after(row)
        type_at_end_of(row, "\\includegraphics{")
        assert wait_until(lambda: (now := shown()) and now == ["fig/flow.pdf"] and now)
        enter()
        assert wait_until(lambda: line(row) == "\\includegraphics{fig/flow.pdf}" and line(row))

        row = new_line_after(row)
        type_at_end_of(row, "\\input{")
        assert wait_until(lambda: (now := shown()) and set(now) == {"paper", "sec/a"} and now)
        browser.execute_script(f"{editor}.completer.detach();")

        row = new_line_after(row)
        type_at_end_of(row, "Plain words, more")                       # prose opens nothing
        time.sleep(0.5)
        assert shown() is None
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

