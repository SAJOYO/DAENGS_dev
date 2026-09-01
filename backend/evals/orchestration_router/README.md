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
