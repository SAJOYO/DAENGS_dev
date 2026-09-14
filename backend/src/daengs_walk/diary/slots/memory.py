"""Deterministic spatial-memory replay. No acquisition, writer or production switch.

The archive is scoped to one replay. Loaded materials are recomputed at recorded
points; leaving a scope removes only its scene relation, allowing later reentry.
"""

from collections import defaultdict
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from daengs_walk.diary.contracts.input import DiaryContract, Identifier, Instant, Point, digest
from daengs_walk.diary.contracts.slots import PartStamp, SlotPolicy
from daengs_walk.diary.slots.admission import admit
from daengs_walk.diary.slots.sources import evidence
from daengs_walk.diary.slots.space import ROLES
from daengs_walk.diary.space.cases import CASES
from daengs_walk.diary.space.coverage import PreparedFeature
from daengs_walk.diary.space.materials import SpaceMaterials
from daengs_walk.diary.space.policy import (
    SpaceApplication,
    SpacePolicy,
    apply_space,
    claim_identity,
)


class ReplayPoint(DiaryContract):
    seq: int = Field(ge=0)
    at: Instant
    point: Point | None


class SpaceBatch(DiaryContract):
    id: Identifier
    available_seq: int = Field(ge=0)
    expires_at: Instant | None = None
    materials: SpaceMaterials

    @model_validator(mode="after")
    def integrity(self):
        value = self.materials
        if value.dictionary_version != digest(CASES):
            raise ValueError("unknown space dictionary")
        for item in value.materials:
            if item.id != "space:" + digest(item.model_dump(mode="json", exclude={"id"})):
                raise ValueError("material changed after normalization")
            query = item.scope.get("point" if item.source == "commerce" else "query_point")
            if query != value.point.model_dump():
                raise ValueError("material query differs from its batch")
            kinds = {
                "commerce": "query_circle",
                "park": "registered_point",
                "land_cover": "feature_at_query",
            }
            if item.scope.get("kind") != kinds[item.source]:
                raise ValueError("unknown material scope")
            try:
                claim_identity(item)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("invalid material identity") from error
        return self


class SpaceReplay(DiaryContract):
    format: Literal["space-replay-input-v1"] = "space-replay-input-v1"
    policy: SpacePolicy
    points: tuple[ReplayPoint, ...] = Field(min_length=1)
    batches: tuple[SpaceBatch, ...]
    comparison: SlotPolicy = Field(default_factory=SlotPolicy)

    @model_validator(mode="after")
    def ordered(self):
        if any(a.seq >= b.seq or a.at > b.at for a, b in zip(self.points, self.points[1:])):
            raise ValueError("replay points must preserve increasing sequence and time")
        starts = {p.seq: p.at for p in self.points}
        if len({b.id for b in self.batches}) != len(self.batches):
            raise ValueError("batch identifiers must be distinct")
        for batch in self.batches:
            if batch.available_seq not in starts:
                raise ValueError("batch availability must name a recorded point")
            if batch.expires_at is not None and batch.expires_at <= starts[batch.available_seq]:
                raise ValueError("batch deadline must follow its availability")
        return self


class SpaceResident(DiaryContract):
    key: str
    source: str
    case_ids: tuple[str, ...]
    material: dict[str, str]
    relation: dict[str, JsonValue]
    references: tuple[str, ...]


class MemoryDecision(DiaryContract):
    reference: str
    key: str
    application: SpaceApplication


class MemoryChange(DiaryContract):
    key: str
    source: str
    change: Literal["loaded", "retained", "updated", "evicted"]
    reason: str


class SpaceFrame(DiaryContract):
    seq: int
    at: Instant
    point: Point | None
    decisions: tuple[MemoryDecision, ...]
    changes: tuple[MemoryChange, ...]
    loaded: tuple[SpaceResident, ...]
    # Comparison only: this is not the final choice to send all/part to a writer.
    capacity_stamp: PartStamp
    input_audit: tuple[dict[str, JsonValue], ...]


class SpaceReplayResult(DiaryContract):
    format: Literal["space-replay-result-v1"] = "space-replay-result-v1"
    input_revision: str
    policy: dict[str, JsonValue]
    comparison: SlotPolicy
    frames: tuple[SpaceFrame, ...]


def _comparison(frame, residents, policy):
    candidates = []
    for item in residents:
        metres = item.relation.get("distance_m")
        facts = {
            "format": "space-material-v1",
            "source": item.source,
            "case_ids": list(item.case_ids),
            "material": item.material,
            "relation": item.relation,
            "temporal_basis": "lookup_snapshot",
            **({"distance_m": metres} if metres is not None else {}),
        }
        candidates.append(
            evidence(
                "space",
                ROLES[item.source],
                item.key,
                digest(item.references),
                item.key,
                facts,
                (metres,) if metres is not None else (),
                scope={"seq": frame.seq, "point": frame.point.model_dump()},
                diagnostics={"references": list(item.references)},
            )
        )
    return admit(f"replay:{frame.seq}", candidates, [], policy)


def replay_spaces(raw: SpaceReplay | dict) -> SpaceReplayResult:
    # Revalidate mutable JSON dictionaries even when the outer models are frozen.
    source = SpaceReplay.model_validate(
        raw.model_dump(mode="json") if isinstance(raw, SpaceReplay) else raw
    )
    policy = source.policy
    # Align distance with the application experiment; isolate capacity/order loss.
    comparison = source.comparison.model_copy(update={"space_radius_m": policy.park_radius_m})
    previous, frames = {}, []
    archive = []
    coverage = {}
    arrivals = defaultdict(list)
    for batch in sorted(source.batches, key=lambda b: b.id):
        arrivals[batch.available_seq].append(batch)
    for point in source.points:
        audits = []
        for batch in arrivals[point.seq]:
            audits.extend({"batch_id": batch.id, **a} for a in batch.materials.audit)
            for item in batch.materials.materials:
                key, claim = claim_identity(item)
                archive.append((batch, item, key, claim))
                if item.source == "land_cover" and claim not in coverage:
                    try:
                        coverage[claim] = PreparedFeature(
                            item.scope["geometry"], item.scope["crs"], item.scope["query_point"]
                        )
                    except (KeyError, TypeError, ValueError, OverflowError):
                        coverage[claim] = None  # apply_space reports the invalid scope.
        groups, decisions = defaultdict(list), []
        for batch, item, key, claim in archive:
            ref = batch.id + "/" + item.id
            if batch.expires_at is not None and point.at >= batch.expires_at:
                app = SpaceApplication(
                    material_id=item.id, eligibility="fail", reason="batch_expired"
                )
                decisions.append(MemoryDecision(reference=ref, key=key, application=app))
            else:
                groups[key].append((ref, item, claim))
        current = {}
        for key, group in sorted(groups.items()):
            # Resolve conflicting subjects before distance checks: a moved registration
            # must not silently win just because the other version is farther away.
            conflict = len({claim for _, _, claim in group}) > 1
            applications = []
            evaluated = None
            for ref, item, claim in sorted(group, key=lambda v: v[0]):
                app = (
                    SpaceApplication(
                        material_id=item.id, eligibility="unknown", reason="conflicting_materials"
                    )
                    if conflict
                    else (
                        evaluated.model_copy(update={"material_id": item.id})
                        if evaluated is not None
                        else apply_space(item, point.point, policy, coverage=coverage.get(claim))
                    )
                )
                evaluated = app
                applications.append((ref, item, app))
                decisions.append(MemoryDecision(reference=ref, key=key, application=app))
            accepted = [
                (ref, item, app) for ref, item, app in applications if app.eligibility == "pass"
            ]
            if accepted:
                _, item, app = accepted[0]
                current[key] = SpaceResident(
                    key=key,
                    source=item.source,
                    case_ids=item.case_ids,
                    material=item.material,
                    relation=app.relation,
                    references=tuple(ref for ref, _, _ in accepted),
                )
        changes = []
        for key in sorted(previous.keys() | current.keys()):
            now, before = current.get(key), previous.get(key)
            if now is None:
                reasons = sorted({d.application.reason for d in decisions if d.key == key})
                change, reason, item = "evicted", "+".join(reasons), before
            elif before is None:
                change, reason, item = "loaded", "applicable", now
            elif now == before:
                change, reason, item = "retained", "still_applicable", now
            else:
                change, reason, item = "updated", "relation_or_provenance_changed", now
            changes.append(MemoryChange(key=key, source=item.source, change=change, reason=reason))
        residents = tuple(current[key] for key in sorted(current))
        frames.append(
            SpaceFrame(
                seq=point.seq,
                at=point.at,
                point=point.point,
                decisions=tuple(sorted(decisions, key=lambda d: d.reference)),
                changes=tuple(changes),
                loaded=residents,
                capacity_stamp=_comparison(point, residents, comparison),
                input_audit=tuple(audits),
            )
        )
        previous = current
    return SpaceReplayResult(
        input_revision=digest(source),
        policy=policy.dictionary(),
        comparison=comparison,
        frames=tuple(frames),
    )
