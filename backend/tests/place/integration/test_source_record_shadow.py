"""원천 shadow는 제품 facility와 독립되고 detail 획득 상태를 잃지 않는다."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from daengs_place.ingest.facility_store import (
    invalidate_pet_details_without_source_payload,
    upsert_rows,
)
from daengs_place.ingest.source_record_store import (
    pending_detail_refs,
    prune_source_records,
    record_detail_result,
    upsert_source_records,
)
from daengs_place.place.restriction_map import RESTRICTION_SEMANTICS_VERSION
from daengs_place.place.source_facts.states import DetailAcquisitionState
from tests.place.support.database import db_session

SOURCE = "test:source_record"


async def _clean(session) -> None:
    await session.rollback()
    await session.execute(
        text("DELETE FROM facility WHERE source = :source"),
        {"source": SOURCE},
    )
    await session.execute(
        text("DELETE FROM facility_source_record WHERE source = :source"),
        {"source": SOURCE},
    )
    await session.commit()


def _records(*refs: str) -> list[dict]:
    return [
        {
            "source_ref": ref,
            "listing_raw": {"contentid": ref, "title": f"장소-{ref}"},
        }
        for ref in refs
    ]


def _facility_row(ref: str, pet: dict) -> dict:
    return {
        "source_ref": ref,
        "name": f"장소-{ref}",
        "kind": "cafe",
        "category3": "카페",
        "sido": None,
        "sigungu": None,
        "address": None,
        "phone": None,
        "homepage": None,
        "hours_text": None,
        "closed_days": None,
        "parking": None,
        "indoor": None,
        "outdoor": None,
        "lat": 37.5,
        "lng": 127.0,
        "last_written": None,
        "pet": json.dumps(pet, ensure_ascii=False),
        "raw": None,
    }


async def test_shadow_record_does_not_require_product_facility() -> None:
    async with db_session() as session:
        await _clean(session)
        try:
            now = datetime.now(UTC)
            await upsert_source_records(
                session,
                SOURCE,
                _records("excluded"),
                "2026-08-31",
                now,
                detail_state=DetailAcquisitionState.NOT_APPLICABLE,
                preserve_detail=False,
            )
            await session.commit()

            shadow = (
                await session.execute(
                    text("""
                        SELECT listing_raw, detail_state
                        FROM facility_source_record
                        WHERE source = :source AND source_ref = 'excluded'
                    """),
                    {"source": SOURCE},
                )
            ).one()
            facility_count = (
                await session.execute(
                    text("SELECT count(*) FROM facility WHERE source = :source"),
                    {"source": SOURCE},
                )
            ).scalar_one()

            assert shadow.listing_raw["title"] == "장소-excluded"
            assert shadow.detail_state == "not_applicable"
            assert facility_count == 0
        finally:
            await _clean(session)


async def test_detail_queue_can_exclude_shadow_only_records() -> None:
    async with db_session() as session:
        await _clean(session)
        try:
            await upsert_source_records(
                session,
                SOURCE,
                _records("hidden"),
                "2026-08-31",
                datetime.now(UTC),
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
            )

            assert await pending_detail_refs(session, SOURCE, 10) == ["hidden"]
            assert await pending_detail_refs(session, SOURCE, 10, require_facility=True) == []
        finally:
            await _clean(session)


async def test_kto_relisting_preserves_fetched_detail_and_state() -> None:
    async with db_session() as session:
        await _clean(session)
        try:
            first = datetime.now(UTC)
            first_records = _records("1")
            first_records[0]["listing_raw"]["modifiedtime"] = "20260831010000"
            await upsert_source_records(
                session,
                SOURCE,
                first_records,
                "2026-08-31",
                first,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
                detail_version_field="modifiedtime",
            )
            await record_detail_result(
                session,
                SOURCE,
                "1",
                DetailAcquisitionState.FETCHED,
                first,
                {"acmpyTypeCd": "전구역 동반가능"},
            )

            second = first + timedelta(minutes=1)
            changed = _records("1")
            changed[0]["listing_raw"]["title"] = "바뀐 장소"
            changed[0]["listing_raw"]["modifiedtime"] = "20260831010000"
            await upsert_source_records(
                session,
                SOURCE,
                changed,
                "2026-09-01",
                second,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
                detail_version_field="modifiedtime",
            )
            await session.commit()

            row = (
                await session.execute(
                    text("""
                        SELECT listing_raw, detail_raw, detail_state,
                               detail_attempted_at, detail_fetched_at
                        FROM facility_source_record
                        WHERE source = :source AND source_ref = '1'
                    """),
                    {"source": SOURCE},
                )
            ).one()
            assert row.listing_raw["title"] == "바뀐 장소"
            assert row.detail_raw == {"acmpyTypeCd": "전구역 동반가능"}
            assert row.detail_state == "fetched"
            assert row.detail_attempted_at == row.detail_fetched_at
        finally:
            await _clean(session)


async def test_kto_new_listing_version_marks_preserved_detail_stale() -> None:
    async with db_session() as session:
        await _clean(session)
        try:
            first = datetime.now(UTC)
            original = _records("1")
            original[0]["listing_raw"]["modifiedtime"] = "20260831010000"
            await upsert_source_records(
                session,
                SOURCE,
                original,
                "2026-08-31",
                first,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
                detail_version_field="modifiedtime",
            )
            await record_detail_result(
                session,
                SOURCE,
                "1",
                DetailAcquisitionState.FETCHED,
                first,
                {"acmpyTypeCd": "전구역 동반가능"},
            )

            changed = _records("1")
            changed[0]["listing_raw"]["modifiedtime"] = "20260901010000"
            await upsert_source_records(
                session,
                SOURCE,
                changed,
                "2026-09-01",
                first + timedelta(days=1),
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
                detail_version_field="modifiedtime",
            )
            await session.flush()

            row = (
                await session.execute(
                    text("""
                        SELECT detail_raw, detail_state,
                               detail_attempted_at, detail_fetched_at
                        FROM facility_source_record
                        WHERE source = :source AND source_ref = '1'
                    """),
                    {"source": SOURCE},
                )
            ).one()
            assert row.detail_raw is None
            assert row.detail_state == "not_fetched"
            assert row.detail_attempted_at is None
            assert row.detail_fetched_at is None
            assert await pending_detail_refs(session, SOURCE, 10) == ["1"]
        finally:
            await _clean(session)


async def test_stale_shadow_detail_invalidates_product_projection() -> None:
    async with db_session() as session:
        await _clean(session)
        try:
            first = datetime.now(UTC)
            original = _records("product")
            original[0]["listing_raw"]["modifiedtime"] = "20260831010000"
            await upsert_source_records(
                session,
                SOURCE,
                original,
                "2026-08-31",
                first,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
                detail_version_field="modifiedtime",
            )
            await record_detail_result(
                session,
                SOURCE,
                "product",
                DetailAcquisitionState.FETCHED,
                first,
                {"acmpyTypeCd": "전구역 동반가능"},
            )
            await upsert_rows(
                session,
                SOURCE,
                [_facility_row("product", {"acmpyTypeCd": "전구역 동반가능"})],
                "2026-08-31",
                first,
            )
            await session.execute(
                text("""
                    UPDATE facility
                    SET pet_allowed = true,
                        restriction_state = 'none_confirmed',
                        restriction_parse_state = 'mapped',
                        restriction_semantics_version = :semantics_version
                    WHERE source = :source AND source_ref = 'product'
                """),
                {
                    "source": SOURCE,
                    "semantics_version": RESTRICTION_SEMANTICS_VERSION,
                },
            )

            changed = _records("product")
            changed[0]["listing_raw"]["modifiedtime"] = "20260901010000"
            await upsert_source_records(
                session,
                SOURCE,
                changed,
                "2026-09-01",
                first + timedelta(days=1),
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
                detail_version_field="modifiedtime",
            )

            assert await invalidate_pet_details_without_source_payload(session, SOURCE) == 1
            row = (
                await session.execute(
                    text("""
                        SELECT pet, pet_allowed, restriction_state
                        FROM facility
                        WHERE source = :source AND source_ref = 'product'
                    """),
                    {"source": SOURCE},
                )
            ).one()
            assert row.pet == {}
            assert row.pet_allowed is None
            assert row.restriction_state is None
        finally:
            await _clean(session)


async def test_pending_selection_and_snapshot_prune_follow_acquisition_state() -> None:
    async with db_session() as session:
        await _clean(session)
        try:
            first = datetime.now(UTC)
            await upsert_source_records(
                session,
                SOURCE,
                _records("not-fetched", "failed", "no-data", "gone"),
                "2026-08-31",
                first,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
            )
            await record_detail_result(
                session,
                SOURCE,
                "failed",
                DetailAcquisitionState.FETCH_FAILED,
                first,
            )
            await record_detail_result(
                session,
                SOURCE,
                "no-data",
                DetailAcquisitionState.NO_DATA,
                first,
            )

            assert await pending_detail_refs(session, SOURCE, 10) == [
                "gone",
                "not-fetched",
                "failed",
            ]

            second = first + timedelta(minutes=1)
            await upsert_source_records(
                session,
                SOURCE,
                _records("not-fetched", "failed", "no-data"),
                "2026-09-01",
                second,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
            )
            assert await prune_source_records(session, SOURCE, second) == 1
            await session.commit()
        finally:
            await _clean(session)


async def test_detail_payload_and_state_must_match() -> None:
    async with db_session() as session:
        with pytest.raises(ValueError, match="only fetched"):
            await record_detail_result(
                session,
                SOURCE,
                "missing",
                DetailAcquisitionState.NO_DATA,
                datetime.now(UTC),
                {"unexpected": True},
            )
