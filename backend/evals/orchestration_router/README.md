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
