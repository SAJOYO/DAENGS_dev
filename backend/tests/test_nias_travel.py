"""nias-pet 이동 두 장 (#347). 네트워크 없이 Target·meta·파서만 잰다."""
from __future__ import annotations

from daengs_life.crawler.sources.registration.nias_pet import WANTED, NiasPet


def test_two_travel_pages_are_declared_with_the_travel_category() -> None:
    assert "함께 외출하기" in WANTED and "함께 여행가기" in WANTED
    outing, travel = WANTED["함께 외출하기"], WANTED["함께 여행가기"]
    assert outing.category == "travel" and travel.category == "travel"
    assert outing.subcategory == travel.subcategory == "travel-guide"
    assert {outing.slug, travel.slug}.isdisjoint({p.slug for k, p in WANTED.items() if k not in ("함께 외출하기", "함께 여행가기")})


def test_every_declared_category_is_one_the_db_accepts() -> None:
    # documents.category 의 CHECK (db/init/01_schema.sql · 2026-09-06 마이그레이션)
    allowed = {"policy", "travel", "food", "insurance"}
    assert {p.category for p in WANTED.values()} <= allowed


def test_slugs_are_unique() -> None:
    slugs = [p.slug for p in WANTED.values()]
    assert len(slugs) == len(set(slugs))


def test_source_class_defaults_are_unchanged() -> None:
    # 두 장은 Target.meta 로 category·subcategory 를 덮어쓴다. 클래스 기본값은 그대로여야
    # 나머지 열 장이 안 흔들린다 (store.py 의 meta 우선 규칙).
    assert NiasPet.category == "policy" and NiasPet.subcategory == "pet-life-guide"
    assert NiasPet.trust_level == "official"
