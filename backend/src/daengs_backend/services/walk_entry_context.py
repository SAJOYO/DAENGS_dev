"""Entry-owned durable outbox, external I/O outside DB transactions, fenced completion."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from daengs_backend.config import settings
from daengs_backend.models.walk import Walk
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope
from daengs_backend.repositories import walk_entry as entries
from daengs_backend.repositories import walk_entry_context as repo
from daengs_backend.services.walk_entry_context_source import collect, digest


async def reserve(session, row):
    if settings.walk_entry_context_enabled and row.payload is not None:
        await repo.enqueue(session, row, datetime.now(UTC))


async def reserve_pin(session, row, sidecar):
    if settings.walk_entry_context_enabled and row.payload is not None:
        if sidecar.payload and sidecar.payload["state"] == "provisional":
            return  # Finalization increments the parent revision and reserves the terminal pin.
        await repo.enqueue(session, row, datetime.now(UTC), policy=repo.PIN_POLICY)


async def read(session, owner, walk_id, entry_id, *, v2=False):
    from daengs_backend.services.walk_entry import EntryNotFound

    if await entries.owned_walk(session, owner, walk_id) is None:
        raise EntryNotFound
    row = await entries.get_entry(session, walk_id, entry_id)
    if row is None or row.payload is None:
        raise EntryNotFound
    from daengs_backend.services.walk_entry_v2 import guard_v1

    policy = repo.POLICY
    if not v2:
        await guard_v1(session, [walk_id], entry_id=entry_id)
    else:
        from daengs_backend.repositories import walk_entry_v2 as pins
        from daengs_backend.services.walk_entry_v2 import require_enabled

        require_enabled()
        if await pins.pin(session, walk_id, entry_id) is not None:
            policy = repo.PIN_POLICY
    if not settings.walk_entry_context_enabled:
        return {"entry_id": row.id, "revision": row.revision, "status": "disabled", "sources": []}
    jobs, latest = (
        await repo.current(session, row, policy=policy) if v2 else await repo.current(session, row)
    )
    return {
        "entry_id": row.id,
        "revision": row.revision,
        "status": "tracked" if jobs else "not_requested",
        "sources": [
            {
                "tag": job.tag,
                "state": job.state,
                "attempts": job.attempts,
                "failure_reason": "attempts_exhausted" if job.state == "failed" else None,
                "envelope": latest[job.id].envelope if job.id in latest else None,
            }
            for job in jobs
        ],
    }


async def take(factory):
    async with factory() as session:
        job = await repo.claim(session, datetime.now(UTC))
        if job is None:
            return None
        row = await entries.get_entry(session, job.walk_id, job.entry_id)
        valid = row is not None and row.payload is not None and row.revision == job.revision
        if not valid:
            job.state, job.lease_token, job.lease_until = "cancelled", None, None
        ticket = {
            "id": job.id,
            "walk_id": job.walk_id,
            "entry_id": job.entry_id,
            "revision": job.revision,
            "policy": job.policy_version,
            "tag": job.tag,
            "token": job.lease_token,
            "attempt": job.attempts,
            "content": dict(row.payload) if valid else None,
        }
        if valid and job.policy_version == repo.PIN_POLICY:
            from daengs_backend.repositories import walk_entry_v2 as pins

            sidecar = await pins.pin(session, job.walk_id, job.entry_id)
            if sidecar is None or (sidecar.payload and sidecar.payload["state"] == "provisional"):
                job.state, job.lease_token, job.lease_until = "cancelled", None, None
                ticket["token"] = None
            else:
                ticket["content"].update(pin=sidecar.payload, pin_revision=sidecar.pin_revision)
        await session.commit()
        return ticket


async def finish(factory, ticket, result):
    async with factory() as session:
        # Same lock order as entry writers: walk first, then queue row.
        walk = await session.scalar(
            select(Walk.id).where(Walk.id == ticket["walk_id"]).with_for_update()
        )
        if walk is None:
            return False
        job = await repo.lock_job(session, ticket["id"])
        if (
            job is None
            or job.state != "running"
            or job.lease_token != ticket["token"]
            or job.lease_until <= datetime.now(UTC)
        ):
            return False
        row = await entries.get_entry(session, ticket["walk_id"], ticket["entry_id"])
        if row is None or row.payload is None or row.revision != ticket["revision"]:
            job.state, job.lease_token, job.lease_until = "cancelled", None, None
            await session.commit()
            return False
        content = ticket["content"]
        envelope_id = uuid.uuid4()
        envelope = {
            "id": str(envelope_id),
            "schema_version": "walk-entry-context-v1",
            "target": {
                "store": "walk_entry",
                "walk_id": str(job.walk_id),
                "id": str(job.entry_id),
                "revision": job.revision,
                "event_at": content["recorded_at"],
                "location": content.get("location"),
            },
            "tags": [job.tag],
            "status": result.status,
            "reason": result.reason,
            "provenance": {
                "provider": "place-search" if job.tag == "space.facility" else "unconfigured",
                "operation": "/v2/places/search" if job.tag == "space.facility" else None,
                "retrieved_at": result.retrieved_at,
                "temporal_basis": "lookup_snapshot" if result.retrieved_at else "unknown",
                "policy_version": job.policy_version,
            },
            "payload": result.payload,
            "payload_sha256": digest(result.payload) if result.payload is not None else None,
        }
        if job.policy_version == repo.PIN_POLICY:
            envelope["schema_version"] = "walk-entry-context-v2"
            envelope["target"].update(pin=content.get("pin"), pin_revision=content["pin_revision"])
            envelope["provenance"]["location_basis"] = (
                content["pin"]["method"] if content.get("pin") else "original_location"
            )
        session.add(
            WalkEntryContextEnvelope(
                id=envelope_id,
                job_id=job.id,
                attempt=job.attempts,
                created_at=datetime.now(UTC),
                envelope=envelope,
            )
        )
        job.state = (
            ("pending" if job.attempts < 3 else "failed") if result.retryable else "completed"
        )
        job.available_at = datetime.now(UTC) + timedelta(seconds=30 * 2 ** (job.attempts - 1))
        job.lease_token, job.lease_until = None, None
        await session.commit()
        return True


async def process(factory, *, collector=collect, limit=12):
    if not settings.walk_entry_context_enabled:
        return 0
    if not 1 <= limit <= 24:
        raise ValueError("batch size must be 1..24")
    completed = 0
    for _ in range(limit):
        ticket = await take(factory)
        if ticket is None:
            break
        if ticket["token"] is None:
            continue
        # No open session during provider I/O; crashed attempts are reclaimed after lease expiry.
        result = await collector(ticket["tag"], ticket["content"])
        completed += await finish(factory, ticket, result)
    return completed
