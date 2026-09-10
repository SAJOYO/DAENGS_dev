"""Atomic budget and owner-token lock scripts, on an explicitly isolated localhost Redis."""

import asyncio
import os
import uuid
from urllib.parse import urlparse

import pytest
from redis.asyncio import Redis

from daengs_backend.services.walk_catalog_refresh import BUDGET, RELEASE


async def test_request_budget_is_atomic_and_old_lock_owner_cannot_unlock_new_owner():
    url = os.environ.get("WALK_CATALOG_TEST_REDIS_URL")
    if not url:
        pytest.skip("disposable localhost Redis not configured")
    parsed = urlparse(url)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port == 56389
    budget, lock = ["walk-public:test:" + uuid.uuid4().hex for _ in range(2)]
    async with Redis.from_url(url) as broker:
        try:
            results = await asyncio.gather(*[broker.eval(BUDGET, 1, budget, 13) for _ in range(50)])
            assert sum(value > 0 for value in results) == 13
            assert int(await broker.get(budget)) == 13
            assert 0 < await broker.ttl(budget) <= 172800
            await broker.set(lock, "new-owner", ex=300)
            assert await broker.eval(RELEASE, 1, lock, "old-owner") == 0
            assert await broker.get(lock) == b"new-owner"
            assert await broker.eval(RELEASE, 1, lock, "new-owner") == 1
        finally:
            await broker.delete(budget, lock)
