"""Real HTTP client parsing with a local mock transport, never a live Place service."""

import httpx
import pytest

from daengs_backend.services import territory_site_batch_lookup as batch
from daengs_backend.services.territory_site_lookup import TerritorySiteUnavailableError

SITE = "territory-site:hex-v1:140:324:777"


def client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        batch.httpx,
        "AsyncClient",
        lambda **kw: original(
            **kw,
            transport=httpx.MockTransport(handler),
        ),
    )
    return batch.HttpTerritorySiteBatchLookup("http://place", timeout_seconds=1)


async def test_lookup_sends_only_ids_and_accepts_missing_sites(monkeypatch):
    def handler(request):
        assert request.url.path == "/territory/sites/by-ids"
        assert list(request.url.params.multi_items()) == [
            ("site_ids", SITE),
            ("site_ids", SITE + "0"),
        ]
        return httpx.Response(200, json={"sites": [{"site_id": SITE, "lat": 37.5, "lng": 127.0}]})

    found = await client(monkeypatch, handler).find_by_ids([SITE, SITE + "0"])
    assert list(found) == [SITE]
    assert float(found[SITE].lat) == 37.5


@pytest.mark.parametrize(
    "kind", ["http", "shape", "duplicate", "unexpected", "coordinate", "large", "timeout"]
)
async def test_invalid_or_failed_response_is_unavailable(monkeypatch, kind):
    def handler(_):
        if kind == "timeout":
            raise httpx.ReadTimeout("timeout")
        if kind == "http":
            return httpx.Response(503)
        if kind == "large":
            return httpx.Response(200, content=b" " * 128001)
        row = {"site_id": SITE, "lat": 37.5, "lng": 127.0}
        payload = {"sites": [row]}
        if kind == "shape":
            payload = {"sites": {}}
        if kind == "duplicate":
            payload["sites"].append(row)
        if kind == "unexpected":
            row["site_id"] = SITE + "0"
        if kind == "coordinate":
            row["lat"] = 91
        return httpx.Response(200, json=payload)

    with pytest.raises(TerritorySiteUnavailableError):
        await client(monkeypatch, handler).find_by_ids([SITE])
