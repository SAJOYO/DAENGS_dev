"""셀 계획 · 쌍 만들기 — 모델 없이 도는 순수 함수들.

셀 파일의 모양은 2026-09-09 `cells_smoke3.jsonl` 실측을 따른다.
"""

from __future__ import annotations

from typing import Any

import pytest

from daengs_evals.profile_fitness.collect import plan_cells, selected_capabilities
from daengs_evals.profile_fitness.pairs import (
    answering_capability,
    build_pairs,
    index_cells,
    summarize_pairs,
)
from daengs_evals.profile_fitness.profiles import Profile
from daengs_evals.profile_fitness.questions import Question

PROFILES = [
    Profile(
        profile_id="toy_puppy",
        label="a",
        dog={"breed": "치와와", "age_months": 4},
        visible_to=["life", "general"],
        axis="size_age",
    ),
    Profile(
        profile_id="large_senior",
        label="b",
        dog={"breed": "골든리트리버", "age_months": 150},
        visible_to=["life", "general"],
        axis="size_age",
    ),
    Profile(
        profile_id="chronic",
        label="c",
        dog={"breed": "말티즈", "age_months": 84, "on_medication": True},
        visible_to=["general"],
        axis="diseases",
    ),
    Profile(
        profile_id="no_screening",
        label="d",
        dog={"breed": "말티즈", "age_months": 84},
        visible_to=["life"],
        axis="screening",
    ),
    Profile(profile_id="none", label="e", dog=None, visible_to=["life", "general"], axis="absent"),
]

REACTIVE = Question(
    question_id="pf_walk_01",
    query="산책 얼마나?",
    kind="reactive",
    tier="coarse",
    arms=["toy_puppy", "large_senior"],
    sensitive_to=["age_months"],
    author="t",
)
INVARIANT = Question(
    question_id="pf_tail_01",
    query="꼬리 왜 흔들어?",
    kind="invariant",
    tier="coarse",
    arms=["toy_puppy", "large_senior"],
    author="t",
)
PROBE = Question(
    question_id="pf_probe_01",
    query="지난 검사 보면?",
    kind="probe",
    tier="coarse",
    arms=["none", "chronic"],
    sensitive_to=["health_conditions"],
    author="t",
)


def cell(
    qid: str,
    arm: str,
    run: int,
    *,
    status: str = "ANSWERED",
    cap: str = "general",
    message: str = "답",
    error: str | None = None,
) -> dict[str, Any]:
    ok = status in ("ANSWERED", "PARTIAL")
    return {
        "kind": "cell",
        "question_id": qid,
        "arm": arm,
        "run": run,
        "status": status,
        "message": message,
        "error": error,
        "results": [{"capability": cap, "status": "OK" if ok else "ERROR"}],
        "capabilities": [cap],
        "plan": {"requests": [{"capability": cap}], "handoffs": []},
    }


# ---------------------------------------------------------------------------
# 셀 계획
# ---------------------------------------------------------------------------


def test_plan_cells_all_conditions_for_a_reactive_question() -> None:
    planned = {
        (q.question_id, arm, run)
        for q, arm, run in plan_cells([REACTIVE], ("contrast", "ablation", "noise"))
    }
    assert planned == {
        ("pf_walk_01", "toy_puppy", 0),
        ("pf_walk_01", "large_senior", 0),
        ("pf_walk_01", "none", 0),
        ("pf_walk_01", "toy_puppy", 1),
    }


def test_ablation_is_only_for_reactive_questions() -> None:
    """invariant 에 절제군을 붙이면 "프로필 없음 vs 있음" 이 뜻이 없고, probe 는 이미 none 이 arm 이다."""
    planned = plan_cells([INVARIANT, PROBE], ("ablation",))
    assert planned == []


def test_plan_cells_deduplicates_across_conditions() -> None:
    planned = plan_cells([REACTIVE], ("contrast", "noise"))
    keys = [(q.question_id, arm, run) for q, arm, run in planned]
    assert len(keys) == len(set(keys))
    assert ("pf_walk_01", "toy_puppy", 0) in keys


def test_selected_capabilities_reads_requests_and_handoffs() -> None:
    plan = {"requests": [{"capability": "general"}], "handoffs": [{"target": "skin"}]}
    assert selected_capabilities(plan) == ["general", "handoff:skin"]
    assert selected_capabilities(None) == []


# ---------------------------------------------------------------------------
# 쌍
# ---------------------------------------------------------------------------


def test_build_pairs_makes_three_conditions_for_reactive() -> None:
    cells = [
        cell("pf_walk_01", "toy_puppy", 0),
        cell("pf_walk_01", "large_senior", 0),
        cell("pf_walk_01", "none", 0),
        cell("pf_walk_01", "toy_puppy", 1),
    ]
    pairs = build_pairs([REACTIVE], PROFILES, cells)
    assert {p.condition for p in pairs} == {"contrast", "ablation", "noise"}
    assert all(p.judgeable for p in pairs)
    assert all(p.capability == "general" for p in pairs)
    noise = next(p for p in pairs if p.condition == "noise")
    assert noise.is_control and noise.arm_a == noise.arm_b and (noise.run_a, noise.run_b) == (0, 1)


def test_missing_cell_is_reported_not_dropped() -> None:
    """없는 셀을 조용히 빼면 "몇 개를 못 쟀는가" 가 사라진다."""
    pairs = build_pairs([REACTIVE], PROFILES, [cell("pf_walk_01", "toy_puppy", 0)])
    assert {p.skip_reason for p in pairs} == {"missing_cell"}
    assert summarize_pairs(pairs)["skipped"] == {"missing_cell": 3}


def test_failed_cell_makes_the_pair_not_answered() -> None:
    cells = [
        cell("pf_walk_01", "toy_puppy", 0),
        cell("pf_walk_01", "toy_puppy", 1, status="FAILED"),
    ]
    pairs = build_pairs([REACTIVE], PROFILES, cells, conditions=("noise",))
    assert pairs[0].skip_reason == "not_answered"


def test_runner_error_is_distinguished_from_model_failure() -> None:
    cells = [
        cell("pf_walk_01", "toy_puppy", 0),
        cell("pf_walk_01", "toy_puppy", 1, error="Timeout: x"),
    ]
    pairs = build_pairs([REACTIVE], PROFILES, cells, conditions=("noise",))
    assert pairs[0].skip_reason == "runner_error"


def test_out_of_scope_when_profile_does_not_reach_the_capability() -> None:
    """피부 이력 arm 은 life 에만 닿는다. general 이 답했으면 그 무변화는 계약의 사실이다."""
    q = Question(
        question_id="pf_skin_01",
        query="피부?",
        kind="reactive",
        tier="fine",
        arms=["no_screening", "chronic"],
        sensitive_to=["screening"],
        author="t",
    )
    cells = [
        cell("pf_skin_01", "no_screening", 0, cap="general"),
        cell("pf_skin_01", "chronic", 0, cap="general"),
    ]
    pairs = build_pairs([q], PROFILES, cells, conditions=("contrast",))
    assert pairs[0].skip_reason == "out_of_scope"


def test_out_of_scope_when_the_two_answers_came_from_different_capabilities() -> None:
    cells = [
        cell("pf_walk_01", "toy_puppy", 0, cap="general"),
        cell("pf_walk_01", "large_senior", 0, cap="life"),
    ]
    pairs = build_pairs([REACTIVE], PROFILES, cells, conditions=("contrast",))
    assert pairs[0].skip_reason == "out_of_scope"


def test_probe_contrast_pair_is_none_versus_profile() -> None:
    cells = [cell("pf_probe_01", "none", 0), cell("pf_probe_01", "chronic", 0)]
    pairs = build_pairs([PROBE], PROFILES, cells, conditions=("contrast",))
    assert (pairs[0].arm_a, pairs[0].arm_b) == ("none", "chronic")
    assert pairs[0].judgeable


def test_index_cells_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="중복"):
        index_cells([cell("q", "a", 0), cell("q", "a", 0)])


def test_answering_capability_prefers_the_ok_result() -> None:
    c = cell("q", "a", 0)
    c["results"] = [
        {"capability": "walk", "status": "ERROR"},
        {"capability": "general", "status": "OK"},
    ]
    assert answering_capability(c) == "general"


def test_pair_id_is_stable_and_distinct() -> None:
    cells = [
        cell("pf_walk_01", "toy_puppy", 0),
        cell("pf_walk_01", "large_senior", 0),
        cell("pf_walk_01", "none", 0),
        cell("pf_walk_01", "toy_puppy", 1),
    ]
    ids = [p.pair_id for p in build_pairs([REACTIVE], PROFILES, cells)]
    assert len(ids) == len(set(ids))


def test_real_capabilities_by_adapter_mode() -> None:
    """모드마다 "진짜였던 능력" 이 다르다 — pairs · report 가 범위 밖 셀을 가르는 근거."""
    from daengs_evals.profile_fitness.collect import real_capabilities

    assert real_capabilities("fallback-only") == frozenset({"general"})
    assert real_capabilities("life") == frozenset({"general", "life"})
    assert real_capabilities("fake") == frozenset()
    assert real_capabilities("real") is None
    assert real_capabilities(None) is None  # 옛 meta 에 adapters 가 없으면 전부 진짜로 본다
