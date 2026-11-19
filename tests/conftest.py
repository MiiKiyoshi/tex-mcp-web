import pytest

from tex_mcp_web import config, plugin_monitor, update


@pytest.fixture(autouse=True)
def no_user_translation(tmp_path, monkeypatch):
    """No test runs the translation command in the user's own config file, whatever that
    file holds on the machine running the tests. A test of that file writes it here."""
    monkeypatch.setattr(config, "user_config_file", lambda: tmp_path / "user-config" / "config.yaml")


@pytest.fixture(autouse=True)
def no_plugin_monitor(tmp_path, monkeypatch):
    """No plugin monitor runs for a test's session, whatever runs for the one running the
    tests. A server a test starts as its own process reads XDG_CACHE_HOME instead."""
    monkeypatch.setattr(plugin_monitor, "DIRECTORY", tmp_path / "no-monitor")


@pytest.fixture(autouse=True)
def no_overleaf_settling(monkeypatch):
    """The server waits a few seconds after news from Overleaf for its writes to land, and
    before it connects again, which a test's fake Overleaf does not need. A test of a wait
    sets its own."""
    from tex_mcp_web.overleaf_live import LivePresence
    from tex_mcp_web.server import TexMcpWebServer

    monkeypatch.setattr(TexMcpWebServer, "OVERLEAF_COMMENT_SETTLE", 0.01)
    monkeypatch.setattr(TexMcpWebServer, "OVERLEAF_TRACKED_SETTLE", 0.01)
    # A connection the fake drops is made again at once.
    monkeypatch.setattr(LivePresence, "RETRY_DELAYS", (0.05,))


@pytest.fixture(autouse=True)
def no_update(monkeypatch):
    """No test asks the package index for the newest release, and none updates the install
    it runs from. A test of the page's Update puts its own answers in their place."""
    async def unknown():
        return None

    async def refused(*command):
        raise AssertionError("a test ran a real update")

    monkeypatch.setattr(update, "latest", unknown)
    monkeypatch.setattr(update, "_run", refused)
