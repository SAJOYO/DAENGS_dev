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


async def test_authenticated_interpret_uses_no_write_or_lookup_and_keeps_member_private(
    monkeypatch,
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
        return Interpretation(goal="show", changes={"parking": "required_true"})

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
                "query": "주차 되는 곳만",
                "filters": {"lat": 37.5, "lng": 127, "radius_m": 3000},
            }
            response = await client.post(
                "/app/places/bookmarks/interpret",
                json=body,
                headers={"Authorization": "Bearer " + create_access_token(owner, SubjectType.APP)},
            )
            assert response.status_code == 200, response.text
            assert response.json()["filters"]["hard"]["all"][0]["value"] is True
            assert response.json()["filters"]["parking"] is False
            assert calls == ["active", "plan"]
            assert (
                await client.post("/app/places/bookmarks/interpret", json=body)
            ).status_code == 401
            assert calls == ["active", "plan"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(before)


@pytest.mark.parametrize(
    "case", ["valid", "wrong_action", "missing_filters", "large", "timeout", "bad_filters"]
)
async def test_interpret_http_bounds_and_no_identity_forwarding(monkeypatch, case):
    def respond(request):
        assert request.url.path == "/internal/place/bookmarks/interpret"
        assert "authorization" not in request.headers
        assert set(json.loads(request.content)) == {"query", "filters"}
        if case == "timeout":
            raise httpx.ReadTimeout("test")
        if case == "bad_filters":
            return httpx.Response(422)
        if case == "large":
            return httpx.Response(200, content=b" " * 64001)
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
    if case == "valid":
        assert (await lookup.interpret("주차 우선", {})).action == "search"
    else:
        with pytest.raises(
            gateway.PlaceBookmarkInvalidFilters
            if case == "bad_filters"
            else gateway.PlaceLookupUnavailable
        ):
            await lookup.interpret("주차 우선", {})
