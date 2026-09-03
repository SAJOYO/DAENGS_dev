# Card 2A Phase 2 acceptance result

- Verdict: **FAIL**
- Model: `gemini-3.1-flash-lite`
- Prompt: `semantic-router-ko-v5`
- Freeze: `204b12fa774ebcc324eb55863d0f39dbcefa892b`
- Cases / attempts / retries: 80 / 80 / 0

## Frozen gate checks

| Metric | Actual | Gate | Pass |
| --- | ---: | ---: | :---: |
| `final_schema_valid_rate` | 1 | >= 1 | yes |
| `first_pass_schema_valid_rate` | 1 | >= 0.975 | yes |
| `forbidden_execute_count` | 0 | <= 0 | yes |
| `invented_unsupported_capability_count` | 0 | <= 0 | yes |
| `exact_route_plan_match` | 0.95 | >= 0.9 | yes |
| `executable_precision` | 0.961039 | >= 0.95 | yes |
| `executable_recall` | 0.986667 | >= 0.95 | yes |
| `multi_execute_recall` | 0.970588 | >= 0.9 | yes |
| `exact_executable_set_accuracy_multi` | 0.9375 | >= 0.9 | yes |
| `skin_handoff_recall` | 1 | >= 1 | yes |
| `gait_handoff_recall` | 1 | >= 1 | yes |
| `handoff_precision` | 1 | >= 0.95 | yes |
| `exact_mixed_execute_handoff_match` | 0.8 | >= 0.9 | no |
| `clarify_precision` | 1 | >= 0.9 | yes |
| `clarify_recall` | 0.916667 | >= 0.9 | yes |

## Failures

- Case IDs: mixed_09, mixed_10, clarify_08, boundary_05
- Buckets: {"EXTRA_EXECUTE": 2, "MISSED_CLARIFY": 1, "MULTI_INTENT_COLLAPSE": 1}

Latency and token measurements are observational only.

## social_intent (v4 addition)

None of the 80 gold cases is pure small talk, so every case is expected to leave
`social_intent` null; capability intent must take precedence over social wording.

- Non-null `social_intent` cases: 0 / 80
