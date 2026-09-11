"""멀티턴 대화 품질 — 안전하지만 상호작용으로 쓸모없는 답을 잰다.

이 패키지는 **런타임을 바꾸지 않는다.** 라우팅 · General 프롬프트 · 거절 규칙 ·
API 계약 · 집계 · 대화 메모리는 전부 이 패키지 밖이다.

**지금 추론은 무상태다** — `routers/assistant.py` 가 `service.run(query=...)` 로
현재 질의 하나만 넘기고, `services/chat.py:run_persisted_turn` 은 턴을 저장만 한다.
그래서 `context_continuity` 와 `repair_success` 의 정답이 코드로 0 에 확정돼 있고,
baseline 랩에서 0 이 아닌 판정이 나오면 그것은 **판정기의 오탐**이다.

숫자의 지위: 사람 라벨 캘리브레이션 전까지 지표가 아니다 (D-060 ⑦ · RAG-075).
하는 일은 채점이 아니라 **사람이 볼 자리를 고르는 것**이다.
"""

from daengs_evals import EVALS_DIR

ASSETS_DIR = EVALS_DIR / "conversation_quality"
CASES_V1_PATH = ASSETS_DIR / "cases_v1.jsonl"

__all__ = ["ASSETS_DIR", "CASES_V1_PATH"]
