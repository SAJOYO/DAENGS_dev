"""Read-only projection of a validated motion-v1 backup into trajectory contracts.

Frozen motion decisions own walking distance. A separate conservative policy may
connect otherwise uncredited observations; HIGH_SPEED alone is not a GPS gap.
No result here is published to an active read view or written back to a walk.
"""

from __future__ import annotations

import asyncio
from math import isclose
from typing import Literal

from pydantic import Field

from daengs_backend.schemas.walk_motion import CHUNK_SIZE, MotionManifest, MotionObservation
from daengs_backend.services.walk_metrics.motion_engine import Point, replay
from daengs_backend.services.walk_session.finalize import walk_input_fingerprint
from daengs_backend.services.walk_session.motion_contract import (
    MotionConflict,
    chunk_digest,
    evidence_digest,
    manifest_digest,
)
from daengs_walk.trajectory import (
    Contract,
    EvidenceJournal,
    IntervalAssessment,
    IntervalLedger,
    JournalEvent,
    PointAssessment,
    SourceRange,
    SourceRef,
)
from daengs_walk.trajectory_projection import (
    PathSection,
    TrajectoryBoundaries,
    TrajectoryLocation,
    boundaries,
    path_sections,
)
from daengs_walk.trajectory_view import MeasurementKey, MeasurementSnapshot, WalkScope, digest

BAD_TIME = frozenset(
    {
        "INVALID_TIME",
        "OUTSIDE_ACTIVE_INTERVAL",
        "OUT_OF_ORDER",
        "DUPLICATE",
        "SAME_TIME_CONFLICT",
    }
)


class ObservedConnectionPolicy(Contract):
    version: Literal["motion-shadow-observed-v1"] = "motion-shadow-observed-v1"
    max_gap_seconds: float = Field(default=20, gt=0)
    max_edge_m: float = Field(default=200, gt=0)


DEFAULT_CONNECTION_POLICY = ObservedConnectionPolicy()


class ShadowResult(Contract):
    version: Literal["walk-trajectory-shadow-v1"] = "walk-trajectory-shadow-v1"
    snapshot: MeasurementSnapshot
    observation_policy: ObservedConnectionPolicy
    motion_distance_m: float = Field(ge=0)
    motion_active_duration_ns: int = Field(ge=0)
    motion_segments: tuple[tuple[int, ...], ...]
    locations: tuple[TrajectoryLocation, ...]
    observed_runs: tuple[PathSection, ...]
    walking_sections: tuple[PathSection, ...]
    boundaries: TrajectoryBoundaries


class ShadowAssembler:
    """Consumes ordered frozen-engine steps; seal only after replay returned normally.

    This is not another GPS speed estimator. Feed partitioning is absent from the
    output identity, and original backup client sequences are never renumbered.
    """

    def __init__(
        self,
        manifest,
        observations,
        raw,
        *,
        owner_id,
        precision_fingerprint=None,
        policy=DEFAULT_CONNECTION_POLICY,
    ):
        self.manifest, self.observations, self.raw = manifest, observations, raw
        self.owner_id, self.precision_fingerprint, self.policy = (
            owner_id,
            precision_fingerprint,
            policy,
        )
        self.steps = []
        self.events, self.points, self.intervals, self.superseded = [], [], [], []
        self.refs, self.indexes, self.quality = {}, {}, {}
        self.epoch_index = 0
        self._begin_epoch()

    def accept(self, step):
        seq = step["decision"]["client_seq"]
        if seq != len(self.steps) or seq >= self.manifest.point_count:
            raise MotionConflict("trajectory_step_order")
        while self.manifest.epochs[self.epoch_index].target_ingress_seq < seq:
            self._end_epoch()
            self._begin_epoch()
        self.steps.append(step)
        observation = self.observations[seq]
        ref = SourceRef(**self._source_scope(), ingress_seq=seq)
        self.refs[seq] = ref
        time_reasons = tuple(r for r in step["estimate"]["reasons"] if r in BAD_TIME)
        quality = step["estimate"]["position_quality"]
        point = PointAssessment(
            ref=ref,
            position_quality={"USABLE": "usable", "UNCERTAIN": "uncertain"}.get(quality, "invalid"),
            reasons=tuple(sorted({quality, *step["estimate"]["reasons"]})),
        )
        self.points.append(point)
        self.quality[ref] = point.position_quality
        self._append_event(
            JournalEvent(
                ref=ref,
                kind="observation",
                elapsed_ns=None if time_reasons else observation.elapsed_realtime_nanos,
                original_elapsed_ns=observation.elapsed_realtime_nanos,
                time_reasons=time_reasons,
            )
        )
        decision = step["decision"]
        if decision["distance_use"] == "INCLUDE":
            start, end = self.indexes[self.refs[decision["from_seq"]]], len(self.events) - 1
            if start >= end:
                raise MotionConflict("trajectory_overlapping_owners")
            removed = self._remove_suffix(start)
            self.superseded.extend(removed)
            self.intervals.append(
                IntervalAssessment(
                    source_range=SourceRange(start=self.events[start].ref, end=ref),
                    continuity="connected",
                    walking_use="included",
                    activity="moving",
                    measured_displacement_m=decision["distance_delta_m"],
                    walking_distance_m=decision["distance_delta_m"],
                    reasons=tuple(sorted({"MOTION_V1_INCLUDED", *decision["reasons"]})),
                    reassessment_basis="motion-v1-anchor-evaluation" if end > start + 1 else None,
                    reentry_basis="motion-v1-accepted-anchor",
                )
            )

    def _source_scope(self):
        epoch = self.manifest.epochs[self.epoch_index]
        return {
            "session_id": self.manifest.client_session_id,
            "source_epoch": epoch.source_epoch,
            "clock_epoch_id": epoch.clock_epoch_id,
        }

    def _begin_epoch(self):
        epoch = self.manifest.epochs[self.epoch_index]
        self._append_event(
            JournalEvent(
                ref=SourceRef(**self._source_scope(), control_kind="epoch_start"),
                kind="start" if self.epoch_index == 0 else "resume",
                elapsed_ns=epoch.started_elapsed_nanos,
            )
        )

    def _end_epoch(self):
        epoch = self.manifest.epochs[self.epoch_index]
        self._append_event(
            JournalEvent(
                ref=SourceRef(**self._source_scope(), control_kind="epoch_end"),
                kind="end" if epoch.end_kind == "STOP" else "pause",
                elapsed_ns=epoch.ended_elapsed_nanos,
            )
        )
        self.epoch_index += 1

    def _remove_suffix(self, start):
        removed = []
        while self.intervals and self.indexes[self.intervals[-1].source_range.end] > start:
            interval = self.intervals[-1]
            if (
                self.indexes[interval.source_range.start] < start
                or interval.walking_use == "included"
            ):
                raise MotionConflict("trajectory_overlapping_owners")
            removed.append(self.intervals.pop())
        return reversed(removed)

    def _append_event(self, event):
        end = len(self.events)
        self.events.append(event)
        self.indexes[event.ref] = end
        if end == 0:
            return
        start = end - 1
        if event.elapsed_ns is not None:
            while (
                self.events[start].kind == "observation" and self.events[start].elapsed_ns is None
            ):
                start -= 1
            if start < end - 1:
                self.superseded.extend(self._remove_suffix(start))
        self.intervals.append(self._uncredited(self.events, start, end, self.quality))

    def seal(self, reference) -> ShadowResult:
        m = self.manifest
        if len(self.steps) != m.point_count:
            raise MotionConflict("trajectory_steps_incomplete")
        while self.epoch_index < len(m.epochs):
            self._end_epoch()
            if self.epoch_index < len(m.epochs):
                self._begin_epoch()
        journal = EvidenceJournal(events=tuple(self.events))
        ledger = IntervalLedger(
            journal=journal,
            points=tuple(self.points),
            intervals=tuple(self.intervals),
            superseded=tuple(self.superseded),
        )
        if not isclose(
            ledger.metrics().walking_distance_m,
            reference["distance_m"],
            abs_tol=1e-7,
            rel_tol=1e-10,
        ):
            raise MotionConflict("trajectory_distance_mismatch")
        metadata_fp = backup_fingerprint(m, self.observations)
        key = MeasurementKey(
            # The old upload fingerprint rounds coordinates to six decimals. Bind
            # the actual computational bits too, including a precise restoration.
            input_fingerprint=digest(
                {
                    "base_raw_fingerprint": m.raw_input_fingerprint,
                    "coordinates": [
                        (
                            p.client_seq,
                            float(p.lat).hex(),
                            float(p.lng).hex(),
                            None if p.accuracy_m is None else float(p.accuracy_m).hex(),
                        )
                        for p in self.raw
                    ],
                }
            ),
            journal_fingerprint=metadata_fp.removeprefix("sha256:"),
            clock_mapping_version="motion-sample-support-v1",
            coordinate_basis="device-fix-bits-v1"
            if self.precision_fingerprint
            else "stored-raw-v1-six-decimals",
            precision_fingerprint=(
                self.precision_fingerprint.removeprefix("sha256:")
                if self.precision_fingerprint
                else None
            ),
            motion_policy_version=m.policy.version,
            config_hash=m.policy.config_hash,
            connectivity_policy_version=f"{self.policy.version}:{digest(self.policy.model_dump())}",
            engine_version="motion-v1-trajectory-adapter-v1",
        )
        result_digest = digest(ledger.model_dump(mode="json", exclude={"superseded"}))
        measurement_id = "shadow-" + digest({"key": key.model_dump(), "result": result_digest})
        snapshot = MeasurementSnapshot(
            scope=WalkScope(owner_id=str(self.owner_id), session_id=m.client_session_id),
            measurement_id=measurement_id,
            key=key,
            source="server",
            ledger=ledger,
        )
        return ShadowResult(
            snapshot=snapshot,
            observation_policy=self.policy,
            motion_distance_m=reference["distance_m"],
            motion_active_duration_ns=reference["recording_duration_nanos"],
            motion_segments=tuple(tuple(path) for path in reference["segments"]),
            locations=tuple(
                TrajectoryLocation(ref=self.refs[i], lat=float(p.lat), lng=float(p.lng))
                for i, p in enumerate(self.raw)
            ),
            observed_runs=path_sections(ledger, walking_only=False),
            walking_sections=path_sections(ledger, walking_only=True),
            boundaries=boundaries(ledger),
        )

    def _uncredited(self, events, start, end, quality):
        left, right = events[start], events[end]
        reasons = {"MOTION_V1_NO_DISTANCE"}
        for event in events[start : end + 1]:
            reasons.update(event.time_reasons)
            if event.kind == "observation":
                reasons.update(self.steps[event.ref.ingress_seq]["decision"]["reasons"])
        continuity, activity, displacement = "unresolved", "unknown", None
        walking_use = "excluded" if {"HIGH_SPEED", "REENTRY_PENDING"} & reasons else "unresolved"
        if left.kind != "observation" or right.kind != "observation":
            continuity, walking_use = "broken", "excluded"
            reasons.add(
                "RECORDING_PAUSE"
                if left.kind == "pause" or right.kind == "resume"
                else "RECORDING_BOUNDARY"
            )
        elif any(e.elapsed_ns is None for e in events[start : end + 1]):
            reasons.add("UNRESOLVED_SAMPLE_TIME")
        elif quality[left.ref] != "usable" or quality[right.ref] != "usable":
            reasons.add("UNUSABLE_POSITION")
        else:
            a, b = left.ref.ingress_seq, right.ref.ingress_seq
            displacement = Point(self.raw[a], self.observations[a]).distance(
                Point(self.raw[b], self.observations[b])
            )
            dt = (right.elapsed_ns - left.elapsed_ns) / 1e9
            if dt <= 0:
                reasons.add("NONINCREASING_SAMPLE_TIME")
            elif dt > self.policy.max_gap_seconds:
                continuity = "broken"
                reasons.add("OBSERVED_TIME_GAP")
            elif displacement > self.policy.max_edge_m:
                continuity = "broken"
                reasons.add("OBSERVED_EDGE_JUMP")
            else:
                continuity = "connected"
                reasons.add("OBSERVED_ENDPOINT_SUPPORT")
                estimates = [self.steps[i]["estimate"] for i in (a, b)]
                if all(e["movement"] == "STILL" for e in estimates) and displacement == 0:
                    activity = "still"
        return IntervalAssessment(
            source_range=SourceRange(start=left.ref, end=right.ref),
            continuity=continuity,
            walking_use=walking_use,
            activity=activity,
            measured_displacement_m=displacement,
            reasons=tuple(sorted(reasons)),
        )


def backup_fingerprint(manifest, observations):
    return evidence_digest(
        manifest_digest(manifest),
        [
            chunk_digest(observations[i : i + CHUNK_SIZE])
            for i in range(0, len(observations), CHUNK_SIZE)
        ],
    )


def replay_shadow(
    manifest: MotionManifest,
    observations: list[MotionObservation],
    raw,
    *,
    owner_id: str,
    precision_fingerprint=None,
    policy=DEFAULT_CONNECTION_POLICY,
    step_batch_size: int = 1,
) -> ShadowResult:
    if step_batch_size < 1:
        raise ValueError("step batch size must be positive")
    # Refined Float/Double inputs no longer have the original decimal wire identity.
    # Precision callers must use completed_input/refine_points after checking the base
    # fingerprint; never round the restored bits to recreate that already-verified input.
    if (
        precision_fingerprint is None
        and walk_input_fingerprint(raw) != manifest.raw_input_fingerprint
    ):
        raise MotionConflict("trajectory_raw_fingerprint")
    assembler = ShadowAssembler(
        manifest,
        observations,
        raw,
        owner_id=owner_id,
        precision_fingerprint=precision_fingerprint,
        policy=policy,
    )
    pending = []

    def consume(step):
        pending.append(step)
        if len(pending) >= step_batch_size:
            for item in pending:
                assembler.accept(item)
            pending.clear()

    reference = replay(manifest, observations, raw, consume)
    for item in pending:
        assembler.accept(item)
    return assembler.seal(reference)


async def calculate_shadow(session, owner, walk_id) -> ShadowResult:
    from daengs_backend.services.walk_session.motion import completed_input

    manifest, raw, observations, fingerprint, precision_fp = await completed_input(
        session, owner, walk_id
    )
    if backup_fingerprint(manifest, observations) != fingerprint:
        raise MotionConflict("trajectory_evidence_fingerprint")
    # completed_input has already released its owner-scoped lock and verified precision.
    return await asyncio.to_thread(
        replay_shadow,
        manifest,
        observations,
        raw,
        owner_id=str(owner),
        precision_fingerprint=precision_fp,
    )
