import json
import re
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
