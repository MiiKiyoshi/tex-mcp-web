"""The plugin's monitor (shared/monitor.py) ended outright, as Claude Code may end it on a reload
or a crash, leaves no waiter running: one left would keep taking presses that reach no one."""

import contextlib
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from tex_mcp_web.shared import plugin_monitor
from tex_mcp_web.tool import TOOL

# A waiter that prints once every process of it has started and then waits. Given 2, it runs as
# two processes, as a waiter does on Windows from an environment's python.exe, a launcher that
# runs the real Python under it.
WAITER = """import subprocess, sys, time
if sys.argv[1] == "2":
    subprocess.run([sys.executable, sys.argv[0], "1"])
else:
    print("waiting", flush=True)
    time.sleep(120)
"""


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has no way to end a process with the one that started it")
@pytest.mark.parametrize("processes", [1, 2])
def test_a_monitor_ended_outright_takes_its_waiter_with_it(tmp_path: Path, monkeypatch, processes: int) -> None:
    if processes == 2 and os.name != "nt":
        pytest.skip("on Linux the kernel ends only the process the monitor starts, and a waiter is one process there")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(TOOL, "monitors", tmp_path / "tex-mcp-web" / "monitor")
    # A process of the test's stands for the session, and lives on while its monitor is ended.
    session = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    monkeypatch.setattr(plugin_monitor, "claude_pid", lambda: session.pid)
    monitor = subprocess.Popen([sys.executable, "-m", "tex_mcp_web.monitor", str(session.pid)],
                               stdout=subprocess.PIPE, text=True)
    below: list[psutil.Process] = []
    try:
        deadline = time.monotonic() + 10
        while plugin_monitor.monitored_session(TOOL) != session.pid:
            assert time.monotonic() < deadline, "the monitor did not record itself"
            time.sleep(0.05)
        waiter = tmp_path / "waiter.py"
        waiter.write_text(WAITER, encoding="utf-8")
        python = shlex.quote(Path(sys.executable).as_posix())
        assert plugin_monitor.hand_over(TOOL, f"{python} {shlex.quote(waiter.as_posix())} {processes}")
        assert monitor.stdout.readline().strip() == "waiting"
        below = psutil.Process(monitor.pid).children(recursive=True)
        assert len(below) >= processes
        # SIGKILL, or TerminateProcess on Windows: the monitor's own cleanup never runs.
        monitor.kill()
        monitor.wait()
        assert psutil.wait_procs(below, timeout=10)[1] == []
    finally:
        for process in below:
            with contextlib.suppress(psutil.Error):
                process.kill()
        for process in (monitor, session):
            if process.poll() is None:
                process.kill()
            process.wait()
