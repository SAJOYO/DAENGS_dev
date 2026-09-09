import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointUpload
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_finalize import (
    FinalizeInputError,
    prepare_finalized_walk,
    walk_input_fingerprint,
)
from daengs_walk import analyze_walk, build_cellophane
from tests.walk.support.paths import WALK_FIXTURES

STARTED_AT = datetime(2026, 9, 1, 9, tzinfo=UTC)
PROMOTION_FIXTURE = WALK_FIXTURES / "finalize-promotion-v1.json"


@dataclass
class Chunk:
    seq_from: int
    seq_to: int
    point_count: int
    payload: dict[str, Any]


def point(seq: int, *, chain_index: int = 0, is_mock: bool = False) -> WalkPointUpload:
    return WalkPointUpload(
        client_seq=seq,
        chain_index=chain_index,
        at=STARTED_AT + timedelta(seconds=seq * 5),
        lat=Decimal("37.497900") + Decimal(seq) / Decimal(1_000_000),
        lng=Decimal("127.027600") + Decimal(seq) / Decimal(1_000_000),
        accuracy_m=8.0,
        is_mock=is_mock,
    )


def chunk(points: list[WalkPointUpload]) -> Chunk:
    sequences = [item.client_seq for item in points]
    return Chunk(
        seq_from=min(sequences),
        seq_to=max(sequences),
        point_count=len(points),
        payload=encode_chunk(points),
    )


def manifest(count: int, *, fingerprint: str | None = None) -> WalkFinalizeRequest:
    return WalkFinalizeRequest(
        expected_point_count=count,
        terminal_client_seq=count - 1 if count else None,
        input_fingerprint=fingerprint,
    )


def assert_error(code: str, chunks: list[Chunk], request: WalkFinalizeRequest) -> None:
    with pytest.raises(FinalizeInputError) as caught:
        prepare_finalized_walk(chunks, request)
    assert caught.value.code == code


def test_manifest_requires_zero_based_contiguous_sequence() -> None:
    with pytest.raises(ValueError, match="terminal_client_seq"):
        WalkFinalizeRequest(expected_point_count=2, terminal_client_seq=2)
    with pytest.raises(ValueError, match="없어야"):
        WalkFinalizeRequest(expected_point_count=0, terminal_client_seq=0)


def test_chunks_are_decoded_in_sequence_order_and_adapted_to_evidence() -> None:
    first = [point(0), point(1)]
    second = [point(2, chain_index=1), point(3, chain_index=1, is_mock=True)]

    prepared = prepare_finalized_walk([chunk(second), chunk(first)], manifest(4))

    assert [item.client_seq for item in prepared.points] == [0, 1, 2, 3]
    assert prepared.points[2].chain_index == 1
    assert prepared.points[3].is_mock is True
    assert prepared.point_count == 4
    assert prepared.terminal_client_seq == 3
    assert prepared.input_fingerprint.startswith("sha256:")


def test_chunk_payload_count_must_match_metadata() -> None:
    stored = chunk([point(0), point(1)])
    stored.point_count = 3

    assert_error("chunk_point_count_mismatch", [stored], manifest(2))


def test_decoder_failure_is_returned_as_a_bounded_finalize_error() -> None:
    stored = chunk([point(0)])
    stored.payload["v"] = 999

    assert_error("chunk_decode_failed", [stored], manifest(1))


def test_chunk_payload_bounds_must_match_metadata() -> None:
    stored = chunk([point(0), point(1)])
    stored.seq_to = 2

    assert_error("chunk_sequence_bounds_mismatch", [stored], manifest(2))


def test_duplicate_sequence_inside_chunk_is_rejected() -> None:
    stored = chunk([point(0), point(1)])
    stored.payload["pts"][1][0] = 0

    assert_error("duplicate_client_sequence", [stored], manifest(2))


def test_gap_between_chunks_is_rejected() -> None:
    assert_error(
        "sequence_gap",
        [chunk([point(0), point(1)]), chunk([point(3), point(4)])],
        manifest(4),
    )


def test_overlap_between_chunks_is_rejected() -> None:
    assert_error(
        "sequence_overlap",
        [chunk([point(0), point(1)]), chunk([point(1), point(2)])],
        manifest(3),
    )


def test_manifest_point_count_is_checked_against_decoded_points() -> None:
    assert_error("point_count_mismatch", [chunk([point(0), point(1)])], manifest(3))


def test_empty_walk_is_a_valid_complete_input() -> None:
    prepared = prepare_finalized_walk([], manifest(0))

    assert prepared.points == ()
    assert prepared.point_count == 0
    assert prepared.terminal_client_seq is None


def test_client_fingerprint_is_verified_when_present() -> None:
    points = [point(0), point(1)]
    fingerprint = walk_input_fingerprint(points)

    prepared = prepare_finalized_walk([chunk(points)], manifest(2, fingerprint=fingerprint))
    assert prepared.input_fingerprint == fingerprint

    assert_error(
        "input_fingerprint_mismatch",
        [chunk(points)],
        manifest(2, fingerprint="sha256:" + "0" * 64),
    )


def test_fingerprint_is_independent_of_chunk_boundaries() -> None:
    points = [point(0), point(1), point(2), point(3)]

    together = prepare_finalized_walk([chunk(points)], manifest(4))
    split = prepare_finalized_walk(
        [chunk(points[:2]), chunk(points[2:])],
        manifest(4),
    )

    assert together.points == split.points
    assert together.input_fingerprint == split.input_fingerprint


def test_upload_point_rejects_naive_time_and_invalid_accuracy() -> None:
    payload = point(0).model_dump()
    payload["at"] = datetime(2026, 9, 1, 9)  # noqa: DTZ001 -- deliberately invalid input
    with pytest.raises(ValueError, match="timezone"):
        WalkPointUpload(**payload)

    payload = point(0).model_dump()
    payload["accuracy_m"] = -1
    with pytest.raises(ValueError, match="greater than or equal"):
        WalkPointUpload(**payload)


def test_uploaded_chunks_flow_through_finalize_evidence_and_cellophane() -> None:
    """Geo 승격 기준과 PR #122/#124/#125 계약을 한 고정 입력으로 관통한다."""

    fixture = json.loads(PROMOTION_FIXTURE.read_text(encoding="utf-8"))
    route = [
        WalkPointUpload(
            client_seq=item["client_seq"],
            chain_index=item["chain_index"],
            at=datetime.fromisoformat(item["at"]),
            lat=Decimal(item["lat"]),
            lng=Decimal(item["lng"]),
            accuracy_m=item["accuracy_m"],
            is_mock=item["is_mock"],
        )
        for item in fixture["points"]
    ]
    prepared = prepare_finalized_walk(
        [chunk(route[3:]), chunk(route[:3])],
        manifest(len(route), fingerprint=fixture["expected"]["input_fingerprint"]),
    )
    walk_id = uuid.UUID(fixture["walk"]["id"])
    started_at = datetime.fromisoformat(fixture["walk"]["started_at"])
    ended_at = datetime.fromisoformat(fixture["walk"]["ended_at"])

    evidence = analyze_walk(
        walk_id,
        started_at,
        ended_at,
        prepared.points,
    )
    sheet = build_cellophane(evidence)

    facts = evidence.facts.model_dump(mode="json")
    receipt = evidence.receipt.model_dump(mode="json")
    for field, expected in fixture["expected"]["facts"].items():
        assert facts[field] == expected
    for field, expected in fixture["expected"]["receipt"].items():
        assert receipt[field] == expected
    assert len(evidence.events) == fixture["expected"]["event_count"]
    assert len(evidence.observations) == fixture["expected"]["observation_count"]
    assert sheet.walk_id == walk_id
    assert sheet.paint_fp == fixture["expected"]["paint_fp"]
    assert len(sheet.occupancy) == fixture["expected"]["cell_count"]
