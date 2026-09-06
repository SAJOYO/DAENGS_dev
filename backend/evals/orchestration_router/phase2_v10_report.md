# Card 2A Phase 2 acceptance result

- Verdict: **FAIL**
- Model: `gemini-3.1-flash-lite`
- Prompt: `semantic-router-ko-v9`
- Freeze: `204b12fa774ebcc324eb55863d0f39dbcefa892b`
- Cases / attempts / retries: 80 / 80 / 0

## Frozen gate checks

| Metric | Actual | Gate | Pass |
| --- | ---: | ---: | :---: |
| `final_schema_valid_rate` | 1 | >= 1 | yes |
| `first_pass_schema_valid_rate` | 1 | >= 0.975 | yes |
| `forbidden_execute_count` | 0 | <= 0 | yes |
| `invented_unsupported_capability_count` | 0 | <= 0 | yes |
| `exact_route_plan_match` | 0.6125 | >= 0.9 | no |
| `executable_precision` | 0.700935 | >= 0.95 | no |
| `executable_recall` | 1 | >= 0.95 | yes |
| `multi_execute_recall` | 1 | >= 0.9 | yes |
| `exact_executable_set_accuracy_multi` | 0.25 | >= 0.9 | no |
| `skin_handoff_recall` | 1 | >= 1 | yes |
| `gait_handoff_recall` | 1 | >= 1 | yes |
| `handoff_precision` | 1 | >= 0.95 | yes |
| `exact_mixed_execute_handoff_match` | 0.4 | >= 0.9 | no |
| `clarify_precision` | 1 | >= 0.9 | yes |
| `clarify_recall` | 1 | >= 0.9 | yes |

## Failures

- Case IDs: training_01, training_02, training_03, training_04, training_05, training_06, training_07, training_08, training_09, training_10, life_09, walk_03, walk_08, multi_01, multi_02, multi_04, multi_05, multi_06, multi_07, multi_08, multi_11, multi_12, handoff_06, mixed_01, mixed_02, mixed_04, mixed_05, mixed_07, mixed_09, boundary_01, boundary_04
- Buckets: {"EXTRA_EXECUTE": 31}

Latency and token measurements are observational only.

## social_intent (v4 addition)

None of the 80 gold cases is pure small talk, so every case is expected to leave
`social_intent` null; capability intent must take precedence over social wording.

- Non-null `social_intent` cases: 0 / 80

## `general` — two views (D-056 ⑤)

The frozen gold has no `general`, so every `general` the v9 router adds is a precision
miss by that gold and the intended policy by D-056. Both views are scored from the ONE
paid run above; the stripped view is what production builds with the flag off.

- Cases where the router selected `general`: 32 / 80
- Case IDs: boundary_01, boundary_04, clarify_07, clarify_12, handoff_06, life_09, mixed_01, mixed_02, mixed_04, mixed_05, mixed_07, mixed_09, multi_01, multi_02, multi_04, multi_05, multi_06, multi_07, multi_08, multi_11, multi_12, training_01, training_02, training_03, training_04, training_05, training_06, training_07, training_08, training_09, training_10, walk_08

| View | Verdict | exact match | executable precision | executable recall |
| --- | :---: | ---: | ---: | ---: |
| raw (general counted) | FAIL | 0.6125 | 0.700935 | 1 |
| general-stripped | PASS | 0.975 | 0.974026 | 1 |

### general-stripped gate checks

| Metric | Actual | Gate | Pass |
| --- | ---: | ---: | :---: |
| `final_schema_valid_rate` | 1 | >= 1 | yes |
| `first_pass_schema_valid_rate` | 1 | >= 0.975 | yes |
| `forbidden_execute_count` | 0 | <= 0 | yes |
| `invented_unsupported_capability_count` | 0 | <= 0 | yes |
| `exact_route_plan_match` | 0.975 | >= 0.9 | yes |
| `executable_precision` | 0.974026 | >= 0.95 | yes |
| `executable_recall` | 1 | >= 0.95 | yes |
| `multi_execute_recall` | 1 | >= 0.9 | yes |
| `exact_executable_set_accuracy_multi` | 1 | >= 0.9 | yes |
| `skin_handoff_recall` | 1 | >= 1 | yes |
| `gait_handoff_recall` | 1 | >= 1 | yes |
| `handoff_precision` | 1 | >= 0.95 | yes |
| `exact_mixed_execute_handoff_match` | 0.9 | >= 0.9 | yes |
| `clarify_precision` | 1 | >= 0.9 | yes |
| `clarify_recall` | 1 | >= 0.9 | yes |

- general-stripped failures: walk_03, mixed_09
