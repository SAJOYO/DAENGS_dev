"""Replay a simulated walk on a real TMAP route through the current diary card graph.

No database, publication, final-story writer, or prompt overrides. Acquisition is
a separate recorded pass; --generate uses its frozen snapshot, --render is offline.
"""

import argparse
import asyncio
import base64
import bisect
import gzip
import json
import logging
import math
import os
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from urllib.parse import unquote


def dump(path, value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > 256_000:
        path.with_suffix(path.suffix + ".gz").write_bytes(gzip.compress(encoded, mtime=0))
    else:
        path.write_bytes(encoded)


def read(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return json.loads(gzip.decompress(path.with_suffix(path.suffix + ".gz").read_bytes()))


def configure(path):
    """Read only provider credentials; never inherit a DB or Redis connection."""
    values = {}
    allowed = {
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DAENGS_DATA_GO_KR_SERVICE_KEY",
        "DATA_GO_KR_KEY",
        "DAENGS_WALK_PUBLIC_DATA_KEY",
        "DAENGS_WALK_SGIS_KEY",
        "DAENGS_WALK_SGIS_SECRET",
    }
    if path:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip().removeprefix("export ")
            if line.lower().startswith("gemini:"):
                values["GEMINI_API_KEY"] = line.split(":", 1)[1].strip().strip("\"'")
            key, sep, value = line.partition("=")
            if sep and key.strip() in allowed:
                values[key.strip()] = value.strip().strip("\"'")
    for key in allowed:
        if key not in values and os.environ.get(key):
            values[key] = os.environ[key]
    if values.get("GEMINI_API_KEY") or values.get("GOOGLE_API_KEY"):
        os.environ["GEMINI_API_KEY"] = values.get("GEMINI_API_KEY") or values["GOOGLE_API_KEY"]
    public_key = unquote(
        values.get("DAENGS_WALK_PUBLIC_DATA_KEY")
        or values.get("DATA_GO_KR_KEY")
        or values.get("DAENGS_DATA_GO_KR_SERVICE_KEY", "")
    )
    for name in ("DAENGS_WALK_SGIS_KEY", "DAENGS_WALK_SGIS_SECRET"):
        if name in values:
            os.environ[name] = values[name]
    os.environ.update(
        {
            "DATA_GO_KR_KEY": public_key,
            "REDIS_URL": "",
            "DAENGS_REALTIME_URL": "",
            "DAENGS_DB_HOST": "127.0.0.1",
            "DAENGS_DB_PORT": "1",
            "DAENGS_DB_PASSWORD": "unused-scenario",
            "DAENGS_KAKAO_APP_KEYS": '["unused-scenario"]',
            "DAENGS_WARM_UP_ENCODER": "false",
        }
    )
    for number, name in enumerate(("AES", "BLIND_INDEX", "JWE"), 1):
        os.environ[f"DAENGS_{name}_KEY"] = base64.urlsafe_b64encode(bytes([number]) * 32).decode()
    # Provider exceptions are recorded as sanitized status codes by the service.
    logging.disable(logging.CRITICAL)
    return public_key


def distance(a, b):
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    return math.hypot(
        (b["lat"] - a["lat"]) * 111195,
        (b["lng"] - a["lng"]) * 111195 * math.cos(lat),
    )


def scenario(route, start):
    from daengs_walk import analyze_walk
    from daengs_walk.contracts import WalkEvidencePoint
    from daengs_walk.diary_input import DiaryInput, RouteVersion, UserRecord, digest
    from daengs_walk.diary_observations import build_observation_pool

    vertices = [route["polyline"][0]]
    for point in route["polyline"][1:]:
        if distance(vertices[-1], point) > 0.01:
            vertices.append(point)
    lengths = [0.0]
    for a, b in pairwise(vertices):
        lengths.append(lengths[-1] + distance(a, b))
    one_way = lengths[-1]

    def position(travelled):
        along = travelled if travelled <= one_way else 2 * one_way - travelled
        index = min(len(vertices) - 2, max(0, bisect.bisect_right(lengths, along) - 1))
        fraction = (along - lengths[index]) / (lengths[index + 1] - lengths[index])
        return {
            k: vertices[index][k] + fraction * (vertices[index + 1][k] - vertices[index][k])
            for k in ("lat", "lng")
        }

    travelled, seconds, pause_done = 0.0, 0, False
    samples = [(seconds, travelled)]
    while travelled < 2 * one_way:
        progress = travelled / (2 * one_way)
        # Fictional changes are GPS input, never hand-authored motion observations.
        speed = 0.55 if 0.13 <= progress < 0.19 else 1.7 if 0.67 <= progress < 0.76 else 1.15
        if travelled >= one_way and not pause_done:
            for _ in range(6):
                seconds += 10
                samples.append((seconds, travelled))
            pause_done = True
        seconds += 10
        travelled = min(2 * one_way, travelled + speed * 10)
        samples.append((seconds, travelled))
    points = tuple(
        WalkEvidencePoint(
            client_seq=i,
            at=start + timedelta(seconds=sec),
            **position(metres),
            accuracy_m=5,
            is_mock=True,
        )
        for i, (sec, metres) in enumerate(samples)
    )
    records = []
    for number, (fraction, content) in enumerate(
        (
            (0.16, {"kind": "behavior", "code": "sniffing", "pet_id": "scenario-pet"}),
            (0.5, {"kind": "note", "text": "여기서 물을 마시고, 왔던 길로 돌아가기로 했다."}),
            (0.87, {"kind": "note", "text": "돌아오는 길에 사진 대신 짧게 기록을 남겼다."}),
        ),
        1,
    ):
        seq = min(range(len(samples)), key=lambda i: abs(samples[i][1] / (2 * one_way) - fraction))
        fix = points[seq]
        records.append(
            UserRecord.model_validate(
                {
                    "ref": {
                        "store": "walk_entry",
                        "id": f"scenario-record-{number}",
                        "version": "1",
                        "version_kind": "revision",
                    },
                    "content": content,
                    "anchor": {
                        "event_at": fix.at,
                        "time_basis": "recorded_at",
                        "location_at": fix.at,
                        "point": {"lat": fix.lat, "lng": fix.lng},
                        "accuracy_m": 5,
                        "position_state": "resolved",
                        "method": "observed",
                        "source_fixes": [{"client_seq": seq, "chain_index": 0, "at": fix.at}],
                    },
                }
            )
        )
    walk_id = uuid.uuid5(uuid.NAMESPACE_URL, "daengs-route-scenario:" + start.isoformat())
    evidence = analyze_walk(walk_id, start, points[-1].at, points)
    version = RouteVersion(
        status="ready",
        analysis_id="simulated-tmap-route",
        input_fingerprint=digest([p.model_dump(mode="json") for p in points]),
        calculation_version=evidence.facts.calculation_version,
    )
    source = DiaryInput(
        owner_id="scenario-owner",
        walk_id=str(walk_id),
        client_session_id="scenario-session",
        started_at=start,
        ended_at=points[-1].at,
        pet_ids=("scenario-pet",),
        evidence_origin="mock",
        route=version,
        records=tuple(records),
        photos_status="not_available",
        backgrounds=(),
        selected_background_ids=(),
        observations=build_observation_pool(evidence, version).observations,
        scene_policy_version="records-first-v1",
        writing_policy_version="diary-part-slots-v2",
    )
    return {
        "source": source.model_dump(mode="json"),
        "points": [p.model_dump(mode="json") for p in points],
        "travel_m": [round(m, 2) for _, m in samples],
        "one_way_geometry_m": one_way,
        "policy": {"intermediate": {"target_scene_count": 6}},
        "provenance": {
            "route": "TMAP pedestrian API; real provider route, not a recorded walk",
            "gps_time_speed_notes_behavior_pet": "simulated",
            "map": "OpenStreetMap; display only, not LLM evidence",
        },
    }


def prepare(raw):
    from daengs_backend.services.walk_diary_base_board import assemble_saved_base_board
    from daengs_backend.services.walk_diary_input import InputAssembly
    from daengs_backend.services.walk_diary_observations import ObservationSource
    from daengs_walk import analyze_walk
    from daengs_walk.contracts import WalkEvidencePoint
    from daengs_walk.diary_board import BaseBoardPolicy
    from daengs_walk.diary_input import DiaryInput, digest
    from daengs_walk.diary_observations import build_observation_pool

    source = DiaryInput.model_validate(raw["source"])
    points = tuple(WalkEvidencePoint.model_validate(p) for p in raw["points"])
    evidence = analyze_walk(uuid.UUID(source.walk_id), source.started_at, source.ended_at, points)
    if source.route.input_fingerprint != digest([p.model_dump(mode="json") for p in points]):
        raise ValueError("route input fingerprint differs from the replay points")
    if source.observations != build_observation_pool(evidence, source.route).observations:
        raise ValueError("observation pool differs from canonical replay")
    assembled = InputAssembly(
        source,
        (),
        ObservationSource(source.route, "mock", evidence=evidence),
        pet_names=(("scenario-pet", "보리"),),
    )
    return assemble_saved_base_board(assembled, BaseBoardPolicy.model_validate(raw["policy"]))


async def acquire(base, public_key):
    from daengs_backend.services.walk_diary_space_collection import collect_spaces
    from daengs_backend.services.walk_weather_context import collect_temperature
    from daengs_walk.diary_input import SavedBackground, digest

    # Keep default 4 s public collection. Weather normally arrives through entry context;
    # the lab explicitly collects it for every selected scene outside the writer deadline.
    snapshot = await collect_spaces(base.board, commerce_key=public_key, include_sgis=True)
    temperatures = []
    for target in snapshot.targets:
        point = target.anchor.point
        collected = await collect_temperature(
            {"recorded_at": target.anchor.event_at.isoformat()}, point.model_dump()
        )
        temperatures.append(
            SavedBackground(
                id="scenario-weather:" + target.scene_id,
                target=target.core_ref,
                provider="weather-observation",
                payload_schema="walk-entry-context-v1",
                policy_version="walk-entry-context-v1",
                query_point=point,
                tags=("environment",),
                status=collected.status,
                reason=collected.reason,
                retrieved_at=collected.retrieved_at,
                temporal_basis=collected.temporal_basis or "unknown",
                payload=collected.payload,
                payload_sha256=digest(collected.payload) if collected.payload is not None else None,
            )
        )
        print(
            json.dumps(
                {
                    "weather_scene": target.scene_id,
                    "status": collected.status,
                    "reason": collected.reason,
                }
            ),
            flush=True,
        )
    return snapshot.model_copy(
        update={"backgrounds": (*snapshot.backgrounds, *temperatures)}
    ).validate_board(base.board)


async def generate(base, snapshot, prepared_input=False):
    from daengs_backend.services.walk_diary_base_board import with_scene_backgrounds
    from daengs_backend.services.walk_diary_board_slot_writing import write_board
    from daengs_backend.services.walk_diary_card_writing import write_cards

    if prepared_input:
        # Lab quality pass: source acquisition AND slot preparation precede the
        # existing graph deadline. This is explicitly not publication latency.
        prepared = with_scene_backgrounds(base, snapshot)
        return await write_cards(base.input.source, prepared, collector=None), prepared

    async def frozen_collector(board):
        return snapshot.validate_board(board)

    result = await write_board(base.input.source, base, collector=frozen_collector)
    prepared = (
        with_scene_backgrounds(base, result.scene_backgrounds) if result.scene_backgrounds else base
    )
    return result, prepared


def render(directory, *, destination=None, whole_title=None, scene_titles=None):
    """Render frozen output without importing backend configuration or calling providers."""
    template = Path(__file__).with_name("diary_route_scenario.html").read_text(encoding="utf-8")
    data = {
        name: read(directory / f"{name}.json")
        for name in ("input", "result", "slots", "backgrounds", "run", "map")
    }
    if whole_title is not None:
        data["whole_title"] = whole_title
    if scene_titles is not None:
        data["scene_titles"] = scene_titles
    if (directory / "source-lineage.json").exists():
        data["source_lineage"] = read(directory / "source-lineage.json")
    if (directory / "source-replay.json").exists():
        data["source_replay"] = read(directory / "source-replay.json")

    # Huge source geometry belongs in the lossless JSON archive, not a browser
    # disclosure. Summaries are view-only and cannot feed the writer or verifier.
    def compact(value):
        from daengs_walk.diary_input import digest

        if isinstance(value, list) and len(value) > 80:
            return {
                "viewer_omitted_items": len(value),
                "sha256": digest(value),
                "original": "See the matching .json or .json.gz archive",
            }
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    data["slots"] = compact(data["slots"])
    data["result"]["jobs"] = compact(data["result"]["jobs"])
    data["result"]["scene_backgrounds"] = None
    data["backgrounds"] = {
        "backgrounds": [
            {k: v for k, v in b.items() if k != "payload"}
            for b in data["backgrounds"]["backgrounds"]
        ]
    }
    embedded = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    (destination or directory / "preview.html").write_text(
        template.replace("__SCENARIO_JSON__", embedded), encoding="utf-8"
    )


def verify(directory):
    from types import SimpleNamespace

    from daengs_backend.services.walk_diary_base_board import with_scene_backgrounds
    from daengs_backend.services.walk_diary_card_writing import CardWritingResult, complete_cards
    from daengs_walk.diary_scene_backgrounds import SceneBackgroundSnapshot

    raw, run = read(directory / "input.json"), read(directory / "run.json")
    base = prepare(raw)
    snapshot = SceneBackgroundSnapshot.model_validate(read(directory / "backgrounds.json"))
    snapshot.validate_board(base.board)
    if run.get("prepared_input"):
        base = with_scene_backgrounds(base, snapshot)
    result = CardWritingResult.model_validate(read(directory / "result.json"))
    complete_cards(SimpleNamespace(board=base), result)
    expected = (
        with_scene_backgrounds(base, result.scene_backgrounds) if result.scene_backgrounds else base
    )
    if expected.slots.model_dump(mode="json") != read(directory / "slots.json"):
        raise ValueError("saved slots differ from the writer input")
    print(
        json.dumps(
            {
                "verified": directory.name,
                "cards": len(result.bundle.scenes),
                "checks": [
                    "canonical_replay",
                    "scene_binding",
                    "source_versions",
                    "original_text",
                    "observation_core",
                    "slot_replay",
                ],
            }
        ),
        flush=True,
    )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("acquire", "generate", "render", "verify"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--route", type=Path, help="Parsed TMAP RouteResult JSON, for acquire")
    parser.add_argument(
        "--env-file", type=Path, help="Only Gemini/public-data credentials are read"
    )
    parser.add_argument("--start", help="Aware ISO datetime, default two hours before execution")
    parser.add_argument(
        "--prepared-input",
        action="store_true",
        help="Quality pass: prepare collected slots before the card graph deadline",
    )
    args = parser.parse_args()
    directory = args.output
    directory.mkdir(parents=True, exist_ok=True)
    if args.phase == "render":
        render(directory)
        return
    public_key = configure(args.env_file)
    if args.phase == "verify":
        verify(directory)
        return
    if args.phase == "acquire":
        if not args.route:
            parser.error("acquire requires --route")
        if (directory / "input.json").exists():
            parser.error("choose a new output directory; frozen runs are never overwritten")
        start = (
            datetime.fromisoformat(args.start)
            if args.start
            else datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
        )
        if start.tzinfo is None:
            parser.error("--start requires a timezone")
        raw = scenario(read(args.route), start)
        dump(directory / "input.json", raw)
        base = prepare(raw)
        dump(directory / "base.json", base.board)
        dump(directory / "plan.json", base.plan)
        print(
            json.dumps({"scenes": len(base.board.scenes), "samples": len(raw["points"])}),
            flush=True,
        )
        snapshot = await acquire(base, public_key)
        dump(directory / "backgrounds.json", snapshot)
        print(
            json.dumps(dict(Counter(f"{b.provider}:{b.status}" for b in snapshot.backgrounds))),
            flush=True,
        )
    else:
        from daengs_backend.services.walk_diary_card_writing import writing_version
        from daengs_walk.diary_scene_backgrounds import SceneBackgroundSnapshot

        if any((directory / name).exists() for name in ("result.json", "result.json.gz")):
            parser.error("result exists; use a new run directory for another generation")
        raw = read(directory / "input.json")
        base = prepare(raw)
        snapshot = SceneBackgroundSnapshot.model_validate(read(directory / "backgrounds.json"))
        start = time.monotonic()
        result, enriched = await generate(base, snapshot, args.prepared_input)
        elapsed = time.monotonic() - start
        dump(directory / "result.json", result)
        dump(directory / "slots.json", enriched.slots)
        stats = {
            "generated_at": datetime.now(UTC).isoformat(),
            "elapsed_s": round(elapsed, 3),
            "writer": writing_version(),
            "acquisition": "frozen before generation; not live publication timing",
            "collection_adopted": result.scene_backgrounds is not None,
            "prepared_input": args.prepared_input,
            "backgrounds_in_writing_input": enriched.scene_backgrounds is not None,
            "jobs": dict(Counter(f"{j.stage}:{j.failure_code or 'accepted'}" for j in result.jobs)),
            "provenance": raw["provenance"],
        }
        dump(directory / "run.json", stats)
        print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
