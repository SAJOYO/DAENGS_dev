"""Explicit review-only substitute for Redis. Never used by production routing."""

import time

from daengs_backend.repositories.facility_sessions import TTL_SECONDS


class MemorySessions:
    def __init__(self):
        self.items = {}

    async def get(self, key):
        key = str(key)
        item = self.items.get(key)
        if item and item[0] > time.time():
            return item[1]
        self.items.pop(key, None)
        return None

    async def create(self, key, value):
        key = str(key)
        if await self.get(key) is not None:
            return False
        self.items = {k: v for k, v in self.items.items() if v[0] > time.time()}
        if len(self.items) >= 256:
            self.items.pop(next(iter(self.items)))
        self.items[key] = (time.time() + TTL_SECONDS, value)
        return True

    async def replace(self, key, previous, value):
        key = str(key)
        if await self.get(key) != previous:
            return False
        self.items[key] = (self.items[key][0], value)
        return True
