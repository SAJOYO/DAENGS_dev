"""업로드된 좌표 chunk를 하나의 봉인 가능한 산책 증거열로 검증한다.

DB 행 잠금과 저장은 application service의 책임이다. 읽기 스냅샷과 잠금 후 재검증에서
같은 순수 검증을 사용해 다음 세 경계를 한 번에 닫는다.

1. chunk metadata가 실제 payload를 설명하는가
2. 여러 chunk가 0부터 terminal까지 겹침과 누락 없이 이어지는가
3. 저장 DTO를 순수 ``daengs_walk`` 계산 계약으로 손실 없이 옮길 수 있는가
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal
from typing import Any, Literal, Protocol

from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointUpload
from daengs_backend.services.walk_chunk import decode_chunk
from daengs_walk import WalkEvidencePoint

INPUT_FINGERPRINT_VERSION = 1
_COORD_PLACES = Decimal("0.000001")

FinalizeInputErrorCode = Literal[
    "chunk_decode_failed",
    "chunk_metadata_invalid",
    "chunk_point_count_mismatch",
    "chunk_sequence_bounds_mismatch",
    "duplicate_client_sequence",
    "sequence_gap",
    "sequence_overlap",
    "point_count_mismatch",
    "terminal_sequence_mismatch",
    "input_fingerprint_mismatch",
    "evidence_contract_invalid",
]


class StoredWalkPointChunk(Protocol):
    seq_from: int
    seq_to: int
    point_count: int
    payload: dict[str, Any]


class FinalizeInputError(ValueError):
    """클라이언트 재전송·DB 손상·계약 불일치를 구분하는 bounded 오류."""

    def __init__(self, code: FinalizeInputErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PreparedWalkEvidence:
    points: tuple[WalkEvidencePoint, ...]
    input_fingerprint: str
    point_count: int
    terminal_client_seq: int | None


def prepare_finalized_walk(
    chunks: Sequence[StoredWalkPointChunk],
    manifest: WalkFinalizeRequest,
) -> PreparedWalkEvidence:
    """chunk 전체를 검증하고 계산 커널이 받을 불변 좌표열을 만든다."""

    ordered_chunks = sorted(chunks, key=lambda chunk: chunk.seq_from)
    decoded: list[WalkPointUpload] = []
    previous_seq_to: int | None = None

    for chunk in ordered_chunks:
        _validate_chunk_metadata(chunk)
        if previous_seq_to is not None:
            if chunk.seq_from <= previous_seq_to:
                raise FinalizeInputError(
                    "sequence_overlap",
                    f"chunk sequence가 겹칩니다: {chunk.seq_from} <= {previous_seq_to}",
                )
            if chunk.seq_from != previous_seq_to + 1:
                raise FinalizeInputError(
                    "sequence_gap",
                    f"chunk 사이 sequence가 비었습니다: {previous_seq_to} -> {chunk.seq_from}",
                )

        try:
            points = decode_chunk(chunk.payload)
        except (ArithmeticError, LookupError, OSError, TypeError, ValueError) as exc:
            raise FinalizeInputError(
                "chunk_decode_failed",
                f"seq_from={chunk.seq_from} chunk를 해석할 수 없습니다.",
            ) from exc

        if len(points) != chunk.point_count:
            raise FinalizeInputError(
                "chunk_point_count_mismatch",
                f"seq_from={chunk.seq_from} point_count가 payload 길이와 다릅니다.",
            )
        sequences = [point.client_seq for point in points]
        if len(sequences) != len(set(sequences)):
            raise FinalizeInputError(
                "duplicate_client_sequence",
                f"seq_from={chunk.seq_from} chunk 안에 중복 sequence가 있습니다.",
            )
        if not sequences or sequences[0] != chunk.seq_from or sequences[-1] != chunk.seq_to:
            raise FinalizeInputError(
                "chunk_sequence_bounds_mismatch",
                f"seq_from={chunk.seq_from} metadata와 payload 경계가 다릅니다.",
            )
        expected_sequences = list(range(chunk.seq_from, chunk.seq_to + 1))
        if sequences != expected_sequences:
            raise FinalizeInputError(
                "sequence_gap",
                f"seq_from={chunk.seq_from} chunk 안의 sequence가 연속이 아닙니다.",
            )

        decoded.extend(points)
        previous_seq_to = chunk.seq_to

    _validate_manifest(decoded, manifest)
    input_fingerprint = walk_input_fingerprint(decoded)
    if manifest.input_fingerprint is not None and manifest.input_fingerprint != input_fingerprint:
        raise FinalizeInputError(
            "input_fingerprint_mismatch",
            "클라이언트와 서버의 산책 입력 fingerprint가 다릅니다.",
        )

    try:
        evidence_points = tuple(
            WalkEvidencePoint(
                client_seq=point.client_seq,
                chain_index=point.chain_index,
                at=point.at,
                lat=float(point.lat),
                lng=float(point.lng),
                accuracy_m=point.accuracy_m,
                is_mock=point.is_mock,
            )
            for point in decoded
        )
    except (TypeError, ValueError) as exc:
        raise FinalizeInputError(
            "evidence_contract_invalid",
            "저장 좌표를 WalkEvidencePoint 계약으로 옮길 수 없습니다.",
        ) from exc

    return PreparedWalkEvidence(
        points=evidence_points,
        input_fingerprint=input_fingerprint,
        point_count=len(evidence_points),
        terminal_client_seq=evidence_points[-1].client_seq if evidence_points else None,
    )


def walk_input_fingerprint(points: Sequence[WalkPointUpload]) -> str:
    """chunk 경계와 무관한 decoded point stream v1 SHA-256 지문."""

    canonical_points = [
        [
            point.client_seq,
            point.chain_index,
            int(point.at.astimezone(UTC).timestamp() * 1000),
            format(point.lat.quantize(_COORD_PLACES), "f"),
            format(point.lng.quantize(_COORD_PLACES), "f"),
            _canonical_number(point.accuracy_m),
            1 if point.is_mock else 0,
        ]
        for point in points
    ]
    blob = json.dumps(
        {"v": INPUT_FINGERPRINT_VERSION, "points": canonical_points},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(blob.encode()).hexdigest()}"


def _canonical_number(value: float | None) -> str | None:
    if value is None:
        return None
    decimal = Decimal(str(value))
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _validate_chunk_metadata(chunk: StoredWalkPointChunk) -> None:
    if (
        chunk.seq_from < 0
        or chunk.seq_to < chunk.seq_from
        or chunk.point_count <= 0
        or not isinstance(chunk.payload, dict)
    ):
        raise FinalizeInputError(
            "chunk_metadata_invalid",
            f"seq_from={chunk.seq_from} chunk metadata가 유효하지 않습니다.",
        )


def _validate_manifest(
    points: Sequence[WalkPointUpload],
    manifest: WalkFinalizeRequest,
) -> None:
    if len(points) != manifest.expected_point_count:
        raise FinalizeInputError(
            "point_count_mismatch",
            f"서버 좌표 {len(points)}개와 선언 {manifest.expected_point_count}개가 다릅니다.",
        )
    terminal = points[-1].client_seq if points else None
    if terminal != manifest.terminal_client_seq:
        raise FinalizeInputError(
            "terminal_sequence_mismatch",
            f"서버 terminal sequence {terminal!r}와 선언이 다릅니다.",
        )
    sequences = [point.client_seq for point in points]
    if sequences != list(range(len(points))):
        raise FinalizeInputError(
            "sequence_gap",
            "전체 client_seq는 0부터 빠짐없이 이어져야 합니다.",
        )
