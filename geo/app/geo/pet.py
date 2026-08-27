"""`facility.pet` 자유텍스트 → 필터 가능한 축. 순수 함수 — DB·시계 없음.

docs/explorations/facility/pet-axes.md · 근거 측정 docs/research/2026-08-24-facility-pet-coverage.md

`app/geo/tagging.py` 와 같은 자리다: 원천이 준 문자열에서 결정론으로 축을 뽑고, **원문은 지우지
않는다.** 다른 점은 필터 사용 여부다 — 병원 태그는 커버리지가 낮아 부스트로만 쓰지만
(활성 5,457곳 중 night 1 · emergency 2), `pet` 은 채움률 100% · 값 2종이라 `WHERE` 에 쓴다.
같은 원칙("모름을 없음으로 취급하지 않는다")을 적용한 결과가 반대로 나온 것이다.
"""

import re
from dataclasses import dataclass
from typing import Literal

SizeClass = Literal["small", "medium", "large", "any"]

# 원천이 "제약 없음 / 미상"을 적는 방식. 구체 제약과 가르는 기준선.
SIZE_OPEN = "모두 가능"
SIZE_UNKNOWN = "해당없음"
EXCLUSIVE_YES = "반려동물 전용"

# kg → 크기 등급 문턱. **측정으로 정한 값이 아니라 국내 통용 기준을 그대로 쓴 잠정값이다**
# (산책 계산 문턱값과 같은 성격). 바뀌면 재적재로 다시 파생한다 — UPSERT 라 재적재가 곧 재계산.
SMALL_MAX_KG = 10.0
MEDIUM_MAX_KG = 25.0

SIZE_ORDER: tuple[SizeClass, ...] = ("small", "medium", "large", "any")


def size_class_accepts(facility_limit: str | None, dog_size: str | None) -> bool | None:
    """시설의 크기 상한이 이 개 등급을 받는지. 어느 값이 미상이면 판단하지 않는다."""
    if facility_limit not in SIZE_ORDER or dog_size not in SIZE_ORDER[:-1]:
        return None
    return SIZE_ORDER.index(dog_size) <= SIZE_ORDER.index(facility_limit)


def accepting_size_classes(dog_size: str | None) -> tuple[SizeClass, ...]:
    """legacy SQL이 쓰는, 이 개를 받는 시설 상한 목록. 평가와 같은 순서를 공유한다."""
    if dog_size not in SIZE_ORDER[:-1]:
        return ()
    return tuple(
        facility_limit
        for facility_limit in SIZE_ORDER
        if size_class_accepts(facility_limit, dog_size)
    )

# "입장 가능 동물 크기" 칸에는 크기와 **종**이 섞여 들어온다 (측정 §5: 고양이 17행 등).
# 종이 열거됐는데 개가 없는 것은 결측이 아니라 명시적 진술이라 `dog_ok=False` 로 본다.
_DOG_WORDS = ("개", "강아지", "반려견")
_SPECIES_WORDS = (
    "고양이", "캣", "포유류", "조류", "파충류", "어류", "해양동물",
    "말", "토끼", "햄스터", "특수동물", "새",
)

_KG = re.compile(r"(\d+(?:\.\d+)?)\s*kg", re.IGNORECASE)
_SPLIT = re.compile(r"[,/·]")


@dataclass(frozen=True)
class PetAxes:
    """`pet` 봉투에서 뽑은 축. 전부 None 가능 — None 은 '미상'이지 '아님'이 아니다."""

    allowed: bool | None = None
    exclusive: bool | None = None
    dog_ok: bool | None = None          # None = 종 표기 없음 (개 전제)
    size_class: SizeClass | None = None
    max_kg: float | None = None

    def to_columns(self) -> dict:
        return {
            "pet_allowed": self.allowed,
            "pet_exclusive": self.exclusive,
            "pet_dog_ok": self.dog_ok,
            "pet_size_class": self.size_class,
            "pet_max_kg": self.max_kg,
        }


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _flag(value) -> bool | None:
    """`Y`/`N`. 적재기의 `_flag()` 와 같은 규칙 — 미상은 None 으로 남긴다."""
    t = _text(value)
    if not t:
        return None
    head = t[:1].upper()
    return True if head == "Y" else False if head == "N" else None


def _tokens(size: str) -> list[str]:
    """구분자로 자른 조각. `새(닭)` · `특수동물(물고기)` 처럼 괄호가 붙어 오므로 앞부분만 본다."""
    return [t.strip() for t in _SPLIT.split(size) if t.strip()]


def _species(size: str) -> bool | None:
    """종 표기가 있으면 개가 그 안에 있는지, 없으면 None (개 전제)."""
    tokens = _tokens(size)
    has_species = any(
        token.startswith(word) or token == word
        for token in tokens
        for word in _SPECIES_WORDS
    )
    if not has_species:
        return None
    if SIZE_OPEN in size:
        return True
    return any(
        token.startswith(word) or token == word
        for token in tokens
        for word in _DOG_WORDS
    )


def _max_kg(size: str) -> float | None:
    """`5kg 미만` · `10kg 이하` 의 숫자. 미만/이하 차이는 등급을 못 가르므로 구분하지 않는다."""
    found = [float(m) for m in _KG.findall(size)]
    return max(found) if found else None


def _size_class(size: str, max_kg: float | None) -> SizeClass | None:
    """라벨이 있으면 라벨이 이긴다 — 원천의 명시적 진술이고, kg 환산은 우리 문턱값이다."""
    if SIZE_OPEN in size:
        return "any"
    labelled: list[SizeClass] = []
    if "대형" in size:
        labelled.append("large")
    if "중형" in size:
        labelled.append("medium")
    if "소형" in size:
        labelled.append("small")
    if labelled:
        # `소형/중형` 은 중형까지 받는다는 뜻이다 — 가장 큰 등급이 상한.
        return max(labelled, key=SIZE_ORDER.index)
    if max_kg is None:
        return None
    # 숫자는 시설이 정한 **상한**이다. 상한이 10kg 이면 소형견만 받는다는 뜻이므로
    # 경계값은 아래 등급에 붙인다 ('10kg 이하' → small, '25kg 이하' → medium).
    if max_kg <= SMALL_MAX_KG:
        return "small"
    if max_kg <= MEDIUM_MAX_KG:
        return "medium"
    return "large"


def derive_axes(pet: dict | None) -> PetAxes:
    """저장된 `pet` 봉투 하나 → 축.

    **입력 dict 가 아니라 저장된 값에서 파생하는 것이 요점이다.** 적재 UPSERT 는 빈 상세가
    기존 상세를 덮지 않게 하므로(`facility_store.py`), 입력에서 축을 만들면 저장된 `pet` 과
    어긋난다. 원천이 둘인데 파싱은 하나여야 하는 이유이기도 하다.
    """
    if not pet:
        return PetAxes()
    size = _text(pet.get("size"))
    if size == SIZE_UNKNOWN:
        size = ""
    max_kg = _max_kg(size)
    exclusive_raw = _text(pet.get("exclusive"))
    return PetAxes(
        allowed=_flag(pet.get("allowed")),
        exclusive=(
            True if exclusive_raw == EXCLUSIVE_YES
            else False if exclusive_raw == SIZE_UNKNOWN
            else None
        ),
        dog_ok=_species(size) if size else None,
        size_class=_size_class(size, max_kg) if size else None,
        max_kg=max_kg,
    )
