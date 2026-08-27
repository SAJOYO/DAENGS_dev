"""페르소나가 '판정 분기를 전부 밟는다'는 약속을 강제한다.

`tests/search/` 에 있었다 — 검증 대상이 `profile.source` 와 `journey.advice` 라 검색과 무관하다.

페르소나는 장식이 아니라 **커버리지 픽스처**다. 누가 한 마리를 지우거나 값을 바꾸면
안 밟히는 분기가 생기는데, 그건 조용히 일어나면 안 된다. docs/contracts/dog-profile.md
"""

import pytest

from app.journey.advice import dog_time_factor, prefers_quiet
from app.profile.source import OWNER_OF, OWNERS, PERSONAS


# ------------------------------------------------------------------ 커버리지
def test_every_branch_is_covered_by_some_dog():
    """advice/spots/engine이 분기하는 축마다 최소 한 마리씩 있어야 한다."""
    dogs = list(PERSONAS.values())
    branches = {
        "senior": lambda p: p.is_senior,
        "joint": lambda p: p.has_joint_issue,
        "brachy": lambda p: p.is_brachy,
        "under_4kg": lambda p: p.size_class == "small" and p.weight_kg < 4,
        "large": lambda p: p.size_class == "large",          # 지하 통로·대중교통 미노출
        "medium": lambda p: p.size_class == "medium",
        "quiet": prefers_quiet,                               # 골목 vs 큰길 선택
        "high_activity": lambda p: p.activity_level == "high",
        "healthy": lambda p: not (p.is_senior or p.has_joint_issue or p.is_brachy),
    }
    missing = [name for name, hit in branches.items() if not any(hit(p) for p in dogs)]
    assert not missing, f"아무 페르소나도 안 밟는 분기: {missing}"


def test_dog_time_factor_spans_a_real_range():
    """계수가 한 점에 뭉쳐 있으면 페르소나가 일을 안 하는 것이다."""
    factors = {k: dog_time_factor(p) for k, p in PERSONAS.items()}
    assert min(factors.values()) <= 1.15, factors
    assert max(factors.values()) >= 1.7, factors
    assert len(set(factors.values())) >= 5, f"계수가 너무 겹친다: {factors}"


def test_large_dogs_exist_so_transit_exclusion_is_exercised():
    """engine.show_transit은 small만 True. large가 없으면 그 분기가 죽는다."""
    assert [p.name for p in PERSONAS.values() if p.size_class == "large"]


# ------------------------------------------------------------------- 견주
def test_every_dog_has_exactly_one_owner():
    owned = [d for o in OWNERS.values() for d in o.dog_ids]
    assert sorted(owned) == sorted(PERSONAS), "개와 견주 매핑이 어긋난다"
    assert len(owned) == len(set(owned)), "한 개가 두 견주에게 붙어 있다"
    assert set(OWNER_OF) == set(PERSONAS)


def test_owner_literacy_spectrum_is_covered():
    levels = {o.vet_literacy for o in OWNERS.values()}
    assert {"none", "basic", "experienced"} <= levels, levels


@pytest.mark.parametrize("owner_id,dog_id,expected", [
    ("eunyoung", "janggun", False),   # 34kg — 못 안는다. 계단이 진짜 장벽
    ("eunyoung", "choco", True),      # 7.5kg — 안을 수 있다
    ("seojun", "bau", False),         # 32kg
    ("jihyun", "samwol", True),       # 1.8kg 퍼피
])
def test_can_carry_compares_dog_weight_to_the_owner_limit(owner_id, dog_id, expected):
    assert OWNERS[owner_id].can_carry(PERSONAS[dog_id]) is expected


def test_can_carry_returns_none_when_unknown():
    """모름은 불가가 아니다."""
    o = OWNERS["jihyun"].model_copy(update={"can_carry_kg": None})
    assert o.can_carry(PERSONAS["samwol"]) is None
