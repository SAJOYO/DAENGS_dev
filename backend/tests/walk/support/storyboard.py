"""Shared storyboard test builders; no test cases."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_finalize import walk_input_fingerprint
from daengs_backend.services.walk_legacy import storyboard as service

OWNER, WALK, SESSION, ENTRY = [uuid.uuid4() for _ in range(4)]

START = datetime(2026, 9, 5, tzinfo=UTC)


@pytest.fixture
def live(monkeypatch):
    points = [
        WalkPointUpload(
            client_seq=i,
            chain_index=0,
            at=START + timedelta(seconds=i * 10),
            lat=37.5,
            lng=127 + i * 0.000113,
            accuracy_m=5,
        )
        for i in range(121)
    ]
    chunk = SimpleNamespace(seq_from=0, seq_to=120, point_count=121, payload=encode_chunk(points))
    walk = SimpleNamespace(
        id=WALK,
        client_session_id=SESSION,
        pet_ids=[],
        started_at=START,
        ended_at=START + timedelta(seconds=1200),
        points=[chunk],
        analysis_state="derived",
    )
    analysis = SimpleNamespace(
        id=uuid.uuid4(),
        point_count=121,
        terminal_client_seq=120,
        input_fingerprint=walk_input_fingerprint(points),
    )
    state = SimpleNamespace(row=None, entries=[], walk=walk)
    db = SimpleNamespace(
        add=lambda row: setattr(state, "row", row), commit=AsyncMock(), expire_all=lambda: None
    )
    monkeypatch.setattr(
        service.walks,
        "get_owned_for_update",
        AsyncMock(side_effect=lambda s, o, w: walk if o == OWNER and w == WALK else None),
    )
    monkeypatch.setattr(service.repo, "latest_analysis", AsyncMock(return_value=analysis))
    monkeypatch.setattr(service.repo, "current", AsyncMock(side_effect=lambda s, w: state.row))
    monkeypatch.setattr(
        service.entries_repo, "entries", AsyncMock(side_effect=lambda s, ws: state.entries)
    )
    lookup = AsyncMock(
        side_effect=lambda selection: {
            a["id"]: {
                "facts": ["등록 카페 주변 (테스트 자료)"],
                "sources": [
                    {
                        "source": "test-place",
                        "status": "known",
                        "captured_at": START.isoformat(),
                        "source_url": None,
                    }
                ],
            }
            for a in selection["anchors"]
        }
    )
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    app.dependency_overrides[router.get_context_lookup] = lambda: lookup
    return TestClient(app), state, lookup


PATH = f"/app/walks/{WALK}/storyboard"


def headings(payload):
    scenes = payload["scenes"]
    return {
        "title": {"text": "함께 남긴 산책 기록", "fact_ids": [scenes[0]["facts"][0]["id"]]},
        "scenes": [
            {"scene_id": s["id"], "text": "산책 관측 기록", "fact_ids": [s["facts"][0]["id"]]}
            for s in scenes
        ],
    }
