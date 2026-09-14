"""Real entry outbox -> stored observation -> diary writer -> historical receipt."""

import uuid
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from daengs_backend.config import settings
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_context import WalkEntryContextEnvelope, WalkEntryContextJob
from daengs_backend.models.walk_storyboard import WalkStoryboard
from daengs_backend.services import walk_entry_context
from daengs_backend.services.walk_diary_board_slot_writing import write_legacy_slot_board
from daengs_backend.services.walk_diary_generation import generate_diary, get_diary
from daengs_life.app import deps
from daengs_life.realtime.cache import Cache, MemoryStore
from daengs_life.realtime.providers import kma_vilage_fcst
from daengs_walk.diary_board_output import BOARD_FORMAT
from tests.walk.diary.test_diary_board_db import board_database, spec  # noqa: F401
from tests.walk.support.entry_v2 import AT, ENTRY, OWNER, WALK
from tests.walk.support.photo_input import entry


async def test_collected_temperature_reaches_writer_and_is_frozen_after_source_removal(
    board_database,  # noqa: F811 -- imported pytest fixture
    monkeypatch,
):
    factory = board_database
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    monkeypatch.setattr(settings, "realtime_url", "")
    monkeypatch.setattr(deps, "get_now", lambda: AT + timedelta(hours=1))
    monkeypatch.setattr(deps, "get_cache", lambda: Cache(MemoryStore()))
    calls = []

    def ncst(grid, hour, **_):
        calls.append((grid, hour))
        return {
            "items": {
                "item": [
                    {
                        "baseDate": hour.strftime("%Y%m%d"),
                        "baseTime": hour.strftime("%H%M"),
                        "nx": grid.nx,
                        "ny": grid.ny,
                        "category": "T1H",
                        "obsrValue": "22.5",
                    }
                ]
            }
        }

    monkeypatch.setattr(kma_vilage_fcst, "raw_ncst_at", ncst)
    payload = entry().payload
    payload["recorded_at"] = payload["location"]["captured_at"] = AT.isoformat()
    async with factory() as db:
        db.add(
            WalkEntry(walk_id=WALK, id=ENTRY, revision=1, mutation_id=uuid.uuid4(), payload=payload)
        )
        await db.flush()
        db.add(
            WalkEntryContextJob(
                id=uuid.uuid4(),
                walk_id=WALK,
                entry_id=ENTRY,
                revision=1,
                policy_version="walk-entry-context-v1",
                tag="environment.weather",
                state="pending",
                attempts=0,
                available_at=AT,
            )
        )
        await db.commit()
    assert await walk_entry_context.process(factory, limit=1) == 1
    assert len(calls) == 1
    async with factory() as db:
        envelope = (await db.scalar(select(WalkEntryContextEnvelope))).envelope
        assert envelope["provenance"]["temporal_basis"] == "source_observation"
        assert envelope["payload"]["temperature_c"] == 22.5
        assert datetime.fromisoformat(envelope["payload"]["observed_at"]) == AT

    async def prose(payload, schema):
        return {
            "scenes": [
                {
                    "scene_id": s["scene_id"],
                    "text": "이 지역의 기온 관측값은 22.5도였다.",
                    "evidence_ids": [s["scene"]["environment"][0]["id"]],
                    "action_id": s["action"]["id"] if s["action"] else None,
                }
                for s in payload["scenes"]
            ]
        }

    async def writer(source, base):
        return await write_legacy_slot_board(source, base, prose)

    async with factory() as db:
        first = await generate_diary(
            db, OWNER, WALK, spec(expected_entries={str(ENTRY): 1}), writer=writer
        )
        assert first.bundle.model_status == "accepted"
        saved = deepcopy((await db.get(WalkStoryboard, WALK)).bundle)
        citations = [e for s in saved["writing_receipt"]["scenes"] for e in s["evidence"]]
        assert len(citations) == 1
        evidence = citations[0]
        assert evidence["part"] == "environment" and evidence["facts"]["temperature_c"] == 22.5
        assert evidence["facts"]["grid"] == list(calls[0][0])
        assert evidence["sources"][0]["source_id"] == envelope["id"]
        assert any(s.body.endswith(payload["note"]) for s in first.bundle.scenes)
        await db.execute(delete(WalkEntryContextEnvelope))
        await db.commit()
    async with factory() as db:
        again = await get_diary(db, OWNER, WALK, 3, BOARD_FORMAT)
        assert again.bundle == first.bundle and again.background_update_available
        assert (await db.get(WalkStoryboard, WALK)).bundle == saved
    assert "writing_receipt" not in first.model_dump_json()
