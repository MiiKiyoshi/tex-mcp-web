#!/bin/sh
# The tex-mcp review monitor. Claude Code starts it with the session and turns each line it
# prints into a notification. It prints nothing of its own: it runs the newest waiter command
# tex-mcp's listen() hands this session (tex_mcp_web/plugin_monitor.py), in place of the one
# it ran before, and passes on what the waiter prints. It never ends while the session's
# Claude Code process lives, since Claude Code does not start an ended monitor again.
# The first argument, when given, names that process in place of the one found above.
dir="${XDG_CACHE_HOME:-$HOME/.cache}/tex-mcp-web/monitor"
session=${1-}
if [ -z "$session" ]; then
  pid=$$
  while [ "$pid" -gt 1 ]; do
    pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
    [ -n "$pid" ] || break
    case $(ps -o comm= -p "$pid") in
      claude|*/claude) session=$pid; break ;;
    esac
  done
fi
if [ -z "$session" ]; then
  # No Claude Code process above: nothing can hand this monitor a waiter.
  while kill -0 "$PPID" 2>/dev/null; do sleep 60; done
  exit 0
fi
until mkdir -p "$dir" 2>/dev/null && echo $$ > "$dir/$session.monitor"; do sleep 30; done
child=
current=
stop_child() {
  # The waiter's own children first, as they lose their parent once it ends.
  [ -n "$child" ] && { pkill -TERM -P "$child" 2>/dev/null; kill -TERM "$child" 2>/dev/null; }
  child=
}
trap 'stop_child; rm -f "$dir/$session.monitor" "$dir/$session.command"' EXIT
trap 'exit 0' TERM INT HUP
while kill -0 "$session" 2>/dev/null; do
  next=$(cat "$dir/$session.command" 2>/dev/null)
  if [ -n "$next" ] && [ "$next" != "$current" ]; then
    stop_child
    current=$next
    sh -c "$(printf '%s\n' "$next" | sed 1d)" &
    child=$!
  fi
  sleep 1
done
