"""Shared claim, photo and verdict builders; scenarios stay in tests."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from daengs_backend.models import TerritoryAttempt
from daengs_backend.schemas.territory_claim import MarkRequest, SessionStart
from daengs_backend.services import territory as visits
from daengs_backend.services import territory_ownership as svc
from daengs_backend.services.territory_site_lookup import (
    TerritorySiteSnapshot,
)

SITE = "territory-site:hex-v1:140:324:777"
SITE2 = "territory-site:hex-v1:140:325:777"


class Lookup:
    async def find_near_capture(self, *, site_id, **kwargs):
        if site_id not in {SITE, SITE2}:
            return None
        return TerritorySiteSnapshot(site_id, Decimal("37.5000000"), Decimal("127.0000000"))


async def begin(factory, owner, pets, client_id=None, *, started_at=None):
    client_id = client_id or uuid.uuid4()
    if started_at is None:
        started_at = datetime.now(UTC) - timedelta(minutes=1)
    body = SessionStart(started_at=started_at, pet_ids=pets)
    async with factory() as db:
        await svc.start_session(db, owner, client_id, body)
    return client_id


def mark_body(client_id, pet, **overrides):
    return MarkRequest(
        client_session_id=client_id,
        claiming_pet_id=pet,
        site_id=overrides.pop("site_id", SITE),
        observed_at=overrides.pop("observed_at", datetime.now(UTC)),
        lat=overrides.pop("lat", "37.5000000"),
        lng="127.0000000",
        accuracy_m=overrides.pop("accuracy_m", 3),
        **overrides,
    )


async def mark(factory, owner, client_id, pet, **overrides):
    async with factory() as db:
        return await svc.mark(db, owner, mark_body(client_id, pet, **overrides), Lookup())


async def photo(
    factory, owner, client_id, claim, *, captured_at=None, site_id=SITE, capture_id=None
):
    photo_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as db:
        db.add(
            TerritoryAttempt(
                id=photo_id,
                app_user_id=owner,
                client_capture_id=capture_id or uuid.uuid4(),
                client_session_id=client_id,
                site_id=site_id,
                captured_at=captured_at or now,
                capture_lat=Decimal("37.5000000"),
                capture_lng=Decimal("127.0000000"),
                site_lat=Decimal("37.5000000"),
                site_lng=Decimal("127.0000000"),
                accuracy_m=3,
                is_mock=False,
                distance_m=0,
                status="PENDING_UPLOAD",
                photo_storage_key=f"test/{photo_id}",
                photo_content_type="image/jpeg",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    async with factory() as db:
        await svc.bind_photo(db, owner, claim.claim_id, photo_id)
    async with factory() as db:
        row = await db.get(TerritoryAttempt, photo_id)
        row.status = "VISION_PENDING"
        row.photo_object_generation = "test-generation"
        row.photo_size_bytes = 4
        await db.commit()
    return photo_id


async def decide(factory, photo_id, monkeypatch, decision="verified"):
    class Storage:
        def redact(self, *args, **kwargs):
            return "redacted"

    monkeypatch.setattr(visits, "get_storage", lambda: Storage())
    async with factory() as db:
        return await visits.record_vision_decision(
            db,
            photo_id,
            decision=decision,
            model="test",
            model_version="1",
        )
