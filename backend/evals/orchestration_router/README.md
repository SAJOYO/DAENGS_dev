# Semantic router benchmark v1 assets

This directory is the human-reviewable, offline source of truth for Card 2A.

- `gold_v1.jsonl` contains exactly 80 synthetic/curated Korean queries, permitted structured
  context, real Card 1 `RoutePlan` gold objects, and human-review rationales.
- `benchmark_v1.yaml` freezes the one selected model, prompt version, retry contract, metrics,
  acceptance gates, and Phase 1 execution lock.

The JSONL field order is stable: `case_id`, `category`, `query`, `context`, `gold_route_plan`,
`rationale`. Rationales and labels are review metadata and must never enter model prompts.

No production logs, private conversation history, telemetry text, or copied team messages are used.
Every query was authored specifically for this benchmark. There is no Phase 2 prediction or result
file in this directory at the human-freeze point.

After human freeze approval, the one authorized Phase 2 run produced `results_v1.jsonl`,
`summary_v1.json`, and `phase2_report.md`. These contain normalized predictions, deterministic
metrics/gate checks, and a concise result; they contain no raw credentials or provider reasoning.

v1 demonstrated that semantic classification and trusted-data assembly should be separate. v2
limits Gemini to selecting EXECUTE/HANDOFF names and deterministically assembles the same Card 1
`RoutePlan` from the original query and permitted structured context. The v1 evidence remains
unchanged; v2 writes separate `results_v2.jsonl`, `summary_v2.json`, and `phase2_v2_report.md` files.

Human review after v2 confirmed that only `mixed_09` had an annotation error: its request is
Walk+Gait, not Training+Walk+Gait. `gold_v3_corrections.json` records that single correction as an
overlay on immutable `gold_v1.jsonl`; it is an annotation fix, not model tuning. The final run uses
`semantic-router-ko-v3` and separate v3 result files.

v4 (`results_v4.jsonl`, `summary_v4.json`, `phase2_v4_report.md`) re-ran the unchanged v3
prompt/gold/gates with the team-approved `gemini-3.1-flash-lite` model. v5 (`results_v5.jsonl`,
`summary_v5.json`, `phase2_v5_report.md`) re-ran the same gold/gates/model with the production
`semantic-router-ko-v4` prompt, which adds an exclusive `social_intent` classification for pure
greeting/thanks/goodbye utterances; the report records that no gold case produced a non-null
`social_intent`. Benchmark run numbers (v1..v5) and prompt versions (`semantic-router-ko-v1..v4`)
are separate axes.

## Orchestrator comparison (D-055)

`comparison_v1_*` (#252) fed the same 80 gold cases to the LangGraph planner-first path and the
LangChain agent, scored both with the frozen evaluator, and recorded that it had measured **two
different CLARIFY contracts**: three of its five divergences were contract differences, not
selection differences. Those files stay frozen as the record of why the agent was changed.

`comparison_v2_*` (#272) is the controlled follow-up. The agent now records a selection through
argument-less tools and hands the complete selection to the shared `planner.assemble_route_plan`
and `OrchestrationEngine`, so both implementations obey the same exclusive-CLARIFY gate; both run
the same model at `temperature=0.0`, one candidate, the same output limit and timeout, no provider
retries, over the same fake adapters. `tools/orchestrator_comparison/runner_v2.py` runs three full
repetitions (`comparison_v2_run_01..03_results.jsonl`, each carrying `benchmark_source_sha`),
alternates which implementation runs first by case index and inverts that per repetition, performs
one non-scored warm-up per implementation per repetition, and derives `comparison_v2_summary.json`
and `comparison_v2_report.md` from the three files. The failure contract (adapter errors, timeouts,
mixed execution/handoff, selector failure before plan freeze) is verified separately and
deterministically in `tests/test_orchestrator_failure_contract.py`; it does not touch the 80-case
score.

### Real tester holdout — pending

No de-identified real tester-query dataset exists in this repository (2026-09-06, searched `evals/`,
`docs/`, `tests/`; real usage at that date was eight chat turns per `docs/life/roadmap.md`). None
was manufactured. When one is collected it goes in a separate file, is never merged into
`gold_v1.jsonl`, and is scored with the same runner under the same controlled settings, reported
apart from the 80-case benchmark. Minimal expected schema, one JSON object per line, the same
`GoldCase` shape the loader already validates:

```json
{"case_id": "tester_01", "category": "boundary_adversarial", "query": "<de-identified utterance>",
 "context": {"location": {"lat": 37.5, "lon": 127.0}},
 "gold_route_plan": {"requests": [], "handoffs": [], "clarify": null, "router": "llm", "model": "gemini-3.1-flash-lite"},
 "rationale": "<why this routing, written by the reviewer>"}
```

Recommended size and coverage: 24–30 cases spanning abbreviations and typos, noisy or fragmentary
utterances, small talk mixed with a request, medical versus routine-care boundaries, mixed intent
(two capabilities, capability plus handoff), and missing context (no or partial coordinates). Every
case must be de-identified before it enters the repository: no names, device identifiers, exact
addresses, or copied private messages.
