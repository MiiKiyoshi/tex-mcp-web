"""A review tool's page in Google Chrome, or in Safari on macOS, for the tool's tests.

Chrome is driven through Selenium, which finds the driver that fits the installed Chrome by
itself. ``launch()`` gives a headless window. ``REVIEW_BROWSER=headed`` shows it, to watch
a test run. ``REVIEW_BROWSER_KIND=safari`` runs the same tests in Safari through
safaridriver, which must be enabled once (``safaridriver --enable``). Safari has no headless
window and runs one automation session at a time, so its window shows and the tests take it
in turn, with ``pytest -n 0``. What Safari cannot be made to do, a test marks with
``not_in_safari``, and a method Safari cannot carry out skips the test that calls it.

Shared by the review tools' tests. It is written in tex-mcp-web, and scripts/share_page.py
copies it to the others. A copy is never edited by hand. What one tool's tests alone use is kept
in that tool's tests. A tool whose tests start another Chromium browser, or Chrome with flags or
preferences of their own, gives them in a subclass of Chrome through options() and start().
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import psutil
import pytest

selenium = pytest.importorskip("selenium")
from selenium import webdriver  # noqa: E402
from selenium.common.exceptions import NoAlertPresentException, SessionNotCreatedException  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

KIND = os.environ.get("REVIEW_BROWSER_KIND", "chrome")
assert KIND in ("chrome", "safari"), f"REVIEW_BROWSER_KIND is chrome or safari, not {KIND}"

# Where Chrome is on each system, when it is not on the PATH.
_INSTALLED = {
    "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "win32": [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
}


def binary() -> str | None:
    """The installed Chrome, or None."""
    for name in ("google-chrome", "google-chrome-stable", "chrome"):
        if (found := shutil.which(name)) is not None:
            return found
    return next((path for path in _INSTALLED.get(sys.platform, []) if Path(path).is_file()), None)


if KIND == "chrome":
    needs_browser = pytest.mark.skipif(binary() is None, reason="Google Chrome is required")
else:
    needs_browser = pytest.mark.skipif(shutil.which("safaridriver") is None, reason="Safari on macOS is required")


def not_in_safari(reason: str):
    """Skip a test in Safari, which cannot do what the reason says."""
    return pytest.mark.skipif(KIND == "safari", reason=f"Safari: {reason}")

# The keys a test presses, by the names it writes them with.
ENTER, CONTROL, SHIFT, ALT, META, ESCAPE = "\ue007", "\ue009", "\ue008", "\ue00a", "\ue03d", "\ue00c"
# The key the page's own shortcuts are held with: Cmd on a Mac, where Option stands for Alt, and
# Ctrl elsewhere.
MOD = META if sys.platform == "darwin" else CONTROL
# The names the page gives those keys, in its tooltips and in Help.
MOD_ALT = "Cmd+Option" if sys.platform == "darwin" else "Ctrl+Alt"


class Actions:
    """One input device's actions, built up and then performed: a keyboard's keys, or a
    mouse's or a finger's moves and presses, at the page's own coordinates."""

    def __init__(self, browser: "Chrome", kind: str, name: str, parameters: dict | None = None):
        self.browser, self.steps = browser, []
        self.source = {"type": kind, "id": name, **({"parameters": parameters} if parameters else {})}

    def key_down(self, key: str) -> "Actions":
        self.steps.append({"type": "keyDown", "value": key})
        return self

    def key_up(self, key: str) -> "Actions":
        self.steps.append({"type": "keyUp", "value": key})
        return self

    def pointer_move(self, x: float, y: float, duration: int = 0) -> "Actions":
        self.steps.append({"type": "pointerMove", "x": int(x), "y": int(y), "duration": duration, "origin": "viewport"})
        return self

    def pointer_down(self, button: int = 0) -> "Actions":
        self.steps.append({"type": "pointerDown", "button": button})
        return self

    def pointer_up(self, button: int = 0) -> "Actions":
        self.steps.append({"type": "pointerUp", "button": button})
        return self

    def pause(self, duration: int) -> "Actions":
        self.steps.append({"type": "pause", "duration": duration})
        return self

    def perform(self) -> None:
        self.browser.perform([{**self.source, "actions": self.steps}])
        self.steps = []


class Chrome:
    """A Chrome window on a tool's page."""

    def __init__(self, downloads: Path | None = None, touch: bool = False):
        options = self.options()
        environment = dict(os.environ)
        if os.environ.get("REVIEW_BROWSER") != "headed":
            options.add_argument("--headless=new")
            # A headless Chrome still connects to the display it is given. Over ssh with X
            # forwarding that display is the far machine's, which prints a refusal for each try.
            for name in ("DISPLAY", "WAYLAND_DISPLAY"):
                environment.pop(name, None)
        # A container has no user namespace for Chrome's sandbox, and its /dev/shm is small.
        for flag in ("--no-sandbox", "--disable-dev-shm-usage", "--window-size=1500,1000",
                     "--force-device-scale-factor=1", "--hide-scrollbars"):
            options.add_argument(flag)
        options.set_capability("unhandledPromptBehavior", "ignore")
        # The page's console, kept for what a failing test leaves behind (keep_failure).
        options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
        if downloads is not None:
            options.add_experimental_option("prefs", {
                **options.experimental_options.get("prefs", {}),
                "download.default_directory": str(downloads), "download.prompt_for_download": False,
                "plugins.always_open_pdf_externally": True})
        self.driver = self.start(options, environment)
        self.driver.set_script_timeout(60)
        # The tests are written for a light system. A machine set to dark, whose setting
        # reaches headless Chrome, would otherwise turn System to dark.
        self.prefer_scheme(None)
        if touch:
            # A finger is a coarse pointer, which the page's touch rules key off.
            self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 2})

    def options(self) -> webdriver.ChromeOptions:
        """The options the browser starts with, before the flags every test gives it: Chrome's,
        with where it is installed."""
        options = webdriver.ChromeOptions()
        options.binary_location = binary()
        return options

    def start(self, options: webdriver.ChromeOptions, environment: dict[str, str]):
        """The driver of the browser, started with *options* in *environment*."""
        return webdriver.Chrome(options=options, service=webdriver.ChromeService(env=environment))

    def quit(self) -> None:
        # A test quits its browser in a finally clause, so a failing one quits with its failure
        # still on its way out: what the page showed then is kept in the folder
        # REVIEW_FAILURES names, which the CI keeps, a failure that passes when run again too.
        failure = sys.exc_info()[1]
        if failure is not None and not isinstance(failure, pytest.skip.Exception) and os.environ.get("REVIEW_FAILURES"):
            self.keep_failure(Path(os.environ["REVIEW_FAILURES"]))
        self.end()

    def end(self) -> None:
        """Close Chrome as its own Close does, then end its driver. Chrome then removes the
        folder it made in the temporary folder, which it leaves when its driver ends it. It has
        exited once it waits for its driver to collect it."""
        try:
            browsers = psutil.Process(self.driver.service.process.pid).children()
            self.driver.execute_cdp_cmd("Browser.close", {})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and any(
                    browser.is_running() and browser.status() != psutil.STATUS_ZOMBIE for browser in browsers):
                time.sleep(0.05)
        except Exception:
            pass
        try:
            self.driver.quit()
        except Exception:
            pass

    def keep_failure(self, folder: Path) -> None:
        """The page as the test left it, under the test's name: a screenshot, the page's markup,
        the values of its fields and where the focus is, its console where the browser gives
        it, and the words of a dialog it left open, which is closed first: Safari answers no
        later command while one is open, and the screenshot waited until the test timed out.
        The record says too at which lines of the tests it failed, which a log leaves out once
        the test passes when run again. Each part that cannot be had is said in the record
        instead."""
        test = os.environ.get("PYTEST_CURRENT_TEST", "test").split(" ")[0]
        name = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', test)[-120:]}-{time.time_ns()}"
        folder.mkdir(parents=True, exist_ok=True)
        _, failure, trace = sys.exc_info()
        record: dict[str, object] = {
            "test": test, "error": repr(failure)[:2000],
            "where": [f"{Path(frame.filename).name}:{frame.lineno}" for frame in traceback.extract_tb(trace)
                      if Path(frame.filename).parent == Path(__file__).parent],
        }
        try:
            dialog = self.driver.switch_to.alert
            record["dialog"] = dialog.text
            dialog.dismiss()
        except NoAlertPresentException:
            pass
        except Exception as error:
            record["dialog"] = repr(error)
        try:
            (folder / f"{name}.png").write_bytes(base64.b64decode(self.driver.get_screenshot_as_base64()))
        except Exception as error:
            record["screenshot"] = repr(error)
        try:
            (folder / f"{name}.html").write_text(
                self.driver.execute_script("return document.documentElement.outerHTML"), encoding="utf-8")
            record["page"] = self.driver.execute_script("""
              const named = (node) => node.id ? `#${node.id}` : `${node.tagName.toLowerCase()}.${[...node.classList].join(".")}`;
              return {url: location.href, focus: document.activeElement ? named(document.activeElement) : null,
                      fields: [...document.querySelectorAll("textarea, input, select")].filter((field) => field.value)
                        .map((field) => [named(field), field.closest("[data-comment-id]")?.dataset.commentId ?? null, field.value])};""")
        except Exception as error:
            record["page"] = repr(error)
        try:
            record["console"] = [f"{entry['level']} {entry['message']}" for entry in self.driver.get_log("browser")][-200:]
        except Exception as error:
            record["console"] = repr(error)
        (folder / f"{name}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")

    def navigate(self, url: str) -> None:
        self.driver.get(url)

    def refresh(self) -> None:
        self.driver.refresh()

    def execute_script(self, script: str, script_args=(), **_):
        """Run the script in the page, its arguments as ``arguments``, and give what it
        returns: a promise's value once it settles."""
        return self.driver.execute_script(script, *script_args)

    def set_window_rect(self, *, x: int = 0, y: int = 0, width: int = 1500, height: int = 1000) -> None:
        """Size the page's viewport, which is what a test measures against."""
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})

    def find_element(self, strategy: str, selector: str):
        assert strategy == "css selector"
        return self.driver.find_element(By.CSS_SELECTOR, selector)

    def console_errors(self) -> list[str]:
        """The errors the page's console took since this was last asked, as Chrome logs them:
        a script's error, console.error, and a request that failed."""
        return [entry["message"] for entry in self.driver.get_log("browser") if entry["level"] == "SEVERE"]

    def assert_quiet_console(self) -> None:
        """Fail on an error the page's console took since it was last asked: the check a test
        ends with, which Safari, giving no console, leaves out while the test's other checks
        stand."""
        assert self.console_errors() == []

    def prefer_scheme(self, scheme: str | None) -> None:
        """Have the system say it prefers dark or light, or with None light, the scheme the
        tests take a system to have whatever this machine is set to."""
        self.driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": scheme or "light"}]})

    def user_agent(self, text: str) -> None:
        """Have the browser say it is the browser *text* names, as navigator.userAgent gives
        it, for the pages loaded from then on."""
        self.driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {"userAgent": text})

    def platform(self, name: str) -> None:
        """Have the browser say it runs on the system *name*, as navigator.platform gives it,
        for the pages loaded from then on."""
        self.driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {
            "userAgent": self.driver.execute_script("return navigator.userAgent"), "platform": name})

    def actions(self, kind: str, name: str, parameters: dict | None = None) -> Actions:
        return Actions(self, kind, name, parameters)

    def perform(self, sources: list[dict]) -> None:
        """Perform the input sources' actions together, as two fingers move at once."""
        self.driver.execute("actions", {"actions": sources})


def osascript(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


class SafariElement:
    """An element in Safari, clicked as a pointer does it. The driver's own click, and a
    quick pointer press, sometimes reach the page as a release before the press when they
    closely follow another click, and the page then sees no click at all. A pause before
    the press and before the release keeps them in order."""

    def __init__(self, browser: "Safari", element):
        self.browser, self.element = browser, element

    def __getattr__(self, name: str):
        return getattr(self.element, name)

    def click(self) -> None:
        box = self.browser.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'nearest', inline: 'nearest'});"
            " const box = arguments[0].getBoundingClientRect();"
            " return [box.left + box.width / 2, box.top + box.height / 2];", self.element)
        self.browser.actions("pointer", "mouse", {"pointerType": "mouse"}) \
            .pointer_move(box[0], box[1]).pause(50).pointer_down().pause(50).pointer_up().perform()


class Safari(Chrome):
    """A Safari window on a tool's page. It shows on the screen, since Safari has no
    headless window, and its viewport is sized by sizing the window around it."""

    def __init__(self, downloads: Path | None = None, touch: bool = False):
        assert downloads is None and not touch, "a test that needs these is marked not_in_safari"
        options = webdriver.SafariOptions()
        options.set_capability("unhandledPromptBehavior", "ignore")
        # One session at a time: the one before may still be closing. One that never closes,
        # left by a test that hung, would refuse every test after it, so Safari and its driver
        # are then ended and started afresh.
        deadline, ended = time.monotonic() + 30, False
        while True:
            try:
                self.driver = webdriver.Safari(options=options)
                break
            except SessionNotCreatedException:
                if time.monotonic() > deadline:
                    if ended:
                        raise
                    for program in ("Safari", "safaridriver"):
                        subprocess.run(["pkill", "-x", program], stdin=subprocess.DEVNULL, check=False)
                    deadline, ended = time.monotonic() + 30, True
                time.sleep(1)
        self.driver.set_script_timeout(60)
        # Safari may open the window in full screen, which no window size changes, and
        # behind another program's window, where no pointer or key action reaches its page.
        window = 'tell application "System Events" to tell process "Safari" to '
        osascript(window + "set frontmost to true")
        if osascript(window + 'get value of attribute "AXFullScreen" of every window') == "true":
            osascript(window + 'set value of attribute "AXFullScreen" of window 1 to false')
            time.sleep(2)
        self.set_window_rect()
        self.system_dark = None
        self.prefer_scheme(None)

    def quit(self) -> None:
        if self.system_dark is not None:
            self.dark(self.system_dark)
        super().quit()

    def end(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def _loaded(self) -> None:
        """Wait for the page's scripts to have run: Safari's driver gives a page back while
        it is still loading, before the page has wired up what a test presses next."""
        deadline = time.monotonic() + 30
        while self.driver.execute_script("return document.readyState") != "complete" and time.monotonic() < deadline:
            time.sleep(0.05)

    def navigate(self, url: str) -> None:
        super().navigate(url)
        self._loaded()

    def refresh(self) -> None:
        super().refresh()
        self._loaded()

    def set_window_rect(self, *, x: int = 0, y: int = 0, width: int = 1500, height: int = 1000) -> None:
        """Size the window so that the viewport inside it has the size asked for."""
        self.driver.set_window_rect(x=x, y=y, width=width, height=height)
        for _ in range(3):
            inner = self.driver.execute_script("return [innerWidth, innerHeight]")
            if inner == [width, height]:
                return
            rect = self.driver.get_window_rect()
            self.driver.set_window_rect(x=x, y=y, width=rect["width"] + width - inner[0],
                                        height=rect["height"] + height - inner[1])

    def console_errors(self) -> list[str]:
        pytest.skip("Safari: its driver gives no console to read")

    def assert_quiet_console(self) -> None:
        pass

    def user_agent(self, text: str) -> None:
        pytest.skip("Safari: what a browser says it is cannot be set")

    def platform(self, name: str) -> None:
        pytest.skip("Safari: the system a browser says it runs on cannot be set")

    def find_element(self, strategy: str, selector: str):
        return SafariElement(self, super().find_element(strategy, selector))

    @staticmethod
    def dark(value: bool | None = None) -> bool:
        """The system's appearance is dark, or is set to dark or light."""
        script = 'tell application "System Events" to tell appearance preferences to '
        return osascript(script + ("get dark mode" if value is None
                                   else f"set dark mode to {str(value).lower()}")) == "true"

    def prefer_scheme(self, scheme: str | None) -> None:
        """Safari takes the colour scheme from the system alone, so this sets the system's
        appearance, dark or light (None), and quitting puts back the one it had."""
        if self.system_dark is None:
            self.system_dark = self.dark()
        if self.dark() != (scheme == "dark"):
            self.dark(scheme == "dark")


def launch(downloads: Path | None = None, touch: bool = False) -> Chrome:
    return (Safari if KIND == "safari" else Chrome)(downloads, touch)
