"""Acquire real public sources and run the existing diary writers on a simulated route."""

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from run_diary_route_scenario import configure, dump, generate, prepare, read, render, scenario

from daengs_walk.diary.contracts.input import SavedBackground, digest


class RecordingTransport(httpx.AsyncBaseTransport):
    """Record actual public responses; never persist auth tokens or key parameters."""

    def __init__(self, directory, secrets):
        self.inner = httpx.AsyncHTTPTransport()
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.secrets = {v for key in secrets if key for v in (key, quote(key, safe=""))}
        self.receipts = []

    async def handle_async_request(self, request):
        receipt = {
            "sequence": len(self.receipts) + 1,
            "endpoint": str(request.url.copy_with(query=None)),
            "parameters": {
                k: v
                for k, v in request.url.params.multi_items()
                if k.lower() not in {"servicekey", "consumer_key", "consumer_secret", "accesstoken"}
            },
            "started_at": datetime.now(UTC).isoformat(),
        }
        self.receipts.append(receipt)
        started = time.monotonic()
        try:
            response = await self.inner.handle_async_request(request)
            raw = await response.aread()
            receipt["http_status"] = response.status_code
            body = json.loads(raw)
            if request.url.path.endswith("/auth/authentication.json"):
                # This response contains the session token, so retain only the status.
                receipt["response"] = {"errCd": body.get("errCd"), "auth_body_omitted": True}
            else:
                serialized = json.dumps(body, ensure_ascii=False)
                for secret in self.secrets:
                    serialized = serialized.replace(secret, "[REDACTED]")
                receipt["response"] = json.loads(serialized)
            receipt["response_sha256"] = digest(receipt["response"])
            return response
        except asyncio.CancelledError:
            receipt["outcome"] = "cancelled_by_collection_deadline"
            raise
        except Exception as exc:
            receipt["error_type"] = type(exc).__name__
            raise
        finally:
            receipt["elapsed_s"] = round(time.monotonic() - started, 3)
            dump(self.directory / f"{receipt['sequence']:03d}.json", receipt)

    async def aclose(self):
        await self.inner.aclose()


def source_lineage(result, slots, backgrounds):
    sources = {b["id"]: b["provider"] for b in backgrounds["backgrounds"]}

    def describe(item):
        return {
            "part": item["part"],
            "role": item["role"],
            "source": sources.get(item["source_id"], item["source_id"]),
        }

    rows = []
    for scene in result["bundle"]["scenes"]:
        stamp = next(s for s in slots["stamps"] if s["scene_id"] == scene["id"])
        job = next(
            j
            for j in result["jobs"]
            if j["stage"] == "space" and j["request"]["card_id"] == scene["id"]
        )
        selected = list(stamp["evidence"])
        if stamp.get("location_reference"):
            selected.append(stamp["location_reference"])
        evidence = job.get("evidence", {})
        used = (job.get("accepted") or {}).get("evidence_ids", [])
        rows.append(
            {
                "scene_id": scene["id"],
                "order": scene["order"],
                "body": scene["body"],
                "space_origin": scene["writing"]["space"]["origin"],
                "selected": [describe(e) for e in selected],
                "writer_input": [{"material_id": i, **describe(e)} for i, e in evidence.items()],
                "model_cited": [{"material_id": i, **describe(evidence[i])} for i in used],
            }
        )
    return {
        "note": "Model citation is recorded attribution, not proof of semantic grounding",
        "scenes": rows,
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--reuse-catalogs", action="store_true")
    parser.add_argument(
        "--input", type=Path, help="Reuse frozen GPS, records and selected-scene policy"
    )
    parser.add_argument("--collection-timeout", type=float, default=4)
    parser.add_argument("--start", help="Aware ISO time, default two hours before execution")
    args = parser.parse_args()
    if not 1 <= args.collection_timeout <= 30:
        parser.error("collection timeout must be between 1 and 30 seconds")
    public_key = configure(args.env_file)
    sgis_key = os.environ.get("DAENGS_WALK_SGIS_KEY", "")
    sgis_secret = os.environ.get("DAENGS_WALK_SGIS_SECRET", "")
    if not all((public_key, sgis_key, sgis_secret)):
        parser.error("public-data and SGIS credentials are required")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("choose an empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    args.catalog_dir.mkdir(parents=True, exist_ok=True)

    # Import settings consumers after configure. Catalogs are isolated from server data.
    os.environ["DAENGS_WALK_PUBLIC_CATALOG_ROOT"] = ""
    os.environ["DAENGS_WALK_COMMERCE_CATALOG_PATH"] = str(args.catalog_dir / "commerce.json")
    from daengs_backend.services.walk_background.catalogs import area as walk_area_catalog
    from daengs_backend.services.walk_background.catalogs import commerce as walk_commerce_catalog
    from daengs_backend.services.walk_background.catalogs import park as walk_park_catalog
    from daengs_backend.services.walk_background.providers.weather import collect_temperature
    from daengs_backend.services.walk_diary.collection.service import collect_spaces
    from daengs_backend.services.walk_diary.writing.policy import writing_version
    from daengs_life.app.deps import get_cache
    from daengs_life.realtime.cache import MemoryStore

    class RecordingStore(MemoryStore):
        def set(self, key, entry, ttl_sec):
            super().set(key, entry, ttl_sec)
            dump(
                args.output / "weather-raw" / (digest(key) + ".json"),
                {
                    "cache_key": key,
                    "entry": json.loads(entry.dumps()),
                    "note": "Actual KMA response body retained by the existing Life transport/cache",
                },
            )

    (args.output / "weather-raw").mkdir()
    get_cache().store = RecordingStore()
    start = (
        datetime.fromisoformat(args.start)
        if args.start
        else (datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2))
    )
    if start.tzinfo is None:
        parser.error("--start requires a timezone")
    raw = read(args.input) if args.input else scenario(read(args.route), start)
    dump(args.output / "input.json", raw)
    base = prepare(raw)
    dump(args.output / "base.json", base.board)
    dump(args.output / "plan.json", base.plan)
    dump(args.output / "map.json", read(args.map))
    # One circle covers every 1 km scene query on this bounded route.
    points = [s.anchor.point for s in base.board.scenes if s.anchor.point is not None]
    center = {
        "lat": sum(p.lat for p in points) / len(points),
        "lng": sum(p.lng for p in points) / len(points),
    }
    transport = RecordingTransport(args.output / "public-http", [public_key, sgis_key, sgis_secret])
    started = time.monotonic()
    async with transport:
        if args.reuse_catalogs:
            parks = walk_park_catalog.read_catalog(str(args.catalog_dir / "park.json"))
            commerce = walk_area_catalog.read(str(args.catalog_dir / "commerce.json"), "commerce")
        else:
            print("Preparing live national park and route-area commerce catalogs", flush=True)
            parks, commerce = await asyncio.gather(
                walk_park_catalog.refresh_catalog(
                    transport, public_key, str(args.catalog_dir / "park.json")
                ),
                walk_commerce_catalog.refresh(
                    transport, public_key, str(args.catalog_dir / "commerce.json"), center, 2000
                ),
            )
        dump(
            args.output / "catalog-preparation.json",
            {
                "park": {
                    "total": parks["total"],
                    "valid": len(parks["parks"]),
                    "rejected": parks["rejected_rows"],
                    "sha256": digest(parks),
                },
                "commerce": {
                    "area": commerce["area"],
                    "valid": len(commerce["rows"]),
                    "rejected": commerce["rejected_rows"],
                    "sha256": commerce["sha256"],
                },
                "elapsed_s": round(time.monotonic() - started, 3),
                "reused": args.reuse_catalogs,
                "park_retrieved_at": parks["retrieved_at"],
                "commerce_retrieved_at": commerce["retrieved_at"],
                "timing_scope": "Real catalog preparation before scene acquisition and writer deadline",
            },
        )
        print(
            json.dumps({"parks": len(parks["parks"]), "commerce": len(commerce["rows"])}),
            flush=True,
        )
        snapshot = await collect_spaces(
            base.board,
            commerce_key=public_key,
            park_catalog=str(args.catalog_dir / "park.json"),
            sgis_key=sgis_key,
            sgis_secret=sgis_secret,
            include_sgis=True,
            transport=transport,
            timeout_s=args.collection_timeout,
        )
    temperatures = []
    for target in snapshot.targets:
        if target.anchor.point is None:
            continue
        collected = await collect_temperature(
            {"recorded_at": target.anchor.event_at.isoformat()}, target.anchor.point.model_dump()
        )
        temperatures.append(
            SavedBackground(
                id="scenario-weather:" + target.scene_id,
                target=target.core_ref,
                provider="weather-observation",
                payload_schema="walk-entry-context-v1",
                policy_version="walk-entry-context-v1",
                query_point=target.anchor.point,
                tags=("environment",),
                status=collected.status,
                reason=collected.reason,
                retrieved_at=collected.retrieved_at,
                temporal_basis=collected.temporal_basis or "unknown",
                payload=collected.payload,
                payload_sha256=digest(collected.payload) if collected.payload is not None else None,
            )
        )
    snapshot = snapshot.model_copy(
        update={"backgrounds": (*snapshot.backgrounds, *temperatures)}
    ).validate_board(base.board)
    dump(args.output / "backgrounds.json", snapshot)
    sources = dict(Counter(f"{b.provider}:{b.status}" for b in snapshot.backgrounds))
    print(json.dumps(sources, ensure_ascii=False), flush=True)
    # The quality run supplies acquired/selected facts before the unchanged card graph.
    writing_started = time.monotonic()
    result, enriched = await generate(base, snapshot, prepared_input=True)
    dump(args.output / "result.json", result)
    dump(args.output / "slots.json", enriched.slots)
    dump(
        args.output / "source-lineage.json",
        source_lineage(
            result.model_dump(mode="json"),
            enriched.slots.model_dump(mode="json"),
            snapshot.model_dump(mode="json"),
        ),
    )
    stats = {
        "generated_at": datetime.now(UTC).isoformat(),
        "elapsed_s": round(time.monotonic() - writing_started, 3),
        "writer": writing_version(),
        "prepared_input": True,
        "collection_adopted": result.scene_backgrounds is not None,
        "backgrounds_in_writing_input": enriched.scene_backgrounds is not None,
        "jobs": dict(Counter(f"{j.stage}:{j.failure_code or 'accepted'}" for j in result.jobs)),
        "provenance": raw["provenance"],
        "public_sources": sources,
        "public_http_calls": len(transport.receipts),
        "collection_timeout_s": args.collection_timeout,
        "reused_public_catalogs": args.reuse_catalogs,
        "acquisition": "Fresh public APIs and catalog preparation before generation; not publication timing",
    }
    dump(args.output / "run.json", stats)
    render(args.output)
    (args.output / "diary.md").write_text(
        "\n\n".join("## " + s.title + "\n\n" + s.body for s in result.bundle.scenes) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
