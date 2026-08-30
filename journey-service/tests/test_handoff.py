from urllib.parse import unquote

from app.journey.handoff import handoff_links
from app.providers.base import LatLng


def test_handoff_keeps_provider_coordinate_order_and_mode() -> None:
    origin = LatLng(37.5, 127.0)
    destination = LatLng(37.6, 127.1)

    handoff = handoff_links(origin, destination, "서울 숲", "transit")

    assert "dlat=37.6&dlng=127.1" in handoff.naver
    assert "/public?" in handoff.naver
    assert "ep=37.6,127.1&by=PUBLICTRANSIT" in handoff.kakao
    assert "goalx=127.1&goaly=37.6" in handoff.tmap
    assert "서울 숲" in unquote(handoff.tmap)
