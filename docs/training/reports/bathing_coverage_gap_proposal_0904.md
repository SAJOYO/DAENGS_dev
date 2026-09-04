# Bathing-Aversion Training — Proposed Future Evaluation Cases

**Scope**: Proposed future evaluation cases, for use only after an authoritative bathing-aversion source is reviewed and approved.
**Status**: Proposal only — not integrated into any permanent or frozen evaluation dataset.
**Date**: 2026-09-04

---

## Investigation Finding

Full-corpus search confirmed zero bathing-related content across all 14 serving documents and the entire training RAG corpus.

- Lexical search: 0 matches for `목욕 씻 욕실 욕조 샤워 드라이기 bath shower wash groom`
- Semantic search: Top-4 results for all bathing queries retrieve unrelated topics
- Model verdict: Correctly reports `model_reported_insufficient_evidence`

---

## Candidate Future Cases

These are not test cases in any current suite. They are recorded here so that, if an authoritative bathing-aversion source is later approved and ingested, a reviewer has a starting list of candidate queries to evaluate against the new content — rather than expected-forever UNCERTAIN cases.

Under the **current** corpus (no bathing content), each of these would return:
```
{
  "decision": "UNCERTAIN",
  "reason": "model_reported_insufficient_evidence",
  "generated": false
}
```

### Direct Bathing-Aversion Cases (6 cases)

#### Case 1: Simple Problem Statement
```json
{"query_id": "bathing_001", "question": "목욕을 싫어해요", "expected_outcome": "UNCERTAIN"}
```

#### Case 2: Fear + Evasion
```json
{"query_id": "bathing_002", "question": "강아지가 목욕을 무서워하고 도망가요", "expected_outcome": "UNCERTAIN"}
```

#### Case 3: Gradual Adaptation Method
```json
{"query_id": "bathing_003", "question": "강아지를 목욕에 천천히 적응시키는 방법을 알려줘", "expected_outcome": "UNCERTAIN"}
```

#### Case 4: Water Aversion
```json
{"query_id": "bathing_004", "question": "물을 묻히면 강아지가 도망가요", "expected_outcome": "UNCERTAIN"}
```

#### Case 5: Dryer Sound Sensitivity
```json
{"query_id": "bathing_005", "question": "드라이기 소리를 무서워해요", "expected_outcome": "UNCERTAIN"}
```

#### Case 6: Biting During Bath
```json
{"query_id": "bathing_006", "question": "목욕할 때 물려고 해요", "expected_outcome": "UNCERTAIN"}
```

### Boundary/Negative Controls (2 cases)

#### Case 7: Hygiene Frequency (Out of Training Scope)
```json
{"query_id": "care_001", "question": "목욕은 몇 주마다 해야 해?", "expected_outcome": "UNCERTAIN"}
```

#### Case 8: Shampoo Recommendation (Product, Out of Scope)
```json
{"query_id": "care_002", "question": "약용 샴푸를 추천해줘", "expected_outcome": "UNCERTAIN"}
```

---

## Not Implemented

None of these cases have been added to `backend/data/eval/queries/training_api_eval_v1.jsonl` or any other evaluation dataset in this PR. This document is a candidate list only.

If a future PR sources and ingests approved bathing-aversion content, that PR should:

1. Decide the correct `expected_outcome` for each candidate case above against the *new* corpus (most should become `ANSWER`, not `UNCERTAIN`).
2. Add the finalized cases to the evaluation dataset following that dataset's current schema and conventions.
3. Run the full existing evaluation suite alongside the new cases to confirm no regressions (existing answerable cases, existing insufficient-evidence cases, medical/safety guardrails, serving corpus configuration).

This investigation performed none of those steps — it stops at documenting the gap.
