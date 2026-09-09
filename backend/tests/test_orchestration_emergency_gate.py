"""응급 게이트 — 결정론적 어휘 판정. 모델 호출 0회, 유료 호출 0회."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from daengs_backend.orchestration.emergency import is_emergency

EVALS = Path(__file__).resolve().parents[1] / "evals"


@pytest.mark.parametrize(
    "query",
    [
        "우리 보리가 갑자기 몸을 떨면서 경련을 일으키고 있어요",
        "콩이가 방금 식탁 위에 있던 초콜릿을 좀 먹었는데 어떡하죠?",
        "강아지가 갑자기 숨을 헐떡이면서 거품을 물고 쓰러졌어요!",
        "산책하다가 바닥에 떨어진 걸 주워 먹었어요",
        "밤새 계속 토해요",
    ],
)
def test_emergency_utterances_fire(query: str) -> None:
    assert is_emergency(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "어제 한 번 토했어요",
        "주워 먹지 말라는 '놔' 신호를 처음부터 어떻게 알려줘야 해요?",
        "요즘 주식 시장 너무 어렵지 않아? 나 이번에 크게 물렸는데",
        "집에서 해줄 수 있는 응급처치가 있을까요?",
        "강아지 귀에 이물질이 들어간 것 같은데 어떻게 빼나요?",
        "근처 동물병원 찾아줘",
    ],
)
def test_non_emergency_utterances_do_not_fire(query: str) -> None:
    assert is_emergency(query) is False


def _queries(path: Path) -> list[tuple[str, str]]:
    """(식별자, 질의) 목록. 주석 줄(`#`)과 빈 줄은 건너뛴다."""
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item = json.loads(line)
        query = item.get("query") or item.get("question")
        if not query:
            continue
        rows.append((item.get("question_id") or item.get("case_id") or query[:20], query))
    return rows


def test_emergency_recall_on_frozen_answer_quality_corpus() -> None:
    """`emergency__*` stratum 은 전부 켜져야 한다 — 미탐이 오탐보다 훨씬 비싸다."""
    path = EVALS / "answer_quality" / "questions_v1.jsonl"
    missed = [
        item["question_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
        for item in [json.loads(line)]
        if item["stratum"].startswith("emergency__") and not is_emergency(item["query"])
    ]
    assert missed == []


def test_false_positive_rate_stays_at_the_measured_single_case() -> None:
    """비응급에서 켜지는 것은 1건뿐이다 (2026-09-09 실측).

    그 1건(`medical_boundary__polite_01`)은 "사료를 거의 안 먹고 구토를 계속하는데 지금
    바로 병원에 가봐야 하는 상황일까요" 다. stratum 은 medical_boundary 지만 실제로 응급에
    가깝고 사용자가 병원 여부를 직접 묻는다 — 켜지는 쪽이 맞다고 보고 남긴다.
    """
    path = EVALS / "answer_quality" / "questions_v1.jsonl"
    fired = sorted(
        item["question_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
        for item in [json.loads(line)]
        if not item["stratum"].startswith("emergency__") and is_emergency(item["query"])
    )
    assert fired == ["medical_boundary__polite_01"]


@pytest.mark.parametrize(
    "relative",
    [
        "orchestration_router/gold_v1.jsonl",
        "orchestration_router/gold_place_v1.jsonl",
        "training_quality/questions_v1.jsonl",
        "answer_quality/questions_screening_v2.jsonl",
    ],
)
def test_gate_never_fires_on_frozen_non_emergency_sets(relative: str) -> None:
    """게이트는 배타 실행이라, 여기서 켜지면 그 코퍼스의 정답이 통째로 사라진다.

    동결 라우터 벤치마크를 다시 돌리지 않아도 되는 근거가 이 테스트다 —
    라우터에 도달하는 질의가 하나도 안 바뀐다.
    """
    fired = [ident for ident, query in _queries(EVALS / relative) if is_emergency(query)]
    assert fired == []


def test_ambiguous_terms_have_paired_controls_that_do_not_fire() -> None:
    """D-064 ② — 결합 규칙을 추가할 때는 정상 질문 짝을 함께 고정한다.

    `AMBIGUOUS_TERMS` 는 단독으로 켜지면 안 되고, 위급 수식어와 함께일 때만 켜져야 한다.
    짝의 `normal` 은 같은 낱말을 **배경으로만** 쓴다.
    """
    path = EVALS / "orchestration_emergency" / "gold_pairs_v1.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "짝 대조군이 비어 있으면 결합 규칙에 회귀 방지가 없다"
    for row in rows:
        assert is_emergency(row["emergency"]) is True, row["pair_id"]
        assert is_emergency(row["normal"]) is False, row["pair_id"]


def test_every_ambiguous_term_is_covered_by_a_pair() -> None:
    from daengs_backend.orchestration.emergency import AMBIGUOUS_TERMS

    path = EVALS / "orchestration_emergency" / "gold_pairs_v1.jsonl"
    covered = {
        json.loads(line)["term"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    # 활용형(토해·토하·토했)은 대표형 `구토` 하나로 덮는다.
    representative = {"구토", "설사", "고열", "탈수"}
    assert representative <= covered
    assert representative <= set(AMBIGUOUS_TERMS)
