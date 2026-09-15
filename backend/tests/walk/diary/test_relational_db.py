"""Disposable PostgreSQL HTTP round trip and source-edit/CAS checks; no shared database."""

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.models.walk import WalkPointChunk
from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk_generation import StoryboardRequest
from daengs_backend.schemas.walk_relational_diary import RELATIONAL_FORMAT
from daengs_backend.services.walk_diary.lifecycle.relational import (
    generate_relational,
    get_relational,
)
from daengs_backend.services.walk_diary.runtime import write_relational_board
from tests.walk.diary.test_relational_orchestration import (
    execution,
    prepare,  # noqa: F401 -- fixture registration
    public_collector,  # noqa: F401 -- fixture registration
    send,
)
from tests.walk.support.entry_v2 import OWNER, WALK
from tests.walk.support.observations import stored, uploaded
from tests.walk.support.paths import REPO


@pytest.fixture
async def relational_database(photo_database, monkeypatch):
    factory = photo_database
    monkeypatch.setattr(settings, "walk_diary_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", False)
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    walk, analysis, points = stored(uploaded([(i * 10, i * 12) for i in range(61)]))
    entry_id = uuid.uuid4()
    async with factory() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute(
            (REPO / "db/migrations/2026-09-05_walk_storyboards.sql").read_text(encoding="utf-8")
        )
        await raw.execute(
            "UPDATE walks SET id=$1, client_session_id=$2, started_at=$3, ended_at=$4, analysis_state='derived' WHERE id=$5",
            walk.id,
            walk.client_session_id,
            walk.started_at,
            walk.ended_at,
            WALK,
        )
        for chunk in walk.points:
            db.add(
                WalkPointChunk(
                    walk_id=walk.id,
                    seq_from=chunk.seq_from,
                    seq_to=chunk.seq_to,
                    point_count=chunk.point_count,
                    payload=chunk.payload,
                )
            )
        db.add(analysis)
        p = points[30]
        payload = {
            "kind": "behavior",
            "behavior_code": "sniffing",
            "pet_id": None,
            "recorded_at": p.at.isoformat(),
            "location": {
                "lat": p.lat,
                "lng": p.lng,
                "captured_at": p.at.isoformat(),
                "accuracy_m": 5,
            },
        }
        await raw.execute(
            "INSERT INTO walk_entries VALUES ($1,$2,1,$3,$4::jsonb)",
            walk.id,
            entry_id,
            uuid.uuid4(),
            json.dumps(payload),
        )
        await db.commit()
    request = StoryboardRequest(
        bundle_format=RELATIONAL_FORMAT, expected_entries={entry_id: 1}, target_scene_count=1
    )
    return factory, walk.id, request


async def test_relational_http_postgres_roundtrip(relational_database, request, monkeypatch):
    factory, walk_id, spec = relational_database
    app = FastAPI()
    app.include_router(router.router)

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    live = os.environ.get("RELATIONAL_LIVE_SMOKE") == "1"
    collector = None if live else request.getfixturevalue("prepare")
    if live:
        from dotenv import dotenv_values
        from pydantic import SecretStr

        values = dotenv_values(os.environ["RELATIONAL_SMOKE_ENV"])
        # The user-supplied credential note also supports the existing scenario's gemini: line.
        for line in (
            Path(os.environ["RELATIONAL_SMOKE_ENV"]).read_text(encoding="utf-8-sig").splitlines()
        ):
            if line.strip().lower().startswith("gemini:"):
                values["GEMINI_API_KEY"] = line.split(":", 1)[1].strip().strip("\"'")
        values["GEMINI_API_KEY"] = values.get("GEMINI_API_KEY") or values.get("GOOGLE_API_KEY")
        for key, attr in (
            ("GEMINI_API_KEY", "gemini_api_key"),
            ("DAENGS_WALK_SGIS_KEY", "walk_sgis_key"),
            ("DAENGS_WALK_SGIS_SECRET", "walk_sgis_secret"),
            ("DAENGS_WALK_PUBLIC_DATA_KEY", "walk_public_data_key"),
        ):
            if values.get(key):
                monkeypatch.setattr(settings, attr, SecretStr(values[key]))
        monkeypatch.setattr(settings, "walk_diary_space_enabled", True)
        assert settings.gemini_api_key.get_secret_value().strip(), "live smoke requires a model key"

    async def writer(source, base, **kwargs):
        # A second session must acquire the Walk lock while external writing runs.
        async with factory() as other:
            await asyncio.wait_for(
                other.execute(
                    text("SELECT id FROM walks WHERE id=:id FOR UPDATE"), {"id": walk_id}
                ),
                2,
            )
            await other.commit()
        return (
            await write_relational_board(source, base, **kwargs)
            if live
            else await write_relational_board(
                source,
                base,
                prepare=collector,
                send=send,
                execution_policy=execution(),
            )
        )

    app.dependency_overrides[router.get_diary_writer] = lambda: writer
    path = f"/app/walks/{walk_id}/storyboard"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        post = await client.post(path, json=spec.model_dump(mode="json"))
        assert post.status_code == 200, post.text
        value = post.json()
        assert value["status"] == "ready", value
        get = await client.get(
            path, params={"bundle_format": RELATIONAL_FORMAT, "target_scene_count": 1}
        )
        assert get.json() == value
        assert len(value["bundle"]["cards"]) == 3
        async with factory() as db:
            raw = await db.scalar(
                text("SELECT bundle FROM walk_storyboards WHERE walk_id=:id"), {"id": walk_id}
            )
            assert raw["payload"]["public"] == value["bundle"]
        if live:
            output = Path(os.environ["RELATIONAL_SMOKE_OUTPUT"])
            output.write_text(
                json.dumps(
                    {
                        "synthetic_route": True,
                        "real_llm": any(
                            c["status"] == "returned"
                            for c in raw["payload"]["receipt"]["execution"]["calls"]
                        ),
                        "real_postgres": True,
                        "http_post_equals_get": get.json() == value,
                        "response": value,
                        "execution": raw["payload"]["receipt"]["execution"],
                        "receipt": raw["payload"]["receipt"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )


async def test_postgres_original_edit_and_newer_generation_win(relational_database, prepare):  # noqa: F811
    factory, walk_id, request = relational_database
    entered, release = asyncio.Event(), asyncio.Event()

    async def call(wait=False):
        async with factory() as db:

            async def writer(source, base, **kwargs):
                if wait:
                    entered.set()
                    await release.wait()
                return await write_relational_board(
                    source, base, prepare=prepare, send=send, execution_policy=execution()
                )

            return await generate_relational(db, OWNER, walk_id, request, writer=writer)

    old = asyncio.create_task(call(True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with factory() as db:
            await db.execute(
                text("UPDATE walk_entries SET revision=2, payload=NULL WHERE walk_id=:id"),
                {"id": walk_id},
            )
            await db.commit()
        async with factory() as db:
            stale = await get_relational(db, OWNER, walk_id, 1)
            assert stale.status == "stale" and stale.bundle is None
        request.expected_entries = {key: 2 for key in request.expected_entries}
        fresh = await call()
        assert fresh.status == "ready" and fresh.generation == 2
        release.set()
        assert await old == fresh
        async with factory() as db:
            await db.execute(text("DELETE FROM app_users WHERE id=:id"), {"id": OWNER})
            await db.commit()
            assert await db.scalar(text("SELECT count(*) FROM walk_storyboards")) == 0
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
