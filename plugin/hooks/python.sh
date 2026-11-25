#!/bin/sh
# Runs a hook script with a Python that runs. On Windows, where Claude Code runs a hook in Git
# Bash, python3 may be only the Microsoft Store's stand-in in WindowsApps, which opens the Store
# instead of running anything, so the py launcher runs it there when there is one. Elsewhere
# python3 does. The hooks need nothing but the standard library.
#
# Shared by the review tools' plugins. It is written in tex-mcp-web, and scripts/share_page.py
# copies it to the others. A copy is never edited by hand.
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    if command -v py >/dev/null 2>&1; then exec py -3 "$@"; fi
    ;;
esac
exec python3 "$@"
