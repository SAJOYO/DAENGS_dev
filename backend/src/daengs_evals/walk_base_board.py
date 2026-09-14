"""Deterministic saved-input examples, without DB, external APIs or LLM credentials."""

import argparse
import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointUpload
from daengs_backend.services.walk_analysis import build_analysis_models
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_finalize import prepare_finalized_walk
from daengs_walk import analyze_walk, build_cellophane
from daengs_walk.diary.board.models import BaseBoardPolicy
from daengs_walk.diary.selection.stamps import StampPolicy

START = datetime(2026, 9, 9, tzinfo=UTC)


def example(case, samples, *, note=False):
    from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
    from daengs_backend.services.walk_diary.preparation.input import assemble_input
    from daengs_backend.services.walk_diary.preparation.observations import (
        prepare_observation_source,
    )

    walk_id = uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-base-board:" + case)
    points = [
        WalkPointUpload(
            client_seq=i,
            at=START + timedelta(seconds=second),
            lat=round(37.5 + metres / 111195, 6),
            lng=127,
            accuracy_m=5,
            chain_index=0,
            is_mock=True,
        )
        for i, (second, metres) in enumerate(samples)
    ]
    chunks = (
        [
            SimpleNamespace(
                seq_from=0,
                seq_to=len(points) - 1,
                point_count=len(points),
                payload=encode_chunk(points),
            )
        ]
        if points
        else []
    )
    walk = SimpleNamespace(
        id=walk_id,
        app_user_id=uuid.UUID(int=1),
        client_session_id=walk_id,
        started_at=START,
        ended_at=max((p.at for p in points), default=START + timedelta(minutes=3)),
        pet_ids=[],
        analysis_state="derived",
        points=chunks,
    )
    prepared = prepare_finalized_walk(
        chunks,
        WalkFinalizeRequest(
            expected_point_count=len(points),
            terminal_client_seq=len(points) - 1 if points else None,
        ),
    )
    evidence = analyze_walk(walk_id, walk.started_at, walk.ended_at, prepared.points)
    analysis = build_analysis_models(prepared, evidence, build_cellophane(evidence))
    analysis.id = uuid.uuid5(walk_id, "analysis")
    entries = []
    if note:
        point = points[len(points) // 2]
        entries.append(
            SimpleNamespace(
                id=uuid.uuid5(walk_id, "note"),
                revision=1,
                payload={
                    "kind": "note",
                    "note": "  두부와 사진을 남겼다.\n바람이 잠깐 불었다.  ",
                    "recorded_at": point.at.isoformat(),
                    "location": {
                        "lat": point.lat,
                        "lng": point.lng,
                        "captured_at": point.at.isoformat(),
                        "accuracy_m": 5,
                    },
                },
            )
        )
    assembled = assemble_input(
        walk,
        analysis,
        entries,
        [],
        None,
        [],
        observation_source=prepare_observation_source(walk, analysis),
    )
    result = assemble_saved_base_board(
        assembled, BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=5))
    )
    return {
        "case": case,
        "fixture": "synthetic_not_a_real_walk",
        "evidence_origin": assembled.source.evidence_origin,
        "target_intermediate": 5,
        "counts": result.plan.counts,
        "limits": result.plan.limits,
        "board_format": result.board.format,
        "title": result.board.title,
        "model_status": result.board.model_status,
        "scenes": [
            {
                "order": s.order,
                "id": s.id,
                "core_kind": s.core.kind,
                "event_at": s.anchor.event_at.isoformat(),
                "point": s.anchor.point.model_dump() if s.anchor.point else None,
                "source_fixes": [f.model_dump(mode="json") for f in s.anchor.source_fixes],
                "route_m": getattr(s.core, "route_m", None),
                "block": getattr(s.core, "block", None),
                "title": s.title,
                "body": s.body,
            }
            for s in result.board.scenes
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # These placeholders satisfy service import-time Settings, not an actual connection.
    # The standalone example process must not need a developer's private credentials.
    os.environ.update(
        DAENGS_DB_HOST="localhost",
        DAENGS_DB_PASSWORD="fixture-only",
        DAENGS_WARM_UP_ENCODER="false",
        DAENGS_KAKAO_APP_KEYS='["fixture-only"]',
    )
    for index, name in enumerate(("AES_KEY", "BLIND_INDEX_KEY", "JWE_KEY"), 1):
        os.environ["DAENGS_" + name] = base64.urlsafe_b64encode(bytes([index]) * 32).decode()
    ordinary = [(i * 10, i * 20) for i in range(101)]
    gap = [(i * 10 + 10, i * 20) for i in range(21)] + [
        (600 + i * 10, 1000 + i * 20) for i in range(21)
    ]
    results = [
        example("ordinary", ordinary),
        example("with_note", ordinary, note=True),
        example("late_start_and_gap", gap),
        example("no_gps", []),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for row in results:
        print(row["case"], row["counts"], row["model_status"])


if __name__ == "__main__":
    main()
