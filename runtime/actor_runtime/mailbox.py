"""Bounded reliable controls plus latest-value commands; never an action backlog."""

import asyncio
from collections import OrderedDict, deque

from .errors import ActorError


class OutboundMailbox:
    def __init__(self, control_capacity=32, command_capacity=32):
        self.controls = deque()
        self.commands = OrderedDict()
        self.control_capacity = control_capacity
        self.command_capacity = command_capacity
        self.ready = asyncio.Event()
        self.closed = False
        self.replaced = 0

    def put_control(self, value):
        if self.closed:
            return
        if len(self.controls) >= self.control_capacity:
            raise ActorError("BackpressureLimit", "Reliable control buffer is full")
        self.controls.append(value)
        self.ready.set()

    def put_latest(self, key, value):
        if self.closed:
            return
        if key in self.commands:
            self.replaced += 1
            self.commands[key] = value
            self.commands.move_to_end(key)
        else:
            if len(self.commands) >= self.command_capacity:
                raise ActorError("BackpressureLimit", "Command target buffer is full")
            self.commands[key] = value
        self.ready.set()

    async def get(self):
        while True:
            if self.controls:
                return self.controls.popleft()
            if self.commands:
                return self.commands.popitem(last=False)[1]
            if self.closed:
                return None
            self.ready.clear()
            await self.ready.wait()

    def close(self):
        self.closed = True
        self.controls.clear()
        self.commands.clear()
        self.ready.set()

    def finish(self):
        self.closed = True
        self.commands.clear()
        self.ready.set()

    def terminate(self, *messages):
        """Replace any backlog with bounded terminal controls, then finish."""
        self.controls.clear()
        self.commands.clear()
        self.closed = True
        self.controls.extend(messages[:self.control_capacity])
        self.ready.set()
