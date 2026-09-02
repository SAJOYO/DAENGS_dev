# Card 2A Phase 2 acceptance result

- Verdict: **FAIL**
- Model: `gemini-3.5-flash-lite`
- Prompt: `semantic-router-ko-v1`
- Freeze: `204b12fa774ebcc324eb55863d0f39dbcefa892b`
- Cases / attempts / retries: 80 / 83 / 3

## Frozen gate checks

| Metric | Actual | Gate | Pass |
| --- | ---: | ---: | :---: |
| `final_schema_valid_rate` | 0.9875 | >= 1 | no |
| `first_pass_schema_valid_rate` | 0.9625 | >= 0.975 | no |
| `forbidden_execute_count` | 0 | <= 0 | yes |
| `invented_unsupported_capability_count` | 0 | <= 0 | yes |
| `exact_route_plan_match` | 0.8125 | >= 0.9 | no |
| `executable_precision` | 0.91358 | >= 0.95 | no |
| `executable_recall` | 0.973684 | >= 0.95 | yes |
| `multi_execute_recall` | 0.972222 | >= 0.9 | yes |
| `exact_executable_set_accuracy_multi` | 0.941176 | >= 0.9 | yes |
| `skin_handoff_recall` | 1 | >= 1 | yes |
| `gait_handoff_recall` | 0.7 | >= 1 | no |
| `handoff_precision` | 0.9 | >= 0.95 | no |
| `exact_mixed_execute_handoff_match` | 0.9 | >= 0.9 | yes |
| `clarify_precision` | 1 | >= 0.9 | yes |
| `clarify_recall` | 0.333333 | >= 0.9 | no |

## Failures

- Case IDs: training_01, handoff_07, handoff_08, handoff_09, mixed_09, clarify_04, clarify_06, clarify_07, clarify_08, clarify_09, clarify_10, clarify_11, clarify_12, boundary_02, boundary_05
- Buckets: {"MISSED_CLARIFY": 7, "MISSED_EXECUTE": 1, "MISSED_HANDOFF": 3, "MULTI_INTENT_COLLAPSE": 1, "PAYLOAD_MISMATCH": 2, "SCHEMA_FAILURE": 1}

Latency and token measurements are observational only.
