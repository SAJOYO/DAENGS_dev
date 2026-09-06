"""Bounded background-place lookup using the already deployed Place service, without an LLM."""

import asyncio
from datetime import UTC, datetime

import httpx

from daengs_backend.config import settings

KINDS = ["leisure", "cafe", "restaurant"]
LABELS = {"leisure": "여가 시설", "cafe": "카페", "restaurant": "음식점"}


def unavailable_contexts(selection):
    """The lookup deadline limits external I/O, not the observed walk's availability."""
    captured = datetime.now(UTC).isoformat()
    return {
        anchor["id"]: {
            "facts": [],
            "sources": [
                {
                    "source": "place-search",
                    "status": "unavailable",
                    "captured_at": captured,
                    "source_url": None,
                }
            ],
        }
        for anchor in selection["anchors"]
    }


async def lookup_contexts(selection, *, client=None):
    if client is None:
        async with httpx.AsyncClient(timeout=3.0) as opened:
            return await lookup_contexts(selection, client=opened)
    semaphore = asyncio.Semaphore(4)

    async def one(anchor):
        facts, status = [], "unavailable"
        captured = datetime.now(UTC).isoformat()
        async with semaphore:
            try:
                response = await client.post(
                    settings.place_search_base_url.rstrip("/") + "/v2/places/search",
                    json={
                        "lat": anchor["location"]["lat"],
                        "lng": anchor["location"]["lng"],
                        "radius_m": 250,
                        "kinds": KINDS,
                        "limit_per_kind": 3,
                    },
                    timeout=3.0,
                )
                response.raise_for_status()
                groups = response.json()["groups"]
                if not isinstance(groups, list):
                    raise TypeError("invalid groups")
                status = "known"
                for group in groups:
                    if group["kind"] not in KINDS:
                        raise ValueError("unexpected kind")
                    for hit in group["results"][:3]:
                        place = hit["place"]
                        distance = float(place["distance_m"])
                        if 0 <= distance <= 250:
                            name = str(place["name"])[:100]
                            key = place["key"]
                            facts.append(
                                f"{LABELS[group['kind']]} {name}의 등록 위치에서 약 {round(distance)}m "
                                f"({str(key['source'])[:40]}:{str(key['ref'])[:80]} · 방문/내부 판정 아님)"
                            )
                    if group.get("truncated"):
                        status = "partial"
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                facts, status = [], "unavailable"
        return anchor["id"], {
            "facts": facts,
            "sources": [
                {
                    "source": "place-search",
                    "status": status,
                    "captured_at": captured,
                    "source_url": None,
                }
            ],
        }

    return dict(await asyncio.gather(*(one(a) for a in selection["anchors"][:8])))
