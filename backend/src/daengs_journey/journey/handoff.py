from urllib.parse import quote

from daengs_journey.journey.models import Handoff
from daengs_journey.providers.base import LatLng


def handoff_links(origin: LatLng, dest: LatLng, dest_name: str, mode: str = "walk") -> Handoff:
    name = quote(dest_name or "도착")
    naver_mode = {"walk": "walk", "car": "car", "transit": "public"}.get(mode, "walk")
    kakao_by = {"walk": "FOOT", "car": "CAR", "transit": "PUBLICTRANSIT"}.get(
        mode, "FOOT"
    )
    return Handoff(
        naver=(
            f"nmap://route/{naver_mode}?slat={origin.lat}&slng={origin.lng}"
            f"&sname={quote('현재 위치')}&dlat={dest.lat}&dlng={dest.lng}"
            f"&dname={name}&appname=daengs"
        ),
        kakao=(
            f"kakaomap://route?sp={origin.lat},{origin.lng}"
            f"&ep={dest.lat},{dest.lng}&by={kakao_by}"
        ),
        tmap=f"tmap://route?goalx={dest.lng}&goaly={dest.lat}&goalname={name}",
    )
