import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

from tests import chrome
from tests.chrome import needs_browser


@needs_browser
@chrome.not_in_safari("a data: page is enough to check this, and Safari's driver refuses one")
def test_a_failing_test_keeps_what_its_page_showed(tmp_path: Path, monkeypatch) -> None:
    """A browser quit with a failure on its way out leaves the page's screenshot, its markup,
    its fields and its console in the folder REVIEW_FAILURES names. One quit without a
    failure leaves nothing."""
    kept = tmp_path / "kept"
    monkeypatch.setenv("REVIEW_FAILURES", str(kept))
    browser = chrome.launch()
    browser.navigate("data:text/html,<textarea id=box>typed</textarea><script>console.error('seen')</script>")
    with pytest.raises(AssertionError):
        try:
            raise AssertionError("the page did not answer")
        finally:
            browser.quit()
    assert sorted(path.suffix for path in kept.iterdir()) == [".html", ".json", ".png"]
    # Each file is named by the test, so two failures never share a name.
    assert all("test_a_failing_test_keeps_what_its_page_showed-" in path.name for path in kept.iterdir())
    record = json.loads(next(kept.glob("*.json")).read_text(encoding="utf-8"))
    assert "the page did not answer" in record["error"]
    assert re.fullmatch(r"test_test_browser\.py:\d+", record["where"][-1])
    assert ["#box", None, "typed"] in record["page"]["fields"] and record["page"]["focus"] == "body."
    assert any("seen" in line for line in record["console"])
    quiet = chrome.launch()
    quiet.navigate("data:text/html,<p>Nothing failed.</p>")
    quiet.quit()
    # A test that skips itself has not failed.
    skipping = chrome.launch()
    with pytest.raises(pytest.skip.Exception):
        try:
            pytest.skip("not on this machine")
        finally:
            skipping.quit()
    assert len(list(kept.iterdir())) == 3


@needs_browser
@chrome.not_in_safari("the folder this checks is Chrome's own")
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Chrome takes its temporary folder from TMPDIR on Linux")
def test_a_browser_a_test_ends_leaves_nothing_in_the_temporary_folder(monkeypatch) -> None:
    """Chrome makes a folder of its own in the temporary folder and removes it when it closes
    by itself, so a test's browser, ended, leaves the folder as it found it. The folder has a
    short path, since Chrome's socket in it takes a path of at most 107 bytes."""
    with tempfile.TemporaryDirectory() as name:
        folder = Path(name)
        monkeypatch.setenv("TMPDIR", name)
        browser = chrome.launch()
        browser.navigate("data:text/html,<p>Nothing is left.</p>")
        assert any(folder.iterdir())
        browser.quit()
        assert list(folder.iterdir()) == []
