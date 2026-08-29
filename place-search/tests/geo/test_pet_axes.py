"""`app/geo/pet.py` — pet 봉투에서 축을 뽑는 규칙.

입력값은 전부 **실제 원천에 있는 문자열**이다 (docs/research/2026-08-24-facility-pet-coverage.md
와 `docs/research/facility-pet-coverage/size-values.csv`). 지어낸 형태를 통과시키는 테스트는
파서가 원천을 따라가는지 말해주지 못한다.
"""

import pytest

from app.geo.pet import PetAxes, accepting_size_classes, derive_axes, size_class_accepts


def axes(**pet) -> PetAxes:
    return derive_axes(pet)


# ------------------------------------------------------- allowed / exclusive (채움률 100%)

def test_allowed_is_a_two_value_enum():
    assert axes(allowed="Y").allowed is True
    assert axes(allowed="N").allowed is False


def test_exclusive_only_true_for_the_dedicated_label():
    assert axes(exclusive="반려동물 전용").exclusive is True
    assert axes(exclusive="해당없음").exclusive is False


def test_missing_keys_stay_unknown_not_false():
    """모름을 없음으로 만들지 않는다 — 축 전체가 이 규칙 위에 있다."""
    a = derive_axes({})
    assert (a.allowed, a.exclusive, a.dog_ok, a.size_class, a.max_kg) == (None,) * 5
    assert derive_axes(None) == PetAxes()


# ------------------------------------------------------- size → 크기 등급

@pytest.mark.parametrize("size,expected", [
    ("모두 가능", "any"),          # 20,184행 — 전체의 84%
    ("소형", "small"),
    ("대형", "large"),
    ("소형/중형", "medium"),        # 상한이 등급이다: 중형까지 받는다
    ("소형/대형", "large"),
    ("5kg 미만 소형", "small"),     # 라벨과 kg 이 같이 오는 형태
])
def test_size_class_from_labels(size, expected):
    assert axes(size=size).size_class == expected


@pytest.mark.parametrize("size,kg,expected", [
    ("10kg 미만", 10.0, "small"),    # 문턱 미만
    ("10kg 이하", 10.0, "small"),    # 미만/이하는 등급을 못 가른다
    ("15kg 이하", 15.0, "medium"),
    ("20kg 이하", 20.0, "medium"),
    ("5kg 미만", 5.0, "small"),
])
def test_size_class_falls_back_to_kg(size, kg, expected):
    a = axes(size=size)
    assert (a.max_kg, a.size_class) == (kg, expected)


def test_unknown_size_is_not_a_constraint():
    """`해당없음` 2,789행. 미상이지 '크기 제한 없음'이 아니다."""
    a = axes(allowed="N", size="해당없음")
    assert a.size_class is None and a.max_kg is None and a.dog_ok is None
    assert a.allowed is False


@pytest.mark.parametrize(
    ("dog_size", "accepted"),
    [
        ("small", ("small", "medium", "large", "any")),
        ("medium", ("medium", "large", "any")),
        ("large", ("large", "any")),
    ],
)
def test_filter_and_evaluation_share_one_size_order(dog_size, accepted):
    assert accepting_size_classes(dog_size) == accepted
    assert tuple(
        limit for limit in ("small", "medium", "large", "any")
        if size_class_accepts(limit, dog_size)
    ) == accepted


# ------------------------------------------------------- size → 종 (개가 되는가)

@pytest.mark.parametrize("size", [
    "고양이",                                  # 17행. 개 서비스에서는 '입장 불가'다
    "포유류 특수동물",
    "해양동물",
    "어류",
    "말",
    "포유류 특수동물, 조류, 파충류",
])
def test_species_listed_without_dog_excludes_dogs(size):
    """종을 열거하면서 개를 뺀 것은 결측이 아니라 명시적 진술이다."""
    assert axes(size=size).dog_ok is False


@pytest.mark.parametrize("size", [
    "개, 고양이",
    "개, 고양이, 새(닭), 특수동물(물고기)",     # 괄호가 붙어 온다
    "모두 가능, 고양이, 포유류 특수동물, 조류, 파충류",
    "모두 가능, 토끼, 햄스터 등 특수동물",
])
def test_species_including_dog_or_open_stays_ok(size):
    assert axes(size=size).dog_ok is True


@pytest.mark.parametrize("size", ["모두 가능", "소형/중형", "10kg 이하"])
def test_size_without_species_leaves_dog_ok_unknown(size):
    """종 표기가 없으면 개 전제다. False 로 만들면 대부분의 시설이 사라진다."""
    assert axes(size=size).dog_ok is None


def test_species_row_still_yields_a_size_class_when_open():
    a = axes(size="모두 가능, 고양이, 토끼")
    assert (a.size_class, a.dog_ok) == ("any", True)


# ------------------------------------------------------- 컬럼 매핑

def test_to_columns_matches_the_migration():
    cols = axes(allowed="Y", exclusive="반려동물 전용", size="5kg 미만 소형").to_columns()
    assert cols == {
        "pet_allowed": True,
        "pet_exclusive": True,
        "pet_dog_ok": None,
        "pet_size_class": "small",
        "pet_max_kg": 5.0,
    }
