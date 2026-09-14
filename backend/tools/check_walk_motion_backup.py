"""Explicit disposable-DB check, never part of default pytest and never silently skipped.

uv run python tools/check_walk_motion_backup.py --dsn postgresql+asyncpg://.../walk_motion_test
Creates and removes only its UUID schema. Includes real HTTP, transactions and SQL mutations.
"""

import argparse
import asyncio
import base64
import copy
import importlib.util
import json
import os
import struct
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]


async def main(dsn):
    address = make_url(dsn)
    if (
        address.drivername != "postgresql+asyncpg"
        or address.host not in ("localhost", "127.0.0.1", "::1")
        or address.database != "walk_motion_test"
    ):
        raise ValueError("requires an explicit loopback walk_motion_test database")
    # Only import the application after setting synthetic test settings; never load shared credentials.
    os.environ.update(
        DAENGS_DB_HOST="localhost",
        DAENGS_DB_PASSWORD="motion-test-only",
        DAENGS_KAKAO_APP_KEYS='["motion-test-only"]',
        DAENGS_WARM_UP_ENCODER="false",
    )
    for name, byte in [("AES", 1), ("BLIND_INDEX", 2), ("JWE", 3)]:
        os.environ[f"DAENGS_{name}_KEY"] = base64.urlsafe_b64encode(bytes([byte]) * 32).decode()
    from daengs_backend.core.database import get_session
    from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
    from daengs_backend.models import WalkPointChunk
    from daengs_backend.routers import walk_motion
    from daengs_backend.schemas.walk import WalkPointUpload
    from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
    from daengs_backend.services.walk_session.chunk import encode_chunk
    from daengs_backend.services.walk_session.finalize import walk_input_fingerprint
    from daengs_backend.services.walk_session.motion_contract import (
        chunk_digest,
        evidence_digest,
        manifest_digest,
    )

    admin = create_async_engine(dsn, poolclass=NullPool)
    schema = "motion_check_" + uuid.uuid4().hex
    engine = create_async_engine(
        dsn, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, other, walk_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fixture = json.loads(
        (ROOT / "backend/tests/walk/fixtures/gps-motion-backup-v1.json").read_text(encoding="utf-8")
    )
    manifest = fixture["manifest"]
    # Two chunks, reverse arrival order. Int64 and special Float bits survive real JSONB.
    points = [dict(fixture["points"][i % 3], client_seq=i) for i in range(257)]
    raw = [
        WalkPointUpload.model_validate(dict(fixture["raw_points"][i % 3], client_seq=i))
        for i in range(257)
    ]
    manifest["point_count"] = 257
    manifest["epochs"][0].update(target_ingress_seq=256, persisted_count=257)
    manifest["raw_input_fingerprint"] = walk_input_fingerprint(raw)
    md = manifest_digest(MotionManifest.model_validate(manifest))
    hashes = [
        chunk_digest([MotionObservation.model_validate(p) for p in part])
        for part in (points[:256], points[256:])
    ]
    complete = {"manifest_fingerprint": md, "evidence_fingerprint": evidence_digest(md, hashes)}
    app = FastAPI()
    app.include_router(walk_motion.router)

    async def current():
        return AppPrincipal(app_user_id=owner)

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = current
    app.dependency_overrides[get_session] = db
    count = 0
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            pg = (await conn.get_raw_connection()).driver_connection
            await pg.execute(
                "CREATE TABLE app_users(id uuid PRIMARY KEY); CREATE TABLE pets(id uuid PRIMARY KEY)"
            )
            await pg.execute((ROOT / "db/init/06_walks.sql").read_text(encoding="utf-8"))
            await pg.execute("INSERT INTO app_users VALUES ($1)", owner)
            await pg.execute(
                "INSERT INTO walks(id,app_user_id,client_session_id,started_at,ended_at,analysis_state) VALUES ($1,$2,$3,$4,$5,'derived')",
                walk_id,
                owner,
                uuid.UUID(manifest["client_session_id"]),
                datetime(2026, 9, 11, tzinfo=UTC),
                datetime(2026, 9, 11, 0, 0, 5, tzinfo=UTC),
            )
        async with sessions.begin() as session:
            session.add(
                WalkPointChunk(
                    walk_id=walk_id,
                    seq_from=0,
                    seq_to=256,
                    point_count=257,
                    payload=encode_chunk(raw),
                )
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = f"/app/walks/{walk_id}/motion-backup"
            calculation_path = f"/app/walks/{walk_id}/motion-calculation"

            async def request(method, url, status=200, **kwargs):
                nonlocal count
                result = await client.request(method, url, **kwargs)
                assert result.status_code == status, result.text
                count += 1
                return result.json()

            assert not (await request("GET", "/app/walks/motion-capabilities"))["backup_supported"]
            await request("PUT", path, 503, json=manifest)
            await request("GET", calculation_path, 503)
            async with engine.begin() as conn:
                pg = (await conn.get_raw_connection()).driver_connection
                for filename in ["db/migrations/2026-09-11_walk_motion_backup.sql"] * 2 + [
                    "db/init/35_walk_motion_backup.sql",
                    "db/migrations/verify_2026-09-11_walk_motion_backup.sql",
                ]:
                    await pg.execute((ROOT / filename).read_text(encoding="utf-8"))
            assert (await request("GET", "/app/walks/motion-capabilities"))["backup_supported"]
            wrong_raw = copy.deepcopy(manifest)
            wrong_raw["raw_input_fingerprint"] = "sha256:" + "0" * 64
            await request("PUT", path, 409, json=wrong_raw)
            await request("GET", path, 404)
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE walks SET analysis_state='collecting' WHERE id=:id"),
                    {"id": walk_id},
                )
            await request("PUT", path, 409, json=manifest)
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE walks SET analysis_state='derived' WHERE id=:id"), {"id": walk_id}
                )
            body = await request("PUT", path, json=manifest)
            assert body["state"] == "collecting" and body["manifest_fingerprint"] == md
            await request("PUT", path, json=manifest)
            await request("POST", path + "/complete", 409, json=complete)
            await request("GET", path + "/chunks/0", 409)
            wrong = copy.deepcopy(manifest)
            wrong["epochs"][0]["ended_at_millis"] += 1
            await request("PUT", path, 409, json=wrong)
            app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: (
                AppPrincipal(app_user_id=other)
            )
            for method, suffix, payload in [
                ("PUT", "", manifest),
                ("GET", "", None),
                ("POST", "/complete", complete),
                ("PUT", "/chunks/0", {"manifest_fingerprint": md, "points": points[:256]}),
                ("GET", "/chunks/0", None),
            ]:
                await request(method, path + suffix, 404, **({"json": payload} if payload else {}))
            app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = current
            await request("GET", calculation_path, 409)
            invalid = copy.deepcopy(points[256:])
            invalid[0]["source_epoch"] = "another"
            await request(
                "PUT", path + "/chunks/1", 409, json={"manifest_fingerprint": md, "points": invalid}
            )
            assert (await request("GET", path))["received_chunks"] == []
            for index, part in [(1, points[256:]), (0, points[:256])]:
                payload = {"manifest_fingerprint": md, "points": part}
                # Concurrent duplicate writes exercise actual PostgreSQL row locking.
                results = await asyncio.gather(
                    *[request("PUT", path + f"/chunks/{index}", json=payload) for _ in range(2)]
                )
                assert results[0] == results[1]
            changed = copy.deepcopy(points[:256])
            changed[0]["speed_mps_bits"] = "00000000"
            await request(
                "PUT", path + "/chunks/0", 409, json={"manifest_fingerprint": md, "points": changed}
            )
            await request(
                "POST",
                path + "/complete",
                409,
                json=dict(complete, evidence_fingerprint="sha256:" + "0" * 64),
            )
            assert (await request("GET", path))["state"] == "collecting"
            done = await request("POST", path + "/complete", json=complete)
            assert done["state"] == "complete" and done["calculation_verified"] is False
            assert done == await request("POST", path + "/complete", json=complete)
            calculation = await request("GET", calculation_path)
            assert calculation["evidence_fingerprint"] == complete["evidence_fingerprint"]
            assert calculation["device_result_verified"] is False
            assert calculation["recording_duration_nanos"] == (
                manifest["epochs"][0]["ended_elapsed_nanos"]
                - manifest["epochs"][0]["started_elapsed_nanos"]
            )
            app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: (
                AppPrincipal(app_user_id=other)
            )
            await request("GET", calculation_path, 404)
            app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = current
            await engine.dispose()  # New connections, no ORM objects reused during restore.
            assert calculation == await request("GET", calculation_path)
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE walk_motion_backups SET evidence_fingerprint=:fp WHERE walk_id=:id"
                    ),
                    {"fp": "sha256:" + "0" * 64, "id": walk_id},
                )
            await request("GET", calculation_path, 409)
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE walk_motion_backups SET evidence_fingerprint=:fp WHERE walk_id=:id"
                    ),
                    {"fp": complete["evidence_fingerprint"], "id": walk_id},
                )
            assert calculation == await request("GET", calculation_path)
            restored = []
            assert done == await request("GET", path)
            for i in range(2):
                page = await request("GET", path + f"/chunks/{i}")
                assert page["chunk_fingerprint"] == hashes[i]
                restored += page["points"]
            assert restored == points
            async with sessions() as session:
                payload = (await session.get(WalkPointChunk, (walk_id, 0))).payload
                assert payload == encode_chunk(raw)
            empty = copy.deepcopy(manifest)
            empty_id, empty_session = uuid.uuid4(), uuid.uuid4()
            empty.update(
                client_session_id=str(empty_session),
                point_count=0,
                raw_input_fingerprint=walk_input_fingerprint([]),
            )
            empty["epochs"][0].update(
                target_ingress_seq=-1, persisted_count=0, ended_at_millis=1789084805001
            )
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO walks(id,app_user_id,client_session_id,started_at,ended_at,analysis_state) "
                        "VALUES (:id,:owner,:client,:start,:end,'derived')"
                    ),
                    {
                        "id": empty_id,
                        "owner": owner,
                        "client": empty_session,
                        "start": datetime(2026, 9, 11, tzinfo=UTC),
                        "end": datetime(2026, 9, 11, 0, 0, 5, 1000, tzinfo=UTC),
                    },
                )
            empty_path = f"/app/walks/{empty_id}/motion-backup"
            empty_md = manifest_digest(MotionManifest.model_validate(empty))
            await request("PUT", empty_path, json=empty)
            empty_done = await request(
                "POST",
                empty_path + "/complete",
                json={
                    "manifest_fingerprint": empty_md,
                    "evidence_fingerprint": evidence_digest(empty_md, []),
                },
            )
            assert empty_done["state"] == "complete" and empty_done["received_chunks"] == []
            assert empty_done == await request("GET", empty_path)
            await request("GET", empty_path + "/chunks/0", 404)
            empty_calc = await request("GET", f"/app/walks/{empty_id}/motion-calculation")
            assert empty_calc["distance_m"] == 0 and empty_calc["segments"] == []
            # Additive precision tables can arrive after the base deployment.
            from daengs_backend.schemas.walk_precision import PrecisionManifest, PrecisionPoint
            from daengs_backend.services.walk_session import precision_contract as pc

            assert (await request("GET", "/app/walks/motion-capabilities"))[
                "precision_versions"
            ] == []
            precision_path = f"/app/walks/{walk_id}/motion-precision"
            await request("GET", precision_path, 503)
            async with engine.begin() as conn:
                pg = (await conn.get_raw_connection()).driver_connection
                for filename in ["db/migrations/2026-09-11_walk_precision_backup.sql"] * 2 + [
                    "db/init/36_walk_precision_backup.sql",
                    "db/migrations/verify_2026-09-11_walk_precision_backup.sql",
                ]:
                    await pg.execute((ROOT / filename).read_text(encoding="utf-8"))
            assert (await request("GET", "/app/walks/motion-capabilities"))[
                "precision_versions"
            ] == [pc.VERSION]
            await request("GET", precision_path, 404)
            await request(
                "POST", f"/app/walks/{walk_id}/measurements?version=walk-measurement-v1", 503
            )
            async with engine.begin() as conn:
                pg = (await conn.get_raw_connection()).driver_connection
                for filename in ["db/migrations/2026-09-13_walk_measurements.sql"] * 2 + [
                    "db/init/37_walk_measurements.sql",
                    "db/migrations/verify_2026-09-13_walk_measurements.sql",
                ]:
                    await pg.execute((ROOT / filename).read_text(encoding="utf-8"))
            assert (await request("GET", "/app/walks/trajectory-capabilities"))[
                "persisted_measurements_supported"
            ]
            for selected_id, selected_raw, base_done in [
                (walk_id, raw, complete),
                (empty_id, [], empty_done),
            ]:
                cid = (
                    manifest["client_session_id"] if selected_id == walk_id else str(empty_session)
                )
                pm = PrecisionManifest(
                    client_session_id=cid,
                    base_evidence_fingerprint=base_done["evidence_fingerprint"],
                    point_count=len(selected_raw),
                )
                pm_hash = pc.manifest_digest(pm)
                pp = [
                    PrecisionPoint(
                        client_seq=i,
                        lat_bits=struct.pack("!d", float(r.lat) + 1e-8).hex(),
                        lng_bits=struct.pack("!d", float(r.lng) + 2e-8).hex(),
                        accuracy_bits=None
                        if r.accuracy_m is None
                        else struct.pack("!f", r.accuracy_m).hex(),
                    )
                    for i, r in enumerate(selected_raw)
                ]
                parts = [pp[i : i + 256] for i in range(0, len(pp), 256)]
                receipt = {
                    "manifest_fingerprint": pm_hash,
                    "evidence_fingerprint": pc.evidence_digest(
                        pm_hash, [pc.chunk_digest(part) for part in parts]
                    ),
                }
                ppath = f"/app/walks/{selected_id}/motion-precision"
                cpath = f"/app/walks/{selected_id}/motion-calculation"
                await request("PUT", ppath, json=pm.model_dump())
                await request("GET", cpath, 409)
                if parts:
                    await request("POST", ppath + "/complete", 409, json=receipt)
                    await request("GET", ppath + "/chunks/0", 409)
                    bad = [q.model_dump() for q in parts[0]]
                    bad[0]["lat_bits"] = "0000000000000000"
                    await request(
                        "PUT",
                        ppath + "/chunks/0",
                        409,
                        json={"manifest_fingerprint": pm_hash, "points": bad},
                    )
                for i in reversed(range(len(parts))):
                    body = {
                        "manifest_fingerprint": pm_hash,
                        "points": [q.model_dump() for q in parts[i]],
                    }
                    replies = await asyncio.gather(
                        *[request("PUT", ppath + f"/chunks/{i}", json=body) for _ in range(2)]
                    )
                    assert replies[0] == replies[1]
                app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: (
                    AppPrincipal(app_user_id=other)
                )
                for method, suffix, body in [
                    ("GET", "", None),
                    ("PUT", "", pm.model_dump()),
                    ("POST", "/complete", receipt),
                    ("GET", "/chunks/0", None),
                ]:
                    await request(method, ppath + suffix, 404, **({"json": body} if body else {}))
                app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = current
                done = await request("POST", ppath + "/complete", json=receipt)
                assert done == await request("POST", ppath + "/complete", json=receipt)
                assert done["state"] == "complete"
                calc = await request("GET", cpath)
                assert (
                    calc["coordinate_basis"] == "device-fix-bits-v1"
                    and calc["precision_fingerprint"] == receipt["evidence_fingerprint"]
                )
                assert calc["device_result_verified"] is False
                mpath = f"/app/walks/{selected_id}/measurements"
                query = "?version=walk-measurement-v1"
                await request("POST", mpath, 422)
                results = await asyncio.gather(*[request("POST", mpath + query) for _ in range(2)])
                assert results[0] == results[1]
                measured = results[0]
                mid = measured["measurement"]["measurement_id"]
                assert measured == await request("GET", mpath + f"/{mid}" + query)
                assert measured == await request("POST", mpath + query)
                # A persisted input is served without rerunning the CPU engine.
                from unittest.mock import patch

                from daengs_backend.services import walk_measurement as measurement_service

                with patch.object(
                    measurement_service, "project", side_effect=AssertionError("recomputed")
                ):
                    assert measured == await request("POST", mpath + query)
                # Exact stored bytes are checked on reads and cache hits too.
                async with engine.begin() as conn:
                    original = await conn.scalar(
                        text("SELECT payload FROM walk_measurements WHERE walk_id=:id"),
                        {"id": selected_id},
                    )
                    await conn.execute(
                        text(
                            "UPDATE walk_measurements SET payload=payload || ' ' WHERE walk_id=:id"
                        ),
                        {"id": selected_id},
                    )
                await request("GET", mpath + f"/{mid}" + query, 409)
                await request("POST", mpath + query, 409)
                async with engine.begin() as conn:
                    await conn.execute(
                        text("UPDATE walk_measurements SET payload=:payload WHERE walk_id=:id"),
                        {"id": selected_id, "payload": original},
                    )
                import hashlib

                for ref in measured["required_route_chunks"]:
                    reply = await client.get(mpath + f"/{mid}/chunks/{ref['index']}" + query)
                    assert reply.status_code == 200, reply.text
                    assert hashlib.sha256(reply.content).hexdigest() == ref["sha256"]
                    assert len(reply.content) == ref["byte_size"]
                    assert reply.headers["etag"] == '"' + ref["sha256"] + '"'
                    assert reply.headers["cache-control"] == "private, no-store"
                    count += 1
                app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: (
                    AppPrincipal(app_user_id=other)
                )
                await request("POST", mpath + query, 404)
                await request("GET", mpath + f"/{mid}" + query, 404)
                await request("GET", mpath + f"/{mid}/chunks/0" + query, 404)
                app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = current
                await request("GET", mpath + f"/{mid}/chunks/1999" + query, 404)

                for i, part in enumerate(parts):
                    assert (await request("GET", ppath + f"/chunks/{i}"))["points"] == [
                        q.model_dump() for q in part
                    ]
                if parts:
                    changed = [q.model_dump() for q in parts[0]]
                    changed[0]["lat_bits"] = struct.pack(
                        "!d", float(selected_raw[0].lat) + 3e-8
                    ).hex()
                    await request(
                        "PUT",
                        ppath + "/chunks/0",
                        409,
                        json={"manifest_fingerprint": pm_hash, "points": changed},
                    )
            # Deletion can win while calculation is detached. No output may resurrect the walk.
            import threading
            from unittest.mock import patch

            from daengs_backend.services import walk_measurement as measurement_service

            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM walk_measurements WHERE walk_id=:id"), {"id": empty_id}
                )
            entered, release = threading.Event(), threading.Event()
            original_project = measurement_service.project

            def blocked_project(*args):
                entered.set()
                assert release.wait(10)
                return original_project(*args)

            with patch.object(measurement_service, "project", blocked_project):
                pending = asyncio.create_task(
                    request(
                        "POST",
                        f"/app/walks/{empty_id}/measurements?version=walk-measurement-v1",
                        404,
                    )
                )
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    async with engine.begin() as conn:
                        await conn.execute(text("DELETE FROM walks WHERE id=:id"), {"id": empty_id})
                finally:
                    release.set()
                await pending
            async with engine.begin() as conn:
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM walk_measurements WHERE walk_id=:id"),
                        {"id": empty_id},
                    )
                    == 0
                )
            async with sessions() as session:
                assert (await session.get(WalkPointChunk, (walk_id, 0))).payload == encode_chunk(
                    raw
                )
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM app_users WHERE id=:id"), {"id": owner})
                assert await conn.scalar(text("SELECT count(*) FROM walk_motion_backups")) == 0
                assert await conn.scalar(text("SELECT count(*) FROM walk_motion_chunks")) == 0
                assert await conn.scalar(text("SELECT count(*) FROM walk_precision_backups")) == 0
                assert await conn.scalar(text("SELECT count(*) FROM walk_precision_chunks")) == 0
                assert await conn.scalar(text("SELECT count(*) FROM walk_measurements")) == 0
                assert await conn.scalar(text("SELECT count(*) FROM walk_measurement_chunks")) == 0
            await request("GET", path, 404)
            await request("GET", calculation_path, 404)
        # Reuse the registered mutation cases, scoped to this migration. No psql or full SQL suite required.
        spec = importlib.util.spec_from_file_location(
            "migration_check", ROOT / "tools/check_migration_verification.py"
        )
        checks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checks)
        mutation_count = 0
        for name in ("walk_motion_backup", "walk_precision_backup", "walk_measurements"):
            case = next(c for c in checks.CHECKS if c[1] == name)
            for mutation in ["", f"DROP TABLE {case[3]} CASCADE", *case[4]]:
                async with engine.connect() as conn:
                    transaction = await conn.begin()
                    # SQLAlchemy begins lazily; force BEGIN before using the underlying asyncpg connection.
                    await conn.execute(text("SELECT 1"))
                    pg = (await conn.get_raw_connection()).driver_connection
                    if mutation:
                        await pg.execute(mutation)
                    try:
                        await pg.execute(
                            (ROOT / f"db/migrations/verify_{case[0]}_{name}.sql").read_text(
                                encoding="utf-8"
                            )
                        )
                    except Exception:
                        if not mutation:
                            raise
                    else:
                        assert not mutation, f"verifier missed: {mutation}"
                    await transaction.rollback()
                    mutation_count += 1
        print(
            f"GPS backup: {count} HTTP checks, {mutation_count} migration cases; all passed, no skips"
        )
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    asyncio.run(main(parser.parse_args().dsn))
