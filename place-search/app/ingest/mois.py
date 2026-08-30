"""행정안전부 동물병원/동물약국 OpenAPI 클라이언트와 정규화.

OpenAPI 좌표는 EPSG:5174다. 이 모듈은 원본 x/y를 보존하고, 실제 EPSG:4326 변환과
대한민국 범위 검증은 PostGIS UPSERT에서 수행한다.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

from app.geo.tagging import tags_for
from app.place.source_catalog import (
    MoisKind as Kind,
)
from app.place.source_catalog import (
    MoisSource,
)

BASE_URL = "https://apis.data.go.kr/1741000"
SUCCESS_CODES = {"0", "00", "0000"}
KST = ZoneInfo("Asia/Seoul")

# 공공데이터포털 공식 '개방자치단체코드_영업상태코드.xlsx' (2026-08-20 확인).
STATUS_NAMES = {
    "01": "영업/정상",
    "02": "휴업",
    "03": "폐업",
    "04": "취소/말소/만료/정지/중지",
    "05": "제외/삭제/전출",
    "06": "기타",
}


class MoisApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class MoisRecord:
    source: str
    source_id: str
    kind: Kind
    name: str
    address: str | None
    phone: str | None
    x_5174: float | None
    y_5174: float | None
    active: bool
    license_status_code: str | None
    license_status_name: str | None
    source_updated_at: datetime | None
    watermark: str | None
    tags: tuple[str, ...]
    raw_data: dict


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coordinate(value: object) -> float | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    # EPSG:5174 좌표는 미터 단위다. WGS84처럼 보이거나 0인 값은 원천 이상으로 격리한다.
    if number <= 1_000 or number >= 1_000_000:
        return None
    return number


def _status_code(value: object) -> str | None:
    text = _clean(value)
    if text and text.isdigit():
        return text.zfill(2)
    return text


def _source_datetime(value: object) -> tuple[datetime | None, str | None]:
    text = _clean(value)
    if text is None:
        return None, None
    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None, None
    return parsed, text


def source_id(item: dict) -> str | None:
    authority = _clean(item.get("OPN_ATMY_GRP_CD"))
    management = _clean(item.get("MNG_NO"))
    if not authority or not management:
        return None
    return f"{authority}:{management}"


def normalize(item: dict, source: MoisSource) -> MoisRecord:
    sid = source_id(item)
    name = _clean(item.get("BPLC_NM"))
    if sid is None:
        raise ValueError("OPN_ATMY_GRP_CD/MNG_NO missing")
    if name is None:
        raise ValueError(f"BPLC_NM missing for {sid}")

    status = _status_code(item.get("SALS_STTS_CD"))
    source_dt, watermark = _source_datetime(item.get("DAT_UPDT_PNT"))
    return MoisRecord(
        source=source.source,
        source_id=sid,
        kind=source.kind,
        name=name,
        address=_clean(item.get("ROAD_NM_ADDR")) or _clean(item.get("LOTNO_ADDR")),
        phone=_clean(item.get("TELNO")),
        x_5174=_coordinate(item.get("CRD_INFO_X")),
        y_5174=_coordinate(item.get("CRD_INFO_Y")),
        active=status == "01",
        license_status_code=status,
        license_status_name=STATUS_NAMES.get(status or "") or _clean(item.get("SALS_STTS_NM")),
        source_updated_at=source_dt,
        watermark=watermark,
        tags=tuple(tags_for(name)),
        raw_data=dict(item),
    )


class MoisClient:
    def __init__(
        self,
        service_key: str,
        *,
        page_size: int = 100,
        client: httpx.AsyncClient | None = None,
        retries: int = 3,
    ):
        if not service_key.strip():
            raise ValueError("data.go.kr service key required")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        # Encoding 키를 붙여 넣어도 httpx가 이중 인코딩하지 않도록 한 번만 decode한다.
        self._key = unquote(service_key.strip())
        self._page_size = page_size
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None
        self._retries = retries

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _page(
        self, source: MoisSource, page: int, updated_since: str | None
    ) -> tuple[list[dict], int]:
        params = {
            "serviceKey": self._key,
            "pageNo": str(page),
            "numOfRows": str(self._page_size),
            "returnType": "json",
        }
        if updated_since:
            params["cond[DAT_UPDT_PNT::GTE]"] = updated_since

        url = f"{BASE_URL}/{source.slug}/info"
        response: httpx.Response | None = None
        for attempt in range(self._retries):
            try:
                response = await self._client.get(url, params=params)
                response.raise_for_status()
                break
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt + 1 >= self._retries:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))
        assert response is not None

        try:
            envelope = response.json()["response"]
            header = envelope["header"]
            body = envelope["body"]
        except (KeyError, TypeError, ValueError) as exc:
            raise MoisApiError("invalid data.go.kr response envelope") from exc

        code = str(header.get("resultCode", ""))
        if code not in SUCCESS_CODES:
            message = header.get("resultMsg") or "unknown error"
            raise MoisApiError(f"data.go.kr error {code}: {message}")

        raw_items = (body.get("items") or {}).get("item") or []
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            raise MoisApiError("invalid data.go.kr items")
        try:
            total = int(body["totalCount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MoisApiError("invalid data.go.kr totalCount") from exc
        return [item for item in raw_items if isinstance(item, dict)], total

    async def fetch_all(
        self, source: MoisSource, *, updated_since: str | None = None
    ) -> list[dict]:
        first, total = await self._page(source, 1, updated_since)
        items = list(first)
        total_pages = (total + self._page_size - 1) // self._page_size
        for page in range(2, total_pages + 1):
            chunk, total = await self._page(source, page, updated_since)
            items.extend(chunk)
            if not chunk:
                raise MoisApiError(f"empty page {page} before totalCount={total}")
        if len(items) < total:
            raise MoisApiError(f"received {len(items)} rows before totalCount={total}")
        return items
