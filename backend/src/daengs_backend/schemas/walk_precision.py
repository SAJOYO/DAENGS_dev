"""Additive original-coordinate evidence, tied to a sealed motion backup."""

from typing import Annotated, Literal

from pydantic import Field

from daengs_backend.schemas.walk_motion import CHUNK_SIZE, Digest, FloatBits, MotionWire, Seq

VERSION = "gps-motion-precision-v1"
DoubleBits = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


class PrecisionManifest(MotionWire):
    version: Literal["gps-motion-precision-v1"] = VERSION
    client_session_id: str = Field(pattern=r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    base_evidence_fingerprint: Digest
    point_count: Seq


class PrecisionPoint(MotionWire):
    client_seq: Seq
    lat_bits: DoubleBits
    lng_bits: DoubleBits
    accuracy_bits: FloatBits | None


class PrecisionChunk(MotionWire):
    manifest_fingerprint: Digest
    points: list[PrecisionPoint] = Field(min_length=1, max_length=CHUNK_SIZE)


class PrecisionChunkResponse(PrecisionChunk):
    chunk_index: int
    chunk_fingerprint: Digest


class PrecisionStatus(MotionWire):
    version: Literal["gps-motion-precision-v1"] = VERSION
    manifest: PrecisionManifest
    manifest_fingerprint: Digest
    received_chunks: list[int]
    evidence_fingerprint: Digest | None
    state: Literal["collecting", "complete"]
