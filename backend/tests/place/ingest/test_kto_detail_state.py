from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_place.ingest.kto import (
    KtoApiError,
    _prune_after_listing,
    fetch_pet_detail,
    source_records,
)
from daengs_place.place.source_facts.states import DetailAcquisitionState


def _payload(item) -> dict:
    return {
        "response": {
            "header": {"resultCode": "0000", "resultMsg": "OK"},
            "body": {"items": {"item": item}},
        }
    }


async def test_detail_fetch_distinguishes_no_data_from_failure() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=_payload([])))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_pet_detail(client, "1")

    assert result.state is DetailAcquisitionState.NO_DATA
    assert result.detail is None


async def test_detail_fetch_preserves_source_payload() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json=_payload(
                {
                    "contentid": "1",
                    "acmpyTypeCd": "전구역 동반가능",
                    "acmpyNeedMtr": "",
                }
            ),
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_pet_detail(client, "1")

    assert result.state is DetailAcquisitionState.FETCHED
    assert result.detail == {"acmpyTypeCd": "전구역 동반가능"}


async def test_exhausted_rate_limit_is_fetch_failed(monkeypatch) -> None:
    sleeps = AsyncMock()
    monkeypatch.setattr("daengs_place.ingest.kto.asyncio.sleep", sleeps)
    transport = httpx.MockTransport(lambda _: httpx.Response(429))

    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_pet_detail(client, "1")

    assert result.state is DetailAcquisitionState.FETCH_FAILED
    assert sleeps.await_count == 4


async def test_malformed_success_response_is_a_detail_failure() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, text="<html>upstream error</html>")
    )

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(KtoApiError, match="invalid KTO detail JSON"):
            await fetch_pet_detail(client, "1")


def test_shadow_list_keeps_hidden_items_filtered_from_product() -> None:
    records = source_records(
        [
            {"contentid": "1", "title": "노출", "showflag": "1"},
            {"contentid": "2", "title": "숨김", "showflag": "0"},
        ]
    )

    assert {record["source_ref"] for record in records} == {"1", "2"}


async def test_incremental_run_prunes_complete_shadow_snapshot(monkeypatch) -> None:
    product_prune = AsyncMock(return_value=7)
    source_prune = AsyncMock(return_value=3)
    monkeypatch.setattr("daengs_place.ingest.kto.prune_unseen", product_prune)
    monkeypatch.setattr("daengs_place.ingest.kto.prune_source_records", source_prune)
    session = object()
    observed_at = datetime.now(UTC)

    result = await _prune_after_listing(session, "incremental", observed_at)

    assert result == (0, 3)
    product_prune.assert_not_awaited()
    source_prune.assert_awaited_once_with(session, "kto", observed_at)
