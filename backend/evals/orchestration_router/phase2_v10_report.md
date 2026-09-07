# Card 2A Phase 2 acceptance result

- Verdict: **PASS**
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
| `exact_route_plan_match` | 0.9875 | >= 0.9 | yes |
| `executable_precision` | 0.986842 | >= 0.95 | yes |
| `executable_recall` | 1 | >= 0.95 | yes |
| `multi_execute_recall` | 1 | >= 0.9 | yes |
| `exact_executable_set_accuracy_multi` | 1 | >= 0.9 | yes |
| `skin_handoff_recall` | 1 | >= 1 | yes |
| `gait_handoff_recall` | 1 | >= 1 | yes |
| `handoff_precision` | 1 | >= 0.95 | yes |
| `exact_mixed_execute_handoff_match` | 1 | >= 0.9 | yes |
| `clarify_precision` | 1 | >= 0.9 | yes |
| `clarify_recall` | 1 | >= 0.9 | yes |

## Failures

- Case IDs: walk_03
- Buckets: {"EXTRA_EXECUTE": 1}

Latency and token measurements are observational only.

## social_intent (v4 addition)

None of the 80 gold cases is pure small talk, so every case is expected to leave
`social_intent` null; capability intent must take precedence over social wording.

- Non-null `social_intent` cases: 0 / 80

## `general` — two views (D-057 ⑤)

The frozen gold has no `general`, so every `general` the v9 router adds is a precision
miss by that gold and the intended policy by D-057. Both views are scored from the ONE
paid run above; the stripped view is what production builds with the flag off.

- Cases where the router selected `general`: 0 / 80
- Case IDs: none

| View | Verdict | exact match | executable precision | executable recall |
| --- | :---: | ---: | ---: | ---: |
| raw (general counted) | PASS | 0.9875 | 0.986842 | 1 |
| general-stripped | PASS | 0.9875 | 0.986842 | 1 |

### general-stripped gate checks

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
| `exact_mixed_execute_handoff_match` | 1 | >= 0.9 | yes |
| `clarify_precision` | 1 | >= 0.9 | yes |
| `clarify_recall` | 1 | >= 0.9 | yes |

- general-stripped failures: walk_03
