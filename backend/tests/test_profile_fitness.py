"""개체 적합성 픽스처와 배선 게이트.

모델도 네트워크도 부르지 않는다. 여기서 잡으려는 사고는 하나다 — **프로필이 프롬프트에
안 닿는데 그걸 모른 채 "안 변했다" 를 모델의 실패로 적는 것.**
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from daengs_backend.orchestration import planner
from daengs_backend.orchestration.contracts import TrainingPayload
from daengs_backend.services.dog_context import BREED_LABELS
from daengs_evals.profile_fitness.profiles import (
    PROFILES_V1_PATH,
    Profile,
    load_profiles,
    profiles_by_id,
    require_dog_wiring,
)
from daengs_evals.profile_fitness.rubric import (
    FAILURES,
    ITEM_MAX,
    PROMPT_VERSIONS,
    ProfileDiffVerdict,
    build_prompt,
    score_pair,
)


@pytest.fixture(scope="module")
def profiles() -> list[Profile]:
    return load_profiles(PROFILES_V1_PATH)


def test_fixture_file_loads(profiles: list[Profile]) -> None:
    assert profiles, "픽스처가 비어 있다"
    assert len(profiles_by_id(profiles)) == len(profiles), "profile_id 가 겹친다"


def test_every_dog_field_survives_the_planner_whitelist(profiles: list[Profile]) -> None:
    """왕복 검사 — 우리가 넣은 칸이 payload 까지 **줄지 않고** 간다.

    이 테스트가 이 패키지의 전제다. `_dog_context()` 가 화이트리스트라, 계약이 좁아지면
    예외가 아니라 **조용히 값이 사라진다.**
    """
    for profile in profiles:
        if profile.dog is None:
            assert planner._dog_context(profile.context()) is None
            continue
        resolved = planner._dog_context(profile.context())
        assert resolved == profile.dog, f"{profile.profile_id}: 칸이 떨어졌다 → {resolved}"


def test_breeds_are_names_the_app_can_actually_produce(profiles: list[Profile]) -> None:
    """견종은 `BREED_LABELS` 가 내는 한국어 이름이어야 한다.

    모르는 이름을 쓰면 운영에서는 나올 수 없는 프로필로 평가하게 된다. 앱은 아바타 id 를
    보내고 서버가 이 표로 번역하며, 표에 없는 id 는 견종을 아예 안 보낸다.
    """
    known = set(BREED_LABELS.values())
    for profile in profiles:
        breed = (profile.dog or {}).get("breed")
        if breed is not None:
            assert breed in known, f"{profile.profile_id}: 앱이 못 만드는 견종명 {breed!r}"


def test_each_axis_has_a_partner(profiles: list[Profile]) -> None:
    """축마다 arm 이 둘 이상 있어야 쌍을 만들 수 있다.

    `absent` 는 예외다 — 절제군은 다른 축의 arm 과 짝지어 쓴다.
    """
    counts: dict[str, int] = {}
    for profile in profiles:
        counts[profile.axis] = counts.get(profile.axis, 0) + 1
    lonely = [axis for axis, n in counts.items() if n < 2 and axis != "absent"]
    assert not lonely, f"짝이 없는 축: {lonely}"


def test_absent_profile_omits_the_dog_key_entirely(profiles: list[Profile]) -> None:
    """절제군은 "프로필이 비었다" 가 아니라 "프로필이 없다" 이다."""
    absent = profiles_by_id(profiles)["none"]
    assert "dog" not in absent.context()


def test_screening_arm_carries_its_context(profiles: list[Profile]) -> None:
    arm = profiles_by_id(profiles)["screened_abnormal"]
    context = arm.context()
    assert context["screening"] == {"verdict": "abnormal", "days_ago": 3}
    assert planner._screening_context(context) == {"verdict": "abnormal", "days_ago": 3}


def test_base_context_is_not_mutated(profiles: list[Profile]) -> None:
    base = {"location": {"lat": 37.5665, "lon": 126.978}}
    arm = profiles_by_id(profiles)["toy_puppy"]
    built = arm.context(base)
    assert built["location"] == base["location"]
    assert "dog" not in base, "호출자의 dict 를 건드리면 arm 이 서로 오염된다"


# ---------------------------------------------------------------------------
# 배선 게이트
# ---------------------------------------------------------------------------


def test_wiring_gate_passes_today() -> None:
    report = require_dog_wiring()
    assert report["life_has_dog"]
    assert report["general_has_dog"]
    assert "health_conditions" in report["round_trip_fields"]


def test_training_still_cannot_see_the_profile() -> None:
    """훈련이 프로필을 못 보는 것은 **계약의 사실**이지 모델의 실패가 아니다.

    그래서 훈련은 축 A 의 측정 대상이 아니고, 리포트가 "측정 대상 아님" 이라고 적는다.
    계약이 바뀌면 이 테스트가 깨져서 우리가 알게 된다 — 그때는 훈련도 재기 시작한다.
    """
    assert "dog" not in TrainingPayload.model_fields
    assert set(TrainingPayload.model_fields) == {"question"}


# ---------------------------------------------------------------------------
# 픽스처가 틀렸을 때 조용히 통과하지 않는다
# ---------------------------------------------------------------------------


def test_unknown_dog_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="DogContext"):
        Profile.model_validate(
            {
                "profile_id": "bogus",
                "label": "몸무게는 계약에 없다",
                "dog": {"breed": "말티즈", "weight_kg": 3.2},
                "visible_to": ["general"],
                "axis": "size_age",
            }
        )


def test_unknown_context_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="컨텍스트 키"):
        Profile.model_validate(
            {
                "profile_id": "bogus",
                "label": "산책 기록은 안 간다",
                "dog": {"breed": "말티즈"},
                "context_extra": {"walks": [1, 2, 3]},
                "visible_to": ["general"],
                "axis": "size_age",
            }
        )


def test_profile_without_a_dog_must_declare_itself_absent() -> None:
    with pytest.raises(ValidationError, match="absent"):
        Profile.model_validate(
            {
                "profile_id": "bogus",
                "label": "빈 arm",
                "dog": None,
                "visible_to": ["general"],
                "axis": "size_age",
            }
        )


def test_fixture_file_is_one_json_object_per_line() -> None:
    """사람이 손으로 고치는 파일이라 형식을 못 박는다."""
    for lineno, line in enumerate(PROFILES_V1_PATH.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            assert isinstance(json.loads(line), dict), f"{lineno}행이 객체가 아니다"


# ---------------------------------------------------------------------------
# 루브릭 진리표 — 이 패키지에서 점수의 뜻이 사는 곳
# ---------------------------------------------------------------------------


def verdict(**kwargs: object) -> ProfileDiffVerdict:
    """기본값이 '아무 차이 없음'인 판정. 테스트가 바꾸는 칸만 적게 한다."""
    base: dict[str, object] = {
        "differences": [],
        "profile_attributable": [],
        "unstated_facts": [],
        "stereotype_leaps": [],
        "changed": False,
        "change_justified": "none",
        "confidence": "high",
        "note": "",
    }
    base.update(kwargs)
    return ProfileDiffVerdict.model_validate(base)


def changed_by_profile(**kwargs: object) -> ProfileDiffVerdict:
    return verdict(
        differences=["산책 시간 권고가 다르다"],
        profile_attributable=["나이가 4개월과 150개월로 다르다"],
        changed=True,
        change_justified="profile",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("no_change", 0),
        ("changed_by_profile", 2),
        ("changed_unjustified", 1),
        ("changed_by_stereotype", 1),
    ],
)
def test_responsiveness_truth_table(case: str, expected: int) -> None:
    """달라져야 하는 질문에서, 무엇이 몇 점인가.

    핵심은 가운데 둘이다. **프로필로 설명되는 변화만 만점**이고, 근거 없이 달라진 것과
    견종 통념으로 갈린 것은 절반이다. 이걸 안 가르면 잡음과 편견이 개인화 점수가 된다.
    """
    cases = {
        "no_change": verdict(),
        "changed_by_profile": changed_by_profile(),
        "changed_unjustified": verdict(
            differences=["권고가 다르다"], changed=True, change_justified="unjustified"
        ),
        "changed_by_stereotype": verdict(
            differences=["권고가 다르다"],
            stereotype_leaps=["치와와는 겁이 많다고 단정"],
            changed=True,
            change_justified="stereotype",
        ),
    }
    score = score_pair(cases[case], kind="reactive")
    assert score.items["responsiveness"] == expected


def test_ignored_is_flagged_only_when_the_answer_should_have_changed() -> None:
    assert "ignored" in score_pair(verdict(), kind="reactive").failures
    assert "ignored" not in score_pair(verdict(), kind="invariant").failures


def test_invariance_truth_table() -> None:
    """같아야 하는 질문(법령·과태료)에서는 안 변한 것이 만점이다."""
    assert score_pair(verdict(), kind="invariant").items["invariance"] == 1
    changed = score_pair(changed_by_profile(), kind="invariant")
    assert changed.items["invariance"] == 0
    # 강아지와 무관해야 할 질문에 프로필을 끌어들인 것 — 이름이 붙는다
    assert "over_personalized" in changed.failures


def test_fabrication_is_scored_on_every_kind() -> None:
    """없는 사실을 말하는 것은 질문 종류와 무관하게 실패다. 가장 위험한 실패이기도 하다."""
    made_up = verdict(unstated_facts=["아토피가 있으시니 — 프로필에 없음"])
    for kind in ("reactive", "invariant", "probe"):
        score = score_pair(made_up, kind=kind)
        assert score.items["no_fabrication"] == 0
        assert "fabricated" in score.failures


def test_stereotype_is_flagged_even_when_the_change_is_otherwise_justified() -> None:
    """프로필로도 설명되지만 통념 도약이 섞였으면 그것도 적는다."""
    score = score_pair(
        changed_by_profile(stereotype_leaps=["소형견은 겁이 많다고 단정"]), kind="reactive"
    )
    assert score.items["responsiveness"] == 2
    assert "stereotype" in score.failures


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"is_control": True}, "control"),
        ({"in_scope": False}, "out_of_scope"),
    ],
)
def test_pairs_that_must_not_be_scored(kwargs: dict[str, bool], reason: str) -> None:
    """대조 쌍과 범위 밖 셀은 점수를 안 낸다.

    대조 쌍은 잡음 바닥을 재는 재료이지 성적이 아니고, 범위 밖은 **계약상 안 변하는 것**이라
    모델의 실패가 아니다. 둘을 점수에 섞으면 지표가 스스로를 희석한다.
    """
    score = score_pair(changed_by_profile(), kind="reactive", **kwargs)
    assert not score.scored
    assert score.skipped == reason
    assert score.items == {}


def test_low_confidence_abstains_instead_of_guessing() -> None:
    score = score_pair(changed_by_profile(confidence="low"), kind="reactive")
    assert score.skipped == "abstained"


def test_derived_scores_never_exceed_their_maximum() -> None:
    for kind in ("reactive", "invariant", "probe"):
        for case in (verdict(), changed_by_profile(), verdict(unstated_facts=["x"])):
            for item, value in score_pair(case, kind=kind).items.items():
                assert 0 <= value <= ITEM_MAX[item]


def test_every_declared_failure_can_actually_be_produced() -> None:
    """`FAILURES` 에 이름만 있고 파생이 절대 안 내는 실패가 있으면, 리포트에 영원히 0 이 뜬다.

    그건 "그 실패가 없다" 로 읽히지만 사실은 **재고 있지 않다** 는 뜻이다.
    """
    produced: set[str] = set()
    cases = (
        verdict(),
        changed_by_profile(),
        verdict(unstated_facts=["없는 사실"]),
        verdict(
            differences=["다름"],
            stereotype_leaps=["통념"],
            changed=True,
            change_justified="stereotype",
        ),
    )
    for kind in ("reactive", "invariant", "probe"):
        for case in cases:
            produced |= set(score_pair(case, kind=kind).failures)
    assert produced == set(FAILURES), f"안 나오는 실패: {set(FAILURES) - produced}"


# ---------------------------------------------------------------------------
# 판정기가 말이 안 되는 조합을 내면 점수로 옮기지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "broken",
    [
        {"changed": False, "change_justified": "profile"},
        {"changed": True, "change_justified": "none", "differences": ["다름"]},
        {"changed": True, "change_justified": "profile", "differences": ["다름"]},
        {"changed": True, "change_justified": "stereotype", "differences": ["다름"]},
    ],
)
def test_incoherent_verdicts_are_schema_failures(broken: dict[str, object]) -> None:
    """관찰과 판단이 어긋난 출력은 스키마 실패다 — 재시도 1회 대상이지 점수가 아니다."""
    with pytest.raises(ValidationError):
        verdict(**broken)


# ---------------------------------------------------------------------------
# 프롬프트 — 우리 골드가 새어 들어가지 않는다
# ---------------------------------------------------------------------------


def test_prompt_never_leaks_our_gold_labels() -> None:
    """`kind` 를 알려주면 판정기가 채점이 아니라 우리 라벨 확인을 하게 된다."""
    for variant in PROMPT_VERSIONS:
        prompt = build_prompt(
            question="산책은 하루에 얼마나 시켜야 해요?",
            profile_a={"breed": "치와와", "age_months": 4},
            profile_b={"breed": "골든리트리버", "age_months": 150},
            answer_a="짧게 여러 번 나눠 주세요.",
            answer_b="관절에 무리가 안 가게 평지로 다녀오세요.",
            variant=variant,
        )
        for leaked in ("reactive", "invariant", "sensitive_to", "probe"):
            assert leaked not in prompt, f"{variant}: 골드 라벨 {leaked!r} 가 프롬프트에 샜다"
        assert PROMPT_VERSIONS[variant] in prompt


def test_prompt_shows_the_profiles() -> None:
    """이게 기존 판정기와 갈리는 지점이다 — 프로필과 답의 관계를 재려면 보여줘야 한다."""
    prompt = build_prompt(
        question="사료 얼마나 줘야 해요?",
        profile_a=None,
        profile_b={"breed": "말티즈", "age_months": 84},
        answer_a="체중에 맞춰 주세요.",
        answer_b="7살 말티즈면 하루 두 번 나눠 주세요.",
        variant="A",
    )
    assert "(프로필 없음)" in prompt
    assert "말티즈" in prompt
