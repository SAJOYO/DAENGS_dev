"""Photo job row locks and bounded recovery scans; services own transactions."""

from sqlalchemy import and_, or_, select

from daengs_backend.models.territory import PHOTO_CLEANUP_BLOCKED_REASON
from daengs_backend.models.territory import TerritoryAttempt as Attempt

TERMINAL = ("VERIFIED", "REJECTED", "FAILED")


async def lock_attempt(session, attempt_id):
    return await session.scalar(
        select(Attempt)
        .where(Attempt.id == attempt_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )


async def due_dispatches(session, now, *, limit):
    return list(
        await session.scalars(
            select(Attempt)
            .where(
                or_(
                    Attempt.status == "VISION_PENDING",
                    and_(
                        Attempt.status.in_(TERMINAL),
                        Attempt.photo_redacted_at.is_(None),
                        Attempt.vision_retry_reason.is_distinct_from(PHOTO_CLEANUP_BLOCKED_REASON),
                    ),
                ),
                Attempt.vision_available_at <= now,
                Attempt.vision_dispatch_after <= now,
                or_(Attempt.vision_lease_until.is_(None), Attempt.vision_lease_until <= now),
            )
            .order_by(Attempt.vision_dispatch_after, Attempt.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
