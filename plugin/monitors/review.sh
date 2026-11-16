#!/bin/sh
# Starts the tex-mcp review monitor (tex_mcp_web/monitor.py) with the Python tex-mcp-web is
# installed in: the checkout's own environment, or the Python on the PATH that has it.
# An argument, when given, names the session's Claude Code process.
root=$(cd "$(dirname "$0")/../.." && pwd)
# Under Git Bash on Windows a process's Windows parent is gone once it is started, so the
# way up to Claude Code is lost to the monitor: the shell's own tree is walked to its top
# here, and the monitor looks for the session from that process's Windows pid.
if [ $# -eq 0 ] && [ -r "/proc/$$/winpid" ]; then
  pid=$$
  while :; do
    above=$(cat "/proc/$pid/ppid" 2>/dev/null)
    if [ -z "$above" ] || [ "$above" -le 1 ]; then break; fi
    pid=$above
  done
  set -- --from "$(cat "/proc/$pid/winpid")"
fi
for python in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe" python3 python; do
  if PYTHONPATH="$root" "$python" -c "import tex_mcp_web.monitor" 2>/dev/null; then
    PYTHONPATH="$root" exec "$python" -m tex_mcp_web.monitor "$@"
  fi
done
