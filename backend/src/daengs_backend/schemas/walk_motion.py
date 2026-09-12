"""Lossless, bounded backup contract. A storage receipt is not a motion calculation."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

BACKUP_VERSION = "gps-motion-backup-v1"
CHUNK_SIZE = 256
MAX_POINTS = 100_000
MAX_EPOCHS = 1024
CALCULATION_VERSION = "gps-motion-calculation-v1"
Int64 = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Seq = Annotated[int, Field(strict=True, ge=0, le=MAX_POINTS)]
Identity = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
# Android Float.toRawBits(), unsigned big-endian hex. Preserves -0, NaN and missing separately.
FloatBits = Annotated[str, Field(pattern=r"^[0-9a-f]{8}$")]


class MotionWire(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MotionPolicyEnvelope(MotionWire):
    version: Literal["motion-v1"]
    observation_schema_version: Literal[15]
    measurement_version: Literal["motion-measurement-v1"]
    config_json: str = Field(min_length=2, max_length=8192)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MotionEpochUpload(MotionWire):
    source_epoch: Identity
    clock_epoch_id: Identity
    chain_index: Seq
    started_at_millis: Int64
    started_elapsed_nanos: Int64
    first_ingress_seq: Seq
    ended_at_millis: Int64
    ended_elapsed_nanos: Int64
    target_ingress_seq: Annotated[int, Field(strict=True, ge=-1, le=MAX_POINTS)]
    persisted_count: Seq
    end_kind: Literal["PAUSE", "STOP"]
    drained: StrictBool
    failure_reason: None
    first_failed_seq: None


class MotionManifest(MotionWire):
    version: Literal["gps-motion-backup-v1"]
    # Kept verbatim and compared with the existing walk's client session id.
    client_session_id: Annotated[str, Field(pattern=r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]
    raw_input_fingerprint: Digest
    point_count: Seq
    policy: MotionPolicyEnvelope
    epochs: list[MotionEpochUpload] = Field(min_length=1, max_length=MAX_EPOCHS)


class MotionObservation(MotionWire):
    client_seq: Seq
    source_epoch: Identity
    clock_epoch_id: Identity
    chain_index: Seq
    elapsed_realtime_nanos: Int64 | None
    received_elapsed_nanos: Int64 | None
    received_at_millis: Int64 | None
    speed_mps_bits: FloatBits | None
    speed_accuracy_mps_bits: FloatBits | None
    bearing_degrees_bits: FloatBits | None
    bearing_accuracy_degrees_bits: FloatBits | None
    provider: Annotated[str, Field(max_length=64, pattern=r"^[a-zA-Z0-9_.-]*$")] | None
    recording_eligible: StrictBool


class MotionChunkUpload(MotionWire):
    manifest_fingerprint: Digest
    points: list[MotionObservation] = Field(min_length=1, max_length=CHUNK_SIZE)


class MotionComplete(MotionWire):
    manifest_fingerprint: Digest
    evidence_fingerprint: Digest


class MotionBackupStatus(MotionWire):
    version: Literal["gps-motion-backup-v1"] = BACKUP_VERSION
    manifest: MotionManifest
    manifest_fingerprint: Digest
    received_chunks: list[int]
    evidence_fingerprint: Digest | None
    state: Literal["collecting", "complete"]
    # No server distance/time equality is promised by this capability.
    calculation_verified: Literal[False] = False


class MotionChunkResponse(MotionChunkUpload):
    chunk_index: int
    chunk_fingerprint: Digest


class MotionCalculation(MotionWire):
    version: Literal["gps-motion-calculation-v1"] = CALCULATION_VERSION
    walk_id: UUID
    client_session_id: str
    policy_version: Literal["motion-v1"]
    measurement_version: Literal["motion-measurement-v1"]
    config_hash: str
    manifest_fingerprint: Digest
    evidence_fingerprint: Digest
    coordinate_basis: Literal["stored-raw-v1-six-decimals", "device-fix-bits-v1"] = (
        "stored-raw-v1-six-decimals"
    )
    precision_fingerprint: Digest | None = None
    # Only the device performs the cross-runtime comparison; this server has no device receipt.
    device_result_verified: Literal[False] = False
    distance_m: float = Field(ge=0, allow_inf_nan=False)
    recording_duration_nanos: Int64
    active_duration_millis: Int64
    point_count: Seq
    segment_count: Seq
    segments: list[list[int]]
    reason_counts: dict[str, int]
