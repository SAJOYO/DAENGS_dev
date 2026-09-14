"""An optimistic in-memory command port for controlled tests and the loopback UI."""

import asyncio
import json

from daengs_place.place.commands.contract import CommandResult


class MemoryWorkspace:
    def __init__(self, state, commands, *, db=None):
        self.state, self.commands, self.db = state, commands, db
        self.lock = asyncio.Lock()
        self.records = {}

    async def read(self):
        return self.state.model_copy(deep=True)

    async def execute(self, command_id, name, arguments, expected_revision):
        signature = json.dumps([name, arguments, expected_revision], sort_keys=True)
        async with self.lock:
            if command_id in self.records:
                previous, task = self.records[command_id]
                if signature != previous:
                    return CommandResult(
                        status="conflict", state=self.state, code="request_id_reused"
                    )
            else:
                before = await self.read()
                task = asyncio.create_task(
                    self._execute(before, name, arguments, expected_revision)
                )
                self.records[command_id] = (signature, task)
        return await asyncio.shield(task)

    async def _execute(self, before, name, arguments, expected_revision):
        output = await self.commands.execute(
            self.db, before, name, arguments, expected_revision=expected_revision
        )
        async with self.lock:
            if self.state.revision != before.revision:
                return CommandResult(status="conflict", state=self.state, code="view_changed")
            self.state = output.state
            return output
