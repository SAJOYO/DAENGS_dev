"""Validate cached measurements against a current, sealed analysis reference."""

from dataclasses import asdict

from daengs_backend.services.activity_core.sessions import SessionSource
from daengs_backend.services.activity_core.walk import (
    AnalysisVersions,
    WalkContribution,
    WalkMetrics,
    WalkSelection,
    WalkStatSource,
    project_walks,
)


def contribution_for(source, revision, *, identity, expected_versions):
    projection = project_walks(
        [WalkSelection(source.session.owner_id, source.session.server_session_id, 1, source)],
        identity=identity,
        expected_versions=expected_versions,
    )
    return WalkContribution(source, revision, projection.contributions[0].exclusion_reason)


def cached_contribution(walk, head, reference, *, identity, expected_versions):
    """Return None for legacy/incompatible cache; the caller must decode the real source."""
    cached = head.contribution
    if not isinstance(cached, dict) or (
        cached.get("source_fingerprint") != reference.source_fingerprint
        or cached.get("statistics_version") != identity.statistics_version
        or cached.get("generation_id") != identity.generation_id
    ):
        return None
    try:
        versions = AnalysisVersions(
            reference.facts_record_version,
            reference.calculation_version,
            reference.receipt_version,
            reference.capsule_version,
        )
        if cached.get("versions") != asdict(versions):
            return None
        measurements = cached["metrics"]
        metrics = WalkMetrics(**measurements) if measurements is not None else None
        observed = reference.observed_s > 0
        expected_metrics = (
            WalkMetrics(
                reference.moving_distance_m,
                reference.moving_s,
                reference.stop_count,
                reference.stop_s,
            )
            if observed
            else None
        )
        if metrics != expected_metrics:
            return None
        source = WalkStatSource(
            SessionSource(
                "WALK",
                str(walk.app_user_id),
                str(walk.client_session_id),
                str(walk.id),
                int(walk.started_at.timestamp() * 1000),
                frozenset(str(p) for p in walk.pet_ids),
            ),
            int(walk.ended_at.timestamp() * 1000),
            str(reference.id),
            reference.input_fingerprint,
            versions,
            "analysis-receipt:" + str(reference.id),
            reference.evidence_origin,
            metrics,
            None if metrics is not None else "no_observed_intervals",
        )
        result = contribution_for(
            source, head.processed_revision, identity=identity, expected_versions=expected_versions
        )
        if cached["exclusion_reason"] != result.exclusion_reason:
            return None
        return result
    except (KeyError, TypeError, ValueError):
        return None
