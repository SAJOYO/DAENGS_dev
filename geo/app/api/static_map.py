"""정적 지도 프록시. 제공사 키를 클라이언트에 노출하지 않기 위해 서버가 대신 받아온다."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from app.core.config import settings
from app.providers.base import LatLng, MapMarker, StaticMapSpec
from app.usage.composition import static_map_fetcher
from app.usage.http import usage_http_exception
from app.usage.models import UsageDenied

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/client-config")
async def map_client_config():
    """웹 지도 부팅 정보. key id는 브라우저 SDK용 공개 식별자이고 secret은 내보내지 않는다."""
    return {
        "provider": settings.map_provider,
        "naver_key_id": (
            settings.naver_ncp_key_id
            if settings.map_provider == "naver" and settings.naver_ncp_key_id
            else None
        ),
        "fallback": "osm",
    }


@router.get("/static")
async def static_map(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    zoom: Annotated[int, Query(ge=1, le=20)] = 16,
    w: Annotated[int, Query(ge=1, le=1024)] = 600,
    h: Annotated[int, Query(ge=1, le=1024)] = 300,
    m: Annotated[str, Query(max_length=4000)] = "",
):
    markers = []
    try:
        items = list(filter(None, m.split(",")))
        if len(items) > 100:
            raise ValueError("at most 100 markers are allowed")
        for item in items:
            mlat, mlng, label, hl = item.split(":")
            marker_lat, marker_lng = float(mlat), float(mlng)
            if not (-90 <= marker_lat <= 90 and -180 <= marker_lng <= 180):
                raise ValueError("marker coordinates out of range")
            if len(label) > 20 or hl not in ("0", "1"):
                raise ValueError("invalid marker label or highlight")
            markers.append(MapMarker(LatLng(marker_lat, marker_lng), label, hl == "1"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid markers: {exc}") from exc
    spec = StaticMapSpec(LatLng(lat, lng), zoom, w, h, tuple(markers))
    fetcher = static_map_fetcher()
    if fetcher is None:
        raise HTTPException(404, "static map provider not configured")
    try:
        png = await fetcher.fetch_static_png(spec)
    except UsageDenied as exc:
        raise usage_http_exception(exc) from exc
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
