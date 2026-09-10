"""Bounded demand from current entry revisions; completed contexts are never reopened."""

from datetime import timedelta

from sqlalchemy import and_, case, or_, select, update

from daengs_backend.models.walk import Walk
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope as Envelope
from daengs_backend.models.walk_entry_context import WalkEntryContextJob as Job
from daengs_backend.models.walk_entry_v2 import WalkEntryPin as Pin
from daengs_backend.repositories.walk_entry_context import PIN_POLICY, POLICY

TAGS = ("space.park", "space.commerce", "space.river")


async def pending_and_recent(session, now, *, limit=3000):
    # No notes/user IDs leave this DAO. Current pin supersedes legacy original location.
    rows = await session.execute(
        select(Job.id, Job.tag, Job.state, WalkEntry.payload["location"], Pin.payload)
        .join(WalkEntry, and_(WalkEntry.walk_id == Job.walk_id, WalkEntry.id == Job.entry_id))
        .join(Walk, Walk.id == Job.walk_id)
        .outerjoin(Pin, and_(Pin.walk_id == Job.walk_id, Pin.entry_id == Job.entry_id))
        .where(
            Job.tag.in_(TAGS),
            Job.revision == WalkEntry.revision,
            WalkEntry.payload.is_not(None),
            Job.state != "cancelled",
            or_(
                and_(Job.policy_version == POLICY, Pin.walk_id.is_(None)),
                and_(Job.policy_version == PIN_POLICY, Pin.walk_id.is_not(None)),
            ),
            or_(Job.state.in_(("pending", "running")), Walk.started_at >= now - timedelta(days=30)),
        )
        .order_by(
            case((Job.state.in_(("pending", "running")), 0), else_=1),
            Walk.started_at.desc(),
            Job.id,
        )
        .limit(limit)
    )
    result = []
    for job_id, tag, state, location, pin in rows:
        if pin and pin.get("state") == "provisional":
            continue
        point = pin.get("point") if pin else location
        if point is not None:
            result.append(
                {
                    "id": job_id,
                    "tag": tag,
                    "state": state,
                    "point": {"lat": point["lat"], "lng": point["lng"]},
                }
            )
    return result


async def wake_ready(session, ids, now):
    if not ids:
        return 0
    # Only a current pending retry whose latest attempt waited for a catalog is expedited.
    result = await session.execute(
        update(Job)
        .where(
            Job.id.in_(ids),
            Job.state == "pending",
            Job.available_at > now,
            select(Envelope.id)
            .where(
                Envelope.job_id == Job.id,
                Envelope.attempt == Job.attempts,
                Envelope.envelope["reason"].astext == "catalog_preparing",
            )
            .exists(),
            select(WalkEntry.id)
            .where(
                WalkEntry.walk_id == Job.walk_id,
                WalkEntry.id == Job.entry_id,
                WalkEntry.revision == Job.revision,
                WalkEntry.payload.is_not(None),
            )
            .exists(),
        )
        .values(available_at=now)
    )
    return result.rowcount
