"""Durable queue queries; transactions belong to the service."""

import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope, WalkEntryContextJob

TAGS = ("space.facility", "space.park", "space.river", "environment.weather")
POLICY = "walk-entry-context-v1"


async def enqueue(session, row, now):
    await session.flush()
    await session.execute(
        update(WalkEntryContextJob)
        .where(
            WalkEntryContextJob.walk_id == row.walk_id,
            WalkEntryContextJob.entry_id == row.id,
            WalkEntryContextJob.revision != row.revision,
            WalkEntryContextJob.state.in_(["pending", "running"]),
        )
        .values(state="cancelled", lease_token=None, lease_until=None)
    )
    for tag in TAGS:
        await session.execute(
            insert(WalkEntryContextJob)
            .values(
                id=uuid.uuid4(),
                walk_id=row.walk_id,
                entry_id=row.id,
                revision=row.revision,
                policy_version=POLICY,
                tag=tag,
                state="pending",
                attempts=0,
                available_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["walk_id", "entry_id", "revision", "policy_version", "tag"]
            )
        )


async def claim(session, now):
    row = await session.scalar(
        select(WalkEntryContextJob)
        .where(
            WalkEntryContextJob.policy_version == POLICY,
            or_(
                and_(
                    WalkEntryContextJob.state == "pending", WalkEntryContextJob.available_at <= now
                ),
                and_(
                    WalkEntryContextJob.state == "running", WalkEntryContextJob.lease_until <= now
                ),
            ),
        )
        .order_by(WalkEntryContextJob.available_at, WalkEntryContextJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is not None:
        # A crash on the last attempt is terminal, never an infinite lease-reclaim loop.
        if row.attempts >= 3:
            row.state, row.lease_token, row.lease_until = "failed", None, None
        else:
            row.attempts += 1
            row.state = "running"
            row.lease_token = uuid.uuid4()
            row.lease_until = now + timedelta(seconds=45)
    return row


async def current(session, row):
    jobs = list(
        await session.scalars(
            select(WalkEntryContextJob)
            .where(
                WalkEntryContextJob.walk_id == row.walk_id,
                WalkEntryContextJob.entry_id == row.id,
                WalkEntryContextJob.revision == row.revision,
                WalkEntryContextJob.policy_version == POLICY,
            )
            .order_by(WalkEntryContextJob.tag)
        )
    )
    envelopes = list(
        await session.scalars(
            select(WalkEntryContextEnvelope)
            .where(WalkEntryContextEnvelope.job_id.in_([job.id for job in jobs]))
            .order_by(WalkEntryContextEnvelope.job_id, WalkEntryContextEnvelope.attempt.desc())
        )
    )
    latest = {}
    for envelope in envelopes:
        latest.setdefault(envelope.job_id, envelope)
    return jobs, latest


async def lock_job(session, job_id):
    return await session.scalar(
        select(WalkEntryContextJob).where(WalkEntryContextJob.id == job_id).with_for_update()
    )
