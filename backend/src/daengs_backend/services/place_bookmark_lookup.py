"""Bounded private HTTP boundary; never forwards member IDs or credentials to Place."""

import json

import httpx

from daengs_backend.config import settings
from daengs_backend.schemas.place_bookmark import BookmarkKey


class PlaceLookupUnavailable(Exception):
    pass


class PlaceBookmarkInvalidFilters(Exception):
    pass


class HttpPlaceBookmarkLookup:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def interpret(self, query, filters):
        from daengs_backend.schemas.place_bookmark import BookmarkInterpretResult

        try:
            async with (
                httpx.AsyncClient(timeout=35.0) as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/internal/place/bookmarks/interpret",
                    json={"query": query, "filters": filters},
                ) as response,
            ):
                if response.status_code == 422:
                    raise PlaceBookmarkInvalidFilters
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 64000:
                        raise ValueError("interpretation response too large")
            result = BookmarkInterpretResult.model_validate_json(body)
            if (result.action == "search") != (result.filters is not None):
                raise ValueError("invalid saved plan")
            return result
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise PlaceLookupUnavailable from exc

    async def lookup(self, keys, filters):
        try:
            async with (
                httpx.AsyncClient(timeout=15.0) as client,
                client.stream(
                    "POST",
                    f"{self.base_url}/internal/place/bookmarks/lookup",
                    json={"keys": [k.model_dump() for k in keys], "filters": filters},
                ) as response,
            ):
                if response.status_code == 422:
                    raise PlaceBookmarkInvalidFilters
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 4_000_000:
                        raise ValueError("lookup response too large")
            payload = json.loads(body)
            allowed, seen = set(keys), set()
            if not isinstance(payload["distance_available"], bool) or not isinstance(
                payload["filters"], dict
            ):
                raise TypeError("invalid lookup envelope")
            for hit in payload["hits"]:
                key = BookmarkKey.model_validate(hit["place"]["key"])
                if key not in allowed or key in seen or not isinstance(hit["place"]["name"], str):
                    raise ValueError("unexpected lookup identity")
                seen.add(key)
            for value in payload["missing_keys"]:
                key = BookmarkKey.model_validate(value)
                if key not in allowed or key in seen:
                    raise ValueError("unexpected missing identity")
                seen.add(key)
            return payload
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise PlaceLookupUnavailable from exc


def get_place_bookmark_lookup():
    return HttpPlaceBookmarkLookup(settings.place_search_base_url)
