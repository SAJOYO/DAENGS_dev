"""Shared observations test builders; no test cases."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointUpload
from daengs_backend.services.walk_analysis import build_analysis_models
from daengs_backend.services.walk_session.chunk import encode_chunk
from daengs_backend.services.walk_session.finalize import prepare_finalized_walk
from daengs_walk import analyze_walk, build_cellophane
from tests.walk.support.photo_input import AT, OWNER, SESSION, WALK


def uploaded(samples):
    """Samples are (seconds, north metres, optional chain, optional accuracy)."""
    return [
        WalkPointUpload(
            client_seq=i,
            at=AT + timedelta(seconds=s[0]),
            lat=round(37.5 + s[1] / 111195, 6),
            lng=127,
            chain_index=s[2] if len(s) > 2 else 0,
            accuracy_m=s[3] if len(s) > 3 else 5,
        )
        for i, s in enumerate(samples)
    ]


def stored(points):
    chunks = []
    for start in range(0, len(points), 10):
        part = points[start : start + 10]
        chunks.append(
            SimpleNamespace(
                seq_from=start,
                seq_to=start + len(part) - 1,
                point_count=len(part),
                payload=encode_chunk(part),
            )
        )
    walk = SimpleNamespace(
        id=WALK,
        app_user_id=OWNER,
        client_session_id=SESSION,
        started_at=AT,
        ended_at=max((p.at for p in points), default=AT),
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
    evidence = analyze_walk(WALK, walk.started_at, walk.ended_at, prepared.points)
    analysis = build_analysis_models(prepared, evidence, build_cellophane(evidence))
    analysis.id = uuid.UUID(int=99)
    return walk, analysis, prepared.points


def varied_route():
    samples, seconds, metres = [(0, 0)], 0, 0
    for count, speed in [(6, 2), (3, 0), (6, 2), (3, 0.7), (6, 2), (3, 4), (6, 2)]:
        for _ in range(count):
            seconds += 10
            metres += speed * 10
            samples.append((seconds, metres))
    return stored(uploaded(samples))
