"""Coverage, atomic publication, and real source-to-dictionary boundaries (offline fixtures)."""

import copy
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from shapely.geometry import box, mapping
from shapely.ops import transform

from daengs_backend.config import settings
from daengs_backend.services import walk_area_catalog as catalog
from daengs_backend.services import walk_commerce_catalog as commerce
from daengs_backend.services import walk_river_catalog as river
from daengs_backend.services.walk_diary.legacy.bundle import write_diary
from daengs_backend.services.walk_public_context import collect_public
from daengs_backend.services.walk_public_http import PublicSourceError
from daengs_walk.diary.contracts.input import SavedBackground, digest, material_ref
from daengs_walk.diary.legacy.writing import prepare_writing
from daengs_walk.diary.selection.stamps import StampPolicy, prepare_stamps
from daengs_walk.diary.space.projection import project_background
from tests.walk.support.diary import record, with_backgrounds

POINT = {"lat": 37.5, "lng": 127.0}


def page(rows, total=None):
    return {
        "response": {
            "header": {"resultCode": "00"},
            "body": {
                "items": rows,
                "totalCount": len(rows) if total is None else total,
            },
        }
    }


def shop(id="a", **updates):
    return {
        "bizesId": id,
        "lat": 37.5,
        "lon": 127.0,
        "indsMclsCd": "I201",
        "indsMclsNm": "음식점",
        "bizesNm": "원문 상호 제거",
        "rdnmAdr": "주소 제거",
        **updates,
    }


def water():
    x, y = catalog.xy(POINT)
    geometry = transform(river.TO_WEB.transform, box(x + 50, y - 50, x + 100, y + 50))
    return {
        "type": "FeatureCollection",
        "numberMatched": 1,
        "numberReturned": 1,
        "crs": {"properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
        "features": [
            {
                "type": "Feature",
                "id": "adm_river.1",
                "properties": {"name": "시험천"},
                "geometry": mapping(geometry),
            }
        ],
    }


def transport(shops=None, water_body=None):
    def handle(request):
        if "sdsc2" in request.url.path:
            return httpx.Response(200, json=page(shops if shops is not None else [shop()]))
        if "river_info_api" in request.url.path:
            # Deliberately no matching standard name: shape remains independently usable.
            return httpx.Response(
                200, json=page([{"rvrCd": "std-1", "rvrNm": "다른천", "dataCrtrYmd": "2026-01-01"}])
            )
        return httpx.Response(200, json=water_body if water_body is not None else water())

    return httpx.MockTransport(handle)


async def enabled(tmp_path, monkeypatch, *, shops=None):
    monkeypatch.setattr(settings, "walk_public_context_enabled", True)
    monkeypatch.setattr(settings, "walk_area_context_enabled", True)
    for kind, module in [("commerce", commerce), ("river", river)]:
        path = tmp_path / (kind + ".json")
        monkeypatch.setattr(settings, f"walk_{kind}_catalog_path", str(path))
        await module.refresh(transport(shops=shops), "fake-key", path, POINT, 1200)


def saved(core, result):
    return SavedBackground(
        id=result.provider,
        target=material_ref(core),
        provider=result.provider,
        payload_schema="walk-entry-context-v1",
        policy_version="walk-entry-context-v1",
        query_point=core.anchor.point,
        tags=("space",),
        status=result.status,
        retrieved_at=result.retrieved_at,
        temporal_basis="lookup_snapshot",
        payload=result.payload,
        payload_sha256=digest(result.payload),
    )


async def test_catalog_to_stamps_and_writer_preserves_records(tmp_path, monkeypatch):
    await enabled(tmp_path, monkeypatch)
    core = record()
    results = [await collect_public(tag, POINT) for tag in ["space.commerce", "space.river"]]
    assert [r.status for r in results] == ["known", "known"]
    assert results[1].payload["items"][0]["distance_m"] == 50
    assert results[1].payload["items"][0]["standard_name_matches"] == []
    assert results[1].payload["standard_status"] == "known"
    source = with_backgrounds(core, backgrounds=[saved(core, r) for r in results])
    prepared = prepare_stamps(source, StampPolicy(target_scene_count=3))
    request = prepare_writing(source, prepared)
    evidence = request.payload["background_dictionary"]
    assert {p["reference"] for p in evidence.values()} == {
        "egis_river_polygon",
        "registered_business_composition",
    }
    assert len(evidence) == 2
    assert all(p["relative_layout"] == "unknown" for p in evidence.values())
    encoded = json.dumps(request.payload, ensure_ascii=False)
    assert all(
        word not in encoded
        for word in [
            '"nearest_point"',
            '"geometry_sha256"',
            '"source_ref"',
            '"lat"',
            "원문 상호 제거",
            "주소 제거",
            "다른천",
        ]
    )
    call = AsyncMock(
        return_value={
            "title": "동네에서 남긴 기록",
            "scenes": [
                {
                    "scene_id": "s1",
                    "text": "음식점들과 하천이 주변에 있었다.",
                    "evidence_ids": list(evidence),
                }
            ],
        }
    )
    output = await write_diary(source, prepared, call)
    assert output.model_status == "accepted" and len(output.scenes) == 1
    assert output.scenes[0].user_record.text == core.content.text


async def test_shop_identity_conflicts_do_not_inflate_counts(tmp_path, monkeypatch):
    await enabled(
        tmp_path,
        monkeypatch,
        shops=[shop(), shop(), shop("b"), shop("b", lon=127.0001), shop("bad", lat="")],
    )
    result = await collect_public("space.commerce", POINT)
    assert result.status == "partial"
    assert result.payload["registered_count"] == 1
    assert result.payload["categories"][0]["count"] == 1
    raw = (tmp_path / "commerce.json").read_text(encoding="utf-8")
    assert "원문 상호 제거" not in raw and "주소 제거" not in raw and "fake-key" not in raw


async def test_zero_is_only_empty_inside_complete_coverage(tmp_path, monkeypatch):
    await enabled(tmp_path, monkeypatch, shops=[])
    assert (await collect_public("space.commerce", POINT)).status == "empty"
    result = await collect_public("space.commerce", {"lat": 37.6, "lng": 127.0})
    assert (result.status, result.reason) == ("unavailable", "outside_catalog_coverage")


@pytest.mark.parametrize("failure", ["changed", "repeated", "missing", "rejected"])
async def test_failed_refresh_preserves_previous_catalog(tmp_path, failure):
    path = tmp_path / "commerce.json"
    await commerce.refresh(transport(), "fake-key", path, POINT, 1200)
    previous = path.read_bytes()

    def handle(request):
        n = int(request.url.params["pageNo"])
        if n == 1:
            return httpx.Response(200, json=page([shop()], 2))
        if failure == "rejected":
            return httpx.Response(403, text="secret may be echoed")
        return httpx.Response(
            200,
            json=page(
                [] if failure == "missing" else [shop() if failure == "repeated" else shop("b")],
                3 if failure == "changed" else 2,
            ),
        )

    with pytest.raises(PublicSourceError):
        await commerce.refresh(httpx.MockTransport(handle), "fake-key", path, POINT, 1200)
    assert path.read_bytes() == previous


@pytest.mark.parametrize(
    "changes", [{"numberMatched": 2}, {"crs": {"properties": {"name": "EPSG:4326"}}}]
)
async def test_incomplete_or_wrong_crs_geometry_never_replaces_cache(tmp_path, changes):
    path = tmp_path / "river.json"
    await river.refresh(transport(), "fake-key", path, POINT, 1200)
    previous = path.read_bytes()
    with pytest.raises(PublicSourceError):
        await river.refresh(
            transport(water_body={**water(), **changes}), "fake-key", path, POINT, 1200
        )
    assert path.read_bytes() == previous


@pytest.mark.parametrize(
    "problem",
    ["stale", "hash", "outside", "pin_basis", "count", "distance", "standard_as_geometry"],
)
async def test_saved_projection_rejects_corruption(tmp_path, monkeypatch, problem):
    await enabled(tmp_path, monkeypatch)
    if problem in {"stale", "hash"}:
        path = tmp_path / "commerce.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["retrieved_at"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        if problem == "stale":
            value.pop("sha256")
            value["sha256"] = digest(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        assert (await collect_public("space.commerce", POINT)).status == "unavailable"
        return
    result = await collect_public(
        "space.river" if problem in {"distance", "standard_as_geometry"} else "space.commerce",
        POINT,
    )
    payload = copy.deepcopy(result.payload)
    if problem == "outside":
        payload["catalog_area"]["center"]["lat"] += 1
    if problem == "pin_basis":
        payload["location_basis"] = "interpolated"
    if problem == "count":
        payload["registered_count"] += 1
    if problem == "distance":
        payload["items"][0]["distance_m"] = 1
    if problem == "standard_as_geometry":
        payload["geometry"] = "standard_endpoint_line"
    core = record()
    evidence = saved(core, result).model_copy(
        update={"payload": payload, "payload_sha256": digest(payload)}
    )
    assert not project_background(evidence, core).pieces


async def test_disabled_flag_does_not_read_cache(tmp_path, monkeypatch):
    await enabled(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "walk_area_context_enabled", False)
    monkeypatch.setattr(catalog, "read", lambda *_: pytest.fail("disabled provider read the cache"))
    assert (await collect_public("space.commerce", POINT)).status == "not_requested"


def test_coverage_includes_entire_query_circle():
    value = {"area": catalog.area(POINT, 500)}
    x, y = catalog.xy(POINT)
    lng, lat = catalog.REVERSE.transform(x + 380, y)
    assert not catalog.covers(value, {"lat": lat, "lng": lng}, 125)


async def test_river_polygon_zero_distance_does_not_claim_bank_path(tmp_path, monkeypatch):
    await enabled(tmp_path, monkeypatch)
    x, y = catalog.xy(POINT)
    lng, lat = catalog.REVERSE.transform(x + 75, y)
    result = await collect_public("space.river", {"lat": lat, "lng": lng})
    assert result.payload["items"][0]["distance_m"] == 0
    assert result.payload["relation"] == "geometry_distance_not_bank_path_or_visit"


async def test_valid_geometry_outside_area_is_not_missing_coverage(tmp_path):
    body = water()
    x, y = catalog.xy(POINT)
    body["features"][0]["geometry"] = mapping(
        transform(river.TO_WEB.transform, box(x + 2000, y, x + 2100, y + 100))
    )
    value = await river.refresh(
        transport(water_body=body), "fake-key", tmp_path / "river.json", POINT, 1200
    )
    result = river.nearby(value, POINT)
    assert result["items"] == [] and result["complete"] is True


async def test_malformed_api_page_is_a_safe_failure(tmp_path):
    tr = httpx.MockTransport(lambda _: httpx.Response(200, json=[]))
    with pytest.raises(PublicSourceError, match="invalid_public_page"):
        await commerce.refresh(tr, "fake-key", tmp_path / "commerce.json", POINT, 1200)
    assert not (tmp_path / "commerce.json").exists()


@pytest.mark.parametrize("failure", ["reordered", "overlap", "changed_entity"])
async def test_cross_page_shop_overlap_preserves_catalog(tmp_path, failure):
    path = tmp_path / "commerce.json"
    await commerce.refresh(transport(), "fake-key", path, POINT, 1200)
    previous = path.read_bytes()
    first = [shop(str(i)) for i in range(1000)]
    second = (
        list(reversed(first))
        if failure == "reordered"
        else [shop(str(i)) for i in range(999, 1999)]
    )
    if failure == "changed_entity":
        second[0] = shop(" 999 ", lon=127.0001)

    def handle(request):
        rows = first if request.url.params["pageNo"] == "1" else second
        return httpx.Response(200, json=page(rows, 2000))

    with pytest.raises(PublicSourceError, match="catalog_changed_or_incomplete"):
        await commerce.refresh(httpx.MockTransport(handle), "fake-key", path, POINT, 1200)
    assert path.read_bytes() == previous


async def test_disjoint_shop_pages_allow_same_page_duplicates(tmp_path):
    def handle(request):
        rows = (
            [shop("a"), shop("a"), shop("b")]
            if request.url.params["pageNo"] == "1"
            else [shop("c")]
        )
        return httpx.Response(200, json=page(rows, 4))

    value = await commerce.refresh(
        httpx.MockTransport(handle), "fake-key", tmp_path / "commerce.json", POINT, 1200
    )
    result = commerce.nearby(catalog.read(tmp_path / "commerce.json", "commerce"), POINT)
    assert len(value["pages"]) == 2
    assert result["registered_count"] == 3 and result["complete"] is True


@pytest.mark.parametrize("failure", ["http", "timeout", "malformed", "missing_key", "late_page"])
async def test_standard_failure_keeps_river_geometry_usable(tmp_path, monkeypatch, failure):
    calls = []

    def handle(request):
        if "river_info_api" not in request.url.path:
            calls.append("egis")
            return httpx.Response(200, json=water())
        calls.append("standard")
        if failure == "timeout":
            raise httpx.ReadTimeout("secret may be echoed", request=request)
        if failure == "late_page" and request.url.params["pageNo"] == "1":
            return httpx.Response(
                200,
                json=page([{"rvrCd": "std-1", "rvrNm": "시험천", "dataCrtrYmd": "2026-01-01"}], 2),
            )
        return (
            httpx.Response(503, text="secret may be echoed")
            if failure in {"http", "late_page"}
            else httpx.Response(200, json=[])
        )

    path = tmp_path / "river.json"
    await river.refresh(
        httpx.MockTransport(handle),
        "" if failure == "missing_key" else "fake-key",
        path,
        POINT,
        1200,
    )
    monkeypatch.setattr(settings, "walk_public_context_enabled", True)
    monkeypatch.setattr(settings, "walk_area_context_enabled", True)
    monkeypatch.setattr(settings, "walk_river_catalog_path", str(path))
    result = await collect_public("space.river", POINT)
    assert calls.count("egis") == 1
    assert calls.count("standard") == (2 if failure == "late_page" else failure != "missing_key")
    assert result.status == "known" and result.payload["complete"] is True
    assert result.payload["standard_status"] == "unavailable"
    assert (
        result.payload["standard_reason"]
        == {
            "http": "http_503",
            "timeout": "transport_error",
            "malformed": "invalid_public_page",
            "missing_key": "provider_not_configured",
            "late_page": "http_503",
        }[failure]
    )
    assert result.payload["items"][0]["standard_name_matches"] == []
    core = record()
    projection = project_background(saved(core, result), core)
    assert len(projection.pieces) == 1 and projection.pieces[0].facts["distance_m"] == 50
    assert "secret may be echoed" not in path.read_text(encoding="utf-8")


async def test_reordered_standard_page_is_unavailable_not_complete(tmp_path):
    rows = [
        {"rvrCd": "std-1", "rvrNm": "시험천", "dataCrtrYmd": "2026-01-01"},
        {"rvrCd": "std-2", "rvrNm": "다른천", "dataCrtrYmd": "2026-01-01"},
    ]

    def handle(request):
        if "river_info_api" not in request.url.path:
            return httpx.Response(200, json=water())
        return httpx.Response(
            200,
            json=page(
                rows if request.url.params["pageNo"] == "1" else list(reversed(rows)),
                4,
            ),
        )

    value = await river.refresh(
        httpx.MockTransport(handle),
        "fake-key",
        tmp_path / "river.json",
        POINT,
        1200,
    )
    result = river.nearby(value, POINT)
    assert result["complete"] is True and result["items"][0]["distance_m"] == 50
    assert result["standard_status"] == "unavailable"
    assert result["standard_reason"] == "catalog_changed_or_incomplete"


async def test_egis_failure_preserves_previous_catalog(tmp_path):
    path = tmp_path / "river.json"
    await river.refresh(transport(), "fake-key", path, POINT, 1200)
    previous, calls = path.read_bytes(), []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(503, text="unavailable")

    with pytest.raises(PublicSourceError, match="http_503"):
        await river.refresh(httpx.MockTransport(handle), "fake-key", path, POINT, 1200)
    assert calls == ["/geoserver/wfs"] and path.read_bytes() == previous


async def test_legacy_river_catalog_remains_readable(tmp_path):
    path = tmp_path / "river.json"
    value = await river.refresh(transport(), "fake-key", path, POINT, 1200)
    for key in ("status", "reason"):
        value["standard"].pop(key)
    value.pop("sha256")
    value["sha256"] = digest(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    result = river.nearby(catalog.read(path, "river"), POINT)
    assert result["standard_status"] == "known" and result["items"][0]["distance_m"] == 50


@pytest.mark.parametrize("kind", ["commerce", "river"])
async def test_cli_requires_key_only_for_commerce(tmp_path, monkeypatch, capsys, kind):
    import sys

    from pydantic import SecretStr

    from daengs_backend.cli.walk_area_catalog import main

    path, calls = tmp_path / f"{kind}.json", []
    monkeypatch.setattr(sys, "argv", ["catalog", kind, "--lat", "37.5", "--lng", "127.0"])
    monkeypatch.setattr(settings, "walk_public_data_key", SecretStr(""))
    monkeypatch.setattr(settings, f"walk_{kind}_catalog_path", str(path))

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=water())

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: httpx.MockTransport(handle))
    if kind == "commerce":
        with pytest.raises(SystemExit, match="Set DAENGS_WALK_PUBLIC_DATA_KEY"):
            await main()
        assert calls == [] and not path.exists()
    else:
        await main()
        assert calls == ["/geoserver/wfs"] and path.exists()
        assert (
            "standard_status=unavailable reason=provider_not_configured" in capsys.readouterr().out
        )
