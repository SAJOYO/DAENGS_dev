"""여러 평가 하네스가 공유하는 기계 부품 (#464, D-072 결합 정리).

`orchestrator_comparison` 이 처음 만들었지만, 내용 자체는 그 비교를 전혀 모른다 —
가짜 어댑터로 도메인 지연을 지우는 것, 모델 호출 하나의 사용량을 세는 것, 오케스트레이션
엔진에 실제로 닿은 `RoutePlan` 을 잡아채는 것, 결과 파일에 "무엇을 쟀나"(소스 커밋·dirty
여부·패키지 버전)를 적어 두는 것 — 전부 두 오케스트레이터를 비교한다는 사실과 무관한
범용 도구다.

`answer_quality` 와 `conversation_quality` 는 이미 이 부품들을 쓰고 있었다(#401 의
"복사하지 말고 재사용하라"). 문제는 재사용 대상이 **일회성 벤치마크**였다는 것이다 —
`orchestrator_comparison` 은 사람이 오케스트레이터 하나를 고르고 나면 지울 패키지라고
스스로 밝히고 있다(그 패키지의 모듈 docstring 참고). 이 모듈은 그 결합을 끊는다 —
`orchestrator_comparison` 을 통째로 지워도 이 둘은 안 부서진다.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    RoutePlan,
)
from daengs_backend.orchestration.graph import CapabilityAdapter, OrchestrationEngine
from daengs_evals import BACKEND_DIR
from daengs_evals.router_benchmark.schemas import PerformanceObservation

# ---------------------------------------------------------------------------
# 가짜 어댑터 — 재는 것은 능력 선택이지 도메인 답이 아니다
# ---------------------------------------------------------------------------


@dataclass
class FakeAdapter:
    """즉시 OK 를 돌려준다. 그래서 남는 시간이 곧 오케스트레이션 시간이다."""

    capability: CapabilityName

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": f"(가짜 {self.capability.value} 어댑터)"},
            elapsed_ms=0,
        )


def fake_adapters() -> dict[CapabilityName, FakeAdapter]:
    return {name: FakeAdapter(capability=name) for name in CapabilityName}


# ---------------------------------------------------------------------------
# 계량 — 비용·지연을 in-process 로 (트레이싱 인프라가 필요 없다)
# ---------------------------------------------------------------------------


@dataclass
class Meter:
    """모델 호출 하나(또는 한 케이스)의 사용량을 센다.

    `reset()` 으로 케이스마다 되돌리고, transport 쪽에서 호출마다 `add()` 로 누적한
    뒤 `observation()` 으로 `PerformanceObservation` 을 뽑는다. `turns` 는 `add()` 가
    불린 횟수라, 호출을 한 번만 하는 경로와 루프를 도는 경로를 같은 숫자로 구분해 낸다.
    """

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def reset(self) -> None:
        self.turns = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, *, input_tokens: int | None, output_tokens: int | None) -> None:
        self.turns += 1
        self.input_tokens += int(input_tokens or 0)
        self.output_tokens += int(output_tokens or 0)

    def observation(self, latency_ms: float) -> PerformanceObservation:
        total = self.input_tokens + self.output_tokens
        return PerformanceObservation(
            latency_ms=latency_ms,
            input_tokens=self.input_tokens or None,
            output_tokens=self.output_tokens or None,
            total_tokens=total or None,
        )


def metered_semantic_generate(meter: Meter, client: Any = None) -> Callable[[str], Any]:
    """LangGraph 의 의미 라우터 호출을 재는 transport.

    `semantic._generate_with_gemini` 는 응답 객체를 버려 `usage_metadata` 가 남지
    않으므로, 여기서 같은 호출을 하되 사용량을 센다. 생성 설정은 `router_generation_
    config()` **그 객체**를 그대로 쓴다 — 숫자를 여기서 다시 적으면 운영과 벤치마크가
    조용히 갈릴 수 있다.
    """
    from daengs_backend.orchestration.semantic import (
        ROUTER_MODEL_ID,
        _gemini_client,
        router_generation_config,
    )

    async def generate(prompt: str) -> object:
        def _call() -> object:
            response = (client or _gemini_client()).models.generate_content(
                model=ROUTER_MODEL_ID,
                contents=prompt,
                config=router_generation_config(),
            )
            usage = getattr(response, "usage_metadata", None)
            meter.add(
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
            )
            parsed = getattr(response, "parsed", None)
            return parsed if parsed is not None else getattr(response, "text", None)

        return await asyncio.to_thread(_call)

    return generate


# ---------------------------------------------------------------------------
# 실제로 쓰인 RoutePlan 잡기
# ---------------------------------------------------------------------------


class RecordingEngine(OrchestrationEngine):
    """엔진에 닿은 `RoutePlan` 을 생성자로 받은 `sink["plan"]` 에 남긴다.

    오케스트레이터가 무엇을 결정했는지는 응답만 보고는 되짚을 수 없다 — 응답 payload 에
    실행 계획이 그대로 담기지 않는 경로가 있기 때문이다. 엔진은 구현이 무엇이든 실행
    직전에 반드시 `RoutePlan` 을 들고 이 지점을 지나므로, 여기 하나만 감싸면 실제로
    쓰인 계획을 구현에 상관없이 대칭적으로 잡을 수 있다.
    """

    def __init__(
        self, adapters: Mapping[CapabilityName, CapabilityAdapter], sink: dict[str, Any]
    ) -> None:
        super().__init__(adapters)
        self._sink = sink

    async def run(self, *, route_plan: RoutePlan, **kwargs: Any) -> Any:  # type: ignore[override]
        self._sink["plan"] = route_plan
        return await super().run(route_plan=route_plan, **kwargs)


# ---------------------------------------------------------------------------
# 출처 · 설정 — 결과 파일이 "무엇을 쟀나" 를 스스로 말하게
# ---------------------------------------------------------------------------


def git(*args: str, strip: bool = True) -> str:
    try:
        output = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True, cwd=BACKEND_DIR
        ).stdout
    except Exception:  # noqa: BLE001 - git 이 없다고 도구를 멈추지 않는다
        return "unknown"
    return output.strip() if strip else output


def dirty_tracked_files() -> list[str]:
    """추적 파일의 변경만. 이 도구가 새로 만드는 결과 파일(untracked)은 dirty 가 아니다.

    porcelain 행은 `XY 경로` 라 앞 공백이 뜻을 가진다 — 통째로 strip 하면 첫 행의 경로가
    한 글자 잘린다. 그래서 strip 없이 받아 행마다 자른다.
    """
    status = git("status", "--porcelain", "--untracked-files=no", strip=False)
    if status == "unknown":
        return ["unknown"]
    return [line[3:] for line in status.splitlines() if line.strip()]


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


__all__ = [
    "FakeAdapter",
    "Meter",
    "RecordingEngine",
    "dirty_tracked_files",
    "fake_adapters",
    "git",
    "metered_semantic_generate",
    "package_version",
]
