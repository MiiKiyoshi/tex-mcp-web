import os
import urllib.request

import pytest

from tex_mcp_web.shared import waiter
from tex_mcp_web.shared.tool import Tool


class Asked(BaseException):
    """Stops the waiter at its first request, which the test reads."""


@pytest.mark.parametrize("base", ["", "/reader"])
def test_the_waiter_asks_under_the_path_its_tool_keeps_its_calls(monkeypatch, base) -> None:
    """A tool whose call routes sit under a path, as a page mounted at /reader, has its waiter
    ask there; one at the server's root asks at the root."""
    asked = []

    def opened(request, timeout=None):
        asked.append(request.full_url)
        raise Asked

    monkeypatch.setattr(urllib.request, "urlopen", opened)
    tool = Tool(name="sample-mcp-web", package="sample", server="review server", event="[sample]", base=base)
    with pytest.raises(Asked):
        waiter.wait(tool, 8765, "one", agent="second", once=True)
    assert asked == [f"http://127.0.0.1:8765{base}/wait-review?listener=one&pid={os.getpid()}&agent=second"]
