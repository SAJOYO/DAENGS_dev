"""A leased photo worker preserves the enabled game's lock and receipt boundaries."""

from datetime import UTC, datetime

from sqlalchemy import func, select, text

from daengs_backend.core import database as connections
from daengs_backend.models import TerritoryAttempt
from daengs_backend.models.activity import ActivityGameReceipt
from daengs_backend.services import territory
from daengs_backend.services import territory_vision as vision
from tests.activity.support.actions import mark, season
from tests.territory.support import ownership as base


async def test_leased_photo_releases_game_lock_during_model_and_records_once(
    database, actors, clock, monkeypatch
):
    owner, pet = actors[0][0], actors[1][0]
    await season(database, clock)
    client = await base.begin(database, owner, [pet])
    claim = await mark(database, clock, owner, client, pet)
    photo_id = await base.photo(
        database,
        owner,
        client,
        claim,
        captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC),
    )
    async with database() as db:
        receipts_before = await db.scalar(select(func.count()).select_from(ActivityGameReceipt))

    class Storage:
        def read_bytes(self, key, *, generation, max_bytes):
            assert generation == "test-generation" and max_bytes == 4
            return b"jpeg"

        def redact(self, key, *, generation):
            assert generation == "test-generation"

    class Classifier:
        provider_name, model_version, calls = "test-vlm", "v1", 0

        async def classify(self, **kwargs):
            self.calls += 1
            async with database() as db:
                assert await db.scalar(text("SELECT pg_try_advisory_xact_lock(260, 36)"))
                saved = await db.scalar(
                    select(TerritoryAttempt)
                    .where(TerritoryAttempt.id == photo_id)
                    .with_for_update(nowait=True)
                )
                assert saved.vision_lease_token is not None
            return vision.TerritoryVisionResult("verified", "dog_visible")

    monkeypatch.setattr(connections, "worker_session", database)
    monkeypatch.setattr(territory, "get_storage", Storage)
    monkeypatch.setattr(vision, "get_storage", Storage)
    classifier = Classifier()
    await vision.process_attempt(photo_id, classifier=classifier)
    assert await vision.process_attempt(photo_id, classifier=classifier) is None
    assert classifier.calls == 1
    async with database() as db:
        assert (await db.get(TerritoryAttempt, photo_id)).status == "VERIFIED"
        assert (
            await db.scalar(select(func.count()).select_from(ActivityGameReceipt))
            == receipts_before + 1
        )
