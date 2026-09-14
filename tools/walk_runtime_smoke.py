"""Explicit, temporary synthetic account/walk through the running nginx API and real Beat."""

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
from daengs_backend.core.database import engine
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from sqlalchemy import text

logging.getLogger("httpx").setLevel(logging.WARNING)


class SmokeFailure(Exception):
    """Only predefined messages; never constructed from external responses."""

    def __init__(
        self, message, *, request_number=None, validation_fields=None, diagnostics=None
    ):
        super().__init__(message)
        self.request_number = request_number
        self.validation_fields = validation_fields
        self.diagnostics = diagnostics


async def prepare_backfill(owner, walk_id):
    """Only this probe's disposable account: emulate missing public collection, then recover."""
    from daengs_backend.core.database import SessionLocal
    from daengs_backend.models.walk import Walk
    from daengs_backend.models.walk_entry_context import (
        WalkEntryContextEnvelope as Envelope,
    )
    from daengs_backend.models.walk_entry_context import WalkEntryContextJob as Job
    from daengs_backend.services.walk_context_backfill import TAGS, run
    from sqlalchemy import select

    walk_id = uuid.UUID(str(walk_id))
    saved = {}
    async with SessionLocal() as db:
        owned = await db.scalar(
            select(Walk.id)
            .where(Walk.id == walk_id, Walk.app_user_id == owner)
            .with_for_update()
        )
        if owned is None:
            raise SmokeFailure("synthetic walk ownership mismatch")
        jobs = list(
            await db.scalars(
                select(Job)
                .where(Job.walk_id == walk_id, Job.tag.in_(TAGS))
                .with_for_update()
            )
        )
        if len(jobs) != 12 or any(
            j.collection_round != 0 or j.attempts >= 3 for j in jobs
        ):
            raise SmokeFailure("unexpected synthetic collection state")
        for job in jobs:
            job.state, job.attempts = "failed", 3
            job.lease_token, job.lease_until = None, None
            raw = {
                "status": "unavailable",
                "reason": "synthetic_prior_provider_unavailable",
            }
            row = Envelope(
                id=uuid.uuid4(),
                job_id=job.id,
                collection_round=0,
                attempt=3,
                created_at=datetime.now(UTC),
                envelope=raw,
            )
            db.add(row)
            saved[row.id] = raw
        await db.commit()
    preview = await run(SessionLocal, walk_ids=[walk_id])
    applied = await run(
        SessionLocal,
        walk_ids=[walk_id],
        apply=True,
        expected_plan=preview["plan_digest"],
    )
    repeated = await run(SessionLocal, walk_ids=[walk_id])
    if applied["scheduled_sources"] != 12 or repeated["eligible_sources"] != 0:
        raise SmokeFailure("backfill did not schedule exactly once")
    return saved


async def verify_backfill(owner, walk_id, saved):
    from daengs_backend.core.database import SessionLocal
    from daengs_backend.models.walk import Walk
    from daengs_backend.models.walk_entry_context import (
        WalkEntryContextEnvelope as Envelope,
    )
    from daengs_backend.services.walk_context_backfill import run
    from sqlalchemy import select

    walk_id = uuid.UUID(str(walk_id))
    async with SessionLocal() as db:
        if (
            await db.scalar(
                select(Walk.id).where(Walk.id == walk_id, Walk.app_user_id == owner)
            )
            is None
        ):
            raise SmokeFailure("synthetic walk ownership mismatch")
        kept = list(await db.scalars(select(Envelope).where(Envelope.id.in_(saved))))
        if len(kept) != 12 or any(e.envelope != saved[e.id] for e in kept):
            raise SmokeFailure("previous collection history changed")
    final = await run(SessionLocal, walk_ids=[walk_id])
    if final["eligible_sources"] or final["skipped"] != {"stored_board": 1}:
        raise SmokeFailure("stored diary was not protected from backfill")
    return {
        "scheduled_sources": 12,
        "prior_envelopes_preserved": True,
        "duplicate_request_skipped": True,
        "stored_board_protected": True,
    }


async def probe_snapshot(owner, walk_id):
    """Read only this probe's walk; fingerprints remain in memory, never in logs."""
    async with engine.connect() as connection:
        return await connection.scalar(
            text("""SELECT jsonb_build_object(
                'row', (SELECT to_jsonb(s) FROM walk_storyboards s WHERE s.walk_id=w.id),
                'walk', md5(to_jsonb(w)::text),
                'entries', (SELECT md5(COALESCE(jsonb_agg(to_jsonb(e) ORDER BY e.id)::text,''))
                    FROM walk_entries e WHERE e.walk_id=w.id),
                'photos', (SELECT md5(to_jsonb(p)::text) FROM walk_photo_manifests p WHERE p.walk_id=w.id),
                'backgrounds', (SELECT md5(COALESCE(jsonb_agg(to_jsonb(e) ORDER BY e.id)::text,''))
                    FROM walk_entry_context_envelopes e JOIN walk_entry_context_jobs j ON j.id=e.job_id
                    WHERE j.walk_id=w.id))
                FROM walks w WHERE w.id=:walk AND w.app_user_id=:owner"""),
            {"walk": uuid.UUID(str(walk_id)), "owner": owner},
        )


def board_diagnostics(response, before, after, notes, elapsed):
    """Only statuses, counts and equality checks; never text, hashes or identifiers."""
    row = (after or {}).get("row") or {}
    raw = row.get("bundle") or {}
    reserved = raw.get("fallback") or raw
    public = response.get("bundle") or {}

    def contents(board):
        scenes = board.get("scenes") or []
        return {
            "scene_count": len(scenes),
            "writing_count": sum(s.get("writing") is not None for s in scenes),
            "notes_in_record": [
                any((s.get("user_record") or {}).get("text") == n for s in scenes)
                for n in notes
            ],
            "notes_in_body": [any(s.get("body") == n for s in scenes) for n in notes],
            "notes_in_writing": [
                any((s.get("writing") or {}).get("original_text") == n for s in scenes)
                for n in notes
            ],
        }

    deadline = raw.get("deadline_at")
    receipt = reserved.get("writing_receipt") or {}
    return {
        "status": response.get("status"),
        "error_code": response.get("error_code"),
        "post_ms": round(elapsed * 1000),
        "budget_ms": 20000,
        "model_status": public.get("model_status"),
        "failure_code": public.get("failure_code"),
        "response": contents(public),
        "stored_status": row.get("status"),
        "storage_format": raw.get("format"),
        "stored_model_status": (reserved.get("bundle") or {}).get("model_status"),
        "stored_failure_code": (reserved.get("bundle") or {}).get("failure_code"),
        "stored_or_reserved": contents(reserved.get("bundle") or {}),
        "deadline_passed": datetime.now(UTC) >= datetime.fromisoformat(deadline)
        if deadline
        else None,
        "response_revision_matches_stored": response.get("input_revision")
        == row.get("input_revision"),
        "generation_matches_stored": response.get("generation")
        == row.get("generation"),
        "input_changed": {
            k: (before or {}).get(k) != (after or {}).get(k)
            for k in ("walk", "entries", "photos", "backgrounds")
        },
        "scene_backgrounds_saved": reserved.get("scene_backgrounds") is not None,
        "writing_result_present": isinstance(receipt.get("result"), dict),
        "job_statuses": [
            {
                "stage": j.get("stage"),
                "accepted": bool(j.get("accepted")),
                "failure": j.get("failure_code"),
            }
            for j in (receipt.get("result") or {}).get("jobs", [])
        ],
    }


async def card_publication(request, owner, walk_id, entries, notes):
    """Exercise the running card graph; export only this probe's synthetic public response."""
    from daengs_backend.schemas.walk_storyboard import DiaryStoryboardResponse
    from daengs_backend.services.walk_diary.storage.board import load_board

    path = f"/app/walks/{walk_id}/storyboard"
    body = {
        "expected_entries": {e["id"]: e["revision"] for e in entries},
        "bundle_format": "walk-diary-board-v1",
        "target_scene_count": 5,
        "preparation_budget_ms": 20000,
    }
    before = await probe_snapshot(owner, walk_id)
    started = time.monotonic()
    result = await request("POST", path, body)
    elapsed = time.monotonic() - started
    after = await probe_snapshot(owner, walk_id)
    diagnosis = board_diagnostics(result, before, after, notes, elapsed)
    parsed = DiaryStoryboardResponse.model_validate(result)
    if parsed.status != "ready" or parsed.bundle is None:
        raise SmokeFailure(
            "card graph did not publish a ready board",
            diagnostics=diagnosis,
        )
    if (
        await request(
            "GET", path + "?bundle_format=walk-diary-board-v1&target_scene_count=5"
        )
        != result
    ):
        raise SmokeFailure("card readback differs")
    if await request("POST", path, body) != result:
        raise SmokeFailure("repeated card request differs")
    scenes = parsed.bundle.scenes
    if parsed.bundle.model_status != "accepted":
        raise SmokeFailure("card graph did not publish accepted writing", diagnostics=diagnosis)
    if not all(
        any(s.writing and s.writing.original_text == note for s in scenes)
        for note in notes
    ):
        raise SmokeFailure("card changed an original note", diagnostics=diagnosis)
    # The activity part can now describe movement without a recorded behavior pin.
    if (
        sum(
            a.action_id is not None
            for s in scenes
            if s.writing
            for a in s.writing.actions
        )
        != 1
    ):
        raise SmokeFailure("card lost the synthetic behavior", diagnostics=diagnosis)
    async with engine.connect() as connection:
        raw = await connection.scalar(
            text(
                "SELECT s.bundle FROM walk_storyboards s JOIN walks w ON w.id=s.walk_id "
                "WHERE w.id=:walk AND w.app_user_id=:owner"
            ),
            {"walk": uuid.UUID(str(walk_id)), "owner": owner},
        )
    stored = load_board(raw)
    if stored.bundle != parsed.bundle or not hasattr(stored.writing_receipt, "result"):
        raise SmokeFailure("card receipt is missing or differs from publication")
    receipt = stored.writing_receipt
    background_counts = (
        Counter(
            (b.provider, b.status, b.reason)
            for b in stored.scene_backgrounds.backgrounds
        )
        if stored.scene_backgrounds is not None
        else Counter()
    )
    diagnostics = {
        "card_graph": True,
        "model": receipt.writer["model"],
        "scene_count": len(scenes),
        "generation": parsed.generation,
        "model_status": parsed.bundle.model_status,
        "failure_code": parsed.bundle.failure_code,
        "same_readback": True,
        "same_repeated_post": True,
        "original_notes_preserved": True,
        "original_action_preserved": True,
        "receipt_valid": True,
        "space_origins": dict(
            Counter(s.writing.space.origin for s in scenes if s.writing)
        ),
        "scene_backgrounds_saved": stored.scene_backgrounds is not None,
        "background_statuses": [
            {"provider": provider, "status": status, "reason": reason, "count": count}
            for (provider, status, reason), count in background_counts.items()
        ],
        "jobs": [
            {
                "stage": j.stage,
                "accepted": bool(j.accepted),
                "failure": j.failure_code,
                "reused": j.reused,
            }
            for j in receipt.result.jobs
        ],
    }
    if parsed.bundle.model_status != "accepted":
        raise SmokeFailure(
            "card graph did not publish accepted writing", diagnostics=diagnostics
        )
    return {**diagnostics, "synthetic_response": result}


async def cycle(
    owner, *, center=None, require_regional=False, backfill=False, card=False
):
    # GPS chunks store milliseconds. Synthetic action/source times must survive that encoding.
    started = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=21)
    center = center or {"lat": 37.4878, "lng": 127.052}
    points = []
    corners = [
        (-0.0012, -0.0007),
        (0.0002, -0.0007),
        (0.0002, 0.0012),
        (-0.0012, 0.0012),
        (-0.0012, -0.0007),
    ]
    for index in range(121):
        leg = min(index // 30, 3)
        fraction = (index - leg * 30) / 30
        a, b = corners[leg], corners[leg + 1]
        points.append(
            {
                "client_seq": index,
                "chain_index": 0,
                "at": (started + timedelta(seconds=index * 10)).isoformat(),
                "lat": round(
                    center["lat"] + a[0] * (1 - fraction) + b[0] * fraction, 7
                ),
                "lng": round(
                    center["lng"] + a[1] * (1 - fraction) + b[1] * fraction, 7
                ),
                "accuracy_m": 5.0,
                "is_mock": False,
            }
        )
    requests = 0
    async with httpx.AsyncClient(base_url="http://nginx:8000", timeout=60) as client:

        async def request(method, path, body=None):
            nonlocal requests
            requests += 1
            response = await client.request(
                method,
                path,
                json=body,
                headers={
                    "Authorization": "Bearer "
                    + create_access_token(owner, SubjectType.APP)
                },
            )
            if response.status_code >= 300:
                fields = []
                if response.status_code == 422:
                    try:
                        details = response.json().get("detail")
                    except (ValueError, AttributeError):
                        details = None
                    if isinstance(details, list):
                        for item in details[:10]:
                            loc = item.get("loc", []) if isinstance(item, dict) else []
                            fields.append(
                                [
                                    v
                                    for v in loc
                                    if isinstance(v, int)
                                    or (
                                        isinstance(v, str)
                                        and v.replace("_", "").isalpha()
                                    )
                                ]
                            )
                raise SmokeFailure(
                    f"API status {response.status_code}",
                    request_number=requests,
                    validation_fields=fields,
                )
            return response.json()

        walk = await request(
            "POST",
            "/app/walks",
            {
                "client_session_id": str(uuid.uuid4()),
                "pet_ids": [],
                "started_at": started.isoformat(),
                "ended_at": points[-1]["at"],
                "points": points,
            },
        )
        wid = walk["id"]
        await request(
            "POST",
            f"/app/walks/{wid}/finalize",
            {"expected_point_count": 121, "terminal_client_seq": 120},
        )
        entries = []
        notes = [
            "검증 산책에서 잠시 멈춰 물을 마셨다.",
            "검증 산책을 마치고 돌아가는 길이었다.",
        ]
        for index, seq in enumerate((20, 50, 90)):
            p = points[seq]
            content = {
                "kind": "behavior" if index == 0 else "note",
                "recorded_at": p["at"],
                "location": {
                    "lat": p["lat"],
                    "lng": p["lng"],
                    "captured_at": p["at"],
                    "accuracy_m": 5.0,
                },
            }
            content.update(
                {"behavior_code": "sniffing"}
                if index == 0
                else {"note": notes[index - 1]}
            )
            pin = None
            if index == 0:
                pin = {
                    "resolution_id": str(uuid.uuid4()),
                    "state": "resolved",
                    "method": "observed",
                    "target_at": p["at"],
                    "point": {"lat": p["lat"], "lng": p["lng"]},
                    "computed_at": p["at"],
                    "resolve_by": p["at"],
                    "policy_version": "action-pin-policy-v1",
                    "algorithm_version": "action-pin-local-v1",
                    "source_refs": [
                        {"client_seq": seq, "chain_index": 0, "at": p["at"]}
                    ],
                    "uncertainty_m": 5.0,
                    "uncertainty_basis": "provider_accuracy",
                    "reason": "direct_fix",
                }
            entry = await request(
                "PUT",
                f"/app/v2/walks/{wid}/entries/{uuid.uuid4()}",
                {
                    "expected_revision": 0,
                    "mutation_id": str(uuid.uuid4()),
                    "content": content,
                    "pin": pin,
                },
            )
            entries.append(entry)
        if card:
            return await card_publication(request, owner, wid, entries, notes)
        saved = await prepare_backfill(owner, wid) if backfill else None
        contexts = []
        for _ in range(36):
            contexts = [
                await request(
                    "GET", f"/app/v2/walks/{wid}/entries/{entry['id']}/contexts"
                )
                for entry in entries
            ]
            if all(
                len(c["sources"]) == 6
                and all(
                    s["state"] in {"completed", "failed", "cancelled"}
                    for s in c["sources"]
                )
                for c in contexts
            ):
                break
            await asyncio.sleep(5)
        public = {"space.address", "space.park", "space.commerce", "space.river"}
        statuses = [
            {
                s["tag"]: (s.get("envelope") or {}).get("status")
                for s in c["sources"]
                if s["tag"] in public
            }
            for c in contexts
        ]
        if not all(
            set(row) == public
            and all(state in {"known", "partial", "empty"} for state in row.values())
            for row in statuses
        ):
            raise SmokeFailure("public context not ready through the running worker")
        if require_regional:
            for context in contexts:
                for source in context["sources"]:
                    if source["tag"] not in {"space.commerce", "space.river"}:
                        continue
                    payload = source["envelope"]["payload"]
                    if payload["catalog_area"]["center"] != center:
                        raise SmokeFailure(
                            "context did not use its managed regional catalog"
                        )
        result = await request(
            "POST",
            f"/app/walks/{wid}/storyboard",
            {
                "expected_entries": {e["id"]: e["revision"] for e in entries},
                "bundle_format": "walk-diary-bundle-v1",
                "target_scene_count": 5,
            },
        )
        again = await request(
            "GET",
            f"/app/walks/{wid}/storyboard?bundle_format=walk-diary-bundle-v1&target_scene_count=5",
        )
        if (
            result != again
            or result["status"] != "ready"
            or result["bundle"]["model_status"] != "accepted"
        ):
            raise SmokeFailure("generated diary not ready or readback differs")
        scenes = result["bundle"]["scenes"]
        # The app assembles one editable body from narration + user_record. Requiring the
        # writer to repeat original notes in narration would contradict that contract.
        records = {
            s["core"]["identity"]: s["user_record"] for s in scenes if s["user_record"]
        }
        for entry, note in zip(entries[1:], notes, strict=True):
            record = records.get("walk_entry:" + entry["id"], {})
            if record.get("kind") != "note" or record.get("text") != note:
                raise SmokeFailure("original note missing or changed in its scene")
        behavior = records.get("walk_entry:" + entries[0]["id"], {})
        if behavior.get("kind") != "behavior" or behavior.get("code") != "sniffing":
            raise SmokeFailure("original action missing or changed in its scene")
        addressed = sum(
            any(
                p["schema_version"] == "sgis-dong-v1" and p["facts"].get("dong")
                for p in s["place_reference"]
            )
            for s in scenes
            if s["core"]["identity"] in records
        )
        generated = sum(s["narration"]["status"] == "generated" for s in scenes)
        if addressed != len(entries) or not generated:
            raise SmokeFailure(
                "dong address or public background missing in diary output"
            )
        recovery = await verify_backfill(owner, wid, saved) if backfill else None
        return {
            **({"backfill": recovery} if backfill else {}),
            "source_statuses": statuses,
            "scene_count": len(scenes),
            "generation": result["generation"],
            "model_status": result["bundle"]["model_status"],
            "same_readback": True,
            "user_notes_preserved": True,
            "user_action_preserved": True,
            "addressed_scene_count": addressed,
            "generated_background_count": generated,
            "http_requests": requests,
            "managed_region_verified": require_regional,
        }


async def main(*, regional=False, backfill=False):
    owner = uuid.uuid4()
    kakao = -(
        owner.int % (2**62) + 1
    )  # A synthetic namespace; never impersonate an existing account.
    created = False
    result = {"format": "walk-runtime-smoke-v1", "ok": False, "cleaned": False}
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO app_users(id,kakao_id) VALUES (:id,:kakao)"),
                {"id": owner, "kakao": kakao},
            )
        created = True
        async with asyncio.timeout(480 if regional else 240):
            if regional:
                from daengs_backend.services.walk_catalog_regions import (
                    path_for,
                    region,
                )

                # Entire synthetic route stays inside each distinct 1 km cell.
                cases = []
                result["regional_cases"] = cases
                for point in (
                    {"lat": 37.5172, "lng": 127.0473},
                    {"lat": 37.556, "lng": 126.9238},
                ):
                    _, center = region(point)
                    existed = all(
                        path_for(kind, center).is_file()
                        for kind in ("commerce", "river")
                    )
                    cases.append(
                        {
                            "catalogs_present_before": existed,
                            **await cycle(owner, center=center, require_regional=True),
                        }
                    )
            else:
                result.update(
                    await cycle(owner, backfill=True)
                    if backfill
                    else await cycle(owner, card=True)
                )
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - never print API bodies, tokens, SQL parameters or user IDs
        result["error_type"] = type(exc).__name__
        # Only locally generated messages are safe; external exception strings are omitted.
        if isinstance(exc, SmokeFailure):
            result["reason"] = str(exc)
            result["request_number"] = exc.request_number
            result["validation_fields"] = exc.validation_fields
            if exc.diagnostics is not None:
                result["diagnostics"] = exc.diagnostics
    finally:
        try:
            if created:
                async with engine.begin() as connection:
                    deleted = await connection.execute(
                        text("DELETE FROM app_users WHERE id=:id AND kakao_id=:kakao"),
                        {"id": owner, "kakao": kakao},
                    )
                    result["cleaned"] = deleted.rowcount == 1
        except Exception as exc:  # noqa: BLE001 - cleanup failures must also be safe to log
            result["cleanup_error"] = type(exc).__name__
        finally:
            await engine.dispose()
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] and result["cleaned"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--regional", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    arguments = parser.parse_args()
    if arguments.regional and arguments.backfill:
        parser.error("choose one probe mode")
    raise SystemExit(
        asyncio.run(main(regional=arguments.regional, backfill=arguments.backfill))
    )
