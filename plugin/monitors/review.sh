#!/bin/sh
# Starts the tex-mcp review monitor (tex_mcp_web/monitor.py) with the Python tex-mcp-web is
# installed in: the checkout's own environment, or the Python on the PATH that has it.
# An argument, when given, names the session's Claude Code process.
root=$(cd "$(dirname "$0")/../.." && pwd)
for python in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe" python3 python; do
  if PYTHONPATH="$root" "$python" -c "import tex_mcp_web.monitor" 2>/dev/null; then
    PYTHONPATH="$root" exec "$python" -m tex_mcp_web.monitor "$@"
  fi
done
