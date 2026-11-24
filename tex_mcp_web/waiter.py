"""Wait for Call agent presses on the review page and print one line for each (shared/waiter.py).

Run as ``python -m tex_mcp_web.waiter <port> <listener> [--codex <thread>]`` with the
listener id ``listen()`` gave, or ``<port> - --agent <name> --once`` for an agent the
configuration names.
"""

from .shared import waiter
from .tool import TOOL

if __name__ == "__main__":
    waiter.main(TOOL)
