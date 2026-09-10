"""검색 후보와 shadow 원천 레코드 사이의 runtime bridge를 실제 DB에서 검증한다."""

from datetime import UTC, datetime

from sqlalchemy import text

from daengs_place.ingest.source_record_store import upsert_source_records
from daengs_place.place.source_facts.bundle import SourceFactKey
from daengs_place.place.source_facts.reader import load_candidate_fact_bundles
from daengs_place.place.source_facts.states import DetailAcquisitionState
from tests.place.support.database import db_session

KCISA_REFS = ("test:candidate-bundle:kcisa:a", "test:candidate-bundle:kcisa:b")
KTO_REF = "test:candidate-bundle:kto"


async def _clean(session) -> None:
    await session.rollback()
    for source, record_ref in (
        *(("kcisa", record_ref) for record_ref in KCISA_REFS),
        ("kto", KTO_REF),
    ):
        await session.execute(
            text(
                "DELETE FROM facility_source_record "
                "WHERE source = :source AND record_ref = :record_ref"
            ),
            {"source": source, "record_ref": record_ref},
        )
    await session.commit()


def _kcisa_listing(category: str, category2: str) -> dict:
    return {
        "시설명": "같은 후보",
        "카테고리1": "반려동물업",
        "카테고리2": category2,
        "카테고리3": category,
        "반려동물 동반 가능정보": "Y",
        "반려동물 전용 정보": "해당없음",
        "입장 가능 동물 크기": "모두 가능",
        "반려동물 제한사항": "없음",
        "장소(실내) 여부": "Y",
        "장소(실외)여부": "N",
    }


async def test_reader_keeps_order_missing_candidates_and_source_variants() -> None:
    async with db_session() as session:
        await _clean(session)
        try:
            now = datetime.now(UTC)
            await upsert_source_records(
                session,
                "kcisa",
                [
                    {
                        "record_ref": KCISA_REFS[0],
                        "source_ref": "test:candidate-bundle:kcisa",
                        "occurrence_count": 2,
                        "listing_raw": _kcisa_listing("카페", "반려동물식당카페"),
                    },
                    {
                        "record_ref": KCISA_REFS[1],
                        "source_ref": "test:candidate-bundle:kcisa",
                        "occurrence_count": 3,
                        "listing_raw": _kcisa_listing("박물관", "반려동반여행"),
                    },
                ],
                "test-snapshot",
                now,
                detail_state=DetailAcquisitionState.NOT_APPLICABLE,
                preserve_detail=False,
            )
            await upsert_source_records(
                session,
                "kto",
                [
                    {
                        "record_ref": KTO_REF,
                        "source_ref": KTO_REF,
                        "listing_raw": {
                            "contentid": KTO_REF,
                            "contenttypeid": "12",
                            "lclsSystm1": "NA",
                            "lclsSystm2": "NA01",
                            "lclsSystm3": "NA010100",
                        },
                    }
                ],
                "test-snapshot",
                now,
                detail_state=DetailAcquisitionState.NOT_FETCHED,
                preserve_detail=True,
            )

            keys = [
                SourceFactKey(source="kto", source_ref=KTO_REF),
                SourceFactKey(source="kcisa", source_ref="test:candidate-bundle:missing"),
                SourceFactKey(source="kcisa", source_ref="test:candidate-bundle:kcisa"),
                SourceFactKey(source="kto", source_ref=KTO_REF),
            ]
            bundles = await load_candidate_fact_bundles(session, keys)

            assert [bundle.key for bundle in bundles] == keys
            assert bundles[0].availability == "present"
            assert bundles[0].projection_state == "complete"
            assert bundles[0].variants[0].detail_state == "not_fetched"
            assert bundles[0].acquisition_states == ("not_fetched",)
            assert bundles[1].availability == "missing"
            assert bundles[2].availability == "present"
            assert bundles[2].has_conflicts is True
            assert bundles[2].projection_state == "complete"
            assert bundles[2].physical_occurrences == 5
            assert {variant.record_ref for variant in bundles[2].variants} == set(KCISA_REFS)
            assert "purpose" in {conflict.section for conflict in bundles[2].conflicts}
            assert bundles[3] == bundles[0]
        finally:
            await _clean(session)
