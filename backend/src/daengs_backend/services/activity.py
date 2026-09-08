"""Activity source selection, processing and owner-scoped reads. No external I/O."""

from dataclasses import asdict

from daengs_backend.config import settings
from daengs_backend.models.activity import (
    ActivityAccount,
    ActivitySeason,
    ActivitySessionLink,
    ActivityWalkHead,
)
from daengs_backend.models.territory_claim import TerritoryClaimSession
from daengs_backend.repositories import activity as repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.services import activity_game
from daengs_backend.services.activity_core.common import ProjectionIdentity
from daengs_backend.services.activity_core.sessions import SessionSource, resolve_session_links
from daengs_backend.services.activity_core.territory import HoldingPeriod, holding_time
from daengs_backend.services.activity_core.walk import (
    AnalysisVersions,
    WalkContribution,
    WalkMetrics,
    WalkProjection,
    WalkSelection,
    WalkStatSource,
    project_walks,
    summarize_walks,
)
from daengs_backend.services.walk_analysis import decode_analysis_model
from daengs_walk.capsule import CAPSULE_VERSION
from daengs_walk.contracts import (
    MEASUREMENT_RECEIPT_VERSION,
    WALK_CALCULATION_VERSION,
    WALK_FACTS_RECORD_VERSION,
)

IDENTITY = ProjectionIdentity("dev-activity.v1")
VERSIONS = AnalysisVersions(
    WALK_FACTS_RECORD_VERSION,
    WALK_CALCULATION_VERSION,
    MEASUREMENT_RECEIPT_VERSION,
    CAPSULE_VERSION,
)


class ActivityNotFound(LookupError):
    pass


class ActivityDisabled(RuntimeError):
    pass


def enabled():
    if not settings.activity_game_enabled:
        raise ActivityDisabled("activity_disabled")


async def remove_owner(db, owner):
    if settings.activity_game_enabled:
        await repo.barrier(db)
        await repo.remove_owner(db, owner)


async def rebuild(db):
    """Discard only projection progress; never replay score awards or change source heads."""
    enabled()
    await repo.barrier(db)
    await repo.requeue(db)
    await db.commit()


def millis(at):
    return int(at.timestamp() * 1000)


def walk_source(walk, analysis):
    if analysis.walk_id != walk.id:
        raise ValueError("analysis_walk_mismatch")
    decoded = decode_analysis_model(analysis)
    if analysis.capsule is None or walk.analysis_state != "derived":
        raise ValueError("analysis_not_sealed")
    facts, receipt = decoded.facts, decoded.measurement_receipt
    observed = receipt.canonical_segment_time_s > 0
    return WalkStatSource(
        SessionSource(
            "WALK",
            str(walk.app_user_id),
            str(walk.client_session_id),
            str(walk.id),
            millis(walk.started_at),
            frozenset(str(p) for p in walk.pet_ids),
        ),
        millis(walk.ended_at),
        str(analysis.id),
        analysis.input_fingerprint,
        AnalysisVersions(
            analysis.facts_record_version,
            analysis.calculation_version,
            analysis.receipt_version,
            analysis.capsule.capsule_version,
        ),
        "analysis-receipt:" + str(analysis.id),
        facts.evidence_origin,
        WalkMetrics(facts.moving_distance_m, facts.moving_s, facts.stop_count, facts.stop_s)
        if observed
        else None,
        None if observed else "no_observed_intervals",
    )


async def record_walk(db, walk, analysis=None):
    if not settings.activity_game_enabled:
        return
    await repo.link(db, walk.app_user_id, walk.client_session_id, walk_id=walk.id)
    if analysis is None:
        return
    walk_source(walk, analysis)  # require real sealed/consistent evidence before queuing
    head = await db.get(ActivityWalkHead, walk.id)
    if head is None:
        db.add(ActivityWalkHead(walk_id=walk.id, analysis_id=analysis.id, revision=1))
    elif head.analysis_id != analysis.id:
        head.analysis_id, head.revision = analysis.id, head.revision + 1


async def record_game(db, game):
    if settings.activity_game_enabled:
        await repo.link(db, game.app_user_id, game.client_session_id, game_session_id=game.id)


def period_value(row):
    return HoldingPeriod(
        (row.season_id, str(row.id), row.site_id),
        str(row.pet_id),
        row.site_id,
        row.started_ms,
        row.ended_ms,
        row.verified_from_ms,
        row.origin,
        row.takeover,
        row.start_order,
        None,
        str(row.game_session_id) if row.game_session_id else None,
        str(row.claim_id) if row.claim_id else None,
    )


def period_statistics(periods, season):
    spans = [
        holding_time(period_value(p), from_ms=season.coverage_start_ms, to_ms=season.confirmed_ms)
        for p in periods
    ]
    changes = []
    for p in periods:
        changes.append((p.started_ms, p.start_order, 1, 1))
        if p.ended_ms is not None:
            changes.append((p.ended_ms, p.end_order, 0, -1))
    count = peak = 0
    for _, _, _, delta in sorted(changes):
        count += delta
        peak = max(count, peak)
    return {
        "statistics_version": IDENTITY.statistics_version,
        "generation_id": IDENTITY.generation_id,
        "coverage_start_ms": season.coverage_start_ms,
        "confirmed_through_ms": season.confirmed_ms,
        "acquisition_count": sum(p.origin == "ACQUIRED" for p in periods),
        "takeover_count": sum(p.takeover for p in periods),
        "held_site_ms": sum(x for x, _ in spans),
        "verified_held_site_ms": sum(x for _, x in spans),
        "owned_site_count": count,
        "peak_owned_site_count": peak,
    }


async def process_pending(db, *, limit=100):
    enabled()
    if not 0 < limit <= 1000:
        raise ValueError("invalid_limit")
    try:
        await repo.barrier(db)
        await activity_game.close_if_due(db, activity_game.now_ms())
        completed = 0
        for head in await repo.pending_walks(db, limit):
            walk, analysis = await repo.walk_analysis(db, head.walk_id, head.analysis_id)
            source = walk_source(walk, analysis)
            projection = project_walks(
                [WalkSelection(str(walk.app_user_id), str(walk.id), 1, source)],
                identity=IDENTITY,
                expected_versions=VERSIONS,
            )
            row = projection.contributions[0]
            # Only measurements/version/exclusion are cached. No owner/pet IDs in JSON.
            head.contribution = {
                "metrics": asdict(source.metrics) if source.metrics else None,
                "versions": asdict(source.versions),
                "exclusion_reason": row.exclusion_reason,
                "statistics_version": IDENTITY.statistics_version,
                "generation_id": IDENTITY.generation_id,
            }
            head.processed_analysis_id = head.analysis_id
            head.processed_revision = head.revision
            completed += 1
        accounts = await repo.pending_accounts(db, limit)
        for row in accounts:
            season = await db.get(ActivitySeason, row.season_id)
            row.statistics = period_statistics(
                await repo.periods(db, row.season_id, row.pet_id), season
            )
            row.processed_revision = row.revision
            completed += 1
        await db.commit()
        return completed
    except Exception:
        await db.rollback()
        raise


async def links(db, owner, client_id):
    enabled()
    await repo.barrier(db)
    link = await db.get(ActivitySessionLink, (owner, client_id))
    if link is None:
        raise ActivityNotFound
    sources = []
    if link.walk_id:
        walk = await walk_repo.get_owned(db, owner, link.walk_id)
        if walk:
            sources.append(
                SessionSource(
                    "WALK",
                    str(owner),
                    str(client_id),
                    str(walk.id),
                    millis(walk.started_at),
                    frozenset(str(p) for p in walk.pet_ids),
                )
            )
    if link.game_session_id:
        game = await db.get(TerritoryClaimSession, link.game_session_id)
        if game and game.app_user_id == owner:
            sources.append(
                SessionSource(
                    "GAME",
                    str(owner),
                    str(client_id),
                    str(game.id),
                    millis(game.started_at),
                    frozenset(str(p) for p in game.pet_ids),
                )
            )
    result = resolve_session_links(sources)
    if not result:
        raise ActivityNotFound
    row = result[0]
    return {
        "client_session_id": client_id,
        "walk_id": link.walk_id,
        "game_session_id": link.game_session_id,
        "status": row.status,
        "conflicts": row.conflicts,
    }


async def walk_summary(db, owner, from_ms, to_ms, pet_id=None):
    enabled()
    await repo.barrier(db)
    if pet_id and await pet_repo.owned_ids(db, owner, [pet_id]) != {pet_id}:
        raise ActivityNotFound
    walks = await repo.walks_in_window(db, owner, from_ms, to_ms)
    contributions = []
    pending = 0
    refs = []
    for walk in walks:
        if not from_ms <= millis(walk.ended_at) < to_ms or (pet_id and pet_id not in walk.pet_ids):
            continue
        head = await db.get(ActivityWalkHead, walk.id)
        if (
            head is None
            or head.processed_revision != head.revision
            or head.contribution is None
            or head.processed_analysis_id is None
        ):
            pending += 1
            continue
        loaded_walk, analysis = await repo.walk_analysis(db, walk.id, head.processed_analysis_id)
        source = walk_source(loaded_walk, analysis)
        contributions.append(
            WalkContribution(source, head.processed_revision, head.contribution["exclusion_reason"])
        )
        refs.append(
            {
                "walk_id": walk.id,
                "analysis_id": head.processed_analysis_id,
                "revision": head.processed_revision,
            }
        )
    result = summarize_walks(
        WalkProjection(IDENTITY, VERSIONS, tuple(contributions), ()),
        owner_id=str(owner),
        from_ms=from_ms,
        to_ms=to_ms,
        pet_id=str(pet_id) if pet_id else None,
    )
    values = asdict(result)
    values.pop("sources")
    return {
        **values,
        "sources": refs,
        "pending_walk_count": pending,
        "status": "PENDING" if pending else "READY",
    }


async def current_season(db):
    enabled()
    await repo.barrier(db)
    season = await repo.active_season(db)
    now = activity_game.now_ms()
    if season is None or not season.starts_ms <= now < season.ends_ms:
        return {"server_now_ms": now, "season": None}
    return {
        "server_now_ms": now,
        "season": {
            "id": season.id,
            "starts_ms": season.starts_ms,
            "ends_ms": season.ends_ms,
            "policy_version": season.rules["version"],
        },
    }


async def territory_summary(db, owner, season_id, pet_id):
    enabled()
    await repo.barrier(db)
    if await pet_repo.owned_ids(db, owner, [pet_id]) != {pet_id}:
        raise ActivityNotFound
    season = await db.get(ActivitySeason, season_id)
    if season is None:
        raise ActivityNotFound
    row = await db.get(ActivityAccount, (season_id, pet_id))
    if row is None:
        return {
            "season_id": season_id,
            "pet_id": pet_id,
            "status": "READY",
            "statistics": period_statistics([], season),
            "score": None,
            "sources": [],
        }
    periods = await repo.periods(db, season_id, pet_id)
    status = (
        "PENDING"
        if row.statistics is None
        else (
            "READY"
            if row.processed_revision == row.revision
            and row.statistics["confirmed_through_ms"] == season.confirmed_ms
            else "STALE"
        )
    )
    return {
        "season_id": season_id,
        "pet_id": pet_id,
        "status": status,
        "source_revision": row.revision,
        "processed_revision": row.processed_revision,
        "statistics": row.statistics,
        "score": row.final_score or row.score,
        "score_as_of_ms": (row.final_score or row.score)["last_ms"],
        "sources": [
            {
                "period_id": p.id,
                "site_id": p.site_id,
                "claim_id": p.claim_id,
                "game_session_id": p.game_session_id,
            }
            for p in periods
        ],
    }
