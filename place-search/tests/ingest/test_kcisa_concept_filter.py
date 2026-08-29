"""컨셉 필터 — 개가 못 들어간다고 원천이 확정한 행은 적재되지 않는다.

경계가 전부다: 확정 불허(N·고양이 전용)만 잘리고, 미상과 조건부는 남아야 한다.
미상을 자르는 순간 "모름 ≠ 없음" 규율이 적재 단계에서 무너진다.
"""

import csv
import json

from app.ingest.kcisa import concept_excluded, load_rows


def test_allowed_n_is_excluded():
    assert concept_excluded({"allowed": "N"})


def test_allowed_y_is_kept():
    assert not concept_excluded({"allowed": "Y"})


def test_missing_allowed_is_kept():
    """미상은 불허가 아니다 — 여기서 자르면 KTO 급 미상층이 통째로 죽는 선례가 된다."""
    assert not concept_excluded({})


def test_cat_only_restriction_is_excluded_even_when_allowed_y():
    """`해당없음` 이 아니라 원문이 직접 고양이 전용을 말하는 행 — 판독표가 판정한다."""
    assert concept_excluded({"allowed": "Y", "restrictions": "고양이 전용"})


def test_ordinary_restriction_is_kept():
    assert not concept_excluded({"allowed": "Y", "restrictions": "목줄"})


def test_unmapped_restriction_is_kept():
    """표에 없는 문장은 raw_only 로 남기는 것이지 자르는 근거가 아니다."""
    assert not concept_excluded({"allowed": "Y", "restrictions": "완전히 처음 보는 문장"})


_HEADER = [
    "시설명", "카테고리3", "위도", "경도",
    "반려동물 동반 가능정보", "반려동물 제한사항",
]


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def test_load_rows_counts_and_drops_excluded(tmp_path):
    path = tmp_path / "kcisa.csv"
    _write_csv(path, [
        {"시설명": "동반가능카페", "카테고리3": "카페", "위도": "37.5", "경도": "127.0",
         "반려동물 동반 가능정보": "Y", "반려동물 제한사항": "목줄"},
        {"시설명": "불허미술관", "카테고리3": "미술관", "위도": "37.5", "경도": "127.1",
         "반려동물 동반 가능정보": "N", "반려동물 제한사항": "해당없음"},
        {"시설명": "고양이카페", "카테고리3": "카페", "위도": "37.5", "경도": "127.2",
         "반려동물 동반 가능정보": "Y", "반려동물 제한사항": "고양이 전용"},
        {"시설명": "미상카페", "카테고리3": "카페", "위도": "37.5", "경도": "127.3",
         "반려동물 동반 가능정보": "정보없음", "반려동물 제한사항": "정보없음"},
    ])
    rows, rejected, duplicates, excluded = load_rows(path)
    assert [r["name"] for r in rows] == ["동반가능카페", "미상카페"]
    assert (rejected, duplicates, excluded) == (0, 0, 2)


def test_loaded_pet_is_json_string(tmp_path):
    """upsert 는 `CAST(:pet AS jsonb)` 를 기대한다 — 필터 뒤에 눕히는 재배치의 계약."""
    path = tmp_path / "kcisa.csv"
    _write_csv(path, [
        {"시설명": "동반가능카페", "카테고리3": "카페", "위도": "37.5", "경도": "127.0",
         "반려동물 동반 가능정보": "Y", "반려동물 제한사항": "목줄"},
    ])
    rows, *_ = load_rows(path)
    assert json.loads(rows[0]["pet"]) == {"allowed": "Y", "restrictions": "목줄"}
