"""Explicit, temporary synthetic account/walk through the running nginx API and real Beat."""

import argparse
import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from daengs_backend.core.database import engine
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token

logging.getLogger("httpx").setLevel(logging.WARNING)


class SmokeFailure(Exception):
    """Only predefined messages; never constructed from external responses."""

    def __init__(self, message, *, request_number=None, validation_fields=None):
        super().__init__(message)
        self.request_number = request_number
        self.validation_fields = validation_fields


async def cycle(owner):
    # GPS chunks store milliseconds. Synthetic action/source times must survive that encoding.
    started = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=21)
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
                "lat": round(37.4878 + a[0] * (1 - fraction) + b[0] * fraction, 7),
                "lng": round(127.052 + a[1] * (1 - fraction) + b[1] * fraction, 7),
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
                headers={"Authorization": "Bearer " + create_access_token(owner, SubjectType.APP)},
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
                                    or (isinstance(v, str) and v.replace("_", "").isalpha())
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
        notes = ["검증 산책에서 잠시 멈춰 물을 마셨다.", "검증 산책을 마치고 돌아가는 길이었다."]
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
                {"behavior_code": "sniffing"} if index == 0 else {"note": notes[index - 1]}
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
                    "source_refs": [{"client_seq": seq, "chain_index": 0, "at": p["at"]}],
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
        contexts = []
        for _ in range(36):
            contexts = [
                await request("GET", f"/app/v2/walks/{wid}/entries/{entry['id']}/contexts")
                for entry in entries
            ]
            if all(
                len(c["sources"]) == 6
                and all(s["state"] in {"completed", "failed", "cancelled"} for s in c["sources"])
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
        records = {s["core"]["identity"]: s["user_record"] for s in scenes if s["user_record"]}
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
            raise SmokeFailure("dong address or public background missing in diary output")
        return {
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
        }


async def main():
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
        async with asyncio.timeout(240):
            result.update(await cycle(owner))
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - never print API bodies, tokens, SQL parameters or user IDs
        result["error_type"] = type(exc).__name__
        # Only locally generated messages are safe; external exception strings are omitted.
        if isinstance(exc, SmokeFailure):
            result["reason"] = str(exc)
            result["request_number"] = exc.request_number
            result["validation_fields"] = exc.validation_fields
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
    parser.parse_args()
    raise SystemExit(asyncio.run(main()))
