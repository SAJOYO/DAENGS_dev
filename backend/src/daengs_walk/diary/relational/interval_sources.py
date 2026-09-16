"""Freeze canonical GPS and unsliced detector claims once per generation."""

from copy import deepcopy
from itertools import pairwise
from typing import Literal

from pydantic import Field, model_validator

from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.diary.relational.relation_flow_contracts import FlowPolicy
from daengs_walk.value_contracts import Instant, ValueContract


class IntervalSources(ValueContract):
    version: Literal["interval-sources-v1"] = "interval-sources-v1"
    source_revision: str
    route_revision: str | None
    started_at: Instant
    ended_at: Instant
    points: tuple[WalkEvidencePoint, ...] = ()
    # Canonical edges, not straight lines invented between all accepted fixes.
    edges: tuple[tuple[int, int, int], ...] = ()
    claims: tuple[dict, ...] = ()
    movement_revision: str | None = None
    policies: dict = Field(default_factory=dict)
    flow_policy: FlowPolicy = Field(default_factory=FlowPolicy)

    @model_validator(mode="after")
    def canonical_bindings(self):
        keys = {(p.chain_index, p.client_seq): p for p in self.points}
        if len(keys) != len(self.points) or any(a.at >= b.at for a, b in pairwise(self.points)):
            raise ValueError("interval fixes must be unique and chronological")
        if self.started_at > self.ended_at or any(
            not self.started_at <= p.at <= self.ended_at for p in self.points
        ):
            raise ValueError("interval fixes exceed session")
        consecutive = {
            (a.chain_index, a.client_seq, b.client_seq)
            for a, b in pairwise(self.points)
            if a.chain_index == b.chain_index
        }
        if len(set(self.edges)) != len(self.edges) or not set(self.edges) <= consecutive:
            raise ValueError("interval edges differ from canonical fixes")
        if self.points and not self.route_revision:
            raise ValueError("interval fixes lack route revision")
        if self.claims and not self.movement_revision:
            raise ValueError("interval claims lack movement revision")
        if len({c["id"] for c in self.claims}) != len(self.claims):
            raise ValueError("duplicate interval claim")
        return self


def freeze_interval_sources(source, route, route_revision, catalog):
    return IntervalSources(
        source_revision=source.revision(),
        route_revision=route_revision,
        started_at=source.started_at,
        ended_at=source.ended_at,
        points=route.evidence.accepted_points if route else (),
        edges=tuple(
            (s.chain_index, s.a.client_seq, s.b.client_seq) for s in route.evidence.segments
        )
        if route
        else (),
        claims=deepcopy(catalog.claims) if catalog else (),
        movement_revision=catalog.source_revision if catalog else None,
        policies=deepcopy(catalog.policies) if catalog else {},
    )


def continuous_points(sources):
    """Yield a local continuity key; even a short rejected edge breaks the series."""
    edges = set(sources.edges)
    previous, block = None, 0
    for point in sources.points:
        if previous and (
            previous.chain_index != point.chain_index
            or (point.chain_index, previous.client_seq, point.client_seq) not in edges
            or (point.at - previous.at).total_seconds() > sources.flow_policy.max_gap_seconds
            or previous.accuracy_m is None
            or point.accuracy_m is None
        ):
            block += 1
        if point.accuracy_m is not None:
            yield point, block
        previous = point
