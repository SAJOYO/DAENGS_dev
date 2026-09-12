"""Durable attempts, fenced leases and recovery publication for territory photos."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from daengs_backend.config import settings
from daengs_backend.repositories import territory_vision as repo

MAX_ATTEMPTS = 2
RETRY_SECONDS = 2
DISPATCH_SECONDS = 30
DISPATCH_LIMIT = 100


@dataclass(frozen=True)
class VisionLease:
    token: uuid.UUID
    generation: str
    storage_key: str
    content_type: str
    size_bytes: int
    attempt_number: int
    exhausted: bool
    retry_reason: str | None


async def claim(session, attempt_id):
    row = await repo.lock_attempt(session, attempt_id)
    now = datetime.now(UTC)
    if (
        row is None
        or row.status != "VISION_PENDING"
        or row.vision_available_at > now
        or (row.vision_lease_until is not None and row.vision_lease_until > now)
    ):
        await session.commit()
        return None
    exhausted = row.vision_attempts >= MAX_ATTEMPTS
    row.vision_attempts += int(not exhausted)
    row.vision_lease_token = uuid.uuid4()
    row.vision_lease_until = now + timedelta(
        seconds=max(60, settings.territory_vision_timeout_ms / 1000 * 3 + 30)
    )
    lease = VisionLease(
        token=row.vision_lease_token,
        generation=row.photo_object_generation,
        storage_key=row.photo_storage_key,
        content_type=row.photo_content_type,
        size_bytes=row.photo_size_bytes,
        attempt_number=row.vision_attempts,
        exhausted=exhausted,
        retry_reason=row.vision_retry_reason,
    )
    await session.commit()
    return lease


def owns_lease(row, token, generation, now):
    return (
        row.status == "VISION_PENDING"
        and row.vision_lease_token == token
        and row.photo_object_generation == generation
        and row.vision_lease_until is not None
        and row.vision_lease_until > now
    )


async def retry(session, attempt_id, lease, *, reason, keep_lease=False):
    row = await repo.lock_attempt(session, attempt_id)
    now = datetime.now(UTC)
    if row is None or not owns_lease(row, lease.token, lease.generation, now):
        await session.commit()
        return False
    row.vision_retry_reason = reason
    if keep_lease:
        # Cancelling an async wait cannot stop a synchronous SDK thread already in flight.
        row.vision_available_at = row.vision_lease_until
    else:
        row.vision_lease_token = row.vision_lease_until = None
        row.vision_available_at = now + timedelta(seconds=RETRY_SECONDS)
    row.vision_dispatch_after = row.vision_available_at
    await session.commit()
    return True


async def recover_pending(*, factory=None, publish=None):
    from daengs_backend.core.database import worker_session
    from daengs_backend.services.territory import (
        TerritoryVisionQueueUnavailable,
        _publish_vision_attempt,
    )

    factory = factory or worker_session
    publish = publish or _publish_vision_attempt
    async with factory() as session:
        now = datetime.now(UTC)
        rows = await repo.due_dispatches(session, now, limit=DISPATCH_LIMIT)
        ids = [row.id for row in rows]
        for row in rows:
            # A crash or broker failure here is recovered after this finite reservation.
            row.vision_dispatch_after = now + timedelta(seconds=DISPATCH_SECONDS)
        await session.commit()
    published = 0
    for attempt_id in ids:
        try:
            await asyncio.to_thread(publish, attempt_id)
        except TerritoryVisionQueueUnavailable:
            # The broker is shared: do not occupy a worker for 100 sequential connection timeouts.
            # Every unpublished ID remains eligible after its finite reservation.
            break
        else:
            published += 1
    return {"selected": len(ids), "published": published, "failed": len(ids) - published}
