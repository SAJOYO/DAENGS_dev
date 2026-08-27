from app.geo.schemas import PlaceOut
from app.geo.search import build_map
from app.providers.base import NullProvider


def _p(i, lat, lng):
    return PlaceOut(id=i, kind="hospital", name=f"h{i}", lat=lat, lng=lng, distance_m=100 * i,
                    address=None, phone=None, is_night=True, is_24h=False, open_now=True, hours_today=None)


def test_deeplink_carries_filters_and_ids():
    m = build_map(37.5, 127.0, 2000, "hospital", True, True, [_p(1, 37.5, 127.0), _p(2, 37.51, 127.01)])
    assert m.deeplink.startswith("daengs://map?")
    assert "type=hospital" in m.deeplink
    assert "filter=open%2Cnight" in m.deeplink
    assert "ids=1%2C2" in m.deeplink
    assert m.web_url.split("?", 1)[1] == m.deeplink.split("?", 1)[1]


def test_no_provider_gives_no_preview(monkeypatch):
    monkeypatch.setattr("app.geo.search.static_map_provider", lambda: NullProvider())
    m = build_map(37.5, 127.0, 2000, None, False, False, [])
    assert m.preview_url is None
    assert "ids" not in m.deeplink


def test_deeplink_preserves_emergency_preference():
    m = build_map(37.5, 127.0, 2000, "hospital", False, False, [], emergency=True)
    assert "filter=emergency" in m.deeplink
    assert m.web_url.split("?", 1)[1] == m.deeplink.split("?", 1)[1]
