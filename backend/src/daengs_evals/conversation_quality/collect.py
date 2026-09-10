"""동결 멀티턴 케이스를 오케스트레이터에 먹여 랩 파일로 모은다 (#401).

`answer_quality/collect.py` 와 같은 모양이다 — meta 행 하나 + 행마다 하나, 어댑터 모드
(`real` / `fake` / `fallback-only`)도 그 모듈의 것을 그대로 쓴다. 갈리는 것은 하나: 여기서는
`ConversationDriver` 이음매를 거쳐 보낸다. 지금은 `StatelessDriver` 하나뿐이지만, 이력
기제가 생겨 `SessionDriver` 가 더해져도 **이 파일은 안 고친다** — 드라이버가 `send()` 뒤에서
무엇을 하든 `run_collect` 는 모른다.

## 랩이 반드시 박아 두는 것 (카드 #401)

`before` 랩과 `after` 랩을 나중에 견주려면 다섯 가지가 안 움직여야 한다 — `LapHeader` 의
`cases_sha256` · `judge_model` · `prompt_version` · `anchor_set` · `adapter_mode`. 여섯째인
"판정 못 한 비율" 은 리포트가 계산할 값이라 여기서 만들지 않는다.

## 가짜 어댑터가 답한 행

`fallback-only` 모드는 General 만 진짜다. 그래서 행 하나가 가짜로 답했는지는 헤더의
`adapter_mode` 만으로 못 가린다 — 그 행에서 **어느 capability 가 뛰었는지**까지 봐야 한다.
`answered_by_fake_adapter` 를 행마다 남기는 이유가 그것이다: 리포트(Task 8)가 두 랩에서
같은 행을 빼려면 랩 파일 자체에 그 표시가 있어야 한다. 표시 없이 비율만 계산하면 다른
브랜치의 평가가 실제로 겪은 일(가짜 답 섞인 14% 대 뺀 5%)이 여기서도 조용히 반복된다.

## 이 이음매가 안 닿는 자리는 값으로 적는다

`route_plan` · `capability` · `general_decision` 은 드라이버가 내주는 payload 에 그 키가
있어야만 채워진다. `FakeDriver` 처럼 안 주는 이음매에서는 `drivers.NOT_REACHED` 값을
그대로 적는다 — 키를 생략하면 "몰라서 없음" 과 "값이 원래 없음" 이 파일에서 똑같아 보인다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from daengs_evals.conversation_quality import CASES_V1_PATH
from daengs_evals.conversation_quality.cases import ConversationCase, file_sha256
from daengs_evals.conversation_quality.drivers import NOT_REACHED, ConversationDriver

CARD = "#401"
AdapterMode = Literal["real", "fake", "fallback-only"]
ADAPTER_MODES: tuple[AdapterMode, ...] = ("real", "fake", "fallback-only")


class LapHeader(BaseModel):
    """랩 파일의 meta 행. 카드가 요구하는 다섯 개의 고정값 + 랩 라벨."""

    model_config = ConfigDict(extra="forbid")

    lap: str
    cases_sha256: str
    judge_model: str
    prompt_version: int
    anchor_set: str
    adapter_mode: str
    case_count: int = 0
    started_at: str = ""
    finished_at: str = ""


class TurnSnapshot(BaseModel):
    """평가 대상 턴 하나의 응답 시점 스냅샷. 나중에 DB 를 다시 읽지 않는다."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    turn_index: int
    query: str
    #: 이 드라이버가 실제로 실어 보낸 이전 턴. 드라이버의 `send()` payload 에서 그대로 옮긴다 —
    #: 하드코딩이 아니다. `PRIOR_TURNS_REACH_INFERENCE` 가 False 인 오늘은 모든 `ConversationDriver`
    #: 구현이 늘 빈 리스트를 돌려주고, 그것이 발견이라 생략하지 않고 명시적으로 적는다. 드라이버가
    #: 이 키를 안 주면(이음매가 안 닿으면) `NOT_REACHED`.
    prior_turns_supplied: Any
    #: 그 시점에 실제로 공급된 구조화 상태. 드라이버가 상태를 받을 자리(`context` 속성)가 있을
    #: 때만 `case.state_snapshot` 그대로 — 재조회가 아니다. 그 자리 자체가 없으면 `NOT_REACHED`:
    #: 실어 보낸 적 없는 값을 실었다고 적지 않는다.
    state_supplied: Any
    route_plan: Any
    capability: Any
    #: General 의 raw 구조화 결정(`kind`·`reason`). 평가 래퍼가 닿을 때만 채워진다.
    general_decision: Any
    status: str
    message: str
    #: 이 행이 가짜 어댑터의 답인가. `fallback-only` 처럼 능력마다 갈리는 모드가 있어
    #: 헤더의 `adapter_mode` 만으로는 못 가린다 — 리포트가 랩 두 개에서 같은 행을 빼려면
    #: 행마다 이 값이 있어야 한다.
    answered_by_fake_adapter: Any
    adapter_mode: str
    #: 케이스 자체의 출처·버전 — "모델·프롬프트·출처 버전" 중 출처 쪽. 모델·프롬프트
    #: 버전은 랩 전체에 하나씩이라 `LapHeader` 에 있다.
    case_source: str
    case_version: int


def _answered_by_fake_adapter(adapter_mode: Any, capability: Any) -> Any:
    """`build_adapters` 의 모드별 배선을 그대로 되짚는다. `answer_quality.collect.build_adapters` 참고."""
    if adapter_mode == NOT_REACHED or capability == NOT_REACHED:
        return NOT_REACHED
    if capability is None:
        return None  # 이 턴에서 어떤 capability 도 안 뛰었다 — 가짜 여부가 성립하지 않는다
    if adapter_mode == "real":
        return False
    if adapter_mode == "fake":
        return True
    if adapter_mode == "fallback-only":
        return str(capability).lower() != "general"
    return NOT_REACHED


def target_turn_row(
    case: ConversationCase, turn_index: int, driver: ConversationDriver
) -> TurnSnapshot:
    """대상 턴 하나를 드라이버에 보내고 스냅샷으로 남긴다.

    `driver.context` 를 이 호출 직전에 `case.state_snapshot` 으로 얹는다 — `send()` 자체는
    질의 하나만 받는 계약(오늘 런타임과 같다)이라, 상태는 그 계약 밖에서 실어야 한다.
    이 함수를 벗어나면 그 값을 다시 읽지 않는다: 스냅샷에 적는 `state_supplied` 는 지금
    이 줄이 실은 값이지, 나중에 드라이버나 DB 에서 되짚은 값이 아니다.

    드라이버가 `context` 속성 자체를 안 가지면(=상태를 받을 자리가 없으면) `state_supplied`
    도 `NOT_REACHED` 다 — 실어 보낸 적 없는 값을 실었다고 적으면 그것이야말로 "부재와 미측정이
    같아 보이는" 실패다. `prior_turns_supplied` 는 하드코딩하지 않는다: 드라이버가 `send()`
    payload 로 돌려준 값을 그대로 옮긴다 — `SessionDriver` 가 이력을 실으면 이 한 줄이 자동으로
    달라지고, `collect.py` 는 안 고쳐도 된다.
    """
    has_context_seam = hasattr(driver, "context")
    if has_context_seam:
        driver.context = dict(case.state_snapshot)
    query = case.turns[turn_index - 1].text
    payload = driver.send(query)
    adapter_mode = getattr(driver, "adapter_mode", NOT_REACHED)
    capability = payload.get("capability", NOT_REACHED)
    return TurnSnapshot(
        case_id=case.case_id,
        turn_index=turn_index,
        query=query,
        prior_turns_supplied=payload.get("prior_turns_supplied", NOT_REACHED),
        state_supplied=dict(case.state_snapshot) if has_context_seam else NOT_REACHED,
        route_plan=payload.get("route_plan", NOT_REACHED),
        capability=capability,
        general_decision=payload.get("general_decision", NOT_REACHED),
        status=str(payload.get("status", NOT_REACHED)),
        message=str(payload.get("message", NOT_REACHED)),
        answered_by_fake_adapter=_answered_by_fake_adapter(adapter_mode, capability),
        adapter_mode=str(adapter_mode),
        case_source=case.source,
        case_version=case.version,
    )


def lap_path(out_dir: Path, lap: str) -> Path:
    return out_dir / f"lap_{lap}.jsonl"


def run_collect(
    *,
    cases: Sequence[ConversationCase],
    driver: ConversationDriver,
    out_dir: Path,
    lap: str,
    cases_path: Path = CASES_V1_PATH,
    judge_model: str = "unspecified",
    prompt_version: int = 1,
    anchor_set: str = "dev",
) -> Path:
    """케이스마다 대상 턴을 드라이버에 보내고 랩 파일 하나로 쓴다.

    한 랩 안의 모든 케이스가 같은 `driver` 를 쓴다 — 어댑터 모드는 드라이버를 조립할 때
    한 번 정해지고, 케이스마다 바뀌는 것은 그 케이스의 상태뿐이다(`target_turn_row` 가 얹는다).
    """
    adapter_mode = getattr(driver, "adapter_mode", NOT_REACHED)
    rows = [
        target_turn_row(case, turn_index, driver)
        for case in cases
        for turn_index in case.target_turns
    ]
    header = LapHeader(
        lap=lap,
        cases_sha256=file_sha256(cases_path) if cases_path.exists() else NOT_REACHED,
        judge_model=judge_model,
        prompt_version=prompt_version,
        anchor_set=anchor_set,
        adapter_mode=str(adapter_mode),
        case_count=len(cases),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = lap_path(out_dir, lap)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "meta", **header.model_dump()}, ensure_ascii=False) + "\n")
        for row in rows:
            handle.write(
                json.dumps({"kind": "turn", **row.model_dump()}, ensure_ascii=False) + "\n"
            )
    return path


def load_lap(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") == "meta":
            meta = row
        else:
            rows.append(row)
    if meta is None:
        raise ValueError(f"{path} 에 meta 행이 없습니다")
    return meta, rows


# ---------------------------------------------------------------------------
# 진짜 오케스트레이터 조립 — 여기서만 provider 를 문다. 모듈을 import 만 해서는 안 닿는다.
# ---------------------------------------------------------------------------


def _general_recording_adapter(sink: dict[str, Any]) -> Any:
    """`GeneralCapabilityAdapter` 를 얇게 감싸 raw `kind`/`reason` 을 `sink` 에 남긴다.

    런타임 클래스는 안 고친다 — `generate` 콜러블만 갈아 끼운다(그 클래스가 이미 받는 생성자
    인자다). 어댑터는 raw 값을 `CapabilityResult.refusal.code` 로 뭉개기(고정 문구로 치환)
    전에 여기서 먼저 판독한다.
    """
    from daengs_backend.orchestration.adapters.general import (
        GeneralCapabilityAdapter,
        _generate_with_gemini,
        validate_general_answer,
    )

    async def generate(prompt: str) -> object:
        raw = await _generate_with_gemini(prompt)
        answer = validate_general_answer(raw)
        sink["decision"] = {"kind": answer.kind, "reason": answer.reason} if answer else None
        return raw

    return GeneralCapabilityAdapter(generate=generate)


def build_adapters(mode: AdapterMode, general_sink: dict[str, Any]) -> Mapping[Any, Any] | None:
    """`answer_quality.collect.build_adapters` 와 같은 갈래. General 자리만 항상 감싼다."""
    from daengs_backend.orchestration.adapters import (
        LifeCapabilityAdapter,
        PlaceCapabilityAdapter,
        TrainingCapabilityAdapter,
        WalkCapabilityAdapter,
    )
    from daengs_backend.orchestration.contracts import CapabilityName
    from daengs_evals.orchestrator_comparison.runner import _fake_adapters

    if mode == "real":
        return {
            CapabilityName.TRAINING: TrainingCapabilityAdapter(),
            CapabilityName.LIFE: LifeCapabilityAdapter(),
            CapabilityName.WALK: WalkCapabilityAdapter(),
            CapabilityName.PLACE: PlaceCapabilityAdapter(),
            CapabilityName.GENERAL: _general_recording_adapter(general_sink),
        }
    if mode == "fake":
        return _fake_adapters()
    if mode == "fallback-only":
        adapters: dict[Any, Any] = dict(_fake_adapters())
        adapters[CapabilityName.GENERAL] = _general_recording_adapter(general_sink)
        return adapters
    raise ValueError(f"adapter_mode 는 {'|'.join(ADAPTER_MODES)} 입니다: {mode!r}")


def build_stateless_driver(mode: AdapterMode) -> Any:
    """운영과 같은 조립(`AssistantOrchestrationService` + 의미 라우터)에 `StatelessDriver` 를 문다.

    자동 테스트는 이 함수를 부르지 않는다 — `FakeDriver` 만 쓴다. 이 함수를 부르면 그 순간
    `GEMINI_API_KEY` 와 실제 provider 클라이언트가 필요해진다(모듈을 import 하는 것만으로는
    필요 없다 — 그래서 이 함수 안에서만 늦게 import 한다).
    """
    from daengs_backend.orchestration.contracts import PrincipalContext
    from daengs_backend.orchestration.semantic import GeminiSemanticRouter
    from daengs_backend.orchestration.service import AssistantOrchestrationService
    from daengs_evals.conversation_quality.drivers import StatelessDriver
    from daengs_evals.orchestrator_comparison.runner import Meter
    from daengs_evals.orchestrator_comparison.runner_v2 import (
        RecordingEngine,
        _metered_semantic_generate,
    )

    plan_sink: dict[str, Any] = {"plan": None}
    general_sink: dict[str, Any] = {"decision": None}
    orchestrator = AssistantOrchestrationService(
        engine=RecordingEngine(build_adapters(mode, general_sink), plan_sink),  # type: ignore[arg-type]
        semantic_router=GeminiSemanticRouter(generate=_metered_semantic_generate(Meter())),
    )
    principal = PrincipalContext(subject="conversation-quality-runner", kind="ADMIN")
    return StatelessDriver(
        orchestrator,
        principal=principal,
        adapter_mode=mode,
        plan_sink=plan_sink,
        general_sink=general_sink,
    )
