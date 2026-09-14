import httpx
import pytest

from daengs_backend.schemas.place_bookmark import BookmarkKey
from daengs_backend.services import place_bookmark_lookup as gateway

KEY = BookmarkKey(source="kcisa", ref="a/b?한글")


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "foreign",
        "duplicate",
        "overlap",
        "invalid-json",
        "large",
        "timeout",
        "invalid-filter",
        "down",
    ],
)
async def test_private_lookup_bounds_and_failure_mapping(monkeypatch, case):
    def respond(request):
        assert request.url.path == "/internal/place/bookmarks/lookup"
        assert "authorization" not in request.headers
        assert b"app_user_id" not in request.content
        if case == "timeout":
            raise httpx.ReadTimeout("test")
        if case == "invalid-filter":
            return httpx.Response(422)
        if case == "down":
            return httpx.Response(503)
        if case in {"large", "invalid-json"}:
            return httpx.Response(200, content=b" " * 4_000_001 if case == "large" else b"bad")
        hit = {"place": {"key": KEY.model_dump(), "name": "시설"}}
        body = {"filters": {}, "distance_available": False, "hits": [hit], "missing_keys": []}
        if case == "foreign":
            hit["place"]["key"]["ref"] = "unrequested"
        if case == "duplicate":
            body["hits"].append(hit)
        if case == "overlap":
            body["missing_keys"].append(KEY.model_dump())
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    lookup = gateway.HttpPlaceBookmarkLookup("http://place-private")
    if case == "valid":
        assert (await lookup.lookup([KEY], {}))["hits"][0]["place"]["name"] == "시설"
    else:
        error = (
            gateway.PlaceBookmarkInvalidFilters
            if case == "invalid-filter"
            else gateway.PlaceLookupUnavailable
        )
        with pytest.raises(error):
            await lookup.lookup([KEY], {})
