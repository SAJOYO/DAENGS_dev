"""기록 삭제/재전송과 프로필 분모·행동 귀속의 경계를 검증한다."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from daengs_backend.schemas.walk_entry import EntryContent, EntryWrite, RecordProfileQuery
from daengs_backend.services import walk_entry as service

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def content(pet=None, code="sniffing"):
    return EntryContent(
        kind="behavior",
        behavior_code=code,
        recorded_at=NOW,
        pet_id=pet,
        location={"lat": 37.5, "lng": 127, "captured_at": NOW},
    )


def row(walk, payload, revision=1):
    return service.apply_change(None, walk, uuid.uuid4(), 0, uuid.uuid4(), payload)


def test_retry_is_idempotent_and_reused_mutation_cannot_change_payload():
    walk, entry, mutation = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    payload = content().model_dump(mode="json")
    saved = service.apply_change(None, walk, entry, 0, mutation, payload)
    assert service.apply_change(saved, walk, entry, 0, mutation, payload).revision == 1
    with pytest.raises(service.EntryConflict):
        service.apply_change(saved, walk, entry, 0, mutation, None)


def test_delete_erases_content_and_blocks_late_create():
    saved = row(uuid.uuid4(), content().model_dump(mode="json"))
    deleted = service.apply_change(saved, saved.walk_id, saved.id, 1, uuid.uuid4(), None)
    assert deleted.payload is None and deleted.revision == 2
    with pytest.raises(service.EntryConflict):
        service.apply_change(
            deleted, saved.walk_id, saved.id, 0, uuid.uuid4(), content().model_dump(mode="json")
        )
    assert (
        service.apply_change(deleted, saved.walk_id, saved.id, 1, uuid.uuid4(), None).revision == 2
    )


def test_delete_before_upload_blocks_delayed_upload():
    saved = row(uuid.uuid4(), None)
    with pytest.raises(service.EntryConflict):
        service.apply_change(
            saved, saved.walk_id, saved.id, 0, uuid.uuid4(), content().model_dump(mode="json")
        )


def test_stale_revision_does_not_overwrite():
    saved = row(uuid.uuid4(), content().model_dump(mode="json"))
    with pytest.raises(service.EntryConflict):
        service.apply_change(
            saved,
            saved.walk_id,
            saved.id,
            0,
            uuid.uuid4(),
            content(code="barking").model_dump(mode="json"),
        )
    assert saved.payload["behavior_code"] == "sniffing"


def test_profile_counts_records_and_distinct_walks_not_actual_behavior():
    pet, other = uuid.uuid4(), uuid.uuid4()
    walks = [SimpleNamespace(id=uuid.uuid4()) for _ in range(3)]
    rows = [row(walks[i].id, content(pet).model_dump(mode="json")) for i in [0, 0, 1]]
    rows += [
        row(walks[0].id, content(other).model_dump(mode="json")),
        row(walks[1].id, content().model_dump(mode="json")),
        row(
            walks[0].id,
            EntryContent(kind="note", note="킁킁했다", recorded_at=NOW).model_dump(mode="json"),
        ),
    ]
    spec = RecordProfileQuery(pet_id=pet)
    before = service.build_profile(spec, walks, rows)
    assert before["walk_count"] == 3
    assert before["unassigned_entry_count"] == 1
    assert before["behaviors"]["sniffing"] == {"entry_count": 3, "walks_with_entries": 2}
    assert len(before["evidence"]) == 3
    service.apply_change(rows[2], rows[2].walk_id, rows[2].id, 1, uuid.uuid4(), None)
    after = service.build_profile(spec, walks, rows)
    assert after["behaviors"]["sniffing"] == {"entry_count": 2, "walks_with_entries": 1}
    assert after["walk_count"] == 3
    assert before["source_revision"] != after["source_revision"]


@pytest.mark.parametrize(
    "patch",
    [
        {"behavior_code": "explore"},
        {"note": "메모"},
        {"location": None},
        {"recorded_at": NOW.replace(tzinfo=None)},
    ],
)
def test_invalid_behavior_is_rejected(patch):
    with pytest.raises(ValidationError):
        EntryContent.model_validate({**content().model_dump(), **patch})


def test_note_without_gps_is_valid_but_empty_note_is_not():
    assert EntryContent(kind="note", note="  오늘의 기억  ", recorded_at=NOW).note == "오늘의 기억"
    with pytest.raises(ValidationError):
        EntryContent(kind="note", note=" ", recorded_at=NOW)


@pytest.mark.asyncio
async def test_owner_boundary_precedes_entry_lookup(monkeypatch):
    monkeypatch.setattr(service.repo, "owned_walk", AsyncMock(return_value=None))
    lookup = AsyncMock()
    monkeypatch.setattr(service.repo, "get_entry", lookup)
    with pytest.raises(service.EntryNotFound):
        await service.write(
            AsyncMock(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            EntryWrite(expected_revision=0, mutation_id=uuid.uuid4(), content=content()),
        )
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_unrelated_dog_is_rejected(monkeypatch):
    monkeypatch.setattr(
        service.repo,
        "owned_walk",
        AsyncMock(return_value=SimpleNamespace(started_at=NOW, ended_at=NOW, pet_ids=[])),
    )
    with pytest.raises(service.EntryInvalid):
        await service.write(
            AsyncMock(),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            EntryWrite(
                expected_revision=0, mutation_id=uuid.uuid4(), content=content(uuid.uuid4())
            ),
        )
