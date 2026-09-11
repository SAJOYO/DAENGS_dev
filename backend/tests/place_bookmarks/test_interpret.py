import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from daengs_backend.core.database import get_snapshot_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.services import place_bookmark as service
from daengs_backend.services import place_bookmark_lookup as gateway
from daengs_place.api import bookmarks_internal
from daengs_place.main import app as place_app
from daengs_place.place.conversation.intent import Interpretation


@pytest.mark.parametrize("handoff", [False, True])
async def test_authenticated_interpret_uses_no_write_or_lookup_and_keeps_member_private(
    monkeypatch,
    handoff,
):
    owner = uuid4()
    calls = []

    async def database():
        yield None

    async def list_saved(db, who):
        assert who == owner
        calls.append("active")

    async def interpret(request):
        assert request.filters.radius_m == 3000
        calls.append("plan")
        assert request.search_policy == ("v1" if handoff else None)
        return Interpretation(
            goal="show",
            search_scope="all_places" if handoff else "keep",
            search_scope_quote="찜 제한 풀고" if handoff else "",
            changes={"parking": "required_true"},
        )

    monkeypatch.setattr(service, "list_saved", list_saved)
    monkeypatch.setattr(
        bookmarks_internal, "provider", lambda: SimpleNamespace(plan_saved=interpret)
    )
    original_client = httpx.AsyncClient

    class PrivateClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.ASGITransport(place_app), **kwargs)

    monkeypatch.setattr(gateway.httpx, "AsyncClient", PrivateClient)
    before = app.dependency_overrides.copy()
    app.dependency_overrides.update(
        {
            get_snapshot_session: database,
            gateway.get_place_bookmark_lookup: lambda: gateway.HttpPlaceBookmarkLookup(
                "http://private"
            ),
        }
    )
    try:
        async with original_client(
            transport=httpx.ASGITransport(app), base_url="http://app"
        ) as client:
            body = {
                "query": "찜 제한 풀고 주차 되는 곳만" if handoff else "주차 되는 곳만",
                "filters": {"lat": 37.5, "lng": 127, "radius_m": 3000, "kinds": ["cafe"]},
                "search_policy": "v1" if handoff else None,
            }
            response = await client.post(
                "/app/places/bookmarks/interpret",
                json=body,
                headers={"Authorization": "Bearer " + create_access_token(owner, SubjectType.APP)},
            )
            assert response.status_code == 200, response.text
            candidate = response.json()["search_filters" if handoff else "filters"]
            assert candidate["hard"]["all"][0]["value"] is True
            if handoff:
                assert response.json()["action"] == "search_places"
                assert not candidate["preferences"]
            else:
                assert candidate["parking"] is False
            assert calls == ["active", "plan"]
            assert (
                await client.post("/app/places/bookmarks/interpret", json=body)
            ).status_code == 401
            assert calls == ["active", "plan"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(before)


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "wrong_action",
        "missing_filters",
        "large",
        "timeout",
        "bad_filters",
        "normal",
        "unnegotiated",
        "mixed",
        "missing_normal",
    ],
)
async def test_interpret_http_bounds_and_no_identity_forwarding(monkeypatch, case):
    def respond(request):
        assert request.url.path == "/internal/place/bookmarks/interpret"
        assert "authorization" not in request.headers
        assert set(json.loads(request.content)) == {"query", "filters", "search_policy"}
        if case == "timeout":
            raise httpx.ReadTimeout("test")
        if case == "bad_filters":
            return httpx.Response(422)
        if case == "large":
            return httpx.Response(200, content=b" " * 64001)
        if case in {"normal", "unnegotiated", "mixed", "missing_normal"}:
            return httpx.Response(
                200,
                json={
                    "action": "search_places",
                    "message": "",
                    "filters": {} if case == "mixed" else None,
                    "search_filters": None if case == "missing_normal" else {},
                },
            )
        return httpx.Response(
            200,
            json={
                "action": "save" if case == "wrong_action" else "search",
                "message": "",
                "filters": None if case == "missing_filters" else {},
            },
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        gateway.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    lookup = gateway.HttpPlaceBookmarkLookup("http://place")
    if case == "normal":
        assert (
            await lookup.interpret("찜 제한 풀어", {}, search_policy="v1")
        ).action == "search_places"
    elif case == "valid":
        assert (await lookup.interpret("주차 우선", {})).action == "search"
    else:
        with pytest.raises(
            gateway.PlaceBookmarkInvalidFilters
            if case == "bad_filters"
            else gateway.PlaceLookupUnavailable
        ):
            await lookup.interpret(
                "주차 우선", {}, search_policy="v1" if case in {"mixed", "missing_normal"} else None
            )
