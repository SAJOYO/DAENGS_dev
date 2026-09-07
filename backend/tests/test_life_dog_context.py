"""로드맵 B4 — 반려견 컨텍스트가 pets 에서 프롬프트까지 가는 길.

DB 는 안 씁니다. `fakes.py` 가 리포지토리를 바꿔치기하므로 여기서 보는 것은 **규칙**입니다 —
가족이 된 날을 나이로 세지 않는가, 앱 아바타 id 를 견종 이름으로 옮기는가, 프로필이 없을 때
프롬프트가 B4 이전과 **한 글자도 같은가**.

마지막 것이 이 파일에서 제일 중요합니다. 프로필 없는 요청의 프롬프트가 조금이라도 달라지면
lap18 과 lap19 를 같은 축에서 못 비교하고, 그러면 이 카드가 지표를 올렸는지 내렸는지 **아무도
말할 수 없게 됩니다** (RAG-028 ⑥ 이 랩 비교를 세운 이유).
"""

import uuid
from datetime import date

import pytest

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    DogContext,
    LifePayload,
)
from daengs_backend.orchestration.planner import assemble_route_plan
from daengs_backend.orchestration.semantic import SemanticRoutingDecision
from daengs_backend.services import dog_context
from fakes import FakeAdmin, FakeAppUser, FakePet, Store, install

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


# ---------------------------------------------------------------- 나이


def test_가족이_된_날은_나이가_아니다() -> None:
    """유기견 출신이 실제보다 어리게 나가면, 나이가 답을 가르는 질문에서 조용히 틀립니다."""
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug",
                  birth_date=date(2023, 1, 1), birth_date_kind="family_day")
    assert dog_context.age_months(pet, today=date(2026, 9, 4)) is None


def test_생일이면_개월로_센다() -> None:
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug",
                  birth_date=date(2023, 7, 10), birth_date_kind="birthday")
    assert dog_context.age_months(pet, today=date(2026, 9, 4)) == 37


def test_생일_전날까지는_한_달이_덜_찬다() -> None:
    """일(day)까지 봐야 합니다. 달만 빼면 생일 하루 전에 한 살을 더 먹습니다."""
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug",
                  birth_date=date(2026, 3, 10), birth_date_kind="birthday")
    assert dog_context.age_months(pet, today=date(2026, 9, 9)) == 5
    assert dog_context.age_months(pet, today=date(2026, 9, 10)) == 6


def test_앞날_생일은_나이를_안_만든다() -> None:
    """`birth_date` 에는 상한 제약이 없습니다 (05_pets.sql). 음수 나이를 만들지 않습니다."""
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="dog_pug",
                  birth_date=date(2027, 1, 1), birth_date_kind="birthday")
    assert dog_context.age_months(pet, today=date(2026, 9, 4)) is None


# ---------------------------------------------------------------- 견종 어휘


def test_아바타_id_를_한국어_견종명으로_옮긴다() -> None:
    """`dog_pug` 는 그림 이름입니다. 항공 약관은 "퍼그"를 말하지 `dog_pug` 를 말하지 않습니다."""
    assert dog_context.breed_label("dog_pug") == "퍼그"
    assert dog_context.breed_label("dog_french_bulldog") == "프렌치불독"


def test_색깔은_견종이_아니다() -> None:
    """"초코 푸들"·"베이지 시바"는 그림 이름입니다. 조문은 색으로 갈리지 않습니다."""
    assert dog_context.breed_label("dog_toy_poodle_chocolate") == "토이푸들"
    assert dog_context.breed_label("dog_toy_poodle_silver") == "토이푸들"
    assert dog_context.breed_label("dog_shiba_inu_orange") == "시바견"


def test_모르는_아바타와_믹스는_견종을_안_보낸다() -> None:
    """앱에 아바타가 늘었는데 표가 안 따라온 경우입니다. 모르는 슬러그를 그대로 넣어
    모델이 그럴듯하게 해석하게 두지 않습니다 — 프로필로 지어낸 답은 **이 아이에게 맞춘
    답처럼 보여서** 틀렸을 때 더 나쁩니다.
    """
    assert dog_context.breed_label("mix") is None
    assert dog_context.breed_label("dog_akita") is None
    assert dog_context.breed_label(None) is None
    assert dog_context.breed_label("") is None


def test_맹견_5종은_앱_어휘에_없다() -> None:
    """**로드맵 B4 의 "견종(맹견 5종)" 근거가 실제로는 성립하지 않습니다.**

    앱 아바타 27종에 도사견·핏불테리어·아메리칸 스태퍼드셔 테리어·스태퍼드셔 불 테리어·
    로트와일러가 하나도 없어, 사용자가 그 견종을 고를 방법이 없습니다. 이 테스트는 그 사실을
    붙잡아 둡니다 — 앱에 그 아바타가 생기는 날 여기가 깨지고, 그때 이 문단을 지우면 됩니다.
    """
    labels = set(dog_context.BREED_LABELS.values())
    맹견 = {"도사견", "아메리칸 핏불테리어", "아메리칸 스태퍼드셔 테리어",
            "스태퍼드셔 불 테리어", "로트와일러"}
    assert labels & 맹견 == set()


# ---------------------------------------------------------------- 조회


async def test_내_강아지만_읽는다(store: Store) -> None:
    """소유권은 `pet_repo.get_owned` 가 쿼리 조건으로 묶습니다. 남의 id 는 못 읽습니다."""
    pet = FakePet(app_user_id=STRANGER, name="남의개", breed="dog_pug")
    store.pets.append(pet)
    session = object()
    assert await dog_context.resolve(session, OWNER, str(pet.id)) is None


async def test_없는_강아지도_오류가_아니다(store: Store) -> None:
    """프로필이 없다고 답할 수 있는 질문을 실패시키지 않습니다."""
    session = object()
    assert await dog_context.resolve(session, OWNER, str(uuid.uuid4())) is None
    assert await dog_context.resolve(session, OWNER, "uuid 아님") is None


async def test_아는_것만_담아_돌려준다(store: Store) -> None:
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="dog_french_bulldog",
                  birth_date=date(2024, 9, 4), birth_date_kind="birthday")
    store.pets.append(pet)
    resolved = await dog_context.resolve(object(), OWNER, str(pet.id))
    assert resolved is not None
    assert resolved["breed"] == "프렌치불독"
    assert isinstance(resolved["age_months"], int)


async def test_아무것도_모르면_None(store: Store) -> None:
    """믹스 + 생일 모름. 빈 dict 를 보내면 payload 에 빈 칸이 생깁니다."""
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    assert await dog_context.resolve(object(), OWNER, str(pet.id)) is None


# ---------------------------------------------------------------- planner


def _life_plan(context: dict) -> LifePayload:
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["life"], handoffs=[]),
        query="펫보험 가입 나이 제한이 어떻게 되나요?",
        context=context,
        router="llm",
    )
    payload = plan.requests[0].payload
    assert isinstance(payload, LifePayload)
    return payload


def test_신뢰된_context_의_dog_만_payload_로_간다() -> None:
    payload = _life_plan({"dog": {"breed": "퍼그", "age_months": 24}})
    assert payload.dog == DogContext(breed="퍼그", age_months=24)


def test_프로필이_없으면_payload_도_그대로다() -> None:
    assert _life_plan({}).dog is None


def test_모양이_틀린_dog_는_요청을_깨지_않는다() -> None:
    """라우터가 낸 값이 아니라 부르는 쪽이 넣는 값이지만, 여기서 422 를 내면 프로필 하나가
    답할 수 있는 질문을 통째로 실패시킵니다.
    """
    assert _life_plan({"dog": "퍼그"}).dog is None
    assert _life_plan({"dog": {"breed": "  ", "age_months": "두 살"}}).dog is None
    assert _life_plan({"dog": {"age_months": True}}).dog is None


def test_training_은_반려견_컨텍스트를_안_받는다() -> None:
    """B4 는 Life 카드입니다. 훈련 프롬프트는 이 카드가 건드리지 않습니다."""
    plan = assemble_route_plan(
        SemanticRoutingDecision(execute=["training", "life"], handoffs=[]),
        query="앉아를 어떻게 가르치나요?",
        context={"dog": {"breed": "퍼그", "age_months": 24}},
        router="llm",
    )
    training = next(r for r in plan.requests if r.capability == CapabilityName.TRAINING)
    assert not hasattr(training.payload, "dog")


# ---------------------------------------------------------------- 어댑터


async def test_어댑터가_견종과_나이를_원시값으로_넘긴다() -> None:
    """`DogContext` 를 그대로 넘기면 `daengs_life` 가 오케스트레이션을 의존하게 됩니다."""
    from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter

    seen: dict = {}

    def ask(question: str, **kw):
        seen.update({"question": question, **kw})
        raise RuntimeError("여기서 멈춘다 — 보는 것은 인자다")

    request = CapabilityRequest(
        capability="life",
        payload=LifePayload(question="비행기 태울 수 있나요?",
                            dog=DogContext(breed="퍼그", age_months=24)),
    )
    await LifeCapabilityAdapter(ask).run(request, request_id="trace")
    # 정확 일치인 것이 의도다 — 칸이 하나 늘면 여기서 걸리고, 그때 그 칸이 무엇인지
    # 사람이 본다. 스크리닝 둘은 #283 이 늘린 칸이고 이 요청에는 판정이 없다.
    assert seen == {"question": "비행기 태울 수 있나요?", "breed": "퍼그", "age_months": 24,
                    "screening_verdict": None, "screening_days_ago": None}


async def test_프로필이_없으면_None_으로_넘어간다() -> None:
    from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter

    seen: dict = {}

    def ask(question: str, **kw):
        seen.update(kw)
        raise RuntimeError("stop")

    request = CapabilityRequest(capability="life", payload=LifePayload(question="질문"))
    await LifeCapabilityAdapter(ask).run(request, request_id="trace")
    assert seen == {"breed": None, "age_months": None,
                    "screening_verdict": None, "screening_days_ago": None}


# ---------------------------------------------------------------- 프롬프트


def _hit():
    from daengs_life.rag.stages.search import Hit

    return Hit(rank=1, score=0.9, chunk_id="c#1", citation="「동물보호법」 제15조",
               citation_url=None, section=None, document_title="동물보호법",
               content="본문", part=None)


def test_프로필이_없으면_프롬프트가_B4_이전과_같다() -> None:
    """**이 카드에서 제일 중요한 테스트입니다.** 한 글자라도 달라지면 lap18↔lap19 비교가 깨집니다."""
    from daengs_life.rag.stages import generate

    base = generate.PROMPT.format(context=generate.build_context([_hit()]), question="질문")
    assert generate.build_prompt("질문", [_hit()]) == base
    assert generate.build_prompt("질문", [_hit()], dog=generate.DogProfile()) == base
    assert generate.build_prompt("질문", [_hit()], dog=None) == base


def test_프로필이_있으면_블록이_뒤에_붙는다() -> None:
    from daengs_life.rag.stages import generate

    prompt = generate.build_prompt(
        "질문", [_hit()], dog=generate.DogProfile(breed="퍼그", age_months=26)
    )
    assert prompt.startswith(
        generate.PROMPT.format(context=generate.build_context([_hit()]), question="질문")
    )
    assert "[반려견] 견종: 퍼그 · 나이: 2년 2개월 (만 2세)" in prompt
    assert "만들어 내지 마세요" in prompt


def test_개월을_년과_달로_읽어_준다() -> None:
    """조문의 연령 조건은 만 나이입니다. "38개월"로 두면 모델이 단위를 헷갈릴 자리가 남습니다."""
    from daengs_life.rag.stages.generate import DogProfile

    assert DogProfile(age_months=38).describe() == "나이: 3년 2개월 (만 3세)"
    assert DogProfile(age_months=7).describe() == "나이: 7개월 (만 0세)"
    assert DogProfile(breed="퍼그").describe() == "견종: 퍼그"
