"""Activity schema extension and clock shared with certified territory tests."""

from datetime import UTC, datetime

import pytest

from daengs_backend.config import settings
from daengs_backend.services import activity_game
from tests.territory.support import ownership as base
from tests.territory.support.paths import REPO as ROOT


@pytest.fixture
async def database(territory_database):
    async with territory_database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute((ROOT / "db/init/06_walks.sql").read_text(encoding="utf-8"))
        sql = (ROOT / "db/migrations/2026-09-06_activity_game.sql").read_text(encoding="utf-8")
        await raw.execute(sql)
        await raw.execute(sql)
        await raw.execute((ROOT / "db/init/21_activity_game.sql").read_text(encoding="utf-8"))
        await raw.execute(
            (ROOT / "db/migrations/verify_2026-09-06_activity_game.sql").read_text(encoding="utf-8")
        )
        await db.commit()
    return territory_database


@pytest.fixture
def clock(monkeypatch):
    current = [int(datetime.now(UTC).timestamp() * 1000)]
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    monkeypatch.setattr(activity_game, "now_ms", lambda: current[0])
    monkeypatch.setattr(base.svc, "_now", lambda: datetime.fromtimestamp(current[0] / 1000, UTC))
    return current
