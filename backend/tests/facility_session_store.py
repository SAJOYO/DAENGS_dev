"""Deterministic CAS store for gateway tests; no production memory fallback."""


class MemorySessions:
    def __init__(self):
        self.items = {}

    async def get(self, key):
        return self.items.get(str(key))

    async def create(self, key, value):
        key = str(key)
        if key in self.items:
            return False
        self.items[key] = value
        return True

    async def replace(self, key, previous, value):
        key = str(key)
        if self.items.get(key) != previous:
            return False
        self.items[key] = value
        return True
