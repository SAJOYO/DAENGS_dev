"""활성 반려견 → Life 가 쓸 수 있는 사실 둘 (로드맵 B4).

**여기서 멈추는 것이 설계입니다.** pets 에는 성별·중성화·체중도 있지만 그 셋은 제도 문서로
답할 질문에 쓰이지 않습니다. 넣으면 프롬프트만 길어지고, 오케스트레이션 계약을 지나는
개인정보만 늡니다. 늘릴 때는 그 값을 쓰는 문항을 골든셋에 먼저 넣으세요.

**어휘 번역이 이 층의 일입니다.** `pets.breed` 에 들어 있는 값은 앱의 아바타 id (`dog_pug`)
이지 견종 이름이 아닙니다. 그대로 프롬프트에 넣으면 코퍼스의 한국어 조문·약관과 아무것도 안
맞습니다 — 항공 약관은 "단두종"과 "퍼그"를 말하지 `dog_pug` 를 말하지 않습니다. pets 행을
"Life 가 추론에 쓸 수 있는 사실"로 바꾸는 것이 이 파일의 정의이고, 번역은 그 일부입니다.

무엇이 맹견인지·단두종인지는 여기서 정하지 않습니다 — 그 목록의 근거는 동물보호법 시행규칙과
항공사 약관이고 코퍼스를 가진 `daengs_life` 가 봅니다. 이 층은 **한국어 견종명까지만** 냅니다.
"""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Pet
from daengs_backend.repositories import pet as pet_repo

__all__ = ["BREED_LABELS", "age_months", "breed_label", "resolve"]

# 앱 아바타 id → 한국어 견종명. **원본은 DAENGS_APP `miniroom/art/DogShapes.kt` 의 `DogBreed`**
# (`id`, `label`) 이고 여기 있는 것은 그 사본입니다. 저장소가 달라 컴파일러가 안 붙잡아 주므로,
# 앱에 아바타가 늘면 이 표에 한 줄을 같이 넣어야 합니다.
#
# ⚠️ **모르는 id 는 견종을 안 보냅니다** (`breed_label` 이 None). 아바타가 늘었는데 이 표가
# 안 따라오면 그 견종만 B4 이전처럼 동작합니다 — 조용하지만, 모르는 슬러그를 그대로 넣어
# 모델이 `dog_akita` 를 그럴듯하게 해석하게 두는 것보다 낫습니다. 프로필로 지어낸 답은
# **이 아이에게 맞춘 답처럼 보여서** 틀렸을 때 더 나쁩니다.
#
# ⚠️ **이 27종에 맹견 5종(도사견·핏불테리어·아메리칸 스태퍼드셔 테리어·스태퍼드셔 불 테리어·
# 로트와일러)이 하나도 없습니다.** 앱에서 그 견종을 고를 방법이 없어 `mix`(믹스)로 들어옵니다.
# 로드맵 B4 가 "견종(맹견 5종)"이라고 적은 근거가 실제로는 성립하지 않고, 견종이 실제로 답을
# 가르는 자리는 **단두종(항공 운송)** 입니다 — 프렌치불독·퍼그가 이 표에 있고 항공사 약관의
# 단두종 목록이 코퍼스에 있습니다 (`parse/parsers/transport/airlines_pet_pages.py`).
BREED_LABELS: dict[str, str] = {
    "dog_beagle": "비글",
    "dog_toy_poodle_silver": "토이푸들",
    "dog_toy_poodle_light_brown": "토이푸들",
    "dog_toy_poodle_chocolate": "토이푸들",
    "dog_maltese": "말티즈",
    "dog_yorkshire_terrier": "요크셔테리어",
    "dog_chihuahua": "치와와",
    "dog_bichon_frise": "비숑프리제",
    "dog_labrador_retriever": "래브라도 리트리버",
    "dog_golden_retriever": "골든리트리버",
    "dog_japanese_spitz": "스피츠",
    "dog_jindo": "진돗개",
    "dog_shiba_inu_black": "시바견",
    "dog_shiba_inu_beige": "시바견",
    "dog_shiba_inu_orange": "시바견",
    "dog_siberian_husky": "시베리안 허스키",
    "dog_pomeranian_black_tan": "포메라니안",
    "dog_pomeranian_beige": "포메라니안",
    "dog_pomeranian_white": "포메라니안",
    "dog_border_collie": "보더콜리",
    "dog_welsh_corgi": "웰시코기",
    "dog_dachshund_short_brown": "닥스훈트",
    "dog_dachshund_short_black": "닥스훈트",
    "dog_dachshund_long_beige": "닥스훈트",
    "dog_french_bulldog": "프렌치불독",
    "dog_pug": "퍼그",
    "dog_schnauzer": "슈나우저",
}


def breed_label(breed: str | None) -> str | None:
    """앱 아바타 id → 한국어 견종명. 모르는 id 와 `mix` 는 `None` 입니다.

    **색깔을 떼어냅니다** — "초코 푸들"·"베이지 시바"는 그림 이름이지 견종이 아니고, 조문이나
    약관은 색으로 갈리지 않습니다. 색까지 보내면 모델이 근거에 없는 구분을 있다고 읽을 자리만
    늡니다. 믹스(`mix`)도 `None` 입니다 — "믹스"라는 사실로 갈리는 조항이 없습니다.
    """
    if not breed:
        return None
    return BREED_LABELS.get(breed.strip())


def age_months(pet: Pet, *, today: date | None = None) -> int | None:
    """개월 나이. **`birth_date_kind` 가 `birthday` 일 때만** 계산합니다.

    `family_day` 는 '가족이 된 날'이라 나이가 아닙니다 (`db/init/05_pets.sql`). 그 값으로
    나이를 세면 세 살짜리 유기견 출신이 두 살로 나가고, 보험 가입 연령처럼 **나이가 답을
    가르는 질문에서 조용히 틀립니다.** 모르는 것은 모르는 채로 두는 편이 낫습니다.
    """
    if pet.birth_date is None or pet.birth_date_kind != "birthday":
        return None
    now = today or date.today()
    if pet.birth_date > now:
        # 상한이 없는 칸이라 앞날이 들어올 수 있습니다. 음수 나이를 만들지 않습니다.
        return None
    months = (now.year - pet.birth_date.year) * 12 + (now.month - pet.birth_date.month)
    if now.day < pet.birth_date.day:
        months -= 1
    return max(months, 0)


async def resolve(
    session: AsyncSession, app_user_id: uuid.UUID, active_dog_id: str
) -> dict[str, object] | None:
    """`context["dog"]` 에 넣을 값. **없으면 None 이고, 그것은 오류가 아닙니다.**

    id 가 UUID 가 아니거나 **구성원이 아닌** 강아지면 조용히 None 입니다 — 여기서 4xx 를 내면
    프로필이 없다는 이유로 답할 수 있는 질문이 실패합니다. 판정은 `pet_repo.get_accessible`
    이 쿼리 조건으로 묶고 있어, 남의 id 를 넣어도 못 읽습니다.

    **구성원 기준인 이유**는 같은 요청의 채팅 쪽(`repositories/chat.py`)이 이미 구성원까지
    열려 있어서입니다 (docs/co-care.md §2). 여기만 대표 기준으로 남기면 돌보미의 답변에서
    지병·복약이 조용히 빠집니다 — 아무 오류도 안 나고 답만 나빠집니다.
    """
    try:
        pet_id = uuid.UUID(active_dog_id)
    except (ValueError, AttributeError, TypeError):
        return None
    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        return None
    resolved: dict[str, object] = {}
    label = breed_label(pet.breed)
    if label is not None:
        resolved["breed"] = label
    months = age_months(pet)
    if months is not None:
        resolved["age_months"] = months
    # 돌봄 (#331). 급식 **시각**은 안 보냅니다 — 소비자가 알림·케어 기록이지 답변이 아닙니다.
    if pet.feeding_style in ("free", "scheduled"):
        resolved["feeding_style"] = pet.feeding_style
    conditions = (pet.health_conditions or "").strip()
    if conditions:
        resolved["health_conditions"] = conditions
    if on_medication(pet):
        resolved["on_medication"] = True
    return resolved or None


def on_medication(pet: Pet) -> bool:
    """정기 복약 **여부**. 약 이름은 여기서 멈춥니다.

    `pets.medications` 는 자유 텍스트인데 그대로 프롬프트에 가면, 약·용량 질문을 거절하는
    일반 답변의 방어(`adapters/general.py`)가 지시문 한 줄로 약해집니다. 비서가 알아야 하는
    것은 "약을 먹는 아이" 라는 사실뿐입니다.

    **빈 칸은 False 가 아니라 모름입니다** — 호출자는 이 값이 True 일 때만 키를 냅니다.
    """
    return bool((pet.medications or "").strip())
