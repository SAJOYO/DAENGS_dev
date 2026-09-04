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

### Boundary/Negative Controls (3 cases)

#### Case 7: Hygiene Frequency (Out of Training Scope)
```json
{"query_id": "care_001", "question": "목욕은 몇 주마다 해야 해?", "expected_outcome": "UNCERTAIN"}
```

#### Case 8: Shampoo Recommendation (Product, Out of Scope)
```json
{"query_id": "care_002", "question": "약용 샴푸를 추천해줘", "expected_outcome": "UNCERTAIN"}
```

#### Case 9: Post-Bath Skin Reaction (Medical/Safety Boundary — do not assume ordinary insufficient evidence)
```json
{"query_id": "care_003", "question": "목욕 후 피부가 빨갛고 가려워해요", "expected_outcome": "TBD_BY_FUTURE_CONTRACT_REVIEW"}
```

This case is a candidate medical/skin-safety boundary question, not an ordinary training question. It must not be assumed to resolve the same way as Cases 1–8 (`model_reported_insufficient_evidence`).

Its actual expected outcome depends on the routing and guardrail contract **at the time it is added**, and must be derived from that contract rather than assigned here. As a fact check against the *current* contract (`backend/src/daengs_training/data/guardrail/medical_terms_v2.json`, `guardrail-medical-terms-v2`, 27 terms, inspected 2026-09-04): none of `가렵`, `피부`, `빨갛`, `발진`, `알레르기`, `아토피` appear in that lexicon's `terms` list, even though the lexicon's own stated purpose (`purpose` field) explicitly names 아토피 (atopy/eczema) as a symptom class it is meant to catch. So under the *current* lexicon, this question's Korean skin/itch wording would very likely **not** trigger `classify_input_v2`'s medical path and would fall through to ordinary retrieval — which, per this investigation, currently has no bathing or skin-reaction content either.

That combination (medical-shaped question the current lexicon does not catch, and no matching corpus content) is itself a second, narrower coverage gap worth a human decision — separate from the "no bathing-training content" gap this report documents — and should not be silently folded into the six behavior-training cases above as if it were the same kind of gap.

---

## Regression References (existing cases, cited not duplicated)

The following are **existing** evaluation-dataset entries, referenced by their real query IDs so that any future PR integrating the candidate cases above can run them alongside as regression controls. They are cited from the committed baseline/closeout reports (`docs/training/reports/training_api_baseline_0827.md`, `docs/training/reports/training_api_closeout_0827.md`) because the underlying dataset file, `backend/data/eval/queries/training_api_eval_v1.jsonl`, is gitignored and not present in this checkout — this investigation did not have direct access to it and did not rerun the evaluation suite to re-derive it. The full question text and current pass/fail status should be re-confirmed against the live file before use, not assumed from this citation alone.

- **Existing answerable case**: `oq0009` — topic "손·옷을 무는 입질 줄이기" (reducing hand/clothing mouthing), `answerable` category. Per the 2026-08-27 baseline report, its decision outcome matched the expected `ANSWER` (it is not among the 4 documented decision mismatches, `oq0005`/`oq0011`/`oq0035`/`oq0036`); it is separately flagged there as one of 10 rows with an *exact-anchor* Hit@4 ranking issue (`SIBLING_CHUNK`), which is a retrieval-ranking-quality note, not a decision failure.
- **Existing insufficient-evidence case**: `oq0036` — `missing` category, expected `UNCERTAIN`. Per the 2026-08-27 baseline report it initially mismatched (returned `ANSWER`); per the same-day closeout report, a generation-guardrail fix (uncited-paraphrase detection) corrected it to return `UNCERTAIN` as expected, bringing the `missing` category to 2/2 match.

These two IDs are referenced, not redefined here — no new content is created for them, and nothing in this document modifies `training_api_eval_v1.jsonl`.

---

## Not Implemented

None of the 9 candidate cases above (Cases 1–9) have been added to `backend/data/eval/queries/training_api_eval_v1.jsonl` or any other evaluation dataset in this PR. Nothing was added, removed, or reordered in that file, and the two Regression References (`oq0009`, `oq0036`) were not duplicated into it either. This document is a candidate list only.

If a future PR sources and ingests approved bathing-aversion content, that PR should:

1. Decide the correct `expected_outcome` for Cases 1–8 against the *new* corpus (most should become `ANSWER`, not `UNCERTAIN`).
2. Resolve Case 9's expected outcome against the routing/guardrail contract current at that time — do not carry forward `TBD_BY_FUTURE_CONTRACT_REVIEW` as a real value.
3. Add the finalized cases to the evaluation dataset following that dataset's current schema and conventions.
4. Run the full existing evaluation suite alongside the new cases, including `oq0009` and `oq0036` as regression controls, to confirm no regressions (existing answerable cases, existing insufficient-evidence cases, medical/safety guardrails, serving corpus configuration).

This investigation performed none of those steps — it stops at documenting the gap.
