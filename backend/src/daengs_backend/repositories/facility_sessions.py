"""Expiring opaque facility continuations. Redis CAS preserves the original expiry."""

from redis.asyncio import Redis

TTL_SECONDS = 900
MAX_SESSION_BYTES = 1024 * 1024

_CAS = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'PX', ttl)
return 1
"""


class RedisFacilitySessions:
    def __init__(self, url: str):
        self.url = url

    def client(self):
        return Redis.from_url(
            self.url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3
        )

    @staticmethod
    def key(search_id):
        return f"facility:continuation:v1:{search_id}"

    async def get(self, search_id):
        async with self.client() as client:
            return await client.get(self.key(search_id))

    async def create(self, search_id, value):
        async with self.client() as client:
            return bool(await client.set(self.key(search_id), value, ex=TTL_SECONDS, nx=True))

    async def replace(self, search_id, previous, value):
        async with self.client() as client:
            return bool(await client.eval(_CAS, 1, self.key(search_id), previous, value))
