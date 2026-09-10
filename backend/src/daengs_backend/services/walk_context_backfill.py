"""Explicit missing-public-context recovery. Never writes entries or storyboards."""

from collections import Counter
from datetime import UTC, datetime

from daengs_backend.config import settings
from daengs_backend.repositories import walk_context_backfill as repo
from daengs_backend.repositories.walk_entry_context import PIN_POLICY, POLICY
from daengs_backend.services.walk_area_catalog import area
from daengs_backend.services.walk_catalog_regions import automatic_ready
from daengs_backend.services.walk_entry_context_source import digest

BACKFILL_POLICY = "walk-public-missing-v1"
TAGS = ("space.address", "space.commerce", "space.park", "space.river")
USABLE = {"known", "partial", "empty"}
MISSING = {"not_requested", "unavailable"}
MAX_SOURCES = 40


class StaleBackfillPlan(ValueError):
    pass


def enabled():
    return (
        settings.walk_entry_context_enabled
        and settings.walk_public_context_enabled
        and settings.walk_area_context_enabled
        and settings.walk_entry_v2_enabled
    )


def location_reason(row, sidecar):
    if row.payload is None:
        return "deleted"
    pin = sidecar.payload if sidecar else None
    if pin and pin.get("state") == "provisional":
        return "provisional_pin"
    point = pin.get("point") if pin else row.payload.get("location")
    if point is None:
        return "no_location"
    try:
        area(point, 1200)
    except (ValueError, KeyError, TypeError, OverflowError):
        return "unsupported_location"
    return None


def disposition(job, envelope):
    if job is None:
        return "missing_job"
    if job.backfill_policy == BACKFILL_POLICY:
        return "already_requested"
    if job.state in {"pending", "running", "cancelled"}:
        return job.state
    status = envelope.envelope.get("status") if envelope else None
    if status in USABLE:
        return "usable"
    if status in MISSING or (job.state == "failed" and envelope is None):
        return "missing_result"
    return "unsupported_result"


async def build_plan(session, walk_ids):
    slots, skipped = [], Counter()
    for walk_id in sorted(set(walk_ids)):
        if await repo.lock_walk(session, walk_id) is None:
            skipped["walk_missing"] += 1
            continue
        # Current diary GET invalidates a board on background changes. Keep it untouched
        # until explicit board-regeneration policy is connected, including running/failed rows.
        if await repo.has_board(session, walk_id):
            skipped["stored_board"] += 1
            continue
        records = await repo.records(session, walk_id)
        if len(records) > 600:
            skipped["entry_limit"] += 1
            continue
        jobs = {
            (j.entry_id, j.revision, j.policy_version, j.tag): (j, e)
            for j, e in await repo.jobs(session, walk_id)
        }
        for row, pin in records:
            reason = location_reason(row, pin)
            if reason:
                skipped[reason] += 1
                continue
            policy = PIN_POLICY if pin else POLICY
            for tag in TAGS:
                job, envelope = jobs.get((row.id, row.revision, policy, tag), (None, None))
                reason = disposition(job, envelope)
                slot = {
                    "walk_id": str(walk_id),
                    "entry_id": str(row.id),
                    "revision": row.revision,
                    "pin_revision": pin.pin_revision if pin else None,
                    "location_revision": digest(
                        {
                            "location": row.payload.get("location"),
                            "pin": pin.payload if pin else None,
                        }
                    ),
                    "policy": policy,
                    "tag": tag,
                    "reason": reason,
                    "job_id": str(job.id) if job else None,
                    "round": job.collection_round if job else None,
                    "state": job.state if job else None,
                    "attempts": job.attempts if job else None,
                    "envelope_id": str(envelope.id) if envelope else None,
                }
                slots.append(slot)
    return {
        "policy": BACKFILL_POLICY,
        "walk_ids": sorted(str(w) for w in set(walk_ids)),
        "slots": slots,
        "skipped": dict(sorted(skipped.items())),
    }


async def run(
    factory,
    *,
    walk_ids=None,
    since=None,
    until=None,
    after=None,
    limit=1,
    apply=False,
    expected_plan=None,
):
    if not enabled():
        raise ValueError("public context and v2 collection must be enabled")
    if not 1 <= limit <= 10:
        raise ValueError("limit must be 1..10 walks")
    if walk_ids is not None:
        if not 1 <= len(set(walk_ids)) <= 10 or since or until or after:
            raise ValueError("select 1..10 walks or one bounded date window")
    elif (
        since is None
        or until is None
        or since.tzinfo is None
        or until.tzinfo is None
        or since >= until
    ):
        raise ValueError("an explicit timezone-aware date window is required")
    if apply and not expected_plan:
        raise ValueError("apply requires the exact dry-run plan digest")
    ready = automatic_ready() and all(
        secret.get_secret_value().strip()
        for secret in (settings.walk_sgis_key, settings.walk_sgis_secret)
    )
    if apply and not ready:
        raise ValueError("automatic catalogs and SGIS credentials must be configured")
    async with factory() as session:
        selected = walk_ids
        more = False
        if selected is None:
            found = await repo.walk_ids(session, since=since, until=until, after=after, limit=limit)
            selected, more = found[:limit], len(found) > limit
        plan = await build_plan(session, selected)
        plan_digest = digest(plan)
        if apply and plan_digest != expected_plan:
            raise StaleBackfillPlan("records or collection state changed; preview again")
        eligible = [s for s in plan["slots"] if s["reason"] in {"missing_job", "missing_result"}]
        if apply:
            for slot in eligible[:MAX_SOURCES]:
                await repo.schedule(session, slot, datetime.now(UTC), BACKFILL_POLICY)
            await session.commit()
        else:
            await session.rollback()
        return {
            "format": "walk-context-backfill-v1",
            "policy": BACKFILL_POLICY,
            "applied": apply,
            "ready_to_apply": bool(ready),
            "plan_digest": plan_digest,
            "walk_count": len(selected),
            "eligible_sources": len(eligible),
            "batch_sources": min(len(eligible), MAX_SOURCES),
            "scheduled_sources": min(len(eligible), MAX_SOURCES) if apply else 0,
            "by_tag": dict(sorted(Counter(s["tag"] for s in eligible).items())),
            "source_reasons": dict(sorted(Counter(s["reason"] for s in plan["slots"]).items())),
            "skipped": plan["skipped"],
            "has_more": more,
            "next_cursor": str(selected[-1]) if more else None,
        }
