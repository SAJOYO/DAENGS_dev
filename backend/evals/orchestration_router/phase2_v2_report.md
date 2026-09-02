# Card 2A Phase 2 acceptance result

- Verdict: **FAIL**
- Model: `gemini-3.5-flash-lite`
- Prompt: `semantic-router-ko-v2`
- Freeze: `204b12fa774ebcc324eb55863d0f39dbcefa892b`
- Cases / attempts / retries: 80 / 80 / 0

## Frozen gate checks

| Metric | Actual | Gate | Pass |
| --- | ---: | ---: | :---: |
| `final_schema_valid_rate` | 1 | >= 1 | yes |
| `first_pass_schema_valid_rate` | 1 | >= 0.975 | yes |
| `forbidden_execute_count` | 0 | <= 0 | yes |
| `invented_unsupported_capability_count` | 0 | <= 0 | yes |
| `exact_route_plan_match` | 0.975 | >= 0.9 | yes |
| `executable_precision` | 1 | >= 0.95 | yes |
| `executable_recall` | 0.973684 | >= 0.95 | yes |
| `multi_execute_recall` | 0.972222 | >= 0.9 | yes |
| `exact_executable_set_accuracy_multi` | 0.941176 | >= 0.9 | yes |
| `skin_handoff_recall` | 1 | >= 1 | yes |
| `gait_handoff_recall` | 0.9 | >= 1 | no |
| `handoff_precision` | 1 | >= 0.95 | yes |
| `exact_mixed_execute_handoff_match` | 0.8 | >= 0.9 | no |
| `clarify_precision` | 0.923077 | >= 0.9 | yes |
| `clarify_recall` | 1 | >= 0.9 | yes |

## Failures

- Case IDs: mixed_07, mixed_09
- Buckets: {"MULTI_INTENT_COLLAPSE": 1, "WRONG_CLARIFY": 1}

Latency and token measurements are observational only.
