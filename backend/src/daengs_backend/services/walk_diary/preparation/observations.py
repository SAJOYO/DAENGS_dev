"""Bind a diary observation pool to verified stored analysis and its exact uploaded points."""

from dataclasses import dataclass, field

from daengs_backend.schemas.walk import WalkFinalizeRequest
from daengs_backend.services.walk_metrics.analysis import decode_analysis_model
from daengs_backend.services.walk_session.finalize import prepare_finalized_walk
from daengs_walk.contracts import WALK_CALCULATION_VERSION, EvidenceOrigin
from daengs_walk.diary.contracts.input import RouteVersion
from daengs_walk.diary.route.observations import ObservationPool, build_observation_pool
from daengs_walk.evidence import WalkEvidenceBundle, analyze_walk


@dataclass(frozen=True)
class ObservationSource:
    route: RouteVersion
    evidence_origin: EvidenceOrigin = "unknown"
    pool: ObservationPool | None = None
    # Already replayed canonical evidence; private base-board preparation reuses it.
    evidence: WalkEvidenceBundle | None = field(default=None, repr=False)


def _unavailable(reason):
    return ObservationSource(
        RouteVersion(
            status="unavailable",
            analysis_id=None,
            input_fingerprint=None,
            calculation_version=None,
            reason=reason,
        )
    )


def prepare_observation_source(walk, analysis) -> ObservationSource:
    if analysis is None or walk.analysis_state != "derived":
        return _unavailable("analysis_not_finalized")
    if analysis.calculation_version != WALK_CALCULATION_VERSION:
        return _unavailable("unsupported_analysis_version")
    try:
        decoded = decode_analysis_model(analysis)
        if (analysis.walk_id, decoded.facts.started_at, decoded.facts.ended_at) != (
            walk.id,
            walk.started_at,
            walk.ended_at,
        ):
            return _unavailable("analysis_session_mismatch")
        prepared = prepare_finalized_walk(
            walk.points,
            WalkFinalizeRequest(
                expected_point_count=analysis.point_count,
                terminal_client_seq=analysis.terminal_client_seq,
                input_fingerprint=analysis.input_fingerprint,
            ),
        )
        evidence = analyze_walk(walk.id, walk.started_at, walk.ended_at, prepared.points)
        if (evidence.facts, evidence.receipt, evidence.events, evidence.observations) != (
            decoded.facts,
            decoded.measurement_receipt,
            decoded.motion_events,
            decoded.micro_observations,
        ):
            return _unavailable("analysis_replay_mismatch")
        route = RouteVersion(
            status="ready",
            analysis_id=str(analysis.id),
            input_fingerprint=prepared.input_fingerprint.removeprefix("sha256:"),
            calculation_version=analysis.calculation_version,
        )
        return ObservationSource(
            route, evidence.facts.evidence_origin, build_observation_pool(evidence, route), evidence
        )
    except (ValueError, TypeError, KeyError, ArithmeticError):
        # Missing/invalid motion must not erase the user's original records or expose raw GPS.
        return _unavailable("invalid_analysis_source")
