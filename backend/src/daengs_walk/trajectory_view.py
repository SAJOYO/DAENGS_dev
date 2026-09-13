"""Immutable measurement comparison and pure read-view transition decisions.

Callers must persist accepted transitions with the same expected-manifest CAS.
This module neither downloads chunks nor claims that a database/UI swap occurred.
"""

from __future__ import annotations

import hashlib
import json
from math import isclose
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from daengs_walk.trajectory import Contract, Identifier, IntervalLedger, Nonnegative

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class WalkScope(Contract):
    owner_id: Identifier
    session_id: Identifier


class MeasurementKey(Contract):
    schema_version: Literal["trajectory-contract-v2"] = "trajectory-contract-v2"
    input_fingerprint: Digest
    journal_fingerprint: Digest
    clock_mapping_version: Identifier
    coordinate_basis: Identifier
    precision_fingerprint: Digest | None
    motion_policy_version: Identifier
    config_hash: Digest
    connectivity_policy_version: Identifier
    engine_version: Identifier


class MeasurementRef(Contract):
    scope: WalkScope
    measurement_id: Identifier
    key: MeasurementKey
    result_digest: Digest


class MeasurementSnapshot(Contract):
    scope: WalkScope
    measurement_id: Identifier
    key: MeasurementKey
    source: Literal["device", "server"]
    completion: Literal["sealed"] = "sealed"
    ledger: IntervalLedger

    @model_validator(mode="after")
    def journal_scope(self) -> Self:
        if self.scope.session_id != self.ledger.journal.events[0].ref.session_id:
            raise ValueError("snapshot and journal sessions differ")
        return self

    def result_payload(self) -> dict:
        # Audit candidates and delivery chunks are not final calculation output.
        return self.ledger.model_dump(mode="json", exclude={"superseded"})

    def ref(self) -> MeasurementRef:
        return MeasurementRef(
            scope=self.scope,
            measurement_id=self.measurement_id,
            key=self.key,
            result_digest=digest(self.result_payload()),
        )


class ComparisonPolicy(Contract):
    version: Literal["trajectory-comparison-v1"] = "trajectory-comparison-v1"
    distance_absolute_tolerance_m: Nonnegative = 1e-7
    distance_relative_tolerance: Nonnegative = 1e-10


DEFAULT_COMPARISON_POLICY = ComparisonPolicy()


class VerificationRecord(Contract):
    left: MeasurementRef
    right: MeasurementRef
    policy: ComparisonPolicy
    outcome: Literal["equivalent", "incomparable", "mismatch"]
    reasons: tuple[Identifier, ...]


def compare_measurements(
    left: MeasurementSnapshot,
    right: MeasurementSnapshot,
    policy: ComparisonPolicy = DEFAULT_COMPARISON_POLICY,
) -> VerificationRecord:
    lref, rref = left.ref(), right.ref()
    if (lref.scope, lref.measurement_id) == (rref.scope, rref.measurement_id) and lref != rref:
        raise ValueError("immutable measurement ID was reused for a different result or key")
    differences = tuple(
        f"key:{field}"
        for field in MeasurementKey.model_fields
        if getattr(left.key, field) != getattr(right.key, field)
    )
    if left.scope != right.scope:
        differences = ("scope", *differences)
    if differences:
        return VerificationRecord(
            left=lref, right=rref, policy=policy, outcome="incomparable", reasons=differences
        )
    a, b = left.result_payload(), right.result_payload()
    numeric_match = len(a["intervals"]) == len(b["intervals"])
    for intervals in zip(a["intervals"], b["intervals"]):
        for field in ("walking_distance_m", "measured_displacement_m"):
            x, y = (interval.pop(field) for interval in intervals)
            numeric_match &= (
                x is y
                if x is None or y is None
                else isclose(
                    x,
                    y,
                    rel_tol=policy.distance_relative_tolerance,
                    abs_tol=policy.distance_absolute_tolerance_m,
                )
            )
    totals_match = isclose(
        left.ledger.metrics().walking_distance_m,
        right.ledger.metrics().walking_distance_m,
        rel_tol=policy.distance_relative_tolerance,
        abs_tol=policy.distance_absolute_tolerance_m,
    )
    reasons = tuple(
        reason
        for failed, reason in (
            (a != b, "structure"),
            (not numeric_match, "distance"),
            (not totals_match, "walking_total"),
        )
        if failed
    )
    return VerificationRecord(
        left=lref,
        right=rref,
        policy=policy,
        outcome="mismatch" if reasons else "equivalent",
        reasons=reasons,
    )


class RouteChunk(Contract):
    index: int = Field(ge=0)
    sha256: Digest


class BindingKey(Contract):
    measurement: MeasurementRef
    event_revision: int = Field(ge=0)
    scene_revision: int = Field(ge=0)
    binding_revision: int = Field(ge=0)
    binding_policy_version: Identifier


class ReadViewManifest(Contract):
    measurement: MeasurementRef
    read_view_revision: int = Field(ge=1)
    event_revision: int = Field(ge=0)
    scene_revision: int = Field(ge=0)
    binding_revision: int | None = Field(default=None, ge=0)
    binding_policy_version: Identifier
    binding_state: Literal["ready", "pending", "failed", "incompatible"]
    required_route_chunks: tuple[RouteChunk, ...] = ()

    @model_validator(mode="after")
    def versions(self) -> Self:
        if (self.binding_state == "ready") != (self.binding_revision is not None):
            raise ValueError("only ready bindings carry an adopted binding revision")
        indexes = [chunk.index for chunk in self.required_route_chunks]
        if indexes != sorted(set(indexes)):
            raise ValueError("required chunks must have unique sorted indexes")
        return self

    def binding_key(self) -> BindingKey | None:
        if self.binding_revision is None:
            return None
        return BindingKey(
            measurement=self.measurement,
            event_revision=self.event_revision,
            scene_revision=self.scene_revision,
            binding_revision=self.binding_revision,
            binding_policy_version=self.binding_policy_version,
        )


class PreparedCore(Contract):
    """Local receipt after hash verification and geometry/index preparation."""

    measurement: MeasurementRef
    verified_chunks: tuple[RouteChunk, ...]
    geometry_ready: bool
    metrics_ready: bool
    boundaries_ready: bool

    @model_validator(mode="after")
    def unique_chunks(self) -> Self:
        if len({c.index for c in self.verified_chunks}) != len(self.verified_chunks):
            raise ValueError("duplicate prepared chunk index")
        return self


class TransitionDecision(Contract):
    adopted: bool
    reason: Literal[
        "accepted",
        "stale_manifest",
        "scope",
        "revision",
        "measurement_identity",
        "verification_required",
        "core_pending",
        "binding_version",
    ]
    active: ReadViewManifest | None


def adopt_read_view(
    *,
    scope: WalkScope,
    current: ReadViewManifest | None,
    expected_current: ReadViewManifest | None,
    target: ReadViewManifest,
    prepared: PreparedCore,
    verification: VerificationRecord | None = None,
    binding_response: BindingKey | None = None,
) -> TransitionDecision:
    def reject(reason):
        return TransitionDecision(adopted=False, reason=reason, active=current)

    if target.measurement.scope != scope:
        return reject("scope")
    if current != expected_current:
        return reject("stale_manifest")
    if current is None:
        if target.read_view_revision != 1:
            return reject("revision")
    else:
        if current.measurement.scope != target.measurement.scope:
            return reject("scope")
        if (
            target.read_view_revision != current.read_view_revision + 1
            or target.event_revision < current.event_revision
            or target.scene_revision < current.scene_revision
        ):
            return reject("revision")
        if current.measurement.measurement_id == target.measurement.measurement_id:
            if current.measurement != target.measurement:
                return reject("measurement_identity")
        elif (
            verification is None
            or verification.outcome != "equivalent"
            or {verification.left, verification.right} != {current.measurement, target.measurement}
        ):
            return reject("verification_required")
        if (
            (
                current.measurement,
                current.event_revision,
                current.scene_revision,
                current.binding_policy_version,
            )
            == (
                target.measurement,
                target.event_revision,
                target.scene_revision,
                target.binding_policy_version,
            )
            and current.binding_revision is not None
            and (
                target.binding_revision is None
                or target.binding_revision < current.binding_revision
            )
        ):
            return reject("revision")
    if (
        prepared.measurement != target.measurement
        or not (prepared.geometry_ready and prepared.metrics_ready and prepared.boundaries_ready)
        or not set(target.required_route_chunks) <= set(prepared.verified_chunks)
    ):
        return reject("core_pending")
    if target.binding_state == "ready" and binding_response != target.binding_key():
        return reject("binding_version")
    return TransitionDecision(adopted=True, reason="accepted", active=target)
