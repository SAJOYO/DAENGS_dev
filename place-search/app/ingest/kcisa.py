"""KCISA 반려동물 동반 문화시설 CSV → facility 기반층 적재.

`python -m app.ingest.kcisa --csv <path> --snapshot 2025-03-24`

원천에 안정 ID가 없다. 그래서 이름+좌표로 **결정적 키**를 만들어 source_ref 로 쓴다 —
같은 시설은 매 스냅샷에서 같은 키가 나오고, facility.id 가 유지된다. 시설이 이전하거나
개명하면 새 키가 되어 옛 행은 prune 으로 사라진다: 안정 ID 없는 원천에서 감수하는 한계다.

스냅샷 의미는 synced_at 스탬프로 지킨다 (facility_store). 한 트랜잭션 안에서
UPSERT → prune → 링크 재구축까지 가고, 실패하면 이전 스냅샷이 그대로 남는다.

원천 좌표는 WGS84. '정보없음'은 None으로 눕힌다 — 모름을 값으로 취급하지 않는다.

## 컨셉 필터 — 개가 못 들어간다고 원천이 확정한 행은 적재하지 않는다

이 서비스는 강아지 케어·생활이다. `동반 가능정보=N`(2,794행)과 `고양이 전용` 제한은
검색·평가·파생 모든 층에서 매번 걸러야 하는 노이즈만 만든다. 확정 불허는 요청별 판정
대상이 아니라 적재 대상이 아니다.

**미상은 자르지 않는다.** 동반 여부가 비어 있는 행은 "모름"이지 "불허"가 아니다.
이름이 고양이인 곳(캣카페 등)도 이름은 근거 최하등급이라 여기서 죽이지 않는다 —
원천이 확정한 것만 자른다. 판단 자체는 `restriction_map` 판독표를 재사용한다
(판단의 유일한 자리 규율).
"""

import argparse
import asyncio
import csv
import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.ingest.facility_store import prune_unseen, upsert_rows
from app.ingest.linking import rebuild_links
from app.place.restriction_map import Subject, read
from app.place.source_catalog import (
    KCISA_KINDS as KINDS,
)

SOURCE = "public:kcisa:pet_facility"
_MISSING = {"", "정보없음", "-", "없음", "NULL"}


def _clean(value: str | None) -> str | None:
    text_ = (value or "").strip()
    return None if text_ in _MISSING else text_


def source_ref(name: str, lat: float, lng: float) -> str:
    """결정적 키 = 정규화 이름 + 5자리 반올림 좌표(약 1m). 원천이 ID를 안 주므로 우리가 만든다.

    좌표를 넣는 이유: 같은 상호의 지점이 전국에 있다 ('현대동물병원' 6곳 실측).
    """
    norm = re.sub(r"[\s()\[\]·.,-]", "", name.lower())
    return hashlib.md5(f"{norm}|{lat:.5f},{lng:.5f}".encode()).hexdigest()[:20]


def _flag(value: str | None) -> bool | None:
    v = _clean(value)
    if v is None:
        return None
    return v.upper().startswith("Y")


def parse_row(row: dict) -> dict | None:
    """CSV 한 행 → insert 파라미터. 좌표 불량이면 None (거부 계수용)."""
    name = _clean(row.get("시설명"))
    category3 = _clean(row.get("카테고리3"))
    if not name or not category3:
        return None
    try:
        lat = float(row["위도"])
        lng = float(row["경도"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (32.0 <= lat <= 40.0 and 123.0 <= lng <= 133.0):
        return None

    written = None
    raw_written = _clean(row.get("최종작성일"))
    if raw_written:
        try:
            written = date.fromisoformat(raw_written[:10])
        except ValueError:
            written = None

    pet = {
        k: v
        for k, v in {
            "allowed": _clean(row.get("반려동물 동반 가능정보")),
            "exclusive": _clean(row.get("반려동물 전용 정보")),
            "size": _clean(row.get("입장 가능 동물 크기")),
            "restrictions": _clean(row.get("반려동물 제한사항")),
            "extra_fee": _clean(row.get("애견 동반 추가 요금")),
        }.items()
        if v is not None
    }

    return {
        "source_ref": source_ref(name, lat, lng),
        "name": name,
        "kind": KINDS.get(category3, "etc"),
        "category3": category3,
        "sido": _clean(row.get("시도 명칭")),
        "sigungu": _clean(row.get("시군구 명칭")),
        "address": _clean(row.get("도로명주소")) or _clean(row.get("지번주소")),
        "phone": _clean(row.get("전화번호")),
        "homepage": _clean(row.get("홈페이지")),
        "hours_text": _clean(row.get("운영시간")),
        "closed_days": _clean(row.get("휴무일")),
        "parking": _flag(row.get("주차 가능여부")),
        "indoor": _flag(row.get("장소(실내) 여부")),
        "outdoor": _flag(row.get("장소(실외)여부")),
        "pet": pet,  # load_rows 가 컨셉 필터 후 json 으로 눕힌다
        "lat": lat,
        "lng": lng,
        "last_written": written,
    }


def concept_excluded(pet: dict) -> bool:
    """개가 들어갈 수 없다고 **원천이 확정**한 행인가.

    `동반 가능정보=N` 이거나, 제한사항 판독이 무조건 `deny:species_dog`(고양이 전용)를
    내는 경우만 True. 미상(None)·조건부는 자르지 않는다 — 모름 ≠ 불허.
    """
    allowed = (pet.get("allowed") or "").upper()
    if allowed.startswith("N"):
        return True
    reading = read(pet["restrictions"]) if pet.get("restrictions") else None
    return reading is not None and any(
        p.code == "deny:species_dog" and p.applies_to is Subject.ALL for p in reading.predicates
    )


def load_rows(path: Path) -> tuple[list[dict], int, int, int]:
    """(적재 행, 거부 수, 중복 수, 컨셉 제외 수). 중복 키 = (시설명, 위도, 경도) 원문."""
    parsed: list[dict] = []
    rejected = 0
    duplicates = 0
    excluded = 0
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            item = parse_row(row)
            if item is None:
                rejected += 1
                continue
            if concept_excluded(item["pet"]):
                excluded += 1
                continue
            # 중복 판정은 source_ref 로 한다 — 좌표 문자열이 미세하게 달라도 같은 키가 되고,
            # 한 배치 안에 같은 키가 두 번 들어가면 UPSERT 가 거부한다.
            if item["source_ref"] in seen:
                duplicates += 1
                continue
            seen.add(item["source_ref"])
            item["pet"] = json.dumps(item["pet"], ensure_ascii=False)
            parsed.append(item)
    return parsed, rejected, duplicates, excluded


async def replace_snapshot(session: AsyncSession, rows: list[dict], snapshot: str) -> dict:
    synced_at = datetime.now(UTC)
    stored = await upsert_rows(session, "kcisa", rows, snapshot, synced_at)
    pruned = await prune_unseen(session, "kcisa", synced_at)
    linked = await rebuild_links(session)
    await session.execute(
        text("""
            INSERT INTO ingest_state (source, watermark, updated_at)
            VALUES (:source, :watermark, now())
            ON CONFLICT (source) DO UPDATE SET watermark = :watermark, updated_at = now()
        """),
        {"source": SOURCE, "watermark": snapshot},
    )
    return {"stored": stored, "pruned": pruned, **linked}


async def _run(csv_path: Path, snapshot: str) -> None:
    rows, rejected, duplicates, excluded = load_rows(csv_path)
    if not rows:
        raise SystemExit(f"refusing empty snapshot from {csv_path}")
    async with SessionLocal() as session:
        stats = await replace_snapshot(session, rows, snapshot)
        await session.commit()
    print(json.dumps(
        {"source": SOURCE, "snapshot": snapshot, "rejected": rejected,
         "duplicates": duplicates, "concept_excluded": excluded, **stats},
        ensure_ascii=False,
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description="KCISA 문화시설 CSV를 facility에 스냅샷 교체 적재")
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, help="원천 스냅샷 날짜, 예: 2025-03-24")
    args = parser.parse_args()
    asyncio.run(_run(args.csv, args.snapshot))


if __name__ == "__main__":
    main()
