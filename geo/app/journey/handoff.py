"""실제 따라가기는 제공사 앱으로. 딥링크 3종 (앱 미설치 시 스토어/웹은 클라이언트가 처리)."""

from urllib.parse import quote

from app.journey.models import Handoff
from app.providers.base import LatLng


def handoff_links(origin: LatLng, dest: LatLng, dest_name: str, mode: str = "walk") -> Handoff:
    n = quote(dest_name or "도착")
    naver_mode = {"walk": "walk", "car": "car", "transit": "public"}.get(mode, "walk")
    kakao_by = {"walk": "FOOT", "car": "CAR", "transit": "PUBLICTRANSIT"}.get(mode, "FOOT")
    return Handoff(
        naver=f"nmap://route/{naver_mode}?slat={origin.lat}&slng={origin.lng}&sname={quote('현재 위치')}"
              f"&dlat={dest.lat}&dlng={dest.lng}&dname={n}&appname=daengs",
        kakao=f"kakaomap://route?sp={origin.lat},{origin.lng}&ep={dest.lat},{dest.lng}&by={kakao_by}",
        tmap=f"tmap://route?goalx={dest.lng}&goaly={dest.lat}&goalname={n}",
    )
