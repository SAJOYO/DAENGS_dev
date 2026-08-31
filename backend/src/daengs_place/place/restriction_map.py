"""KCISA `pet.restrictions` 자유 문장 → 감사 가능한 술어. **판단의 유일한 자리.**

[결정 #70](../../docs/decisions/2026-08-27-place-row-tags.md)이 "태그는 행에 달리고 근거
등급을 기록한다"고 정했다. 이 파일은 그중 `restriction_map` 등급 하나를 소유한다 —
원천이 **문장으로 적어 놓은** 제약을 코드가 읽을 수 있는 술어로 옮긴다.

`source_catalog.py` 와 같은 지위다. 원천 category → canonical kind 가 사람의 분류 판단이듯,
`"대형견 입마개, 목줄"` → `[목줄(모두), 입마개(대형견)]` 도 판단이다. 숨기지 않고 한 파일에
모아 리뷰 가능하게 두고, 버전을 붙여 바뀔 때 전체를 재파생한다.

## 왜 표인가 — 정규식이 아니라

`목줄` 정규식은 949행을 세지만 그 안에 셋이 섞여 있다.

    대형견은 목줄, 입마개        소형견에겐 제한이 아니다
    실내 목줄 필수               구역 조건부다
    목줄, 케이지, 안기           콤마가 AND 인지 OR 인지 원문이 말하지 않는다

**조건부를 무조건으로 읽는 것은 사실 추출이 아니라 (틀린) 판단이다.** 그래서 조합을
기계가 만들지 않는다 — 291종을 사람이 전수 판독해 표에 적는다. 새 문자열이 나타나면
매핑되지 않고 `raw_only` 로 떨어진다. 조용히 틀리는 것보다 낫다.

## 넓게 적고 좁게 판정한다

`deny:behavior`(공격성·입질)나 `require:vaccination` 은 술어로 **적는다** — 사용자에게
보여줄 값어치가 있다. 그러나 프로필과 대조해 `incompatible` 을 만들지는 **않는다.**
우리는 이 개가 공격성이 있는지 모르고, 접종 여부도 모른다. 판정 가능한 것만 판정하는
경계는 소비자(`place.evaluations`)가 세우며 이 표는 사실만 옮긴다.
`evaluate_dog_access` 가 `weight_boundary_unknown` 으로 경계값을 지어내지 않는 것과 같다.

## 상태 셋 — 291종 전부가 하나를 받는다

    mapped    술어가 원문을 다 담았다
    partial   담았지만 원문에 더 있다 → UI 가 원문을 함께 보여야 한다
    raw_only  술어가 없다. 원문만 보여주며 **사유(`Reason`)를 반드시 단다**

`raw_only` 에 사유를 강제하는 이유가 중요하다. 사유가 없으면 "아직 안 했다" 와 "일부러
안 한다" 가 구분되지 않고, 다음 사람이 선의로 코드화하다가 판정 불가능한 조건을 발명한다.

`partial` 도 그냥 두면 안 된다 — 칩 목록만 보이면 **완결로 읽힌다.** 술어 셋 중 둘만
담은 행이 칩 두 개만 달고 있으면 조용한 truncation 과 같은 거짓말이다. 소비자는 `partial`
에 원문 보기를 강제해야 한다 (`truncated` 규율의 칩 버전).
"""

from enum import StrEnum
from typing import NamedTuple

# 이 표의 의미 버전. 술어·`applies_to` 가 바뀌면 올리고 그 변경에서 전체를 재파생한다.
# **표시 라벨(LABELS)은 이 버전에 영향을 주지 않는다** — `#입마개` 를 `#입마개 필요` 로
# 고치는 것은 의미 변경이 아니므로 스냅샷을 다시 만들 이유가 없다.
RESTRICTION_SEMANTICS_VERSION = "kcisa-restrictions/3"

# 원문에 정보가 없다고 원천이 말한 값. 결측이 아니다 —
# `해당없음` 2,789행 중 2,781행이 `pet_allowed=false` 다 (동반 불가라 제한이 해당 없음).
NO_RESTRICTION = "제한사항 없음"
NOT_APPLICABLE = "해당없음"
NON_INFORMATIVE = frozenset({NO_RESTRICTION, NOT_APPLICABLE})


class ParseState(StrEnum):
    MAPPED = "mapped"
    PARTIAL = "partial"
    RAW_ONLY = "raw_only"


class Subject(StrEnum):
    """술어가 **누구에게** 적용되는가. 판정 가능 여부는 소비자가 정한다."""

    ALL = "all"
    SIZE_LARGE = "size:large"  # 대형견
    SIZE_MEDIUM_UP = "size:medium_up"  # 중·대형견
    SIZE_SMALL = "size:small"  # 소형견
    SEX_MALE = "sex:male"  # 수컷
    INTACT = "neuter:intact"  # 중성화 안 한
    IN_CYCLE = "cycle:in_cycle"  # 생리·발정·임신 중
    BREED_GUARD = "breed:guard"  # 맹견류 (법정 분류)
    BREED_NAMED = "breed:named"  # 원문이 열거한 견종
    AGE_SENIOR = "age:senior"  # 노령견·10살 이상
    AGE_PUPPY = "age:puppy"  # 4개월 미만 등


class Reason(StrEnum):
    """`raw_only` 사유. 왜 일부러 술어로 안 옮겼는가.

    개의 성격(공격성·입질)은 여기 없다 — `deny:behavior` 로 **적는다.** 적는 것과
    판정하는 것은 다르고, 판정을 안 하는 경계는 소비자가 세운다 (모듈 docstring).
    """

    CONDITIONAL_GRANT = "conditional_grant"  # "X 하면 Y 가능" — 술어가 아니라 규칙이다
    FACILITY_SPECIFIC = "facility_specific"  # 그 시설에만 있는 구역·절차 이름
    VAGUE = "vague"  # "얌전하면 가능"


class Certainty(StrEnum):
    """이 술어가 **원문에서 얼마나 확실한가.**

    코드가 아니라 **(문자열 → 술어) 쌍의 속성**이다. 같은 `deny:age` 라도

        "10살 이상 불가"                 확실 — 못 들어간다고 말했다
        "노견일 경우 미용 어려울 수 있음"  약함 — 어려울 수 있다고만 말했다

    코드 전역 속성으로 두면 둘 중 하나를 반드시 잘못 다룬다. 약한 술어는 칩으로
    보여주되 `incompatible` 로 승격하지 않는다 — "어려울 수 있음" 을 "이용 불가" 로
    올리는 것은 결정 #70 의 "넓게 적고 좁게 판정" 과 정반대다.
    """

    FIRM = "firm"
    SOFT = "soft"


class P(NamedTuple):
    """술어 하나.

    `code` 는 의미, `applies_to` 는 적용 대상, `params` 는 원문의 **수치·조건**,
    `certainty` 는 그 원문이 단정했는지 여부다.

    `params` 가 없으면 판독표가 원문의 값을 버린 것이다 — `최대 2마리` 와 `최대 5마리`
    가 같은 술어가 되면 `mapped`("술어가 원문을 다 담았다")가 거짓말이 되고, 이 표를
    gold set 으로 쓸 때 정확히 읽은 파서와 대충 읽은 파서가 같은 점수를 받는다.
    """

    code: str
    applies_to: Subject = Subject.ALL
    params: tuple[tuple[str, str], ...] = ()
    certainty: Certainty = Certainty.FIRM

    def param(self, name: str) -> str | None:
        return next((value for key, value in self.params if key == name), None)

    def int_param(self, name: str) -> int | None:
        value = self.param(name)
        return int(value) if value is not None else None


def _p(spec: str) -> P:
    """`"deny:age@age:puppy(max_months=4)~soft"` → `P(...)`.

    표를 한 줄로 유지하려고 수식자를 접미로 붙인다. 순서는 `code@subject(params)~certainty`
    이고 뒤 셋은 전부 선택이다.
    """
    rest, _, certainty = spec.partition("~")
    rest, _, params = rest.partition("(")
    code, _, subject = rest.partition("@")
    parsed: tuple[tuple[str, str], ...] = ()
    if params:
        parsed = tuple(
            (key.strip(), value.strip())
            for key, _, value in (item.partition("=") for item in params.rstrip(")").split(","))
        )
    return P(
        code,
        Subject(subject) if subject else Subject.ALL,
        parsed,
        Certainty(certainty) if certainty else Certainty.FIRM,
    )


# ---------------------------------------------------------------- 술어 어휘
# require:*  갖춰야 하는 것        deny:*   못 들어가는 대상
# zone:*     어디까지 갈 수 있나    limit:*  마리 수
# admin:*    서류·절차            fee:*    돈        schedule:* 때
KNOWN_CODES = frozenset(
    {
        "require:leash",
        "require:poop_bag",
        "require:muzzle",
        "require:manner_belt",
        "require:carrier",
        "require:hold",
        "require:harness",
        "require:stroller",
        "require:diaper",
        "require:supplies_byo",
        "require:bedding_byo",
        "require:vaccination",
        "require:neutered",
        "deny:breed",
        "deny:size",
        "deny:age",
        "deny:species_cat",
        "deny:species_dog",
        "deny:behavior",
        "deny:health",
        "deny:cycle",
        "zone:outdoor_only",
        "zone:terrace_only",
        "zone:floor1_only",
        "zone:indoor_partial",
        "zone:named_area",
        "limit:max_dogs",
        "limit:max_dogs_by_size",
        "admin:registration",
        "admin:document",
        "admin:prior_consult",
        "admin:consent_form",
        "fee:extra",
        "fee:deposit",
        "schedule:limited",
    }
)

# 표시 라벨. **의미 버전과 분리된다** — 여기를 고쳐도 재파생하지 않는다.
# 서버가 소유하는 이유는 웹과 Android 가 같은 의미를 봐야 하기 때문이다 (결정 #65 §6).
LABELS: dict[str, str] = {
    "require:leash": "목줄",
    "require:poop_bag": "배변봉투",
    "require:muzzle": "입마개",
    "require:manner_belt": "매너벨트",
    "require:carrier": "케이지",
    "require:hold": "안기",
    "require:harness": "하네스",
    "require:stroller": "유모차",
    "require:diaper": "기저귀",
    "require:supplies_byo": "용품 지참",
    "require:bedding_byo": "침구 지참",
    "require:vaccination": "접종 확인",
    "require:neutered": "중성화",
    "deny:breed": "견종 제한",
    "deny:size": "크기 제한",
    "deny:age": "나이 제한",
    "deny:species_cat": "고양이 불가",
    "deny:species_dog": "개 불가",
    "deny:behavior": "성격 제한",
    "deny:health": "건강 제한",
    "deny:cycle": "생리·발정 제한",
    "zone:outdoor_only": "야외만",
    "zone:terrace_only": "테라스만",
    "zone:floor1_only": "1층만",
    "zone:indoor_partial": "실내 일부",
    "zone:named_area": "지정 구역만",
    "limit:max_dogs": "마리 수 제한",
    "limit:max_dogs_by_size": "크기별 마리 수",
    "admin:registration": "동물등록",
    "admin:document": "서류 지참",
    "admin:prior_consult": "사전 문의",
    "admin:consent_form": "서약서",
    "fee:extra": "추가 요금",
    "fee:deposit": "예치금",
    "schedule:limited": "요일 제한",
}

# 대상 한정어. 개 조건이 없을 때 칩에 붙는다 — `#입마개·대형견`.
SUBJECT_LABELS: dict[Subject, str] = {
    Subject.ALL: "",
    Subject.SIZE_LARGE: "대형견",
    Subject.SIZE_MEDIUM_UP: "중대형견",
    Subject.SIZE_SMALL: "소형견",
    Subject.SEX_MALE: "수컷",
    Subject.INTACT: "중성화 전",
    Subject.IN_CYCLE: "생리·발정 중",
    Subject.BREED_GUARD: "맹견류",
    Subject.BREED_NAMED: "일부 견종",
    Subject.AGE_SENIOR: "노령견",
    Subject.AGE_PUPPY: "어린 개",
}


# ---------------------------------------------------------------- 판독표 (291종 전수)
# 형식: 원문 → 술어 spec 들. `code@subject` 로 적용 대상을 붙인다.
# 빈도 내림차순. 주석의 숫자는 2026-08-27 스냅샷의 행 수다.
_MAP: dict[str, tuple[str, ...]] = {
    # ---- 상위 빈도 (10행 이상) — 여기까지가 1,300행을 덮는다
    "목줄, 배변봉투": ("require:leash", "require:poop_bag"),  # 489
    "목줄": ("require:leash",),  # 340
    "야외만 반려동물 동반 가능": ("zone:outdoor_only",),  # 183
    "케이지 이용": ("require:carrier",),  # 144
    "안고 있어야 함": ("require:hold",),  # 47
    "객실당 최대 2마리": ("limit:max_dogs(max=2)",),  # 33
    "입질, 공격성 있는 경우 입장 제한": ("deny:behavior",),  # 22
    "실외만 동반 가능": ("zone:outdoor_only",),  # 22
    "매너벨트 필수": ("require:manner_belt",),  # 14
    "목줄, 안기": ("require:leash", "require:hold"),  # 11
    "객실당 최대 1마리": ("limit:max_dogs(max=1)",),  # 10
    "객실당 최대 3마리": ("limit:max_dogs(max=3)",),  # 10
    # ---- 중간 빈도 (3~8행)
    "맹견류 입장 불가": ("deny:breed@breed:guard",),  # 8
    "고양이 전용": ("deny:species_dog",),  # 8
    "최대 2마리": ("limit:max_dogs(max=2)",),  # 8
    "애견용품 개별준비": ("require:supplies_byo",),  # 8
    "객실당 최대 2마리, 애견용품 개별준비": ("limit:max_dogs(max=2)", "require:supplies_byo"),  # 6
    "접종 완료 필수": ("require:vaccination",),  # 6
    "야외만 반려동물 동반 가능, 대형견은 목줄, 입마개": (
        "zone:outdoor_only",
        "require:leash@size:large",
        "require:muzzle@size:large",
    ),  # 5
    "대형견 입장 불가": ("deny:size@size:large",),  # 5
    "야외만 반려동물 동반 가능, 목줄": ("zone:outdoor_only", "require:leash"),  # 5
    "대형견 입마개, 목줄": ("require:muzzle@size:large", "require:leash"),  # 5
    "객실당 최대 5마리": ("limit:max_dogs(max=5)",),  # 4
    "최대 3마리": ("limit:max_dogs(max=3)",),  # 4
    "배변봉투": ("require:poop_bag",),  # 4
    "목줄, 케이지": ("require:leash", "require:carrier"),  # 4
    "공격성 있는 경우 불가": ("deny:behavior",),  # 4
    "목줄, 매너벨트": ("require:leash", "require:manner_belt"),  # 4
    "소, 중, 대형견 공간분리": ("zone:named_area",),  # 4
    "입마개": ("require:muzzle",),  # 4
    "입질, 공격성 심한 경우, 맹견 입실 불가": ("deny:behavior", "deny:breed@breed:guard"),  # 4
    "안고 있어야 함, 목줄": ("require:hold", "require:leash"),  # 4
    "목줄, 입마개": ("require:leash", "require:muzzle"),  # 4
    "케이지, 안기": ("require:carrier", "require:hold"),  # 4
    "맹견, 공격성 있는 경우 입장 제한": ("deny:breed@breed:guard", "deny:behavior"),  # 4
    "5차 접종 필수": ("require:vaccination",),  # 3
    "케이지, 유모차, 가방, 안기, 대형견 일부 매장에서 입장 거부 당할 수 있음": (
        "require:carrier",
        "require:stroller",
        "require:hold",
    ),  # 3
    "케이지: 반려견용 캔넬, 유모차, 백팩": ("require:carrier", "require:stroller"),  # 3
    "목줄 필수, 맹견 입장 불가": ("require:leash", "deny:breed@breed:guard"),  # 3
    "사전상담 필수": ("admin:prior_consult",),  # 3
    "수컷 매너벨트 필수": ("require:manner_belt@sex:male",),  # 3
    "실내에서 매너벨트 필수": ("require:manner_belt", "zone:indoor_partial"),  # 3
    "객실당 최대 4마리": ("limit:max_dogs(max=4)",),  # 3
    # ---- 2행
    "입질, 공격성 있는 경우 입장 제한, 최대 3마리": ("deny:behavior", "limit:max_dogs(max=3)"),
    "객실당 최대 2마리, 목줄": ("limit:max_dogs(max=2)", "require:leash"),
    "목줄, 케이지, 안기, 배변봉투": (
        "require:leash",
        "require:carrier",
        "require:hold",
        "require:poop_bag",
    ),
    "목줄, 케이지, 배변봉투": ("require:leash", "require:carrier", "require:poop_bag"),
    "공격성, 전염질환 있는 경우 입장 제한": ("deny:behavior", "deny:health"),
    "대형견 사전 문의": ("admin:prior_consult@size:large",),
    "고양이 불가": ("deny:species_cat",),
    "유모차 필수": ("require:stroller",),
    "물지 않도록 주의": ("deny:behavior",),
    "목줄, 안기, 배변봉투": ("require:leash", "require:hold", "require:poop_bag"),
    "실내 목줄 필수": ("require:leash", "zone:indoor_partial"),
    "최대 4마리": ("limit:max_dogs(max=4)",),
    "입질, 공격성, 거부 심한 경우 미용 중단": ("deny:behavior",),
    "질병, 입질, 공격성 있는 경우 불가": ("deny:health", "deny:behavior"),
    "안기": ("require:hold",),
    "입질, 공격성 있는 경우 입장 제한, 애견샤워 금지": ("deny:behavior",),
    "반려견 수영장 입수 불가": ("zone:named_area",),
    "목줄, 실내에서 안기": ("require:leash", "require:hold", "zone:indoor_partial"),
    "5차 접종 필수, 전염질환, 공격성, 생리 중인 경우 입장 불가": (
        "require:vaccination",
        "deny:health",
        "deny:behavior",
        "deny:cycle",
    ),
    "1층만 이용 가능": ("zone:floor1_only",),
    "야외만 동반 가능, 목줄, 배변봉투, 대형견 입마개": (
        "zone:outdoor_only",
        "require:leash",
        "require:poop_bag",
        "require:muzzle@size:large",
    ),
    "공격성, 입질 있는 경우 불가": ("deny:behavior",),
    "10살 이상 불가": ("deny:age@age:senior(min_years=10)",),
    # **신규예약** 불가지 입장 불가가 아니다 — 기존 고객은 간다. soft 로 둔다.
    "10살 이상 노령견 신규예약 불가": ("deny:age@age:senior(min_years=10)~soft",),
    "야외테라스만 동반 가능": ("zone:terrace_only",),
    "1층은 안고 있어야 함": ("require:hold", "zone:floor1_only"),
    "털빠짐 심한 경우 입장 불가": ("deny:health",),
    # ---- 1행 (꼬리). 어휘는 반복되고 조합만 다르다.
    "공격성, 짖음, 입질, 생리 중인 경우 입장 불가": ("deny:behavior", "deny:cycle"),
    "목줄, 배변봉투, 맹견 입마개 착용": (
        "require:leash",
        "require:poop_bag",
        "require:muzzle@breed:guard",
    ),
    "맹견 제한, 목줄 필수, 트랙터 이용 시 안거나 케이지 필요": (
        "deny:breed@breed:guard",
        "require:leash",
    ),
    "대형견은 목줄": ("require:leash@size:large",),
    "중, 대형견은 독채 이용": ("zone:named_area@size:medium_up",),
    "맹견류, 공격성, 전염질환 있는 경우 입장 불가, 매너벨트 필수": (
        "deny:breed@breed:guard",
        "deny:behavior",
        "deny:health",
        "require:manner_belt",
    ),
    "목줄, 대형견 입마개 필수, 배변관리, 시설 내부에 강아지들이 있어 흥분하지 않게 주의 바람": (
        "require:leash",
        "require:muzzle@size:large",
        "require:poop_bag",
    ),
    "대형견, 진도믹스 이용 제한, 짖음 심한 경우 이용 제한, 접종 필수": (
        "deny:size@size:large",
        "deny:breed@breed:named",
        "deny:behavior",
        "require:vaccination",
    ),
    "3차 예방접종 완료, 매너벨트 착용": ("require:vaccination", "require:manner_belt"),
    "맹견류, 사회화 되어있지 않은 테리어 계열, 공격성, 전염질환 있는 경우 입장 불가, 5차 접종, 중성화 수술 필수": (
        "deny:breed@breed:guard",
        "deny:breed@breed:named",
        "deny:behavior",
        "deny:health",
        "require:vaccination",
        "require:neutered",
    ),
    "공격성, 전염질환, 맹견류, 입질 심한 반려견 입장 불가": (
        "deny:behavior",
        "deny:health",
        "deny:breed@breed:guard",
    ),
    "입질, 공격성, 마운팅, 불리불한 심한 강아지는 이용 불가": ("deny:behavior",),
    "닥스훈트, 미니핀, 코카스파니엘, 비글, 웰시코기, 프렌치불독, 단모치와와 입장 불가": (
        "deny:breed@breed:named",
    ),
    "입질, 공격성, 생리중인 경우 불가": ("deny:behavior", "deny:cycle"),
    "강아지만 입실 가능, 객실당 최대 6마리": ("deny:species_cat", "limit:max_dogs(max=6)"),
    "목줄, 배변봉투, 경기장 내 출입불가": ("require:leash", "require:poop_bag", "zone:named_area"),
    "5차 접종, 중성화 전 수컷 매너벨트 필수": (
        "require:vaccination",
        "require:manner_belt@neuter:intact",
    ),
    "목줄 착용 시 대형견도 입장 가능": ("require:leash@size:large",),
    "진영역사공원만 반려동물 동반 가능": ("zone:named_area",),
    # "어려울 수 있음" 은 배제가 아니다. 칩으로 보여주되 판정에 쓰지 않는다.
    "노견일 경우 미용 어려울 수 있음": ("deny:age@age:senior~soft",),
    "맹견 입장 불가, 노키즈존": ("deny:breed@breed:guard",),
    "케이지, 유모차, 배변봉투": ("require:carrier", "require:stroller", "require:poop_bag"),
    "애견용품 개인지참 필수, 최대 2마리": ("require:supplies_byo", "limit:max_dogs(max=2)"),
    "실외만 동반 가능, 목줄, 매너벨트": (
        "zone:outdoor_only",
        "require:leash",
        "require:manner_belt",
    ),
    "객실당 최대 3마리, 애견용품 개별준비": ("limit:max_dogs(max=3)", "require:supplies_byo"),
    "맹견 입장 불가, 목줄, 배변봉투, 동물등록 필수": (
        "deny:breed@breed:guard",
        "require:leash",
        "require:poop_bag",
        "admin:registration",
    ),
    "객실당 최대 4마리, 입실 불가 견종 별도 문의": (
        "limit:max_dogs(max=4)",
        "deny:breed@breed:named",
        "admin:prior_consult",
    ),
    "놀이터 아닌 곳에서 목줄 필수, 생리 중인 경우 매너벨트 착용": (
        "require:leash",
        "require:manner_belt@cycle:in_cycle",
    ),
    "야외만 동반 가능, 실내는 안고 있으면 동반 가능": ("zone:outdoor_only", "require:hold"),
    "마킹, 스크래치, 짖음 심한 경우 사전문의 필수": ("deny:behavior", "admin:prior_consult"),
    "목줄, 배변봉투, 동물등록 필수, 맹견 입마개 필수": (
        "require:leash",
        "require:poop_bag",
        "admin:registration",
        "require:muzzle@breed:guard",
    ),
    "전염질환, 입질과 거부 심한 경우, 노령견 제한": (
        "deny:health",
        "deny:behavior",
        "deny:age@age:senior",
    ),
    "객실당 최대 1마리, 대형견 불가, 애견용품 개별준비": (
        "limit:max_dogs(max=1)",
        "deny:size@size:large",
        "require:supplies_byo",
    ),
    "목줄, 3차 접종 필수, 공격성, 전염질환, 생리 중인 경우 입장 불가": (
        "require:leash",
        "require:vaccination",
        "deny:behavior",
        "deny:health",
        "deny:cycle",
    ),
    "입질, 공격성 있는 경우 입장 제한, 목줄": ("deny:behavior", "require:leash"),
    "수요일만 가능, 케이지, 유모차 탑승, 목줄, 배변봉투 필수, 공격성, 짖음 심한 경우 퇴장 조치": (
        "schedule:limited",
        "require:carrier",
        "require:stroller",
        "require:leash",
        "require:poop_bag",
        "deny:behavior",
    ),
    "안고 있어야 함, 기저귀, 유모차": ("require:hold", "require:diaper", "require:stroller"),
    "실내견만 가능": ("deny:behavior",),
    "털빠짐 심한 경우 입장 불가, 애견용품 개별준비": ("deny:health", "require:supplies_byo"),
    "맹견 출입 제한, 산책로 목줄 필수": ("deny:breed@breed:guard", "require:leash"),
    "맹견류 및 혼종견, 입질, 공격성, 생리 중인 경우 입장 불가, 중성화 수술, 접종 필수": (
        "deny:breed@breed:guard",
        "deny:behavior",
        "deny:cycle",
        "require:neutered",
        "require:vaccination",
    ),
    "2층 출입 불가": ("zone:named_area",),
    "실외만 동반 가능, 목줄, 대형견 입마개": (
        "zone:outdoor_only",
        "require:leash",
        "require:muzzle@size:large",
    ),
    "실외만 동반 가능, 마킹 금지": ("zone:outdoor_only", "deny:behavior"),
    "입질, 공격성 심한 경우, 맹견, 3개월 이하 입실 불가": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "deny:age@age:puppy(max_months=3)",
    ),
    "케이지, 안기, 배변봉투": ("require:carrier", "require:hold", "require:poop_bag"),
    "수컷 중성화 필수": ("require:neutered@sex:male",),
    "입질, 전염성 있는 경우 제한, 5차 접종 필수": (
        "deny:behavior",
        "deny:health",
        "require:vaccination",
    ),
    "목줄, 입마개, 배변봉투, 접종 필수": (
        "require:leash",
        "require:muzzle",
        "require:poop_bag",
        "require:vaccination",
    ),
    "케이지 및 유모차, 야외 정원은 반드시 리드줄 착용": (
        "require:carrier",
        "require:stroller",
        "require:leash",
    ),
    "중형견 및 대형견 입마개 필수, 실내는 안아서 입장, 일부 매장은 입장 불가": (
        "require:muzzle@size:medium_up",
        "require:hold",
        "zone:indoor_partial",
    ),
    "목줄, 입마개, 매너벨트, 안고 있어야 함": (
        "require:leash",
        "require:muzzle",
        "require:manner_belt",
        "require:hold",
    ),
    "5차 접종 필수, 발정기, 질환, 노령견, 임신 중인 경우 입장 불가, 공격성, 입질 심한 경우 입장 불가": (
        "require:vaccination",
        "deny:cycle",
        "deny:health",
        "deny:age@age:senior",
        "deny:behavior",
    ),
    "맹견은 입마개 필수": ("require:muzzle@breed:guard",),
    "불독, 웰시코기, 시바견 입장 불가, 공격성, 입질, 전염질환, 생리 중인 경우 입장 불가": (
        "deny:breed@breed:named",
        "deny:behavior",
        "deny:health",
        "deny:cycle",
    ),
    "객실당 최대 5kg 이하 2마리 또는 5kg 이상 1마리": ("limit:max_dogs_by_size",),
    "목줄 필수, 대형견 입장 불가, 1층 및 야외 테라스 가능": (
        "require:leash",
        "deny:size@size:large",
        "zone:floor1_only",
    ),
    "목줄과 매너벨트, 짖음이 심한 반려견 제한": (
        "require:leash",
        "require:manner_belt",
        "deny:behavior",
    ),
    "3차 접종 필수": ("require:vaccination",),
    "케이지에 넣은 반려견만 셔틀버스 탑승 가능": ("require:carrier", "zone:named_area"),
    "10살 이상 노령견, 지병 있는 경우, 공격성 및 입질이 심한 경우 불가": (
        "deny:age@age:senior(min_years=10)",
        "deny:health",
        "deny:behavior",
    ),
    "평일만 애견동반 가능": ("schedule:limited",),
    "평일오후만 가능, 대형견 입마개, 목줄": (
        "schedule:limited",
        "require:muzzle@size:large",
        "require:leash",
    ),
    "접종 완료 필수, 실내에서 매너벨트 필수": (
        "require:vaccination",
        "require:manner_belt",
        "zone:indoor_partial",
    ),
    "야외만 반려동물 동반 가능, 목줄, 배변봉투": (
        "zone:outdoor_only",
        "require:leash",
        "require:poop_bag",
    ),
    "5차 접종 필수, 공격성, 입질 있는 경우 제한": ("require:vaccination", "deny:behavior"),
    "마당, 1층만 입장 가능, 목줄, 배변봉투": (
        "zone:floor1_only",
        "require:leash",
        "require:poop_bag",
    ),
    "케이지 지참 시 실내 동반 가능, 미지참 시 1층 야외테라스 건물 앞 뒤 만 동반 가능, 목줄 필수": (
        "require:carrier",
        "zone:indoor_partial",
        "require:leash",
    ),
    "입질, 공격성 심한 경우, 맹견 입실 불가, 온돌방만 입실 가능, 객실당 최대 2마리": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "zone:named_area",
        "limit:max_dogs(max=2)",
    ),
    "목줄, 반려동물용품 지참": ("require:leash", "require:supplies_byo"),
    "소형견은 야외 테이블 이용 불가능": ("zone:named_area@size:small",),
    "카트에 태우거나 안고 있어야 함": ("require:carrier", "require:hold"),
    "대형견, 맹견류 및 혼종견, 공격성, 입질이 있는 경우, 생리중인 강아지 입장 불가": (
        "deny:size@size:large",
        "deny:breed@breed:guard",
        "deny:behavior",
        "deny:cycle",
    ),
    "목줄, 공격성 있는 경우 제한": ("require:leash", "deny:behavior"),
    "매너벨트, 맹견 불가": ("require:manner_belt", "deny:breed@breed:guard"),
    "목줄 필수, 마킹 금지": ("require:leash", "deny:behavior"),
    "맹견, 대형견 제한": ("deny:breed@breed:guard", "deny:size@size:large"),
    "캔넬, 유모차, 백팩 등 외부노출 막는 이동수단 이용(캔넬 대여 가능), 식당가는 출입 제한": (
        "require:carrier",
        "require:stroller",
        "zone:named_area",
    ),
    "통제 가능해야 하며, 공격성 있는 경우 목줄, 입마개 필수": (
        "deny:behavior",
        "require:leash",
        "require:muzzle",
    ),
    "접종 완료 필수, 목줄, 케이지, 맹견 입장 불가, 배변봉투": (
        "require:vaccination",
        "require:leash",
        "require:carrier",
        "deny:breed@breed:guard",
        "require:poop_bag",
    ),
    "맹견, 대형견, 그외 제한 견종 별도 문의 필수, 공격성, 짖음, 생리 중인 경우 입장 불가, 입마개, 목줄, 매너벨트 필수": (
        "deny:breed@breed:guard",
        "deny:size@size:large",
        "admin:prior_consult",
        "deny:behavior",
        "deny:cycle",
        "require:muzzle",
        "require:leash",
        "require:manner_belt",
    ),
    "입질, 공격성 심한 경우, 맹견 입실 불가, 객실당 최대 3마리": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "limit:max_dogs(max=3)",
    ),
    "중성화 필수": ("require:neutered",),
    "객실당 최대 1마리, 애견용품 개인준비 필수": ("limit:max_dogs(max=1)", "require:supplies_byo"),
    "맹견, 13kg 초과 대형견 입장 불가": ("deny:breed@breed:guard", "deny:size@size:large(min_kg=13)"),
    "목줄, 입마개, 고양이 입실 불가": ("require:leash", "require:muzzle", "deny:species_cat"),
    "3차 이상의 예방접종, 중성화 수술, 공격성/발정기인 반려견은 제한": (
        "require:vaccination",
        "require:neutered",
        "deny:behavior",
        "deny:cycle",
    ),
    "매너벨트, 노령견, 공격성 있는 경우 입장 제한": (
        "require:manner_belt",
        "deny:age@age:senior",
        "deny:behavior",
    ),
    "전용가방 또는 유모차 필수, 없으면 안고 있어야 함": (
        "require:carrier",
        "require:stroller",
        "require:hold",
    ),
    "반려견 수영장 입수 불가 , 애견용품 개별준비": ("zone:named_area", "require:supplies_byo"),
    "목줄, 케이지, 안고 있어야 함, 맹견 입장 불가, 접종 필수, 입질, 공격성 있으면 입장 제한": (
        "require:leash",
        "require:carrier",
        "require:hold",
        "deny:breed@breed:guard",
        "require:vaccination",
        "deny:behavior",
    ),
    "맹견류, 생리 중인 경우 입장 불가, 5차 접종 필수": (
        "deny:breed@breed:guard",
        "deny:cycle",
        "require:vaccination",
    ),
    "5개월 이상, 5차 접종 필수": ("deny:age@age:puppy(max_months=5)", "require:vaccination"),
    "생리, 발정기인 경우 불가, 입질 심한 경우 입마개 필수": ("deny:cycle", "deny:behavior"),
    "목줄, 배변봉투, 질병이나 공격성 있는 경우 입장 불가": (
        "require:leash",
        "require:poop_bag",
        "deny:health",
        "deny:behavior",
    ),
    "객실당 최대 4마리, 맹견 입실 불가, 입질, 공격성 심한 경우 입실 불가": (
        "limit:max_dogs(max=4)",
        "deny:breed@breed:guard",
        "deny:behavior",
    ),
    "반려동물 침구 필수, 짖음, 공격성 심한 경우 제한": ("require:bedding_byo", "deny:behavior"),
    "테라스와 루프탑만 동반 가능": ("zone:terrace_only",),
    "접종 완료 필수, 중성화 안 한 경우 매너벨트 필수": (
        "require:vaccination",
        "require:manner_belt@neuter:intact",
    ),
    "얌전하면 가능": (),
    "목줄, 입마개, 환경예치금 50,000원": ("require:leash", "require:muzzle", "fee:deposit(amount=50000)"),
    "목줄, 배변봉투, 야외만 가능": ("require:leash", "require:poop_bag", "zone:outdoor_only"),
    "입질, 공격성 심한 경우 입실 불가, 애견용품 개별준비": (
        "deny:behavior",
        "require:supplies_byo",
    ),
    "조각공원은 동반 가능": ("zone:named_area",),
    "단모종만 스파 가능": ("deny:breed@breed:named",),
    "마킹, 공격성 강한 경우 입장 제한": ("deny:behavior",),
    "맹견, 전염병, 공격성, 생리중인 경우 입장 불가": (
        "deny:breed@breed:guard",
        "deny:health",
        "deny:behavior",
        "deny:cycle",
    ),
    "목줄, 케이지, 접종증명서 제출": ("require:leash", "require:carrier", "admin:document"),
    "신분증 제시 필수(유기 방지 차원)": ("admin:document",),
    "1층 야외테라스만 가능": ("zone:terrace_only", "zone:floor1_only"),
    "푸들, 말티즈, 치와와, 요크셔테리어, 비숑, 시츄, 포메라니안만 가능, 이외 견종 입실 불가": (
        "deny:breed@breed:named",
    ),
    "목줄, 최대 2마리까지 입실가능": ("require:leash", "limit:max_dogs(max=2)"),
    "실내 목줄 필수, 맹견 입장 불가": (
        "require:leash",
        "zone:indoor_partial",
        "deny:breed@breed:guard",
    ),
    "입질, 공격성 심한 경우, 맹견 입실 불가, 매너벨트, 객실당 최대 2마리, 애견용품 개별준비": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "require:manner_belt",
        "limit:max_dogs(max=2)",
        "require:supplies_byo",
    ),
    "유모차, 중형견(20kg)까지 탑승 가능한 유모차 대여, 맹견 입장 불가, 야외 반려견 놀이터": (
        "require:stroller",
        "deny:size@size:large",
        "deny:breed@breed:guard",
        "zone:named_area",
    ),
    "5차 접종 필수, 공격성, 전염질환, 생리 중, 임신 중인 경우 제한": (
        "require:vaccination",
        "deny:behavior",
        "deny:health",
        "deny:cycle",
    ),
    "목줄 착용 시 가능, 맹견류 입장 불가": ("require:leash", "deny:breed@breed:guard"),
    "전시장 내부 케이지 이용, 입장전 문의": (
        "require:carrier",
        "zone:indoor_partial",
        "admin:prior_consult",
    ),
    "노령견 별도 동의서": ("admin:consent_form@age:senior",),
    "목줄, 반려동물 등록, 배변봉투 필수, 맹견류, 전염성, 공격성 있는 경우 입장 불가": (
        "require:leash",
        "admin:registration",
        "require:poop_bag",
        "deny:breed@breed:guard",
        "deny:health",
        "deny:behavior",
    ),
    "케이지, 3차 접종 필수": ("require:carrier", "require:vaccination"),
    "객실당 최대 3마리, 맹견 입실 불가, 애견용품 개별준비": (
        "limit:max_dogs(max=3)",
        "deny:breed@breed:guard",
        "require:supplies_byo",
    ),
    "접종 완료 필수, 공격성, 전염질환, 임신, 생리 및 피부병, 맹견류 포함 일부 견종 입장 불가": (
        "require:vaccination",
        "deny:behavior",
        "deny:health",
        "deny:cycle",
        "deny:breed@breed:guard",
    ),
    "애견용품 개인지참 필수": ("require:supplies_byo",),
    "목줄 필수, 실외만 이용 가능": ("require:leash", "zone:outdoor_only"),
    "진도견, 웰시코기 입장 불가, 객실당 최대 2마리": ("deny:breed@breed:named", "limit:max_dogs(max=2)"),
    "중성화 수술 필수": ("require:neutered",),
    "최대 5마리까지 입실 가능, 대형견은 입마개 필수": (
        "limit:max_dogs(max=5)",
        "require:muzzle@size:large",
    ),
    "맹견류, 초소형견 입장 불가, 그 외 제한 견종 별도 문의 필수, 대형견은 목요일과 마지막 주말에만 입장 가능": (
        "deny:breed@breed:guard",
        "deny:size@size:small",
        "admin:prior_consult",
        "schedule:limited@size:large",
    ),
    "목줄, 이동 시 안기, 배변봉투, 입마개, 짖지 않도록 주의, 마애불 입장 불가": (
        "require:leash",
        "require:hold",
        "require:poop_bag",
        "require:muzzle",
        "deny:behavior",
        "zone:named_area",
    ),
    "반려동물 등록, 목줄, 배변봉투, 맹견, 질병, 발정기인 경우 입장 불가": (
        "admin:registration",
        "require:leash",
        "require:poop_bag",
        "deny:breed@breed:guard",
        "deny:health",
        "deny:cycle",
    ),
    "주의사항 사전고지 필수": ("admin:prior_consult",),
    "대형견 외부 공간만 가능": ("zone:outdoor_only@size:large",),
    "대형견, 12kg이상 중형견, 고양이 불가": ("deny:size@size:large(min_kg=12)", "deny:species_cat"),
    "공격성, 입질, 미접종 반려견 제한": ("deny:behavior", "require:vaccination"),
    "맹견류, 전염성 있는 경우 입장 제한, 목줄, 배변봉투, 동물등록 필수": (
        "deny:breed@breed:guard",
        "deny:health",
        "require:leash",
        "require:poop_bag",
        "admin:registration",
    ),
    "보호자 동반": (),
    "공격성 있는 경우 입장 불가": ("deny:behavior",),
    "맹견류, 공격성, 짖음 심한 경우 불가, 5차 접종, 매너벨트 필수": (
        "deny:breed@breed:guard",
        "deny:behavior",
        "require:vaccination",
        "require:manner_belt",
    ),
    "케이지 또는 안고 있으면 동반 가능": ("require:carrier", "require:hold"),
    "중성화 수술 필수, 생리 중인 경우 입실 불가": ("require:neutered", "deny:cycle"),
    "실내 정원 좌석만 동반 가능, 짖음 심한 경우 제한": ("zone:indoor_partial", "deny:behavior"),
    "대소변 야외에서 처리, 수컷 매너벨트": ("require:poop_bag", "require:manner_belt@sex:male"),
    "입질, 공격성 심한 경우, 맹견 입실 불가, 객실당 최대 2마리": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "limit:max_dogs(max=2)",
    ),
    "루프탑 외 입장 불가": ("zone:terrace_only",),
    "공격성 심한 경우 미용비 추가": ("deny:behavior", "fee:extra"),
    "기본 펫티켓 준수": (),
    "3차 접종 이상부터 미용 가능": ("require:vaccination",),
    "놀이방 3회 이용한 강아지만 호텔링 가능, 신규 강아지 호텔링 불가능": (),
    "문 밖에 있어야 함": ("zone:outdoor_only",),
    "맹견류, 입질, 짖음 심한 경우 입장 불가": ("deny:breed@breed:guard", "deny:behavior"),
    "대형견, 마킹하는 친구들은 매너벨트 착용": ("require:manner_belt@size:large", "deny:behavior"),
    "매너벨트, 5차 접종 필수, 대형견 및 진도견 불가": (
        "require:manner_belt",
        "require:vaccination",
        "deny:size@size:large",
        "deny:breed@breed:named",
    ),
    "4개월 미만 5차 접종 미만, 10살 이상 노령견 신규예약 불가": (
        "deny:age@age:puppy(max_months=4)",
        "require:vaccination",
        "deny:age@age:senior(min_years=10)~soft",
    ),
    "입질 심한 경우, 맹견 제한, 접종 필수": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "require:vaccination",
    ),
    "입질, 거부 심한 경우 입장 제한": ("deny:behavior",),
    "실내, 루프탑 불가": ("zone:named_area",),
    "4개월 미만 입장 불가": ("deny:age@age:puppy(max_months=4)",),
    "1층만 이용 가능, 목줄": ("zone:floor1_only", "require:leash"),
    "애견용품 (패드, 사료) 지참 필수": ("require:supplies_byo",),
    "1층, 실외만 동반 가능": ("zone:floor1_only", "zone:outdoor_only"),
    "푸들, 비숑 전문": (),
    "공격성 있는 경우, 노령견 사전상담 필수": ("deny:behavior", "admin:prior_consult@age:senior"),
    "애견용품 개별준비, 맹견 입장 불가": ("require:supplies_byo", "deny:breed@breed:guard"),
    "목줄, 안기, 탑승 시 케이지 이용": ("require:leash", "require:hold", "require:carrier"),
    "1층만 이용 가능, 맹견 입장불가": ("zone:floor1_only", "deny:breed@breed:guard"),
    "고양이는 케이지": ("require:carrier",),
    "맹견 입장 불가, 목줄,배변봉투": (
        "deny:breed@breed:guard",
        "require:leash",
        "require:poop_bag",
    ),
    "케이지, 목줄": ("require:carrier", "require:leash"),
    "입질, 공격성 심한 경우, 맹견 입실 불가, 객실당 최대 소형 4마리 또는 중형 2마리": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "limit:max_dogs_by_size",
    ),
    "10살 이상 노령견 신규예약 불가, 입질, 공격성 심한 경우 불가": (
        "deny:age@age:senior(min_years=10)~soft",
        "deny:behavior",
    ),
    "5차 접종 필수, 전염질환, 입질과 거부 심한 경우, 노령견 제한": (
        "require:vaccination",
        "deny:health",
        "deny:behavior",
        "deny:age@age:senior",
    ),
    "매너벨트, 4차 접종 필수": ("require:manner_belt", "require:vaccination"),
    "목줄, 배변봉투, 동반 서약서 작성": ("require:leash", "require:poop_bag", "admin:consent_form"),
    "공격성 있는 경우, 노령견 이용 불가": ("deny:behavior", "deny:age@age:senior"),
    "접종 완료 필수, 유모차, 케이지": (
        "require:vaccination",
        "require:stroller",
        "require:carrier",
    ),
    "맹견류 입장 불가, 대형견 테라스만 이용 가능": (
        "deny:breed@breed:guard",
        "zone:terrace_only@size:large",
    ),
    "객실당 최대 6마리": ("limit:max_dogs(max=6)",),
    "수컷 매너벨트 필수, 맹견 불가": ("require:manner_belt@sex:male", "deny:breed@breed:guard"),
    "매너벨트, 안거나 목줄 착용": ("require:manner_belt", "require:hold", "require:leash"),
    "맹견류, 사회화 되어있지 않은 테리어 계열, 공격성 있는 경우 입장 불가": (
        "deny:breed@breed:guard",
        "deny:breed@breed:named",
        "deny:behavior",
    ),
    "목줄, 야외 산책로만 동반 가능": ("require:leash", "zone:outdoor_only"),
    "목줄, 배변봉투, 동물등록 필수, 맹견 입장 불가": (
        "require:leash",
        "require:poop_bag",
        "admin:registration",
        "deny:breed@breed:guard",
    ),
    "목줄, 배변봉투, 동물등록 필수": ("require:leash", "require:poop_bag", "admin:registration"),
    "짖음, 공격성, 전염질환 있는 경우 입장 불가, 5차 접종 필수": (
        "deny:behavior",
        "deny:health",
        "require:vaccination",
    ),
    "짖음, 공격성 심한 경우 입실 불가, 목줄, 객실당 최대 2마리": (
        "deny:behavior",
        "require:leash",
        "limit:max_dogs(max=2)",
    ),
    "객실당 최대 3마리, 대형견 불가": ("limit:max_dogs(max=3)", "deny:size@size:large"),
    "입질, 공격성 심한 경우, 맹견 입실 불가, 객실당 최대 4마리": (
        "deny:behavior",
        "deny:breed@breed:guard",
        "limit:max_dogs(max=4)",
    ),
    "안고 있어야 함, 1층만 이용 가능": ("require:hold", "zone:floor1_only"),
    "객실당 최대 소형 4마리 또는 중대형 2마리, 초대형견 입실 불가, 예약 시 견종 꼭 써야 함": (
        "limit:max_dogs_by_size",
        "deny:size@size:large",
        "admin:prior_consult",
    ),
    "실내 이용 시 기저귀 착용 필수, 맹견류 입장 불가": (
        "require:diaper",
        "zone:indoor_partial",
        "deny:breed@breed:guard",
    ),
    "전염질환, 공격성 있는 경우 입장 불가": ("deny:health", "deny:behavior"),
    "입질, 공격성 있는 경우 입장 제한, 목줄, 소형견 최대 4마리": (
        "deny:behavior",
        "require:leash",
        "limit:max_dogs_by_size",
    ),
    "반려동물 등록, 접종 필수, 목줄, 배변봉투": (
        "admin:registration",
        "require:vaccination",
        "require:leash",
        "require:poop_bag",
    ),
    "객실당 최대 3마리, 13kg 이상 대형견 입실 불가": ("limit:max_dogs(max=3)", "deny:size@size:large(min_kg=13)"),
    "주말, 공휴일은 입장 불가": ("schedule:limited",),
    "목줄, 실외만 동반 가능": ("require:leash", "zone:outdoor_only"),
    "접종 완료 필수, 매너벨트": ("require:vaccination", "require:manner_belt"),
    "입질견, 노령견(10세이상) 불가": ("deny:behavior", "deny:age@age:senior(min_years=10)"),
    "숲사이트 실외만 출입 가능": ("zone:outdoor_only", "zone:named_area"),
    "입질 하는 경우 사전상담 필수": ("deny:behavior", "admin:prior_consult"),
    "중성화 안 한 경우 매너벨트 착용": ("require:manner_belt@neuter:intact",),
    "목줄, 배변봉투, 입마개 필수, 맹견 목줄 및 입마개 필수": (
        "require:leash",
        "require:poop_bag",
        "require:muzzle",
    ),
    "진도견, 시바견, 웰시코기, 닥스훈트 입장 불가": ("deny:breed@breed:named",),
    "매너벨트, 맹견, 공격성 있는 경우 제한": (
        "require:manner_belt",
        "deny:breed@breed:guard",
        "deny:behavior",
    ),
    "목줄, 하네스 필수, 실내 배변 불가": ("require:leash", "require:harness"),
    "대형견은 수요일만 가능": ("schedule:limited@size:large",),
    "목줄, 배변봉투 필수, 실외만 이용가능": (
        "require:leash",
        "require:poop_bag",
        "zone:outdoor_only",
    ),
    "매너벨트, 객실당 최대 2마리": ("require:manner_belt", "limit:max_dogs(max=2)"),
    "애견 수영장": (),
    "중성화 필수, 전염질환, 공격성, 생리 중인 경우 입장 제한": (
        "require:neutered",
        "deny:health",
        "deny:behavior",
        "deny:cycle",
    ),
    "매너벨트, 입마개": ("require:manner_belt", "require:muzzle"),
    "전염질환, 미등록 동물, 인식표를 하지 않은 동물, 발정기인 경우 입장 불가, 목줄, 배변봉투": (
        "deny:health",
        "admin:registration",
        "deny:cycle",
        "require:leash",
        "require:poop_bag",
    ),
    "입질, 질환, 엉킴, 거부 심한 경우 불가": ("deny:behavior", "deny:health"),
    "애견용품 개별준비, 객실당 2마리까지만 가능": ("require:supplies_byo", "limit:max_dogs(max=2)"),
    "고양이 불가, 객실당 최대 2마리, 애견용품 개별준비": (
        "deny:species_cat",
        "limit:max_dogs(max=2)",
        "require:supplies_byo",
    ),
    "맹견류 및 혼종견, 풍산개, 아메리칸불리, 차우차우, 핏불테리어, 도베르만 입장 불가": (
        "deny:breed@breed:guard",
        "deny:breed@breed:named",
    ),
    "객실당 최대 3마리, 목줄, 애견용품 개별준비": (
        "limit:max_dogs(max=3)",
        "require:leash",
        "require:supplies_byo",
    ),
    "객실당 최대 소형 4마리 또는 대형 2마리": ("limit:max_dogs_by_size",),
    "실내운동장은 15kg 미만 전용, 실내운동장은 수컷은 매너벨트 필수,생리 중인 반려견은 입장이 불가, 맹견에 속한 강아지 및 공격성이 강한 반려견은 입장이 제한": (
        "zone:indoor_partial",
        "require:manner_belt@sex:male",
        "deny:cycle",
        "deny:breed@breed:guard",
        "deny:behavior",
    ),
    "불독 견종 입장 제한": ("deny:breed@breed:named",),
}


# ---------------------------------------------------------------- 상태 배정
# 술어가 원문의 **전부를 담지 못한** 것. UI 는 칩과 함께 원문을 보여야 한다 —
# 칩 목록만 보이면 완결로 읽히고, 그건 조용한 truncation 과 같은 거짓말이다.
_PARTIAL: frozenset[str] = frozenset(
    {
        # 조건부 허가 — "X 하면 Y 가능". 술어는 요구사항만 담고 조건 구조는 못 담는다
        "케이지 지참 시 실내 동반 가능, 미지참 시 1층 야외테라스 건물 앞 뒤 만 동반 가능, 목줄 필수",
        "야외만 동반 가능, 실내는 안고 있으면 동반 가능",
        "케이지 또는 안고 있으면 동반 가능",
        "전용가방 또는 유모차 필수, 없으면 안고 있어야 함",
        "목줄 착용 시 대형견도 입장 가능",
        "목줄 착용 시 가능, 맹견류 입장 불가",
        "중형견 및 대형견 입마개 필수, 실내는 안아서 입장, 일부 매장은 입장 불가",
        "케이지, 유모차, 가방, 안기, 대형견 일부 매장에서 입장 거부 당할 수 있음",
        "맹견 제한, 목줄 필수, 트랙터 이용 시 안거나 케이지 필요",
        "카트에 태우거나 안고 있어야 함",
        "매너벨트, 안거나 목줄 착용",
        "목줄, 안기, 탑승 시 케이지 이용",
        "생리, 발정기인 경우 불가, 입질 심한 경우 입마개 필수",
        "통제 가능해야 하며, 공격성 있는 경우 목줄, 입마개 필수",
        "놀이터 아닌 곳에서 목줄 필수, 생리 중인 경우 매너벨트 착용",
        # 시설 고유 구역·절차 이름 — `zone:named_area` 로는 어디인지 말하지 못한다
        "조각공원은 동반 가능",
        "진영역사공원만 반려동물 동반 가능",
        "숲사이트 실외만 출입 가능",
        "2층 출입 불가",
        "실내, 루프탑 불가",
        "소, 중, 대형견 공간분리",
        "반려견 수영장 입수 불가",
        "반려견 수영장 입수 불가 , 애견용품 개별준비",
        "목줄, 배변봉투, 경기장 내 출입불가",
        "케이지에 넣은 반려견만 셔틀버스 탑승 가능",
        "캔넬, 유모차, 백팩 등 외부노출 막는 이동수단 이용(캔넬 대여 가능), 식당가는 출입 제한",
        "목줄, 이동 시 안기, 배변봉투, 입마개, 짖지 않도록 주의, 마애불 입장 불가",
        "입질, 공격성 심한 경우, 맹견 입실 불가, 온돌방만 입실 가능, 객실당 최대 2마리",
        "유모차, 중형견(20kg)까지 탑승 가능한 유모차 대여, 맹견 입장 불가, 야외 반려견 놀이터",
        "실내운동장은 15kg 미만 전용, 실내운동장은 수컷은 매너벨트 필수,생리 중인 반려견은 입장이 불가, 맹견에 속한 강아지 및 공격성이 강한 반려견은 입장이 제한",
        "중, 대형견은 독채 이용",
        "소형견은 야외 테이블 이용 불가능",
        "실내 정원 좌석만 동반 가능, 짖음 심한 경우 제한",
        "전시장 내부 케이지 이용, 입장전 문의",
        "목줄 필수, 대형견 입장 불가, 1층 및 야외 테라스 가능",
        "마당, 1층만 입장 가능, 목줄, 배변봉투",
        "1층 야외테라스만 가능",
        "테라스와 루프탑만 동반 가능",
        "루프탑 외 입장 불가",
        "대형견 외부 공간만 가능",
        "맹견류 입장 불가, 대형견 테라스만 이용 가능",
        "목줄, 야외 산책로만 동반 가능",
        "목줄, 하네스 필수, 실내 배변 불가",
        "케이지 및 유모차, 야외 정원은 반드시 리드줄 착용",
        "실외만 동반 가능, 마킹 금지",
        "문 밖에 있어야 함",
        "1층은 안고 있어야 함",
        "실내견만 가능",
        # 수치가 술어에 안 들어간다 — 마리 수·kg·요일의 구체값은 v1 이 담지 않는다
        "객실당 최대 5kg 이하 2마리 또는 5kg 이상 1마리",
        "맹견, 13kg 초과 대형견 입장 불가",
        "객실당 최대 3마리, 13kg 이상 대형견 입실 불가",
        "대형견, 12kg이상 중형견, 고양이 불가",
        "맹견류, 초소형견 입장 불가, 그 외 제한 견종 별도 문의 필수, 대형견은 목요일과 마지막 주말에만 입장 가능",
        "대형견은 수요일만 가능",
        "평일오후만 가능, 대형견 입마개, 목줄",
        "평일만 애견동반 가능",
        "주말, 공휴일은 입장 불가",
        "수요일만 가능, 케이지, 유모차 탑승, 목줄, 배변봉투 필수, 공격성, 짖음 심한 경우 퇴장 조치",
        "5개월 이상, 5차 접종 필수",
        "입질, 공격성 심한 경우, 맹견, 3개월 이하 입실 불가",
        "4개월 미만 5차 접종 미만, 10살 이상 노령견 신규예약 불가",
        "목줄, 입마개, 환경예치금 50,000원",
        "객실당 최대 소형 4마리 또는 대형 2마리",
        "입질, 공격성 심한 경우, 맹견 입실 불가, 객실당 최대 소형 4마리 또는 중형 2마리",
        "객실당 최대 소형 4마리 또는 중대형 2마리, 초대형견 입실 불가, 예약 시 견종 꼭 써야 함",
        "입질, 공격성 있는 경우 입장 제한, 목줄, 소형견 최대 4마리",
        # 열거된 견종 이름을 술어가 담지 못한다 — `breed:named` 는 "일부 견종" 까지다
        "닥스훈트, 미니핀, 코카스파니엘, 비글, 웰시코기, 프렌치불독, 단모치와와 입장 불가",
        "푸들, 말티즈, 치와와, 요크셔테리어, 비숑, 시츄, 포메라니안만 가능, 이외 견종 입실 불가",
        "불독, 웰시코기, 시바견 입장 불가, 공격성, 입질, 전염질환, 생리 중인 경우 입장 불가",
        "진도견, 시바견, 웰시코기, 닥스훈트 입장 불가",
        "진도견, 웰시코기 입장 불가, 객실당 최대 2마리",
        "맹견류 및 혼종견, 풍산개, 아메리칸불리, 차우차우, 핏불테리어, 도베르만 입장 불가",
        "맹견류, 사회화 되어있지 않은 테리어 계열, 공격성 있는 경우 입장 불가",
        "맹견류, 사회화 되어있지 않은 테리어 계열, 공격성, 전염질환 있는 경우 입장 불가, 5차 접종, 중성화 수술 필수",
        "대형견, 진도믹스 이용 제한, 짖음 심한 경우 이용 제한, 접종 필수",
        "매너벨트, 5차 접종 필수, 대형견 및 진도견 불가",
        "불독 견종 입장 제한",
        "단모종만 스파 가능",
        "고양이는 케이지",
        "강아지만 입실 가능, 객실당 최대 6마리",
        "목줄, 대형견 입마개 필수, 배변관리, 시설 내부에 강아지들이 있어 흥분하지 않게 주의 바람",
        "신분증 제시 필수(유기 방지 차원)",
        "맹견 입장 불가, 노키즈존",
        "노견일 경우 미용 어려울 수 있음",
        "입질, 공격성 있는 경우 입장 제한, 애견샤워 금지",
        "공격성 심한 경우 미용비 추가",
        "입질, 공격성, 거부 심한 경우 미용 중단",
        "3차 접종 이상부터 미용 가능",
    }
)

# 술어가 하나도 안 나오는 것. **일부러 안 옮긴다** — 사유를 적어 다음 사람이 선의로
# 코드화하다가 판정 불가능한 조건을 발명하지 않게 한다 (결정 #70 §4).
_UNREPRESENTABLE: dict[str, Reason] = {
    "얌전하면 가능": Reason.VAGUE,
    "기본 펫티켓 준수": Reason.VAGUE,
    "보호자 동반": Reason.VAGUE,
    "푸들, 비숑 전문": Reason.VAGUE,
    "애견 수영장": Reason.FACILITY_SPECIFIC,
    "놀이방 3회 이용한 강아지만 호텔링 가능, 신규 강아지 호텔링 불가능": Reason.CONDITIONAL_GRANT,
}


class Reading(NamedTuple):
    """원문 한 줄의 판독 결과."""

    predicates: tuple[P, ...]
    parse_state: ParseState
    reason: Reason | None = None


def read(text: str) -> Reading | None:
    """원문 → 판독. 표에 없으면 `None` — 호출자가 `raw_only` 로 다룬다.

    새 문자열은 **조용히 추측되지 않는다.** 재적재로 처음 보는 문장이 들어오면
    술어 없이 원문만 남고, 표를 갱신할 때까지 그 상태가 유지된다.
    """
    specs = _MAP.get(text)
    if specs is None:
        return None
    if (reason := _UNREPRESENTABLE.get(text)) is not None:
        return Reading((), ParseState.RAW_ONLY, reason)
    state = ParseState.PARTIAL if text in _PARTIAL else ParseState.MAPPED
    return Reading(tuple(_p(spec) for spec in specs), state)


def label_of(predicate: P) -> str:
    """칩 문구. 대상 한정어가 있으면 붙인다 — `입마개·대형견`."""
    base = LABELS[predicate.code]
    qualifier = SUBJECT_LABELS[predicate.applies_to]
    return f"{base}·{qualifier}" if qualifier else base


def mapped_texts() -> frozenset[str]:
    """표가 아는 원문 전부. 커버리지 테스트가 쓴다."""
    return frozenset(_MAP)


class RestrictionState(StrEnum):
    """**원천이 무엇을 말했나.** `ParseState`(우리가 읽었나)와 다른 축이다.

    둘을 한 값으로 합치면 사용자가 할 행동이 다른 두 경우가 섞인다:

        unknown        원문 자체가 없다 (KTO 9,692행) → 전화로 확인해야 한다
        none_confirmed 원천이 "제한 없음" 이라고 말했다 → 확인된 사실이다
        not_applicable 원천이 "해당없음" 이라고 말했다 → 입장 가능의 근거가 아니다
        restricted     제한이 있다 → 술어 또는 원문을 보면 된다

    태그가 0개인 것은 셋 다 같지만 의미는 전혀 다르다. 칩이 없다고 제한이 없는 것이
    아니다 — 그 오독이 "미상을 무제한으로 읽는" 사고다 (결정 #70 §6).
    """

    UNKNOWN = "unknown"
    NONE_CONFIRMED = "none_confirmed"
    NOT_APPLICABLE = "not_applicable"
    RESTRICTED = "restricted"


class Derivation(NamedTuple):
    """행 하나의 파생 결과. 배치가 저장하고 adapter 가 읽는다."""

    state: RestrictionState
    parse_state: ParseState | None
    predicates: tuple[P, ...]
    reason: Reason | None = None

    def to_columns(self) -> dict:
        """`facility` 컬럼으로. `predicates` 는 JSONB 배열이다."""
        return {
            "restriction_state": self.state.value,
            "restriction_parse_state": (
                self.parse_state.value if self.parse_state is not None else None
            ),
            "restriction_predicates": [
                {
                    "code": p.code,
                    "applies_to": p.applies_to.value,
                    "params": dict(p.params),
                    "certainty": p.certainty.value,
                }
                for p in self.predicates
            ],
            "restriction_semantics_version": RESTRICTION_SEMANTICS_VERSION,
        }


def derive(raw: str | None) -> Derivation:
    """저장된 원문 한 줄 → 두 축 + 술어. **원천을 다시 호출하지 않는다.**

    `pet_axes` 와 같은 성격이다 — 저장된 값의 함수이므로 표가 바뀌면 이 단계만 다시 돌린다.
    """
    text = (raw or "").strip()
    if not text:
        # 원문이 없다. KTO 9,692행이 여기다 — 제한이 없는 것이 아니라 **모르는 것**이다.
        return Derivation(RestrictionState.UNKNOWN, None, ())
    if text == NO_RESTRICTION:
        return Derivation(RestrictionState.NONE_CONFIRMED, ParseState.MAPPED, ())
    if text == NOT_APPLICABLE:
        # 2,789행 중 2,781행은 pet_allowed=false다. 제한 없음이 아니라 동반 불가라
        # 제한란이 적용되지 않는다는 뜻이므로 입장 가능의 근거로 승격하지 않는다.
        return Derivation(RestrictionState.NOT_APPLICABLE, ParseState.MAPPED, ())
    reading = read(text)
    if reading is None:
        # 표에 없는 새 문자열. **추측하지 않는다** — 원문만 남기고 표 갱신을 기다린다.
        return Derivation(RestrictionState.RESTRICTED, ParseState.RAW_ONLY, ())
    return Derivation(
        RestrictionState.RESTRICTED, reading.parse_state, reading.predicates, reading.reason
    )
