"""라우터가 고른 담당 vs 기대 — 모델 없이 도는 부분."""

from __future__ import annotations

from daengs_evals.route_check import DEFAULT_EXPECT, expected_of, got_of, render


def test_expected_follows_question_set_and_emergency_expectation() -> None:
    exp = {
        "q_emerg": {"expect": "defer", "expected_reason": "emergency"},
        "q_diag": {"expect": "defer", "expected_reason": "diagnosis"},
    }
    assert expected_of("pf_ask_v1", "q_emerg", exp) == "vet_contact"
    assert expected_of("pf_ask_v1", "q_diag", exp) == "general"  # 진단 거절은 general 이 한다
    assert expected_of("pf_ask_v1", "q_none", exp) == "general"
    assert expected_of("pf_life_v1", "q_any", {}) == "life"
    assert set(DEFAULT_EXPECT.values()) == {"general", "life"}


def test_got_joins_capabilities() -> None:
    assert got_of({"capabilities": ["walk", "general"]}) == "walk+general"
    assert got_of({"capabilities": ["handoff:gait"]}) == "handoff:gait"
    assert got_of({"capabilities": []}) == "(없음)"


def test_render_lists_totals_and_mismatches() -> None:
    results = [
        {
            "label": "pf_ask_v1",
            "router": {"prompt_version": "semantic-router-ko-v10"},
            "n": 4,
            "match": 3,
            "rate": {"point": 0.75, "low": 0.3, "high": 0.95, "n": 4},
            "mismatches": [
                {
                    "question_id": "q1",
                    "query": "오늘 컨디션 어때 보여?",
                    "expected": "general",
                    "got": "handoff:gait",
                    "status": "HANDOFF",
                    "cells": 2,
                }
            ],
        }
    ]
    md = render(results)
    assert "| **전체** | | 4 | 3 |" in md
    assert "| pf_ask_v1 | 오늘 컨디션 어때 보여? | general | handoff:gait | 2 | HANDOFF |" in md
