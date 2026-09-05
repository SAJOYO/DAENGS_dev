"""LangSmith 트레이싱 배선. **기본은 꺼져 있습니다.**

D-037 은 일반 관측에 질문 원문을 남기지 않습니다. 이 모듈은 그 결정을 뒤집지 않고
**명시적 옵트인 예외**를 만듭니다 — 환경 변수가 없으면 아무것도 안 나가고, 켜야만
나갑니다. 예외가 필요한 이유는 실사용 민원이 실사용 질문에서 나오기 때문입니다.
동결된 골드셋으로는 "무엇이 잘못되고 있는지 모르는 상태"를 못 좁힙니다.

**켜면 질문 원문·검색 청크·프롬프트·답변이 나갑니다. 그것이 목적입니다.**
`hide_inputs` 를 쓰지 않는 이유가 그것입니다 — 그건 입력을 통째로 `{}` 로 만들어
(langsmith `client.py` `_hide_run_inputs`) 트레이스의 진단 가치를 0 으로 만듭니다.
지우는 것은 **진단에 안 쓰이는 신원·위치**뿐입니다 (`scrub_payload`).

## 켜는 법

`LANGSMITH_TRACING=true` 와 `LANGSMITH_API_KEY` 를 **프로세스 환경 변수**로 줍니다.

⚠ **`backend/.env` 에 적으면 안 켜집니다.** 그 파일은 pydantic-settings 가 자기
`Settings` 로만 읽고 `os.environ` 에 넣지 않는데, langsmith 는 `os.environ` 만
봅니다. `docker-compose.yml` 의 `environment:` 가 그 자리입니다.

그래서 이 모듈은 `DAENGS_` 설정을 새로 만들지 않습니다. 만들면 "우리 설정은 켜졌는데
SDK 는 꺼져 있는" 상태가 생기고, 그건 아무 데도 안 찍히면서 켜진 줄 알게 되는 상태입니다.
권위는 `LANGSMITH_*` 한 곳입니다.

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

#: 통째로 지우는 키. 지금은 `PrincipalContext.subject` 하나입니다.
#:
#: **해시로 남기지 않는 이유**: 남길 이유가 없습니다. "이 사람이 또 신고했나" 를 묻는 길은
#: `request_id` → `chat_turns` → `chat_sessions` 로 **우리 DB 안에** 이미 있습니다.
#: 제3자에게 회원 식별자를 보내서 얻는 것이 0 인데 보내면, 그건 그냥 유출 표면입니다.
_IDENTITY_KEYS = frozenset({"subject"})

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


def configure_tracing() -> bool:
    """마스킹이 걸린 클라이언트를 전역 캐시에 심습니다. 켜졌으면 True.

    **lifespan 의 맨 앞에서 부르세요.** 이유는 모듈 docstring 의 "먼저 만든 쪽이 이깁니다".
    꺼져 있으면 클라이언트를 아예 만들지 않습니다 — 만들면 API 키가 없을 때 거기서
    터지고, 그건 트레이싱을 안 쓰는 사람의 서버가 안 뜨는 것입니다.
    """
    if not tracing_enabled():
        return False

    from langsmith import run_trees

    client = run_trees.get_cached_client(
        anonymizer=scrub_payload,
        # anonymizer 는 inputs/outputs 에만 걸립니다 (`client.py` `_hide_run_inputs`
        # `_hide_run_outputs`). metadata 는 별도 훅이라, 같은 함수를 여기도 겁니다 —
        # 우리가 metadata 에 원문을 안 넣는다는 규칙에만 기대면 언젠가 넣습니다.
        hide_metadata=scrub_payload,
    )
    # `get_cached_client` 는 전역이 비었을 때만 kwargs 를 씁니다. 우리 것이 안 걸렸다면
    # 누군가 먼저 만든 것이고, 그 클라이언트에는 마스킹이 없습니다. 조용히 넘어가면
    # 신원·좌표가 그대로 나가므로 **크게 알립니다.**
    if getattr(client, "_anonymizer", None) is not scrub_payload:
        LOGGER.error(
            "LangSmith 클라이언트가 이미 만들어져 있어 마스킹이 걸리지 않았습니다. "
            "configure_tracing() 이 lifespan 맨 앞에서 불렸는지 확인하세요. "
            "이 상태로는 신원·정밀 좌표가 트레이스로 나갑니다."
        )
        return False

    LOGGER.info("LangSmith 트레이싱을 켰습니다 (신원 마스킹 · 좌표 %d자리 반올림).",
                COORDINATE_PRECISION)
    return True


def trace_config(
    *,
    request_id: str,
    run_name: str,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """LangGraph `ainvoke` 에 줄 config. **트레이싱이 꺼져 있어도 같은 것을 만듭니다.**

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
    try:
        config["run_id"] = uuid.UUID(request_id)
    except (ValueError, AttributeError, TypeError):
        LOGGER.debug("request_id 가 UUID 가 아니라 run_id 를 붙이지 않습니다.")
    return config


__all__ = [
    "COORDINATE_PRECISION",
    "REDACTED",
    "configure_tracing",
    "scrub_payload",
    "trace_config",
    "tracing_enabled",
]
