from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.ingest.mois import (
    STATUS_NAMES,
    MoisApiError,
    MoisClient,
    normalize,
)
from app.ingest.mois_sync import overlap_watermark, sync_source
from app.place.source_catalog import MOIS_SOURCES as SOURCES


def _item(**overrides):
    item = {
        "OPN_ATMY_GRP_CD": "3220000",
        "MNG_NO": "H-001",
        "BPLC_NM": "강남24시동물병원",
        "ROAD_NM_ADDR": "서울 강남구 테헤란로 1",
        "LOTNO_ADDR": "서울 강남구 역삼동 1",
        "TELNO": "02-000-0000",
        "CRD_INFO_X": "203456.7",
        "CRD_INFO_Y": "445678.9",
        "SALS_STTS_CD": "01",
        "SALS_STTS_NM": "영업/정상",
        "DAT_UPDT_PNT": "20260819030405",
    }
    item.update(overrides)
    return item


def test_normalize_maps_identity_status_tags_and_source_time():
    record = normalize(_item(), SOURCES["hospital"])
    assert record.source_id == "3220000:H-001"
    assert record.kind == "hospital" and record.active is True
    assert record.tags == ("24h",)
    assert record.source_updated_at == datetime(2026, 8, 19, 3, 4, 5, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.mark.parametrize("code", ["02", "03", "04", "05", "06"])
def test_only_official_normal_status_is_active(code):
    record = normalize(_item(SALS_STTS_CD=code), SOURCES["hospital"])
    assert record.active is False
    assert record.license_status_name == STATUS_NAMES[code]


def test_numeric_status_code_is_zero_padded():
    record = normalize(_item(SALS_STTS_CD=1), SOURCES["hospital"])
    assert record.license_status_code == "01" and record.active is True


def test_coordinate_contract_violations_are_quarantined_for_store():
    record = normalize(
        _item(CRD_INFO_X="127.01", CRD_INFO_Y="not-a-number"),
        SOURCES["hospital"],
    )
    assert record.x_5174 is None and record.y_5174 is None


async def test_client_paginates_and_decodes_encoded_service_key():
    requested_pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        requested_pages.append(query["pageNo"][0])
        assert query["serviceKey"][0] == "abc+def"
        page = int(query["pageNo"][0])
        rows = [_item(MNG_NO=f"H-{i}") for i in range((page - 1) * 2, min(page * 2, 3))]
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00", "resultMsg": "OK"},
                    "body": {
                        "totalCount": "3",
                        "items": {"item": rows},
                    },
                }
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MoisClient("abc%2Bdef", page_size=2, client=http)
    rows = await client.fetch_all(SOURCES["hospital"])
    await http.aclose()
    assert len(rows) == 3
    assert requested_pages == ["1", "2"]


async def test_client_raises_for_portal_result_code():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "-10", "resultMsg": "quota exceeded"},
                    "body": {},
                }
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MoisClient("key", client=http)
    with pytest.raises(MoisApiError, match="quota exceeded"):
        await client.fetch_all(SOURCES["hospital"])
    await http.aclose()


def test_overlap_watermark():
    assert overlap_watermark("20260819030405", 3) == "20260816030405"
    assert overlap_watermark(None, 3) is None


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.updated_since = None

    async def fetch_all(self, _source, *, updated_since=None):
        self.updated_since = updated_since
        return self.items


class FakeStore:
    def __init__(self, watermark=None, reject_ids=()):
        self.watermark = watermark
        self.reject_ids = set(reject_ids)
        self.records = []
        self.deactivated_with = None
        self.committed = False
        self.rolled_back = False

    async def upsert(self, record):
        self.records.append(record)
        return record.source_id not in self.reject_ids

    async def get_watermark(self, _source):
        return self.watermark

    async def set_watermark(self, _source, watermark):
        self.watermark = watermark

    async def deactivate_missing(self, _source, seen_ids):
        self.deactivated_with = seen_ids
        return 2

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


async def test_full_sync_reconciles_only_complete_snapshot():
    client = FakeClient([_item(MNG_NO="1"), _item(MNG_NO="2")])
    store = FakeStore()
    stats = await sync_source(client, store, SOURCES["hospital"], mode="full")
    assert stats.received == stats.stored == 2
    assert stats.deactivated == 2 and stats.reconciled is True
    assert store.deactivated_with == ["3220000:1", "3220000:2"]
    assert store.committed is True


async def test_incremental_sync_uses_overlap_and_never_deactivates_missing():
    client = FakeClient([_item(MNG_NO="1")])
    store = FakeStore(watermark="20260819030405")
    stats = await sync_source(client, store, SOURCES["hospital"], mode="incremental")
    assert client.updated_since == "20260816030405"
    assert stats.reconciled is False and store.deactivated_with is None


async def test_unstored_coordinate_record_is_still_part_of_full_snapshot():
    client = FakeClient([_item(MNG_NO="1"), _item(MNG_NO="2")])
    store = FakeStore(reject_ids={"3220000:2"})
    stats = await sync_source(client, store, SOURCES["hospital"], mode="full")
    assert stats.rejected == 1 and stats.reconciled is True
    assert store.deactivated_with == ["3220000:1", "3220000:2"]
