"""LangSmith 트레이싱 배선. **기본은 꺼져 있습니다.**

D-037 은 일반 관측에 질문 원문을 남기지 않습니다. 이 모듈은 그 결정을 뒤집지 않고
**명시적 옵트인 예외**를 만듭니다 — 환경 변수가 없으면 아무것도 안 나가고, 켜야만
나갑니다. 예외가 필요한 이유는 실사용 민원이 실사용 질문에서 나오기 때문입니다.
동결된 골드셋으로는 "무엇이 잘못되고 있는지 모르는 상태"를 못 좁힙니다.

**켜면 질문 원문·검색 청크·프롬프트·답변이 나갑니다. 그것이 목적입니다.**
`hide_inputs` 를 쓰지 않는 이유가 그것입니다 — 그건 입력을 통째로 `{}` 로 만들어
(langsmith `client.py` `_hide_run_inputs`) 트레이스의 진단 가치를 0 으로 만듭니다.
지우는 것은 **진단에 안 쓰이는 신원·위치**뿐입니다 (`scrub_payload`).

## 어디로 보내나 — 기본은 **우리 GCP** 입니다

`langsmith` 는 **계측 SDK 로만** 씁니다. 목적지는 `LANGSMITH_TRACING_MODE` 가 정하고,
운영값은 `otel` 입니다 (D-054):

    backend ──OTLP──▶ otel-collector (compose) ──▶ Cloud Trace (우리 GCP 프로젝트)

그래서 **LangSmith 라는 회사에는 아무것도 안 갑니다.** 새 벤더도, 새 계정도, API 키도
없습니다 — `_validate_api_key_if_hosted` 가 `tracing_mode == "otel"` 이면 키 요구를
건너뜁니다("no LangSmith REST calls are made"). 이미 쓰는 GCP 프로젝트라 처리방침의
제3자 목록도 그대로입니다.

마스킹은 두 모드에 똑같이 걸립니다. OTel exporter 가 받는 것이
`serialize_run_dict("post", run_create)` 인데 그 `run_create` 는 이미 `_run_transform`
(=`_hide_run_*`)을 지난 것이기 때문입니다.

`mode=langsmith` 로 두면 그때는 진짜로 제3자에게 나갑니다. 기동 로그가 그것을 경고합니다.

## 켜는 법

`LANGSMITH_TRACING=true` + `LANGSMITH_TRACING_MODE=otel` +
`OTEL_EXPORTER_OTLP_ENDPOINT` 를 **프로세스 환경 변수**로 줍니다.

⚠ **`backend/.env` 에 적으면 안 켜집니다.** 그 파일은 pydantic-settings 가 자기
`Settings` 로만 읽고 `os.environ` 에 넣지 않는데, langsmith 는 `os.environ` 만
봅니다. `docker-compose.yml` 의 `environment:` 가 그 자리입니다.

그래서 이 모듈은 `DAENGS_` 설정을 새로 만들지 않습니다. 만들면 "우리 설정은 켜졌는데
SDK 는 꺼져 있는" 상태가 생기고, 그건 아무 데도 안 찍히면서 켜진 줄 알게 되는 상태입니다.
권위는 `LANGSMITH_*` 한 곳입니다.

## 신고 → 트레이스

`request_trace` 가 요청의 **루트 런** `run_id` 를 `request_id` 로 못박고, langsmith 의
`get_otel_trace_id_from_uuid` 가 `int(uuid.hex, 16)` 이라 **Cloud Trace 의 trace id 가
`request_id` 의 hex 와 글자 그대로 같습니다.** 신고 한 건에서 그 요청의 라우팅·검색
청크·프롬프트로 바로 갑니다.

## 루트는 서비스가, 그래프는 자식이

루트 런은 **오케스트레이션 서비스**(`orchestration/service.py` 의 `assistant_query`)가
만듭니다. 그래프(`OrchestrationEngine.run`)는 그 아래 자식 `orchestration_engine` 입니다.
(D-072 이전에는 이제는 지워진 `orchestration/agent/service.py` 의 `assistant_query_agent`
가 같은 자리에서 두 번째 루트 런 이름이었습니다.)

그래프를 루트로 두면 안 되는 이유: 시맨틱 라우터의 Gemini 호출이 그래프 **앞**에서
돕니다. 라우터가 루트보다 먼저 끝나면 그 LLM 런은 어느 트레이스에도 못 붙고, D-054 가
가르려는 세 질문 중 "라우터가 능력을 잘못 골랐나"가 트레이스에서 사라집니다.

자식은 `run_id` 를 **갖지 않습니다** (`trace_config(root=False)`). 루트와 같은
`request_id` 를 자식에도 주면 같은 id 의 런이 둘이 되어 하나가 다른 하나를 덮습니다 —
에이전트 경로가 정확히 그 상태였습니다 (선택 루프와 엔진이 둘 다 `run_id=request_id`).

## 왜 주입점이 하나인가

langsmith 클라이언트를 우리 것으로 바꿔야 마스킹이 걸립니다. 그런데 트레이스를 만드는
경로는 둘입니다 — LangGraph 자동 계측(`langchain_core.tracers.langchain.get_client`)과
`@traceable`(`langsmith.run_trees.RunTree.client`). **둘 다 폴백이
`run_trees.get_cached_client()` 라는 하나의 모듈 전역입니다.** 그래서 기동 때 그 전역을
우리 클라이언트로 한 번 채워 두면 양쪽이 함께 덮입니다.

**단, 먼저 만든 쪽이 이깁니다.** `get_cached_client(**kwargs)` 는 전역이 비었을 때만
kwargs 를 씁니다. 다른 코드가 먼저 클라이언트를 만들면 마스킹 없는 것이 굳어지므로,
`configure_tracing()` 은 lifespan 의 **맨 앞**에서 불러야 하고, 늦었으면 조용히 넘어가지
않고 경고합니다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

LOGGER = logging.getLogger(__name__)

#: 신원 필드에 대신 넣는 값. 빈 문자열이 아니라 표식인 이유는 "안 왔다" 와
#: "지웠다" 가 트레이스에서 구분돼야 하기 때문입니다.
REDACTED = "[redacted]"

#: 좌표를 남기는 소수점 자리. 2 자리는 약 1km 입니다.
#:
#: 0 으로 지우지 않는 이유: Walk·Place 가 좌표를 **받기는 했는지**, 한국 안의 값이었는지가
#: 디버깅 대상입니다. 그대로 두지 않는 이유: 그건 사는 곳입니다.
#:
#: ⚠ **이 값으로 Place 결과를 재현하지 마세요.** 트레이스의 좌표는 Place 가 실제로 검색한
#: 좌표가 아닙니다. 재현이 필요하면 반올림 전 값을 가진 곳은 우리 DB 뿐입니다.
COORDINATE_PRECISION = 2

#: 통째로 지우는 키.
#:
#: **해시로 남기지 않는 이유**: 남길 이유가 없습니다. "이 사람이 또 신고했나" 를 묻는 길은
#: `request_id` → `chat_turns` → `chat_sessions` 로 **우리 DB 안에** 이미 있습니다.
#: 관측 저장소로 식별자를 복제해서 얻는 것이 0 인데 복제하면, 그건 그냥 유출 표면입니다.
#:
#: `active_dog_id` 가 여기 있는 이유: **그것도 사람을 가리킵니다.** 반려견 UUID 는
#: 회원당 안정적이라, 트레이스를 사람 단위로 묶는 데 `subject` 와 똑같이 쓸 수 있습니다.
#: `subject` 만 지우고 이것을 두면 지운 의미가 없습니다. 답에 필요한 반려견 사실
#: (견종·월령)은 `context.dog` 에 따로 있고 그쪽은 그대로 남습니다 — 그건 답의
#: 입력이지 신원이 아닙니다.
_IDENTITY_KEYS = frozenset({"subject", "active_dog_id"})

#: 반올림하는 키. `WalkPayload` · `PlacePayload` · `context.location` 이 같은 이름을 씁니다.
_COORDINATE_KEYS = frozenset({"lat", "lon"})


def scrub_payload(value: Any) -> Any:
    """트레이스로 나가기 직전의 값에서 신원·정밀 위치를 걷어냅니다.

    **질문·답변·검색 청크·프롬프트는 건드리지 않습니다.** 그것을 보려고 켜는 것입니다.

    langsmith 가 이미 JSON 으로 만든 뒤에 부르므로 (`client._hide_run_inputs`) 여기 오는
    것은 dict·list·스칼라뿐입니다. 그래도 재귀는 방어적으로 씁니다 — `OrchestratorState`
    가 중첩이고, 나중에 필드가 한 겹 더 들어가도 규칙이 따라가야 합니다.
    """
    if isinstance(value, dict):
        scrubbed: dict[Any, Any] = {}
        for key, item in value.items():
            name = str(key).lower()
            if name in _IDENTITY_KEYS and isinstance(item, str):
                scrubbed[key] = REDACTED
            elif name in _COORDINATE_KEYS and isinstance(item, (int, float)) and not isinstance(
                item, bool
            ):
                scrubbed[key] = round(float(item), COORDINATE_PRECISION)
            else:
                scrubbed[key] = scrub_payload(item)
        return scrubbed
    if isinstance(value, list):
        return [scrub_payload(item) for item in value]
    return value


def tracing_enabled() -> bool:
    """`LANGSMITH_TRACING` 이 켜져 있나. SDK 에게 직접 묻습니다."""
    from langsmith import utils as ls_utils

    return bool(ls_utils.tracing_is_enabled())


def tracing_mode() -> str:
    """트레이스가 **어디로** 가나 — `langsmith` · `otel` · `hybrid`.

    `LANGSMITH_TRACING_MODE` 를 SDK 와 같은 방식으로 읽습니다 (`LANGSMITH_`/`LANGCHAIN_`
    두 접두사). 우리 설정을 따로 만들지 않는 이유는 `tracing_enabled()` 와 같습니다 —
    권위가 둘이면 어긋납니다.

    운영 기본값은 `otel` 입니다 (D-054). 트레이스는 우리 GCP 프로젝트의 Cloud Trace 로
    가고, LangSmith 라는 회사에는 아무것도 안 갑니다. `langsmith` 는 계측 SDK 로만 씁니다.
    """
    from langsmith import utils as ls_utils

    return (ls_utils.get_env_var("TRACING_MODE") or "langsmith").lower()


def _force_tracing_off() -> None:
    """트레이싱을 프로세스 전체에서 끕니다 — 설정이 안전하지 않다고 판명됐을 때.

    환경 변수를 직접 끄는 이유: 계측은 `LANGSMITH_TRACING` 을 **각자** 봅니다
    (`@traceable` · LangGraph 콜백). 우리가 `False` 를 돌려주는 것만으로는 그들이
    안 멈춥니다. 끄려면 그들이 읽는 값을 꺼야 합니다.

    `get_env_var` 가 lru_cache 라 캐시도 같이 비웁니다. 안 비우면 이미 읽어 둔
    "true" 가 그대로 남습니다.
    """
    import os

    from langsmith import utils as ls_utils

    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        os.environ.pop(name, None)
    os.environ["LANGSMITH_TRACING"] = "false"
    ls_utils.get_env_var.cache_clear()


def configure_tracing() -> bool:
    """마스킹이 걸린 클라이언트를 전역 캐시에 심습니다. 켜졌으면 True.

    **lifespan 의 맨 앞에서 부르세요.** 이유는 모듈 docstring 의 "먼저 만든 쪽이 이깁니다".
    꺼져 있으면 클라이언트를 아예 만들지 않습니다 — 만들면 API 키가 없을 때 거기서
    터지고, 그건 트레이싱을 안 쓰는 사람의 서버가 안 뜨는 것입니다.
    """
    if not tracing_enabled():
        return False

    from langsmith import run_trees

    try:
        client = run_trees.get_cached_client(
            anonymizer=scrub_payload,
            # anonymizer 는 inputs/outputs 에만 걸립니다 (`client.py` `_hide_run_inputs`
            # `_hide_run_outputs`). metadata 는 별도 훅이라, 같은 함수를 여기도 겁니다 —
            # 우리가 metadata 에 원문을 안 넣는다는 규칙에만 기대면 언젠가 넣습니다.
            hide_metadata=scrub_payload,
        )
    except Exception:
        # `Client.__init__` 은 던집니다 — 예를 들어 `LANGSMITH_TRACING_MODE` 에 오타가
        # 있으면 `LangSmithUserError` 입니다. 여기가 lifespan 의 첫 줄이라, 그대로
        # 올리면 **backend 가 아예 안 뜨고 API 전체가 내려갑니다.** 오타 하나로
        # 로그인까지 죽는 것은 트레이싱이 가질 권한이 아닙니다.
        LOGGER.exception("트레이싱 설정에 실패해 트레이싱 없이 계속합니다.")
        _force_tracing_off()
        return False

    # `get_cached_client` 는 전역이 비었을 때만 kwargs 를 씁니다. 우리 것이 안 걸렸다면
    # 누군가 먼저 만든 것이고, 그 클라이언트에는 마스킹이 없습니다.
    if getattr(client, "_anonymizer", None) is not scrub_payload:
        # **감지만 하고 두면 안 됩니다.** 이 상태로 계속 돌면 신원·정밀 좌표가 그대로
        # 나가는데, 그건 로그 한 줄로 막을 수 있는 종류가 아닙니다. 트레이싱을 끕니다 —
        # 트레이스를 잃는 것과 개인정보를 흘리는 것 중 전자가 낫습니다.
        LOGGER.error(
            "LangSmith 클라이언트가 이미 만들어져 있어 마스킹이 걸리지 않았습니다. "
            "마스킹 없이 내보내지 않도록 트레이싱을 끕니다. "
            "configure_tracing() 이 lifespan 맨 앞에서 불렸는지 확인하세요."
        )
        _force_tracing_off()
        return False

    mode = tracing_mode()
    LOGGER.info(
        "트레이싱을 켰습니다 — 모드=%s (신원 마스킹 · 좌표 %d자리 반올림). "
        "%s",
        mode,
        COORDINATE_PRECISION,
        "트레이스는 우리 GCP 프로젝트의 Cloud Trace 로 갑니다."
        if mode == "otel"
        else "⚠ 트레이스가 LangSmith(제3자)로 나갑니다. 처리방침의 제3자 제공 목록을 확인하세요.",
    )
    if mode != "otel":
        # 기본값이 `langsmith` 라 **환경 변수를 안 넣으면 제3자로 나갑니다.** 운영에서
        # 그 상태를 조용히 지나가면 안 됩니다 (D-054 는 otel 을 운영 기본으로 정했습니다).
        LOGGER.warning(
            "LANGSMITH_TRACING_MODE 가 %r 입니다. 자체 수집(Cloud Trace)을 쓰려면 "
            "'otel' 로 두세요.",
            mode,
        )
    return True


def trace_config(
    *,
    request_id: str,
    run_name: str,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    root: bool = True,
) -> dict[str, Any]:
    """LangGraph `ainvoke` 에 줄 config. **트레이싱이 꺼져 있어도 같은 것을 만듭니다.**

    `root=False` 면 `run_id` 를 **넣지 않습니다.** 그래프가 `request_trace` 루트 아래의
    자식으로 도는 자리라, 같은 id 를 또 주면 루트와 충돌합니다 (모듈 docstring
    "루트는 서비스가, 그래프는 자식이"). `request_id` 는 metadata 에는 그대로 남아
    자식 런만 따로 걸러도 요청을 찾을 수 있습니다.

    분기하지 않는 이유: 분기하면 켠 경로와 끈 경로가 다른 코드가 되고, 문제는 늘 켠
    쪽에서만 납니다. 트레이서가 안 붙어 있으면 이 값들은 그냥 안 읽힙니다.

    **`run_id` 를 `request_id` 로 못박는 것이 이 함수의 요점입니다.** 그래야
    `answer_reports` → `chat_turns.request_id` → LangSmith 트레이스가 조회 없이
    바로 이어집니다 — 신고 한 건에서 그 요청의 라우팅·검색·프롬프트로 가는 길입니다.

    `request_id` 가 UUID 가 아니면 `run_id` 를 빼고 나머지만 돌려줍니다. 호출자가
    `X-Request-ID` 를 그대로 넘길 수 있고, 그 경우 트레이스는 남되 링크만 못 겁니다 —
    링크 하나 때문에 요청을 실패시킬 일은 아닙니다.
    """
    config: dict[str, Any] = {
        "run_name": run_name,
        "metadata": {"request_id": request_id, **(metadata or {})},
        "tags": list(tags or []),
    }
    if root:
        run_id = _run_id_from_request(request_id)
        if run_id is not None:
            config["run_id"] = run_id
    return config


def _run_id_from_request(request_id: str) -> uuid.UUID | None:
    """`request_id` 가 UUID 면 그것을 `run_id` 로. 아니면 None (링크만 포기)."""
    try:
        return uuid.UUID(request_id)
    except (ValueError, AttributeError, TypeError):
        LOGGER.debug("request_id 가 UUID 가 아니라 run_id 를 붙이지 않습니다.")
        return None


def request_trace(
    *,
    request_id: str,
    run_name: str,
    inputs: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Any:
    """요청 하나의 **루트 런**. `async with request_trace(...) as run:` 으로 씁니다.

    돌려주는 것은 langsmith 의 `trace` 컨텍스트입니다. 안에서 도는 `@traceable`
    (시맨틱 라우터 · `training_rag`)과 LangGraph 자동 계측(그래프 노드)이 전부 이 런의
    자식이 됩니다 — langchain_core 가 부모를 langsmith 의 현재 런 컨텍스트에서
    읽습니다 (`callbacks/manager.py` `_configure`).

    **트레이싱이 꺼져 있어도 같은 코드가 돕니다** (`trace_config` 와 같은 이유).
    그때 langsmith 는 RunTree 객체만 만들고 post 도, 컨텍스트 설정도, 클라이언트
    생성도 하지 않습니다 (`run_helpers.trace._setup` — `enabled` 가 False 인 갈래).
    그래서 켠 경로와 끈 경로가 갈리지 않습니다.

    `run_id` 는 `request_id` 입니다 — 신고 → 트레이스 링크의 근거 (모듈 docstring).
    `inputs` 는 anonymizer(`scrub_payload`)를 거칩니다. 신원 필드는 애초에 넣지 마세요 —
    지워지긴 하지만, 안 넣는 것이 규칙입니다. 답의 입력(질문 · context)은 넣습니다.
    그것을 보려고 켜는 것입니다.
    """
    from langsmith import trace as ls_trace

    return ls_trace(
        name=run_name,
        run_type="chain",
        run_id=_run_id_from_request(request_id),
        inputs=inputs,
        metadata={"request_id": request_id, **(metadata or {})},
        tags=list(tags or []),
    )


#: 신고가 트레이스에 붙는 이름. LangSmith 에서 이 키로 거르면 신고된 요청만 남습니다.
REPORT_FEEDBACK_KEY = "user_report"


async def record_report_feedback(*, request_id: str | None) -> None:
    """신고 한 건을 그 요청의 트레이스에 붙입니다. **실패해도 신고는 성공입니다.**

    이것이 "민원을 하나하나 못 본다" 에 대한 답의 절반입니다 — LangSmith 에서
    `user_report` 로 거르면 신고된 트레이스만 모이고, 그대로 데이터셋으로 올라갑니다.
    골드셋을 새로 만드는 대신 **민원이 곧 평가셋**이 되는 자리입니다.

    **신고 사유 원문은 안 보냅니다.** 사용자가 자유롭게 쓰는 칸이라 무엇이든 들어올 수
    있고, LangSmith 가 알아야 하는 것은 "이 트레이스가 신고됐다" 뿐입니다. 왜 신고했는지는
    관리자 콘솔에 있고 그쪽이 D-053 의 열람 범위 안입니다.

    **호출 규칙 두 가지.**

    1. **열린 트랜잭션 없이 부르세요.** 외부 호출 동안 요청 DB 세션과 행 잠금이 살아
       있으면 안 됩니다 (D-048 의 외부 호출 경계). 신고 저장을 커밋한 **뒤**가 그 자리입니다.
    2. **예외를 밖으로 내보내지 않습니다.** 신고는 이미 우리 DB 에 들어갔습니다.
       LangSmith 가 죽었다고 사용자에게 실패를 돌려주면 같은 신고를 다시 누르게 되고,
       그건 유니크 제약에 걸려 409 가 됩니다.
    """
    if not request_id or not tracing_enabled():
        return
    if tracing_mode() == "otel":
        # 피드백은 LangSmith **REST API** 의 기능이라 Cloud Trace 에는 붙일 자리가 없습니다
        # (스팬은 한 번 쓰이면 못 고칩니다). 여기서 막지 않으면 API 키도 없이 LangSmith 로
        # 요청이 나가서, 실패를 삼키느라 아무도 모르는 채 매 신고마다 헛돕니다.
        #
        # 대신 신고된 요청을 찾는 길은 그대로 있습니다 — 콘솔의 신고 목록이
        # `request_id` 를 주고, 그것이 곧 Cloud Trace 의 trace id 입니다 (D-054).
        return
    try:
        run_id = uuid.UUID(request_id)
    except (ValueError, AttributeError, TypeError):
        # `run_id` 를 못 박지 못한 요청입니다 (`trace_config` 의 같은 갈래).
        return

    import asyncio

    from langsmith import run_trees

    client = run_trees.get_cached_client()

    def _send() -> None:
        # `stop_after_attempt` 기본값이 10 입니다. 그대로 두면 LangSmith 가 죽었을 때
        # 신고 POST 하나가 재시도 열 번을 기다립니다 — 여기서 재시도는 가치가 없습니다.
        client.create_feedback(
            run_id, key=REPORT_FEEDBACK_KEY, score=0, stop_after_attempt=1
        )

    try:
        # requests 기반 동기 호출이라 이벤트 루프를 막습니다. 스레드로 뺍니다.
        await asyncio.to_thread(_send)
    except Exception:
        LOGGER.warning(
            "신고를 LangSmith 피드백으로 보내지 못했습니다 (신고 자체는 저장됨).",
            exc_info=True,
        )


__all__ = [
    "COORDINATE_PRECISION",
    "REDACTED",
    "REPORT_FEEDBACK_KEY",
    "configure_tracing",
    "record_report_feedback",
    "request_trace",
    "scrub_payload",
    "trace_config",
    "tracing_enabled",
    "tracing_mode",
]
