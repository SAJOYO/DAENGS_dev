"""Prompt isolation, scope and lightweight-import tests."""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from daengs_evals.router_benchmark.prompt import MODEL_ID, PROMPT_VERSION, build_router_prompt
from daengs_evals.router_benchmark.schemas import load_gold_cases

TOOLS_DIR = Path(__file__).parents[1] / "src" / "daengs_evals" / "router_benchmark"


def test_prompt_version_policy_and_schema_are_frozen() -> None:
    prompt = build_router_prompt(
        query="오늘 산책 괜찮아?",
        context={"location": {"lat": 37.5665, "lon": 126.978}},
    )
    assert PROMPT_VERSION == "semantic-router-ko-v1"
    assert MODEL_ID == "gemini-3.5-flash-lite"
    for value in ("training", "life", "walk", "skin", "gait", "RoutePlan"):
        assert value in prompt
    assert "Return no Markdown" in prompt
    assert "do not answer the dog question" in prompt
    assert '"lat":37.5665' in prompt


def test_prompt_builder_cannot_receive_gold_category_or_rationale() -> None:
    assert set(inspect.signature(build_router_prompt).parameters) == {"query", "context"}
    with pytest.raises(TypeError):
        build_router_prompt(  # type: ignore[call-arg]
            query="질문",
            context={},
            category="training_only",
            gold_route_plan={"requests": []},
            rationale="SECRET_RATIONALE",
        )


@pytest.mark.parametrize("key", ["category", "gold_route_plan", "rationale", "metrics"])
def test_prompt_context_rejects_gold_and_evaluator_fields(key: str) -> None:
    with pytest.raises(ValueError, match="forbidden|unsupported"):
        build_router_prompt(query="질문", context={"source": {key: "LEAK_SENTINEL"}})


def test_prompt_contains_no_case_gold_or_rationale() -> None:
    prompt = build_router_prompt(query="LEAK_TEST_QUERY", context={"source": "assistant_chat"})
    assert "SECRET_RATIONALE" not in prompt
    assert "gold_route_plan" not in prompt
    assert "training_only" not in prompt
    assert "LEAK_TEST_QUERY" in prompt

    case = load_gold_cases()[0]
    case_prompt = build_router_prompt(query=case.query, context=case.context)
    assert case.category not in case_prompt
    assert case.rationale not in case_prompt
    assert (
        json.dumps(case.gold_route_plan.model_dump(mode="json"), ensure_ascii=False)
        not in case_prompt
    )


@pytest.mark.parametrize(
    "context",
    [
        {"location": {"lat": 40.0, "lon": 127.0}},
        {"location": {"lat": 37.5, "lon": "secret"}},
        {"location": {"lat": 37.5, "altitude": 10}},
        {"source": {"screen": "walk"}},
    ],
)
def test_prompt_rejects_unapproved_context_shapes(context: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        build_router_prompt(query="질문", context=context)


def test_benchmark_modules_do_not_reference_engine_or_capability_adapters() -> None:
    forbidden_names = {
        "OrchestrationEngine",
        "TrainingCapabilityAdapter",
        "LifeCapabilityAdapter",
        "WalkCapabilityAdapter",
    }
    found: list[str] = []
    for path in TOOLS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for name in sorted(names & forbidden_names):
            found.append(f"{path.name}:{name}")
    assert found == []


def test_only_phase_2_runner_references_provider_or_network_client() -> None:
    forbidden_roots = {"google", "httpx", "requests", "urllib"}
    found: list[str] = []
    for path in TOOLS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            for root in roots & forbidden_roots:
                found.append(f"{path.name}:{root}")
    assert set(found) == {"runner.py:google", "runner_v2.py:google"}


def test_benchmark_imports_stay_lightweight_and_offline() -> None:
    code = (
        "import json,sys; import daengs_evals.router_benchmark; "
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    loaded = set(json.loads(done.stdout.strip()))
    assert not loaded & {
        "torch",
        "sentence_transformers",
        "transformers",
        "daengs_life",
        "daengs_training",
        "daengs_screening",
        "daengs_gait",
        "google",
    }
