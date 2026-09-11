"""프로필 픽스처의 스키마 · 로더 · 배선 게이트.

    uv run python -m daengs_evals.profile_fitness.profiles check

**실제 `pets` 행이 필요 없다.** `active_dog_id → pets` 조회는 HTTP 경계에서만 일어나고
(`routers/assistant.py` `_with_dog_context`), 수집 러너는 그 아래 `orchestrator.run(context=...)`
를 직접 부른다 (`answer_quality/collect.py`). 그 사이의 `planner._dog_context()` 는
`context["dog"]` 를 **Mapping 으로 읽어 화이트리스트만 통과**시키므로, 픽스처는 평범한 dict 면
된다. Postgres 도 Redis 도 없이 돈다.

그래서 이 파일의 절반은 **그 전제가 아직 사실인지 확인하는 코드**다 (`require_dog_wiring`).
계약이 조용히 바뀌어 프로필이 프롬프트에 안 닿게 되면, 이 평가는 "안 변했다" 를 모델의 실패로
적게 된다 — 실제로는 배선이 끊어진 것인데. 그 사고를 먼저 막는다.

**픽스처는 합성이다.** 실제 사용자의 강아지에서 베끼지 않는다
(`evals/orchestration_router/README.md`: 실사용 로그를 골드에 넣지 않는다).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daengs_backend.orchestration.contracts import (
    CareLogContext,
    DogContext,
    ScreeningContext,
    ScreeningHistory,
)
from daengs_evals import EVALS_DIR

ASSETS_DIR = EVALS_DIR / "profile_fitness"
PROFILES_V1_PATH = ASSETS_DIR / "profiles_v1.jsonl"

#: 프로필이 실제로 닿는 능력. Life 는 breed · age_months 만 읽고 (`adapters/life.py`),
#: 돌봄 사실 셋(feeding_style · health_conditions · on_medication)은 general 전용이다
#: (`contracts.DogContext` 주석). 훈련은 `TrainingPayload = {question}` 이라 아예 안 닿는다.
Capability = Literal["life", "general"]

#: `_dog_context()` 가 통과시키는 칸. 여기 없는 키를 픽스처에 쓰면 조용히 사라진다.
DOG_FIELDS: frozenset[str] = frozenset(DogContext.model_fields)


class Profile(BaseModel):
    """프로필 하나 = 반사실 쌍의 arm 하나. 필드 순서가 파일의 열 순서다."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=40)
    #: `context["dog"]` 에 그대로 들어갈 dict. `None` 이면 프로필 없는 arm (절제군).
    dog: dict[str, Any] | None
    #: `dog` 밖의 컨텍스트 (`screening` 등). Life 전용 자리다.
    context_extra: dict[str, Any] = Field(default_factory=dict)
    #: 이 프로필이 실제로 도달하는 능력. 여기 없는 능력이 답한 셀은 **미측정**이다 —
    #: 계약상 당연한 무변화를 모델의 실패로 적지 않기 위해 픽스처가 스스로 말한다.
    visible_to: list[Capability]
    #: 무엇을 가르는 arm 인가 (size_age · breed_split · diseases · screening · absent).
    axis: str = Field(pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def dog_matches_the_contract(self) -> Profile:
        if self.dog is None:
            if self.axis != "absent":
                raise ValueError(f"{self.profile_id}: dog 가 없으면 axis 는 'absent' 여야 한다")
            return self
        unknown = set(self.dog) - DOG_FIELDS
        if unknown:
            raise ValueError(f"{self.profile_id}: DogContext 에 없는 칸 {sorted(unknown)}")
        # 계약이 실제로 받는지 확인한다. 여기서 걸리면 픽스처가 틀린 것이다.
        DogContext.model_validate(self.dog)
        return self

    @model_validator(mode="after")
    def context_extra_matches_the_contract(self) -> Profile:
        """`dog` 밖의 컨텍스트도 계약으로 검증한다.

        `_screening_context()` 도 화이트리스트라, 모양이 틀리면 예외가 아니라 **조용히 빠진다.**
        그러면 "피부 이력 arm 인데 이력이 안 갔다" 를 아무도 모른 채 무변화를 실패로 적게 된다.
        """
        allowed = {"screening", "screening_history", "care_log"}
        unknown = set(self.context_extra) - allowed
        if unknown:
            raise ValueError(f"{self.profile_id}: 다루지 않는 컨텍스트 키 {sorted(unknown)}")
        # 오늘 기록 (#344, 시험 ②). `_care_log_context()` 도 화이트리스트라 모양이 틀리면 조용히 빠진다 —
        # 그러면 "기록 있는 arm 인데 기록이 안 갔다" 를 무변화 실패로 적게 되므로 여기서 계약으로 막는다.
        care_log = self.context_extra.get("care_log")
        if care_log is not None:
            CareLogContext.model_validate(care_log)
        screening = self.context_extra.get("screening")
        if screening is not None:
            ScreeningContext.model_validate(screening)
        history = self.context_extra.get("screening_history")
        if history is not None:
            ScreeningHistory.model_validate(history)
        return self

    def context(self, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """이 프로필을 얹은 오케스트레이터 컨텍스트.

        `dog` 가 `None` 이면 **칸 자체를 넣지 않는다.** `None` 을 넣는 것과 안 넣는 것은
        `_dog_context()` 에게 같지만, 서버 스키마가 `extra="forbid"` 인 자리들과 모양을
        맞춰 둔다 — 절제군은 "프로필이 비었다" 가 아니라 "프로필이 없다" 이다.
        """
        ctx: dict[str, Any] = dict(base or {})
        if self.dog is not None:
            ctx["dog"] = dict(self.dog)
        ctx.update(self.context_extra)
        return ctx


def load_profiles(path: Path | str = PROFILES_V1_PATH) -> list[Profile]:
    """픽스처 파일을 읽는다. id 중복은 오류다."""
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    profiles = [Profile.model_validate(row) for row in rows]
    seen: set[str] = set()
    for profile in profiles:
        if profile.profile_id in seen:
            raise ValueError(f"profile_id 중복: {profile.profile_id}")
        seen.add(profile.profile_id)
    return profiles


def profiles_by_id(profiles: Iterable[Profile]) -> dict[str, Profile]:
    return {p.profile_id: p for p in profiles}


# ---------------------------------------------------------------------------
# 배선 게이트 — 프로필이 프롬프트까지 실제로 가는가
# ---------------------------------------------------------------------------


def require_dog_wiring() -> dict[str, Any]:
    """프로필이 능력 payload 까지 도달하는지 확인한다. 모델을 부르지 않는다.

    셋을 본다.

    ⓐ `LifePayload` · `GeneralPayload` 에 `dog` 칸이 아직 있는가
    ⓑ `_dog_context()` 가 우리가 넣은 값을 **줄이지 않고** 돌려주는가 (왕복)
    ⓒ 훈련은 여전히 프로필을 못 보는가 — 이건 실패가 아니라 **계약의 사실**이고,
      바뀌면 우리가 알아야 한다 (그때는 훈련도 측정 대상이 된다)

    `answer_quality/collect.py` 의 `require_screening_wiring()` 과 같은 자리 · 같은 이유다.
    """
    from daengs_backend.orchestration import planner
    from daengs_backend.orchestration.contracts import (
        GeneralPayload,
        LifePayload,
        TrainingPayload,
    )

    missing = [
        name
        for name, model in (("life", LifePayload), ("general", GeneralPayload))
        if "dog" not in model.model_fields
    ]
    if missing:
        raise RuntimeError(f"payload 에 dog 칸이 없다: {missing} — 프로필이 프롬프트에 안 간다")

    probe = {
        "breed": "치와와",
        "age_months": 4,
        "feeding_style": "scheduled",
        "health_conditions": "슬개골 탈구 2기",
        "on_medication": True,
    }
    resolved = planner._dog_context({"dog": dict(probe)})
    if resolved != probe:
        dropped = {k: v for k, v in probe.items() if resolved is None or k not in resolved}
        raise RuntimeError(f"_dog_context 가 칸을 떨궜다: {dropped} — 픽스처가 프롬프트에 안 간다")

    return {
        "life_has_dog": True,
        "general_has_dog": True,
        "round_trip_fields": sorted(probe),
        "training_sees_dog": "dog" in TrainingPayload.model_fields,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="프로필 픽스처 검증 + 배선 게이트")
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--profiles", default=str(PROFILES_V1_PATH))
    args = parser.parse_args(argv)

    wiring = require_dog_wiring()
    profiles = load_profiles(args.profiles)

    print(f"배선 OK — 왕복 통과 칸 {', '.join(wiring['round_trip_fields'])}")
    if wiring["training_sees_dog"]:
        print("  ⚠ TrainingPayload 에 dog 칸이 생겼다 — 훈련도 측정 대상이 된다")
    else:
        print("  훈련은 프로필을 못 본다 (TrainingPayload = {question}) — 계약대로다")

    print(f"\n프로필 {len(profiles)}개")
    for p in profiles:
        reach = ", ".join(p.visible_to)
        print(f"  {p.profile_id:20s} {p.label:14s} axis={p.axis:12s} → {reach}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
