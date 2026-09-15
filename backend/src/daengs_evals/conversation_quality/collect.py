"""동결 멀티턴 케이스를 오케스트레이터에 먹여 랩 파일로 모은다 (#401).

`answer_quality/collect.py` 와 같은 모양이다 — meta 행 하나 + 행마다 하나, 어댑터 모드
(`real` / `fake` / `fallback-only`)도 그 모듈의 것을 그대로 쓴다. 갈리는 것은 하나: 여기서는
`ConversationDriver` 이음매를 거쳐 보낸다. `#416` 이 Turn Resolver 를 놓아 `SessionDriver`
가 더해졌을 때 이 파일이 새로 얻은 것은 세 가지다 — `build_session_driver`(
`build_stateless_driver` 와 같은 조립에 클래스만 다르게 문다), `LapHeader.driver`(어느
드라이버로 모았는지를 헤더에 남긴다), 그리고 `run_collect` 안 두 줄(`driver_kind` 를 읽어
그 헤더 필드에 적는 것 — `adapter_mode` 를 읽는 자리와 같다). **`target_turn_row` 는 안
고쳤고, `run_collect` 도 그 두 줄 밖에서는 안 고쳤다** — 두 함수 다 여전히 드라이버가
`send()` 뒤에서 무엇을 하는지 모른다. 이것은 스펙 §7 이 말하는 "이 파일들을 고쳐야 한다면
이음매가 샌 것" 의 예외가 아니라 의도한 초과다 — 어느 드라이버로 모았는지를 **기록**하는
것과 드라이버 종류에 따라 **분기**하는 것은 다른 일이고, 후자만 이음매 누수다. 이 파일은
전자만 한다; 분기는 `report.py` 가 한다(그쪽 docstring 참고).

## 랩이 반드시 박아 두는 것 (카드 #401)

`before` 랩과 `after` 랩을 나중에 견주려면 여섯 가지가 안 움직여야 한다 — `LapHeader` 의
`cases_sha256` · `judge_model` · `prompt_version` · `anchor_set` · `adapter_mode` ·
`general_fallback`. 일곱째인 "판정 못 한 비율" 은 리포트가 계산할 값이라 여기서 만들지 않는다.

## `general_fallback` 도 값이 아니라 고정값이다

실측(2026-09-10) — 프로세스 기본값(`settings.general_fallback = False`)으로 `--adapter-mode
real` 을 돌리면 라우터가 전문 capability 를 하나도 못 고른 턴마다 General 이 아예 안
조립되고 빈 계획 그대로 `FAILED` 로 끝난다. `DAENGS_GENERAL_FALLBACK=true`(서버 값,
`docs/decisions.md:3252`)로 돌리면 그 자리에 Gemini 생성 답변이 붙는다 — 같은 케이스,
같은 모델, 같은 프롬프트인데 이 스위치 하나로 답 자체가 다른 종류가 된다. 다른 다섯 핀은
전부 그대로라 이 차이가 헤더에 안 남으면 `render_compare` 가 조용히 통과시킨다 — 그래서
`LapHeader.general_fallback` 에 수집 시점에 실제로 켜져 있었는지를 적는다.
`run_collect` 는 이 값을 함수 안에서 늦게 읽는다 — 모듈 최상단에서 읽으면 이 패키지를
import 만 해도 `daengs_backend.config.settings`(DB 접속 정보 · 암호화 키)가 필요해지고,
`test_every_module_in_the_package_imports_without_backend_settings` 가 그것을 막는다.

**`judge_model` · `prompt_version` · `anchor_set` 은 여기서는 아직 계획일 뿐이다.** `collect`
시점에는 judge 를 부르지 않았으니 이 세 값은 "이 랩을 나중에 이 조건으로 판정할 생각이다"
라는 의도이지, 실제로 그 조건으로 판정됐다는 보증이 아니다. 실제 판정은 나중에 `run_score`
가 별도로 `JudgeHeader` 에 같은 이름의 세 값을 적는데, 그때 `--judge-model` 을 다르게 넘기면
둘이 어긋난다 — 이 파일은 그 어긋남을 만들지도 막지도 않는다. 랩과 판정 파일이 서로 맞는
전제로 만들어졌는지는 `report.summarize` 가 두 헤더를 실제로 대조해서 확인한다.

## 가짜 어댑터가 답한 행

`fallback-only` 모드는 General 만 진짜다. 그래서 행 하나가 가짜로 답했는지는 헤더의
`adapter_mode` 만으로 못 가린다 — 그 행에서 **어느 capability 가 뛰었는지**까지 봐야 한다.
`answered_by_fake_adapter` 를 행마다 남기는 이유가 그것이다: 리포트(`report.py`)가 두 랩에서
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from daengs_evals.conversation_quality import CASES_V1_PATH
from daengs_evals.conversation_quality.cases import ConversationCase, file_sha256
from daengs_evals.conversation_quality.drivers import NOT_REACHED, ConversationDriver

AdapterMode = Literal["real", "fake", "fallback-only"]
#: `build_adapters` 가 실제로 조립할 수 있는 값 — 이 셋은 전부 **진짜 오케스트레이터**를
#: 돌린다("fake" 도 오케스트레이터는 진짜고 capability 어댑터만 가짜다).
ADAPTER_MODES: tuple[AdapterMode, ...] = ("real", "fake", "fallback-only")
#: 랩 헤더 `adapter_mode` 에 나타날 수 있는 값 전부. `ADAPTER_MODES` 에 `FakeDriver` 전용
#: 값을 더한 것 — `FakeDriver` 는 오케스트레이터 자체를 안 돌리므로 `build_adapters` 의
#: 어휘에는 없지만, 헤더를 읽는 사람이 "이 문자열이 낯설면 오타다"라고 판단할 수 있으려면
#: 어딘가 한곳에 전체 어휘가 있어야 한다.
ALL_ADAPTER_MODES: tuple[str, ...] = (*ADAPTER_MODES, "fake-driver")


class LapHeader(BaseModel):
    """랩 파일의 meta 행. 카드가 요구하는 여섯 개의 고정값 + 랩 라벨."""

    model_config = ConfigDict(extra="forbid")

    lap: str
    cases_sha256: str
    judge_model: str
    prompt_version: int
    anchor_set: str
    adapter_mode: str
    #: 수집 시점에 `settings.general_fallback` 이 실제로 켜져 있었는지 — 모듈
    #: docstring "`general_fallback` 도 값이 아니라 고정값이다" 참고.
    general_fallback: bool
    #: 이 랩을 모은 드라이버(`drivers.StatelessDriver.driver_kind` /
    #: `SessionDriver.driver_kind`). `adapter_mode` 와 같은 자리 — `run_collect` 가
    #: `getattr(driver, "driver_kind", ...)` 로 읽는다. 기본값이 `"stateless"` 인 것은
    #: 이 칸이 생기기 전에 얼어붙은 랩(그리고 `FakeDriver` 처럼 이 속성이 아예 없는
    #: 드라이버 — 그쪽도 `prior_turns_supplied` 가 늘 비어 있어 `stateless` 와 같은
    #: 사실이다)이 그 기본값으로도 정직하기 때문이다. `report.render_compare` 가 이
    #: 값으로 `context_continuity`·`repair_success` 를 0 으로 못박을지 정한다 (R25).
    driver: str = "stateless"
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
    #: 되묻기의 구조화된 흔적 (#415) — `question` · `missing` · `missing_axes`.
    #: 드라이버가 이 키를 안 주면(이음매가 안 닿으면) `NOT_REACHED`, 되묻지 않았으면 `None`.
    #: **기본값이 있는 것은 before 랩 때문이다** — 이 필드가 생기기 전에 얼어붙은 파일을
    #: 다시 돌리지 않고 읽어야 `compare` 가 성립한다.
    clarify: Any = None
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
    """`build_adapters` 의 모드별 배선을 그대로 되짚는다. `answer_quality.collect.build_adapters` 참고.

    `"fake-driver"`(`drivers.FakeDriver`)는 capability 를 안 물어도 무조건 `True` 다 —
    이 드라이버는 오케스트레이터 자체를 안 돌리므로 그 행에 실제 응답이라 부를 것이 처음부터
    없다. capability 가 `NOT_REACHED` 라고 여기서 `NOT_REACHED` 를 돌리면(과거의 결함이
    그랬다) 100% 합성 랩이 리포트에서 "전부 측정됨"으로 보인다 — 진짜 응답이 하나도 없는
    랩이 가장 신뢰도 높은 랩처럼 읽히는 것이다.
    """
    if adapter_mode == "fake-driver":
        return True
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


def _replay_intermediate_turn(
    case: ConversationCase, user_turn_index: int, driver: ConversationDriver
) -> None:
    """대상이 아닌 user 턴도 보낸다 — 이력을 실제로 만드는 것은 이 호출이다 (#446).

    응답은 버린다: 이 턴은 평가 대상이 아니라, 그다음 대상 턴이 볼 이력을 실제로
    쌓기 위한 것뿐이다. `context` 를 얹는 것까지 `target_turn_row` 와 같다 — 두 함수가
    갈리는 것은 결과를 `TurnSnapshot` 으로 남기느냐뿐이다.
    """
    if hasattr(driver, "context"):
        driver.context = dict(case.state_snapshot)
    driver.send(case.turns[user_turn_index].text)


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
        clarify=payload.get("clarify", NOT_REACHED),
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

    한 랩 안의 모든 케이스가 같은 `driver` **인스턴스**를 쓴다 — 어댑터 모드는 드라이버를
    조립할 때 한 번 정해지고, 케이스마다 바뀌는 것은 그 케이스의 상태뿐이다(`target_turn_row`
    가 얹는다). 인스턴스를 같이 쓰는 것과 **상태를 같이 쓰는 것**은 다른 일이다 — 케이스마다
    `getattr(driver, "reset_for_new_case", None)` 가 있으면 그것을 불러 케이스 경계를 긋는다.
    실측(#446) — `SessionDriver` 는 `_history` 를 자기 인스턴스에 쌓기만 할 뿐 "케이스가
    바뀌었다"를 모른다, 그래서 이 호출 없이 여러 케이스를 먹이면 앞 케이스의 이력이 뒤
    케이스로 새어(『심장사상충 질문』이 무관한 『구토 질문』을 이어받아 되묻는 식으로) 랩
    전체의 `prior_turns_supplied` 가 케이스 경계 없이 그냥 누적된다. `FakeDriver` ·
    `StatelessDriver` 는 이 메서드가 없다 — 둘 다 케이스를 넘어 쌓는 상태가 원래 없으므로
    없어도 정직하다(`getattr` 의 기본값 `None` 이 그 경우를 그냥 지나친다). **드라이버
    종류로 분기하지 않는다** — 이 함수는 여전히 `reset_for_new_case` 가 있는지만 보지,
    어떤 클래스인지는 모른다.

    `settings.general_fallback` 은 여기서 늦게 읽는다(함수 안, 이 줄에서만) — 모듈
    최상단에서 읽으면 이 패키지를 import 만 해도 backend 설정이 필요해진다. `FakeDriver`
    처럼 오케스트레이터를 안 돌리는 이음매를 써도 이 값은 실제 프로세스 설정 그대로
    적힌다 — 무엇을 돌렸는지와 무관하게 "그 순간 스위치가 어느 쪽이었는지"는 항상 사실이다.

    ## 대상 턴 앞이 아니라 케이스 전체를 순서대로 보낸다 (#446)

    `case.turns` 는 인덱스 0 부터 user/assistant 가 번갈아 나온다 — user 턴 *i* 의 응답이
    assistant 턴 *i+1* 이다. 이 함수는 `max(case.target_turns)` 까지 **모든** user 턴을
    순서대로 드라이버에 보낸다: *i+1* 이 `target_turns` 에 있으면 그 응답을 행으로 남기고,
    없으면 보내기만 하고 버린다(`_replay_intermediate_turn`). 대상 바로 앞 user 턴만
    보내던 이전 코드는 대상들 **사이**의 user 턴을 통째로 건너뛰어, 그 턴이 만들었어야 할
    이력이 이후 대상에 전혀 안 실렸다 — 대명사("그거")가 가리키는 앞 턴이 사라지거나,
    관찰 케이스 중간 턴이 이력에서 빠지는 식으로.

    이 재생은 **이력을 나르는 드라이버에만** 적용한다 — `reset_for_new_case` 가 있는지로
    가른다(케이스 경계를 긋는 자리와 같은 신호: 이 메서드가 있다는 것 자체가 "이 드라이버는
    호출 사이에 상태를 쌓는다"는 뜻이다). `StatelessDriver`·`FakeDriver` 는 `send()` 가
    매번 독립이라(#446 스펙 §7) 중간 턴을 더 보내도 다음 대상이 보는 것이 하나도 안
    바뀐다 — 그런데 `StatelessDriver` 가 무는 `real` 조립에서는 호출 하나하나가 유료
    모델 호출이라, 아무것도 안 바뀌는 호출을 보태면 비용만 는다. 그래서 이 두 드라이버는
    옛 경로(대상 바로 앞 user 턴만) 그대로 둔다 — **이미 모은 `before` 랩(`--driver
    stateless`)이 보낸 것과 이 코드로 다시 모을 `--driver stateless` 랩이 보내는 것이
    완전히 같다**는 뜻이다. 어느 경로든 **행은 대상 턴 개수만큼만** 나온다 — 재생은
    무엇을 보내는지만 바꾸고, 무엇을 기록하는지는 안 바꾼다.
    """
    from daengs_backend.config import settings as backend_settings

    adapter_mode = getattr(driver, "adapter_mode", NOT_REACHED)
    driver_kind = str(getattr(driver, "driver_kind", "stateless"))
    general_fallback = backend_settings.general_fallback
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    reset_for_new_case = getattr(driver, "reset_for_new_case", None)
    rows: list[TurnSnapshot] = []
    for case in cases:
        if reset_for_new_case is not None:
            reset_for_new_case()
        if reset_for_new_case is not None:
            # 이력을 나르는 드라이버만 케이스 전체를 순서대로 재생한다 (위 docstring 절 참고).
            max_target = max(case.target_turns)
            for user_turn_index in range(0, max_target, 2):
                assistant_index = user_turn_index + 1
                if assistant_index in case.target_turns:
                    rows.append(target_turn_row(case, assistant_index, driver))
                else:
                    _replay_intermediate_turn(case, user_turn_index, driver)
        else:
            for turn_index in case.target_turns:
                rows.append(target_turn_row(case, turn_index, driver))
    finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    header = LapHeader(
        lap=lap,
        cases_sha256=file_sha256(cases_path) if cases_path.exists() else NOT_REACHED,
        judge_model=judge_model,
        prompt_version=prompt_version,
        anchor_set=anchor_set,
        adapter_mode=str(adapter_mode),
        general_fallback=general_fallback,
        driver=driver_kind,
        case_count=len(cases),
        started_at=started_at,
        finished_at=finished_at,
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
        SkinCapabilityAdapter,
        TrainingCapabilityAdapter,
        VetContactCapabilityAdapter,
        WalkCapabilityAdapter,
    )
    from daengs_backend.orchestration.contracts import CapabilityName
    from daengs_evals.eval_harness import fake_adapters as _fake_adapters

    if mode == "real":
        return {
            CapabilityName.TRAINING: TrainingCapabilityAdapter(),
            CapabilityName.LIFE: LifeCapabilityAdapter(),
            CapabilityName.WALK: WalkCapabilityAdapter(),
            CapabilityName.PLACE: PlaceCapabilityAdapter(),
            CapabilityName.GENERAL: _general_recording_adapter(general_sink),
            # 응급 컨트롤 케이스(cq_emergency_immediate_01)가 실측으로 잡은 구멍 (#446).
            # 결정론적 게이트로만 들어오고 모델을 안 태우므로 real 모드에서도 recording
            # 래퍼가 필요 없다 — 진짜 어댑터를 그대로 문다.
            CapabilityName.VET_CONTACT: VetContactCapabilityAdapter(),
            # 판정 기록이 붙은 `skin` 신호로만 들어온다 (D-079). 하네스는 기록 id 를 안
            # 보내므로 실제로는 안 돈다 — 운영 엔진과 같은 등록을 두는 것뿐이다.
            CapabilityName.SKIN: SkinCapabilityAdapter(),
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
    from daengs_evals.eval_harness import Meter, RecordingEngine, metered_semantic_generate

    plan_sink: dict[str, Any] = {"plan": None}
    general_sink: dict[str, Any] = {"decision": None}
    orchestrator = AssistantOrchestrationService(
        engine=RecordingEngine(build_adapters(mode, general_sink), plan_sink),  # type: ignore[arg-type]
        semantic_router=GeminiSemanticRouter(generate=metered_semantic_generate(Meter())),
    )
    principal = PrincipalContext(subject="conversation-quality-runner", kind="ADMIN")
    return StatelessDriver(
        orchestrator,
        principal=principal,
        adapter_mode=mode,
        plan_sink=plan_sink,
        general_sink=general_sink,
    )


def build_session_driver(mode: AdapterMode) -> Any:
    """`build_stateless_driver` 와 같은 조립에 `SessionDriver` 를 문다 (#416).

    갈리는 것은 드라이버 클래스 하나뿐이다 — 오케스트레이터 · 어댑터 조립은 그대로다,
    그래야 `PRIOR_TURNS_REACH_INFERENCE` 가 뒤집힌 것 말고는 두 랩이 같은 조건에서
    갈린다. `run_collect` · `target_turn_row` 는 이 함수를 몰라도 된다 — 어떤 드라이버를
    받든 `send()` 계약만 지키면 그만이다.
    """
    from daengs_backend.orchestration.contracts import PrincipalContext
    from daengs_backend.orchestration.semantic import GeminiSemanticRouter
    from daengs_backend.orchestration.service import AssistantOrchestrationService
    from daengs_evals.conversation_quality.drivers import SessionDriver
    from daengs_evals.eval_harness import Meter, RecordingEngine, metered_semantic_generate

    plan_sink: dict[str, Any] = {"plan": None}
    general_sink: dict[str, Any] = {"decision": None}
    orchestrator = AssistantOrchestrationService(
        engine=RecordingEngine(build_adapters(mode, general_sink), plan_sink),  # type: ignore[arg-type]
        semantic_router=GeminiSemanticRouter(generate=metered_semantic_generate(Meter())),
    )
    principal = PrincipalContext(subject="conversation-quality-runner", kind="ADMIN")
    return SessionDriver(
        orchestrator,
        principal=principal,
        adapter_mode=mode,
        plan_sink=plan_sink,
        general_sink=general_sink,
    )
