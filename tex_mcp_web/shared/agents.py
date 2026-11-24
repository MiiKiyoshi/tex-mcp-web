"""The agents a page's bell can call beside the session listening, as a tool's configuration
names them under agents: each with the client names that wait in its lane and the models and
efforts a press picks from.

    agents:
      second:
        clients: [second, other]
        efforts: [low, high]
"""

from __future__ import annotations

import re
from typing import Any

KEYS = ("clients", "models", "efforts")
# A lane's name, and what a press passes on: held to the shape a name has, since a waiter
# prints them on a line an agent reads.
WORD = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def parse(raw: Any) -> dict[str, dict[str, list[str]]]:
    """The agents a configuration's agents value names, none when it has none. clients
    defaults to the agent's own name, models and efforts to none. ValueError for any other
    shape."""
    if raw is None:
        return {}
    shape = "agents must be names, each with clients, models and efforts as lists of names"
    if not isinstance(raw, dict):
        raise ValueError(shape)
    agents: dict[str, dict[str, list[str]]] = {}
    for name, given in raw.items():
        given = {} if given is None else given
        if not isinstance(name, str) or not WORD.fullmatch(name) or not isinstance(given, dict) or set(given) - set(KEYS):
            raise ValueError(shape)
        lane = {key: given.get(key, [name] if key == "clients" else []) for key in KEYS}
        if not all(isinstance(words, list) and all(isinstance(word, str) and WORD.fullmatch(word) for word in words)
                   for words in lane.values()) or not lane["clients"]:
            raise ValueError(shape)
        agents[name] = lane
    return agents


def lane_of(client: str, agents: dict[str, dict[str, list[str]]]) -> str | None:
    """The lane a client of this name waits in: that of the agent whose clients' names its own
    holds. None is the session listening."""
    name = client.casefold()
    return next((lane for lane, agent in agents.items() if any(part in name for part in agent["clients"])), None)
