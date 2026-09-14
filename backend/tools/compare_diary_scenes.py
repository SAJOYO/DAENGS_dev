"""Compare exact APP scenes through the shared DEV collector, slots and writer.

Run with `uv run tools/compare_diary_scenes.py` from backend. All inputs and outputs
are private local files. Does not connect to the member DB or publish a diary.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.services.walk_diary.collection.comparison import collect_scene_backgrounds
from daengs_backend.services.walk_diary.legacy.slots import (
    MODEL,
    PROMPT,
    generate_slot_prose,
    slot_payload,
    write_slot_stamps,
    writing_version,
)
from daengs_backend.services.walk_session.finalize import walk_input_fingerprint
from daengs_walk import analyze_walk
from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.diary.board.assembly import assemble_base_board
from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
from daengs_walk.diary.board.models import (
    BaseBoard,
    BaseBoardPolicy,
    BoardScene,
    VerifiedBoardRoute,
)
from daengs_walk.diary.board.output import PublishedBoardScene, publish_board
from daengs_walk.diary.contracts.input import DiaryInput, digest
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.selection.board import prepare_base_board
from daengs_walk.diary.selection.stamps import StampPolicy
from daengs_walk.diary.slots.service import prepare_board_slots


def read_keys(path):
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip().removeprefix("export ")
        if line.lower().startswith("gemini:"):
            values["GEMINI_API_KEY"] = line.split(":", 1)[1].strip().strip("\"'")
        name, sep, value = line.partition("=")
        if sep and name.strip() in {"GEMINI_API_KEY", "GOOGLE_API_KEY", "DAENGS_KAKAO_REST_KEY"}:
            values[name.strip()] = value.strip().strip("\"'")
    return values


def replay(source, points, target_scenes):
    uploaded = [WalkPointUpload.model_validate(p.model_dump(mode="json")) for p in points]
    fingerprint = walk_input_fingerprint(uploaded).removeprefix("sha256:")
    if source.route.input_fingerprint != fingerprint:
        raise ValueError("Canonical points differ from the saved source fingerprint")
    evidence = analyze_walk(UUID(source.walk_id), source.started_at, source.ended_at, points)
    if source.route.calculation_version != evidence.facts.calculation_version:
        raise ValueError("Route calculation version changed")
    route = VerifiedBoardRoute(source.route, evidence)
    plan = prepare_base_board(
        source,
        BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=target_scenes)),
        route=route,
    )
    board = assemble_base_board(source, plan, route=route)
    return route, plan, board


def bind_snapshot(request, source, plan, board, snapshot_hash):
    """Verify replayed cores, then project visible order/edits without moving any pin."""
    if (
        request["format"] != "diary-scene-comparison-input-v2"
        or request["owner_id"] != source.owner_id
        or request["session_id"] != source.client_session_id
        or not 2 <= len(request["scenes"]) <= 12
    ):
        raise ValueError("Comparison requires this account's 2–12 saved board scenes")
    expected = {s.id: s for s in publish_board(board, plan).scenes}
    cores = {s.id: s for s in board.scenes}
    display_ids, source_ids, selected = set(), set(), []
    for order, item in enumerate(request["scenes"], 1):
        saved = PublishedBoardScene.model_validate(item["source_scene"])
        baseline = expected.get(saved.id)
        # Published prose may already contain AI text; its origin metadata must match exactly.
        if baseline is None or baseline.model_dump(exclude={"title", "body"}) != saved.model_dump(
            exclude={"title", "body"}
        ):
            raise ValueError("Saved scene center differs from canonical replay")
        point = saved.anchor.point
        if (
            item["id"] in display_ids
            or saved.id in source_ids
            or not item["id"].startswith(source.client_session_id + "/")
            or item["point"] != ([point.lat, point.lng] if point else None)
            or item["at_millis"] != int(saved.anchor.event_at.timestamp() * 1000)
        ):
            raise ValueError("Visible scene identity, position or time changed")
        display_ids.add(item["id"])
        source_ids.add(saved.id)
        # Empty or oversized edits are unsupported by the shared writer: fail before collection.
        selected.append(
            BoardScene.model_validate(
                {
                    **cores[saved.id].model_dump(mode="json"),
                    "order": order,
                    "title": item["title"],
                    "body": item["body"],
                }
            )
        )
    return BaseBoard.model_validate(
        {
            **board.model_dump(mode="json"),
            "plan_revision": digest(
                {"replayed_plan": board.plan_revision, "app_snapshot": snapshot_hash}
            ),
            "scenes": selected,
        }
    )


def display_time(value):
    return (
        datetime.fromisoformat(value)
        .astimezone(ZoneInfo("Asia/Seoul"))
        .strftime("%Y-%m-%d %H:%M:%S (한국시간)")
    )


def describe(evidence):
    facts = evidence.facts
    if evidence.role == "scene_registered_point_distance":
        text = f"공간 · {facts['name']} · 등록 위치까지 약 {facts['distance_m']:g}m"
    elif evidence.role == "scene_address_reference":
        text = "위치 참고 · " + str(facts.get("address") or facts.get("name") or "주소 자료")
    elif evidence.part == "motion":
        kind = {
            "observed_slow": "상대적으로 느린 이동",
            "observed_fast": "상대적으로 빠른 이동",
            "observed_dwell": "한곳에 모인 동선",
        }[facts["kind"]]
        text = "기기 동선 · " + kind + "\n" + facts["temporal_relation"]
        text += (
            "\n관측: " + display_time(facts["started_at"]) + " ~ " + display_time(facts["ended_at"])
        )
    else:
        # Keep exact verified fields readable when the provider has no product-facing label.
        label = {"space": "공간", "environment": "환경", "motion": "동선"}[evidence.part]
        text = label + " · " + json.dumps(facts, ensure_ascii=False, indent=2)
    interpretation = facts.get("interpretation", "")
    source = facts.get("source_ref", {})
    provider = source.get("source", "") if isinstance(source, dict) else str(source)
    provenance = "\n출처: " + provider if provider else ""
    if facts.get("retrieved_at"):
        provenance += "\n조회: " + display_time(facts["retrieved_at"])
    return (text + "\n" + interpretation + provenance)[:2000]


def comparison_result(request, snapshot_hash, board, slots, receipt, captured):
    if receipt.model_status != "accepted":
        raise ValueError("Shared writer did not accept this comparison")
    written = {s.scene_id: s for s in receipt.writing.scenes}
    items = []
    for app_scene, scene, stamp in zip(request["scenes"], board.scenes, slots.stamps, strict=True):
        prose = written.get(scene.id)
        counts = Counter(e.part for e in stamp.evidence)
        coverage = "선정 근거: " + " · ".join(
            f"{label} {counts[part]}"
            for part, label in (("space", "공간"), ("environment", "환경"), ("motion", "동선"))
        )
        if not counts["environment"]:
            coverage += "\n산책 당시의 환경 자료가 없어 날씨는 덧붙이지 않았어요."
        if scene.anchor.point is None:
            coverage += "\n이 장면에는 확인된 위치가 없어 주변 장소를 조회하지 않았어요."
        items.append(
            {
                "id": app_scene["id"],
                "background": prose.text.strip() if prose else "",
                "evidence_ids": list(prose.evidence_ids) if prose else [],
                "coverage": coverage,
                "evidence": [{"id": e.id, "description": describe(e)} for e in stamp.materials()],
            }
        )
    return {
        "format": "diary-scene-comparison-result-v2",
        "snapshot_sha256": snapshot_hash,
        "model_status": receipt.model_status,
        "model": MODEL,
        "retrieved_at": display_time(captured),
        "scenes": items,
    }


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="Saved/replayed DiaryInput")
    parser.add_argument(
        "--points", type=Path, required=True, help="Canonical uploaded input points"
    )
    parser.add_argument(
        "--target-scenes", type=int, required=True, help="Original intermediate target"
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="New private directory; never overwritten"
    )
    parser.add_argument("--place-api", default="http://daengback.weareithero.cloud")
    parser.add_argument(
        "--backgrounds", type=Path, help="Replay saved collection; no Place/Kakao calls"
    )
    args = parser.parse_args()
    raw = args.request.read_bytes()
    if len(raw) > 512_000:
        raise ValueError("Comparison input exceeds budget")
    request, snapshot_hash = json.loads(raw), hashlib.sha256(raw).hexdigest()
    source = DiaryInput.model_validate_json(args.source.read_bytes())
    points = tuple(
        WalkEvidencePoint.model_validate(p) for p in json.loads(args.points.read_bytes())
    )
    route, plan, replayed = replay(source, points, args.target_scenes)
    board = bind_snapshot(request, source, plan, replayed, snapshot_hash)
    keys = read_keys(args.env_file)
    key = keys.get("GEMINI_API_KEY") or keys.get("GOOGLE_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY missing")
    args.output.mkdir(parents=True, exist_ok=False)
    captured = datetime.now(UTC).isoformat()
    collection = (
        SceneBackgroundSnapshot.model_validate_json(args.backgrounds.read_bytes()).validate_board(
            board
        )
        if args.backgrounds
        else await collect_scene_backgrounds(
            board, place_api=args.place_api, kakao_key=keys.get("DAENGS_KAKAO_REST_KEY", "")
        )
    )
    slots = prepare_board_slots(
        source, board, SlotPolicy(), route=route, scene_backgrounds=collection
    )
    save(args.output / "request.json", request)
    save(args.output / "board.json", board.model_dump(mode="json"))
    save(args.output / "backgrounds.json", collection.model_dump(mode="json"))
    save(args.output / "slots.json", slots.model_dump(mode="json"))
    save(args.output / "writer-input.json", slot_payload(board, slots))
    save(
        args.output / "manifest.json",
        {
            "snapshot_sha256": snapshot_hash,
            "source_revision": source.revision(),
            "replayed_plan_revision": plan.revision(),
            "comparison_plan_revision": board.plan_revision,
            "slot_revision": slots.revision(),
            "writer": writing_version(),
            "prompt": PROMPT,
            "retrieved_at": captured,
            "scene_count": len(board.scenes),
            "route_input_fingerprint_verified": True,
            "visible_source_scenes_verified": True,
        },
    )

    async def generate(payload, schema):
        save(args.output / "writer-schema.json", schema)
        response = await generate_slot_prose(payload, schema, api_key=key)
        save(args.output / "writer-response.json", response)
        return response

    receipt = await write_slot_stamps(board, slots, generate)
    save(args.output / "receipt.json", receipt.model_dump(mode="json"))
    if receipt.model_status != "accepted":
        print(
            json.dumps({"model_status": receipt.model_status, "failure_code": receipt.failure_code})
        )
        raise SystemExit(1)
    result = comparison_result(request, snapshot_hash, board, slots, receipt, captured)
    save(args.output / "result.json", result)
    print(
        json.dumps(
            {
                "model_status": receipt.model_status,
                "scenes": len(board.scenes),
                "generated": sum(bool(s["background"]) for s in result["scenes"]),
            }
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:  # noqa: BLE001 - provider errors can contain credentials
        print(f"Comparison failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
