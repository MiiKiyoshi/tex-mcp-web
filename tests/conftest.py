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
def no_update(monkeypatch):
    """No test asks the package index for the newest release, and none updates the install
    it runs from. A test of the page's Update puts its own answers in their place."""
    async def unknown():
        return None

    async def refused(*command):
        raise AssertionError("a test ran a real update")

    monkeypatch.setattr(update, "latest", unknown)
    monkeypatch.setattr(update, "_run", refused)
