"""The review page in Google Chrome, the browser most of its readers use, for the tests.

Chrome is driven through Selenium, which finds the driver that fits the installed Chrome by
itself. ``launch()`` gives a headless window. ``TEX_MCP_BROWSER=headed`` shows it, to watch
a test run.
"""

from __future__ import annotations

import base64
import os
import shutil
import sys
from pathlib import Path

import pytest

selenium = pytest.importorskip("selenium")
from selenium import webdriver  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

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


needs_chrome = pytest.mark.skipif(binary() is None, reason="Google Chrome is required")

# The keys a test presses, by the names it writes them with.
ENTER, CONTROL, SHIFT, ALT, META, ESCAPE = "\ue007", "\ue009", "\ue008", "\ue00a", "\ue03d", "\ue00c"


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
    """A Chrome window on the review page."""

    def __init__(self, downloads: Path | None = None, touch: bool = False):
        options = webdriver.ChromeOptions()
        options.binary_location = binary()
        if os.environ.get("TEX_MCP_BROWSER") != "headed":
            options.add_argument("--headless=new")
        # A container has no user namespace for Chrome's sandbox, and its /dev/shm is small.
        for flag in ("--no-sandbox", "--disable-dev-shm-usage", "--window-size=1500,1000",
                     "--force-device-scale-factor=1", "--hide-scrollbars"):
            options.add_argument(flag)
        options.set_capability("unhandledPromptBehavior", "ignore")
        if downloads is not None:
            options.add_experimental_option("prefs", {
                "download.default_directory": str(downloads), "download.prompt_for_download": False,
                "plugins.always_open_pdf_externally": True})
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_script_timeout(60)
        if touch:
            # A finger is a coarse pointer, which the page's touch rules key off.
            self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 2})

    def quit(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def navigate(self, url: str) -> None:
        self.driver.get(url)

    def refresh(self) -> None:
        self.driver.refresh()

    def execute_script(self, script: str, script_args=(), **_):
        """Run the script in the page, its arguments as ``arguments``, and give what it
        returns: a promise's value once it settles."""
        return self.driver.execute_script(script, *script_args)

    def set_window_rect(self, x: int = 0, y: int = 0, width: int = 1500, height: int = 1000) -> None:
        """Size the page's viewport, which is what a test measures against."""
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})

    @property
    def window_rect(self) -> dict:
        width, height = self.driver.execute_script("return [innerWidth, innerHeight]")
        return {"x": 0, "y": 0, "width": width, "height": height}

    def find_element(self, strategy: str, selector: str):
        assert strategy == "css selector"
        return self.driver.find_element(By.CSS_SELECTOR, selector)

    def screenshot(self, format: str = "base64"):
        shot = self.driver.get_screenshot_as_base64()
        return base64.b64decode(shot) if format == "binary" else shot

    def switch_to_alert(self):
        return self.driver.switch_to.alert

    def prefer_scheme(self, scheme: str | None) -> None:
        """Have the system say it prefers dark or light, or with None say what it does."""
        self.driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": scheme or ""}]})

    def actions(self, kind: str, name: str, parameters: dict | None = None) -> Actions:
        return Actions(self, kind, name, parameters)

    def perform(self, sources: list[dict]) -> None:
        """Perform the input sources' actions together, as two fingers move at once."""
        self.driver.execute("actions", {"actions": sources})


def launch(downloads: Path | None = None, touch: bool = False) -> Chrome:
    return Chrome(downloads, touch)


@pytest.fixture
def browser():
    """A Chrome window, closed when the test ends."""
    window = launch()
    yield window
    window.quit()
