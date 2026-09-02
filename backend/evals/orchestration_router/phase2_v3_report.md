# Card 2A Phase 2 acceptance result

- Verdict: **PASS**
- Model: `gemini-3.5-flash-lite`
- Prompt: `semantic-router-ko-v3`
- Freeze: `204b12fa774ebcc324eb55863d0f39dbcefa892b`
- Cases / attempts / retries: 80 / 80 / 0

## Frozen gate checks

| Metric | Actual | Gate | Pass |
| --- | ---: | ---: | :---: |
| `final_schema_valid_rate` | 1 | >= 1 | yes |
| `first_pass_schema_valid_rate` | 1 | >= 0.975 | yes |
| `forbidden_execute_count` | 0 | <= 0 | yes |
| `invented_unsupported_capability_count` | 0 | <= 0 | yes |
| `exact_route_plan_match` | 0.9875 | >= 0.9 | yes |
| `executable_precision` | 0.986842 | >= 0.95 | yes |
| `executable_recall` | 1 | >= 0.95 | yes |
| `multi_execute_recall` | 1 | >= 0.9 | yes |
| `exact_executable_set_accuracy_multi` | 1 | >= 0.9 | yes |
| `skin_handoff_recall` | 1 | >= 1 | yes |
| `gait_handoff_recall` | 1 | >= 1 | yes |
| `handoff_precision` | 1 | >= 0.95 | yes |
| `exact_mixed_execute_handoff_match` | 0.9 | >= 0.9 | yes |
| `clarify_precision` | 1 | >= 0.9 | yes |
| `clarify_recall` | 1 | >= 0.9 | yes |

## Failures

- Case IDs: mixed_09
- Buckets: {"EXTRA_EXECUTE": 1}

Latency and token measurements are observational only.
