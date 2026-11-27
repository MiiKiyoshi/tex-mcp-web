"""shared/listening.py tells a session's agent how to listen in the words of its own tool, and
claims the page's presses under the path its tool keeps its calls, with its headers."""

import urllib.request

import pytest

from tex_mcp_web.shared import listening, plugin_monitor
from tex_mcp_web.shared.tool import Tool


def sample(**values) -> Tool:
    return Tool(name="sample-mcp-web", package="sample", server="reading server", event="[sample]",
                plugin="sample-mcp", page="reading page", person="reader", work="reading", press="[call]", **values)


@pytest.mark.parametrize("client, lane, words", [
    ("claude-code", None, 'description="sample-mcp reading"'),
    ("codex-mcp-client", None, "Allow the sample-mcp waiter"),
    ("other", None, "listening resumes when the user asks again"),
    ("claude-code", "second", "waiting on the reading page and ends as soon as the reader presses"),
])
def test_the_agent_is_told_how_to_listen_in_its_tools_words(monkeypatch, client, lane, words) -> None:
    monkeypatch.setattr(plugin_monitor, "hand_over", lambda tool, command: False)
    told = listening.how(sample(), client, "wait", lane)
    assert words in told
    assert "[gone] means the reading server is unreachable" in told
    # Nothing of another tool's: every word that differs comes from the tool.
    assert "review" not in told.casefold() and "tex" not in told.casefold()


@pytest.mark.parametrize("base", ["", "/reader"])
def test_a_claim_goes_under_the_path_its_tool_keeps_its_calls(monkeypatch, base) -> None:
    asked = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exception):
            return False

        def read(self) -> bytes:
            return b'{"listening": false}'

    def opened(request, timeout=None):
        asked.append((request.get_method(), request.full_url, request.get_header("X-token")))
        return Reply()

    monkeypatch.setattr(urllib.request, "urlopen", opened)
    tool = sample(base=base, headers=lambda: {"X-Token": "secret"})
    assert listening.claim(tool, 8765, 42, if_free=True) is False
    assert asked == [("POST", f"http://127.0.0.1:8765{base}/wait-review/listen"
                      f"?listener={listening.LISTENER}&if_free=1&session=42", "secret")]
