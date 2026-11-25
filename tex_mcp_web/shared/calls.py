"""The reviewer's Call agent presses, as a tool's page server keeps them, and the routes the
page and the waiters (waiter.py) use for them.

An agent cannot be spoken to first over MCP, but its harness can watch a process and wake the
agent at each line it prints. So a press is a count the server keeps: a waiter parks in a long
poll on /wait-review, a press on /review-request releases it with one line, and the waiter
acknowledges on /wait-review/ack what it printed. Presses go to one waiter, the one that last
claimed them on /wait-review/listen. An agent the configuration names (agents.py) has a lane
of its own beside that one session, with its own count.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import psutil
from aiohttp import web

from . import plugin_monitor, shared_file


class Lane:
    """The presses for one agent the configuration names: how many were made, how many its
    waiter took, and the model and effort picked at the last, kept across a restart, and the
    last its waiter was handed, which a restart forgets."""

    def __init__(self, name: str, path: Path) -> None:
        self.name, self.path = name, path
        self.calls, self.consumed, self.choice = 0, 0, {}
        self.handed = 0
        self.called = asyncio.Event()
        self.waiters = 0
        if path.is_file():
            data = json.loads(shared_file.read_text(path))
            self.calls, self.consumed, self.choice = int(data["calls"]), int(data["consumed"]), dict(data["choice"])

    @property
    def queued(self) -> bool:
        """Whether a press waits for the agent's waiter to be handed it."""
        return self.calls > max(self.consumed, self.handed)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_name(self.path.name + ".new")
        staging.write_text(json.dumps({"calls": self.calls, "consumed": self.consumed, "choice": self.choice}), encoding="utf-8")
        shared_file.replace(staging, self.path)


class Calls:
    """The presses on one page: how many were made and how many a waiter confirmed it
    delivered, kept in *records* so that a press outlives a server restart, the waiter they go
    to, and the lanes of the agents the configuration names.

    The server gives it *config_name*, its configuration's file name, and four calls it makes
    when it needs them: *agents()*, the agents that configuration names at that moment
    (agents.parse); *review_line(press)*, the line a waiter prints for that press;
    *broadcast(message)*, which tells the pages; and *on_press(body)*, which takes up what a
    press for the session listening carries besides, before it is counted."""

    # A parked waiter is answered within this long even when nothing happened, so the
    # waiter's request never trips its own timeout and a lost connection is noticed.
    POLL_TIMEOUT = 55.0
    # How long a session that asked to listen has to start its waiter before a session
    # starting up may listen in its place.
    LISTENER_START = 60.0

    def __init__(self, records: Path, config_name: str, agents: Callable[[], dict[str, dict[str, list[str]]]],
                 review_line: Callable[[int], str], broadcast: Callable[[dict[str, Any]], Awaitable[None]],
                 on_press: Callable[[dict[str, Any]], None] = lambda body: None) -> None:
        self.records, self.config_name = records, config_name
        self.agents, self.review_line, self.broadcast, self.on_press = agents, review_line, broadcast, on_press
        self.state_path = records / "review-state.json"
        self.calls, self.consumed = self._load()
        # The last press a waiter was handed. A press is offered again until its waiter
        # acknowledges it, and a page that did not hear the waiter come learns from this that
        # an agent took it. A restarted server has handed none.
        self.handed = 0
        self.called = asyncio.Event()
        self.waiters = 0
        # An agent the configuration names has a lane of its own beside the one listening
        # session: its waiter names no listener, so a press for it never moves that session's,
        # and that session's never moves its.
        self.lanes: dict[str, Lane] = {}
        # Presses go to one waiter, named by the id listen() gave it: two sessions answering
        # one press would edit the same threads. Its pid, which its polls report, says
        # whether it still runs, and the Claude Code session that named it, when known,
        # whose own earlier waiter hears that it is that session listening on.
        self.listener: str | None = None
        self.listener_pid: int | None = None
        self.listener_session: int | None = None
        self.listener_claimed = 0.0
        self.closing = False

    @property
    def queued(self) -> bool:
        """Whether a press waits for the listening session's waiter to be handed it."""
        return self.calls > max(self.consumed, self.handed)

    def add_routes(self, app: web.Application) -> None:
        app.router.add_post("/review-request", self._handle_press)
        app.router.add_get("/wait-review", self._handle_wait)
        app.router.add_post("/wait-review/listen", self._handle_claim)
        app.router.add_post("/wait-review/ack", self._handle_ack)

    def close(self) -> None:
        """Release the parked waiters. A parked waiter holds its connection for the whole poll;
        released as the server stops, it asks again at once and finds the port closed instead
        of holding it half-alive."""
        self.closing = True
        self.called.set()

    def _load(self) -> tuple[int, int]:
        try:
            data = json.loads(shared_file.read_text(self.state_path))
            calls, consumed = int(data["calls"]), int(data["consumed"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return 0, 0
        if calls < 0 or not 0 <= consumed <= calls:
            return 0, 0
        return calls, consumed

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.state_path.with_name(self.state_path.name + ".new")
        staging.write_text(json.dumps({"calls": self.calls, "consumed": self.consumed}), encoding="utf-8")
        shared_file.replace(staging, self.state_path)

    def lane(self, name: str) -> Lane:
        """The lane of an agent the configuration names, made when first asked for."""
        if name not in self.agents():
            raise web.HTTPNotFound(text=f"agents in {self.config_name} names no {name}")
        if name not in self.lanes:
            self.lanes[name] = Lane(name, self.records / f"review-lane-{name}.json")
        return self.lanes[name]

    def page_lanes(self) -> dict[str, dict[str, Any]]:
        """What the page shows of each agent the bell can call beside the session listening,
        with the presses made for it so far, which tell a page whether what it hears was so
        before or after its own press."""
        return {name: {"waiters": self.lane(name).waiters, "queued": self.lane(name).queued,
                       "calls": self.lane(name).calls, "models": agent["models"], "efforts": agent["efforts"]}
                for name, agent in self.agents().items()}

    async def _lane_press(self, lane: Lane, body: dict[str, Any]) -> web.Response:
        # A press carries the model and effort picked from those the configuration lists.
        choice = {}
        for key, listed in (("model", "models"), ("effort", "efforts")):
            allowed = self.agents()[lane.name][listed]
            if allowed:
                choice[key] = body.get(key, allowed[0])
                if choice[key] not in allowed:
                    return web.json_response({"error": f"{listed} of {lane.name} lists no {choice[key]}"}, status=400)
        lane.choice = choice
        lane.calls += 1
        lane.save()
        delivered = lane.waiters > 0
        lane.called.set()
        lane.called = asyncio.Event()
        await self.broadcast({"type": "review_requested", "agent": lane.name, "calls": lane.calls, "delivered": delivered})
        return web.json_response({"calls": lane.calls, "delivered": delivered})

    async def _wait_lane(self, lane: Lane) -> web.Response:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.POLL_TIMEOUT
        lane.waiters += 1
        await self.broadcast({"type": "lanes", "lanes": self.page_lanes()})
        try:
            while lane.calls <= lane.consumed:
                remaining = deadline - loop.time()
                if remaining <= 0 or self.closing:
                    return web.Response(status=204)
                try:
                    await asyncio.wait_for(lane.called.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return web.Response(status=204)
            line = self.review_line(lane.calls)
            lane.handed = lane.calls
            if lane.choice:
                line += " [" + " ".join(f"{key}={value}" for key, value in lane.choice.items()) + "]"
            return web.Response(text=line, headers={"X-Press": str(lane.calls)})
        finally:
            lane.waiters -= 1
            await self.broadcast({"type": "lanes", "lanes": self.page_lanes()})

    def _wake(self) -> None:
        released = self.called
        self.called = asyncio.Event()
        released.set()

    def _listener_alive(self) -> bool:
        if self.listener is None:
            return False
        if self.listener_pid is None:
            return asyncio.get_running_loop().time() - self.listener_claimed < self.LISTENER_START
        return psutil.pid_exists(self.listener_pid)

    async def _handle_press(self, request: web.Request) -> web.Response:
        try:
            body = await request.json() if request.can_read_body else {}
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        if "agent" in body:
            return await self._lane_press(self.lane(str(body["agent"])), body)
        self.on_press(body)
        self.calls += 1
        self._save()
        delivered = self.waiters > 0
        self._wake()
        await self.broadcast({"type": "review_requested", "calls": self.calls, "delivered": delivered})
        return web.json_response({"calls": self.calls, "delivered": delivered})

    async def _handle_claim(self, request: web.Request) -> web.Response:
        """Make the waiter with this listener id the one presses go to. With if_free, only
        when no other waiter runs: a session listening from its start takes presses from
        no one, and a session asked to listen takes them from whoever had them. session,
        when given, is the pid of the Claude Code process the listener runs under, which
        tells a waiter left by the same session from one another session took over."""
        try:
            listener = request.query["listener"]
            session = int(request.query["session"]) if "session" in request.query else None
        except (KeyError, ValueError) as error:
            raise web.HTTPBadRequest(text="listener is required, and session is a pid") from error
        if "if_free" in request.query and self._listener_alive():
            return web.json_response({"listening": False})
        self.listener, self.listener_pid = listener, None
        self.listener_session = session
        self.listener_claimed = asyncio.get_running_loop().time()
        # A waiter parked for the listener before is released to hear that it is not one.
        self._wake()
        return web.json_response({"listening": True})

    async def _handle_wait(self, request: web.Request) -> web.Response:
        if "agent" in request.query:
            return await self._wait_lane(self.lane(request.query["agent"]))
        # The consumption watermark lives here, not in the waiter: a waiter carrying its
        # own could never see a press made before its script was written. Waiting does
        # not consume: the response can die on the wire after the watermark moved, and
        # the wake-up died with it. The waiter acks what it printed (the X-Press header
        # names it), and until then the press is offered again.
        try:
            listener, pid = request.query["listener"], int(request.query["pid"])
        except (KeyError, ValueError) as error:
            raise web.HTTPBadRequest(text="listener and pid are required") from error
        # A restarted server knows no listener, and the first waiter back is the one.
        if self.listener is None:
            self.listener = listener
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.POLL_TIMEOUT
        self.waiters += 1
        await self.broadcast({"type": "review_waiters", "waiters": self.waiters})
        try:
            while True:
                if self.listener != listener:
                    own = (self.listener_session is not None
                           and plugin_monitor.session_of(pid) == self.listener_session)
                    return web.Response(
                        text="[moved] this session listens for Call agent with a newer waiter, so this one ends"
                        if own else "[moved] another session took over listening for Call agent, so "
                        "presses go to that session; call listen() to take them back",
                        headers={"X-Moved": "1"})
                self.listener_pid = pid
                if self.calls > self.consumed:
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return web.Response(status=204)
                waited = self.called
                try:
                    await asyncio.wait_for(waited.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return web.Response(status=204)
                if self.closing:
                    return web.Response(status=204)
            self.handed = self.calls
            return web.Response(text=self.review_line(self.calls), headers={"X-Press": str(self.calls)})
        finally:
            self.waiters -= 1
            await self.broadcast({"type": "review_waiters", "waiters": self.waiters})

    async def _handle_ack(self, request: web.Request) -> web.Response:
        try:
            upto = int(request.query["upto"])
        except (KeyError, ValueError) as error:
            raise web.HTTPBadRequest(text="upto must be an integer") from error
        if "agent" in request.query:
            lane = self.lane(request.query["agent"])
            lane.consumed = max(lane.consumed, min(upto, lane.calls))
            lane.save()
            return web.json_response({"calls": lane.calls, "consumed": lane.consumed})
        # Idempotent and monotonic: a late or repeated ack never moves the watermark back,
        # and one beyond the count (a script talking to a restarted server whose counters
        # are behind it) clamps rather than marking presses that do not exist yet.
        self.consumed = max(self.consumed, min(upto, self.calls))
        self._save()
        return web.json_response({"calls": self.calls, "consumed": self.consumed})
