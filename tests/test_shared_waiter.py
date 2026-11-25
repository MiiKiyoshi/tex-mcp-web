import os
import urllib.request
from pathlib import Path

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


@pytest.mark.parametrize("place", ["codex", "bin"])
def test_on_windows_a_line_goes_to_codex_whole_through_the_program_npm_carries(monkeypatch, tmp_path, place) -> None:
    """npm installs codex on Windows as codex.cmd, and cmd.exe would cut the line at its first
    newline: the waiter runs the codex.exe the package carries instead, wherever the package's
    version puts it, and hands it the line whole as one argument."""
    import subprocess
    import sys

    npm = tmp_path / "npm"
    package = npm / "node_modules" / "@openai" / "codex"
    program = (package / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc"
               / place / "codex.exe")
    program.parent.mkdir(parents=True)
    program.write_bytes(b"")
    (program.parent / "codex-command-runner.exe").write_bytes(b"")
    (npm / "codex.CMD").write_text("@ECHO off\n", encoding="utf-8")
    ran = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(waiter.shutil, "which", lambda name: str(npm / "codex.CMD") if name == "codex" else None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    tool = Tool(name="sample-mcp-web", package="sample", server="review server", event="[sample]")
    waiter._deliver(tool, 'Two lines & | % ^ "quoted"', "thread-1")
    assert ran == [[str(program), "queue", "--thread", "thread-1", "--message", '[sample]\nTwo lines & | % ^ "quoted"']]


def test_a_codex_exe_on_path_is_run_as_it_is_and_elsewhere_codex_by_name(monkeypatch, tmp_path) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(waiter.shutil, "which", lambda name: str(tmp_path / "codex.exe"))
    assert waiter._codex() == str(tmp_path / "codex.exe")
    monkeypatch.setattr(sys, "platform", "linux")
    assert waiter._codex() == "codex"


def test_the_waiter_writes_utf8_whatever_the_system_writes_a_pipe_in(tmp_path) -> None:
    """On Windows a pipe takes the system's code page, cp949 on a Korean one, and the [gone]
    line holds Windows' own words for a refused connection: the waiter writes UTF-8, which its
    readers read."""
    import subprocess
    import sys

    script = ("import sys; from tex_mcp_web.shared import waiter; from tex_mcp_web.shared.tool import Tool\n"
              "tool = Tool(name='sample-mcp-web', package='sample', server='review server', event='[sample]')\n"
              "waiter.wait = lambda *args: waiter._deliver(tool, '[gone] 대상 컴퓨터에서 연결을 거부했으므로', None)\n"
              "waiter.main(tool, ['8765', 'one'])\n")
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          env={**os.environ, "PYTHONIOENCODING": "cp949"}, cwd=Path(waiter.__file__).parents[2])
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode("utf-8") == "[gone] 대상 컴퓨터에서 연결을 거부했으므로\n"
