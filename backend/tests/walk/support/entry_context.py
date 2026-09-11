"""Shared entry context test builders; no test cases."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.config import settings
from daengs_backend.services import walk_entry_context as service
from daengs_backend.services import walk_entry_context_source as source

NOW = datetime.now(UTC)

CONTENT = {
    "kind": "note",
    "note": "사용자가 남긴 원문",
    "recorded_at": NOW.isoformat(),
    "location": {"lat": 37.5, "lng": 127, "captured_at": NOW.isoformat(), "accuracy_m": 8},
}


def body(*, truncated=False, empty=False):
    return {
        "groups": [
            {
                "kind": kind,
                "truncated": truncated,
                "results": []
                if empty
                else [
                    {
                        "place": {
                            "key": {"source": "public", "ref": kind, "unneeded": "discard"},
                            "name": "등록 시설",
                            "distance_m": 35,
                            "phone": "discard",
                        }
                    }
                ],
            }
            for kind in source.KINDS
        ]
    }


@pytest.fixture
def state(monkeypatch):
    job = SimpleNamespace(
        id=uuid.uuid4(),
        walk_id=uuid.uuid4(),
        entry_id=uuid.uuid4(),
        revision=1,
        policy_version=service.repo.POLICY,
        tag="space.facility",
        state="running",
        attempts=1,
        collection_round=0,
        lease_token=uuid.uuid4(),
        lease_until=datetime.now(UTC) + timedelta(seconds=45),
    )
    record = SimpleNamespace(
        revision=1, payload=dict(CONTENT), id=job.entry_id, walk_id=job.walk_id
    )
    added = []
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=job.walk_id), commit=AsyncMock(), add=added.append
    )
    active = [False]

    @asynccontextmanager
    async def factory():
        assert not active[0]
        active[0] = True
        try:
            yield db
        finally:
            active[0] = False

    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    monkeypatch.setattr(service.repo, "claim", AsyncMock(side_effect=[job, None]))
    monkeypatch.setattr(service.repo, "lock_job", AsyncMock(return_value=job))
    monkeypatch.setattr(service.entries, "get_entry", AsyncMock(return_value=record))
    ticket = {
        "id": job.id,
        "walk_id": job.walk_id,
        "entry_id": job.entry_id,
        "revision": 1,
        "policy": job.policy_version,
        "tag": job.tag,
        "token": job.lease_token,
        "attempt": 1,
        "collection_round": 0,
        "content": dict(CONTENT),
    }
    return SimpleNamespace(
        job=job, record=record, db=db, factory=factory, active=active, added=added, ticket=ticket
    )
