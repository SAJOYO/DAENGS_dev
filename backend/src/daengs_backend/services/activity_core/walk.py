"""Project explicitly selected, sealed walk analyses; never recompute GPS facts."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .common import ProjectionIdentity, identifier, natural, require
from .sessions import SessionSource


@dataclass(frozen=True)
class AnalysisVersions:
    facts: int
    calculation: int
    receipt: int
    capsule: int

    def __post_init__(self):
        for value in (self.facts, self.calculation, self.receipt, self.capsule):
            natural(value)
            require(value > 0, "invalid_analysis_version")


@dataclass(frozen=True)
class WalkMetrics:
    moving_distance_m: int
    moving_s: int
    stop_count: int
    stop_s: int

    def __post_init__(self):
        for value in (self.moving_distance_m, self.moving_s, self.stop_count, self.stop_s):
            natural(value)


@dataclass(frozen=True)
class WalkStatSource:
    """Adapter attests analysis/capsule sealing and receipt-based observability.

    observation_reason references a measurement limitation, not a behavior judgement.
    Participants come from the authorized walk relation, not duplicated dog sensors.
    """

    session: SessionSource
    ended_ms: int
    analysis_id: str
    input_fingerprint: str
    versions: AnalysisVersions
    receipt_id: str
    evidence_origin: Literal["device", "mock", "mixed", "unknown"]
    metrics: WalkMetrics | None
    observation_reason: str | None = None

    def __post_init__(self):
        require(self.session.kind == "WALK", "walk_source_required")
        natural(self.ended_ms)
        require(self.ended_ms >= self.session.started_ms, "walk_time_reversed")
        for value in (self.analysis_id, self.input_fingerprint, self.receipt_id):
            identifier(value)
        require(self.evidence_origin in {"device", "mock", "mixed", "unknown"}, "invalid_origin")
        if self.metrics is None:
            identifier(self.observation_reason)
        else:
            require(self.observation_reason is None, "ambiguous_observation")
            # Canonical Facts round seconds; accept its existing wall-time convention.
            wall_s = round((self.ended_ms - self.session.started_ms) / 1000)
            require(self.metrics.moving_s + self.metrics.stop_s <= wall_s, "invalid_walk_duration")


@dataclass(frozen=True)
class WalkSelection:
    """Ordered head selection, not all analyses. None withdraws this walk terminally.

    Revision is a contiguous per-owner/walk selection stream starting at one.
    Participant corrections arrive as a new selection referencing the same analysis.
    """

    owner_id: str
    walk_id: str
    revision: int
    source: WalkStatSource | None

    def __post_init__(self):
        identifier(self.owner_id)
        identifier(self.walk_id)
        natural(self.revision)
        require(self.revision > 0, "invalid_selection_revision")
        if self.source:
            require(
                (self.owner_id, self.walk_id)
                == (self.source.session.owner_id, self.source.session.server_session_id),
                "walk_selection_identity_mismatch",
            )


@dataclass(frozen=True)
class WalkContribution:
    source: WalkStatSource
    selection_revision: int
    exclusion_reason: str | None


@dataclass(frozen=True)
class WalkProjection:
    identity: ProjectionIdentity
    expected_versions: AnalysisVersions
    contributions: tuple[WalkContribution, ...]
    selections: tuple[WalkSelection, ...]


def project_walks(
    selections: Iterable[WalkSelection],
    *,
    identity: ProjectionIdentity,
    expected_versions: AnalysisVersions,
) -> WalkProjection:
    unique: dict[tuple[str, str, int], WalkSelection] = {}
    for selection in selections:
        key = (selection.owner_id, selection.walk_id, selection.revision)
        require(key not in unique or unique[key] == selection, "selection_payload_conflict")
        unique[key] = selection
    ordered = tuple(unique[key] for key in sorted(unique))
    heads: dict[tuple[str, str], WalkSelection] = {}
    analyses = {}
    walk_identities = {}
    client_walks = {}
    walk_owners = {}
    for selection in ordered:
        key = (selection.owner_id, selection.walk_id)
        require(
            selection.walk_id not in walk_owners
            or walk_owners[selection.walk_id] == selection.owner_id,
            "walk_identity_conflict",
        )
        walk_owners[selection.walk_id] = selection.owner_id
        previous = heads.get(key)
        require(selection.revision == (previous.revision + 1 if previous else 1), "selection_gap")
        require(previous is None or previous.source is not None, "walk_already_withdrawn")
        source = selection.source
        if source:
            # Immutable analysis identity excludes mutable participant relations.
            evidence = (
                key,
                source.session.client_walk_session_id,
                source.session.started_ms,
                source.ended_ms,
                source.input_fingerprint,
                source.versions,
                source.receipt_id,
                source.evidence_origin,
                source.metrics,
                source.observation_reason,
            )
            require(
                source.analysis_id not in analyses or analyses[source.analysis_id] == evidence,
                "analysis_payload_conflict",
            )
            analyses[source.analysis_id] = evidence
            client_key = (source.session.owner_id, source.session.client_walk_session_id)
            walk_identity = (client_key, source.session.started_ms, source.ended_ms)
            require(
                selection.walk_id not in walk_identities
                or walk_identities[selection.walk_id] == walk_identity,
                "walk_identity_conflict",
            )
            walk_identities[selection.walk_id] = walk_identity
            require(
                client_key not in client_walks or client_walks[client_key] == selection.walk_id,
                "client_walk_conflict",
            )
            client_walks[client_key] = selection.walk_id
        heads[key] = selection
    contributions = []
    for selection in heads.values():
        source = selection.source
        if source is None:
            continue
        reason = None
        if source.versions != expected_versions:
            reason = "unsupported_analysis_versions"
        elif source.evidence_origin != "device":
            reason = "non_device_evidence"
        elif source.metrics is None:
            reason = source.observation_reason
        contributions.append(WalkContribution(source, selection.revision, reason))
    return WalkProjection(identity, expected_versions, tuple(contributions), ordered)


@dataclass(frozen=True)
class WalkSummary:
    identity: ProjectionIdentity
    expected_versions: AnalysisVersions
    owner_id: str
    pet_id: str | None
    from_ms: int
    to_ms: int
    recorded_walk_count: int
    observed_walk_count: int
    moving_distance_m: int | None
    moving_s: int | None
    stop_count: int | None
    stop_s: int | None
    avg_speed_mps: float | None
    exclusions: tuple[tuple[str, int], ...]
    sources: tuple[WalkContribution, ...]
    window_basis: str = "walk_end"


def summarize_walks(
    projection: WalkProjection,
    *,
    owner_id: str,
    from_ms: int,
    to_ms: int,
    pet_id: str | None = None,
) -> WalkSummary:
    identifier(owner_id)
    if pet_id is not None:
        identifier(pet_id)
    natural(from_ms)
    natural(to_ms)
    require(from_ms <= to_ms, "invalid_window")
    rows = tuple(
        row
        for row in projection.contributions
        if row.source.session.owner_id == owner_id
        and (pet_id is None or pet_id in row.source.session.pet_ids)
        and from_ms <= row.source.ended_ms < to_ms
    )
    observed = [row.source.metrics for row in rows if row.exclusion_reason is None]
    excluded: dict[str, int] = {}
    for row in rows:
        if row.exclusion_reason:
            excluded[row.exclusion_reason] = excluded.get(row.exclusion_reason, 0) + 1
    distance = sum(m.moving_distance_m for m in observed) if observed else None
    moving = sum(m.moving_s for m in observed) if observed else None
    stops = sum(m.stop_count for m in observed) if observed else None
    stopped = sum(m.stop_s for m in observed) if observed else None
    return WalkSummary(
        projection.identity,
        projection.expected_versions,
        owner_id,
        pet_id,
        from_ms,
        to_ms,
        len(rows),
        len(observed),
        distance,
        moving,
        stops,
        stopped,
        distance / moving if moving else None,
        tuple(sorted(excluded.items())),
        rows,
    )
