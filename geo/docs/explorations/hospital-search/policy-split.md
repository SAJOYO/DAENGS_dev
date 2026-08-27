---
status: adopted
date: 2026-08-19
decision: ../../decisions/README.md #25, #29
---
# 정책 분리 — target(어디를 갈까) vs journey(어떻게 갈까)

두 조건은 **결과에 하는 짓이 다르다.** 코드에 안 박아두면 나중에 조용히 섞인다.

| | target | journey |
|---|---|---|
| 질문 | 어디를 갈까 | 어떻게 갈까 |
| 결과에 하는 일 | **필터** — 안 맞으면 결과 집합에서 사라짐 | **판정** — 결과를 빼지 않음. 경로 계산 방식과 advice만 바꿈 |
| 소비처 | `geo.search.find_places` | `geo.transport.snapshot_for` |
| 내용 | 반경 · 영업중/야간/응급 · 특화 · 필수태그 · 제외/고정 · 기준시각 | 이동수단 · 도보 옵션 · 피할 시설 · 시간 상한 |

`view`(정렬·undo·reset)는 어느 쪽도 아니다. 표시 정책.

## 두 번째 축: `scope` (2026-08-20, #29)

policy 만으론 부족했다. journey 안에 **계층이 다른 것**이 섞여 있었다.

| | scope: any | scope: walk |
|---|---|---|
| 질문 | 무엇을 타고 갈까 | 걸어서 갈 때 어떤 길로 갈까 |
| 예 | `preferred_mode` · `max_total_min` | `walk.max_walk_min` |
| 툴 | `MODE_TOOLS` | `WALK_TOOLS` |

policy(결과를 바꾸나)와 scope(어느 수단에서 의미 있나)는 **직교한다.** 같은 journey 정책 안에서도
개가 걸어도 되는 시간은 도보에서만 뜻이 있고, 전체 이동시간은 무엇을 타든 뜻이 있다.

> 결정 #66 이후 `scope: walk` 에 남은 것은 `walk.max_walk_min` 하나다. 축이 하나라고 계층이
> 없어지는 것은 아니지만, 다시 하나도 안 남으면 이 갈래는 `MODE_TOOLS` 로 접는 게 맞다.

### 이게 없어서 생겼던 것

1. **하위가 상위를 세웠다.** (당시) `avoid()`·`set_walk_option()`·`set_max_min()`이 `mode`가 비어 있으면
   `walk`로 바꿔버렸다. 자식이 부모를 정하고, `applied`/`diff`에 안 남아서 사용자는 왜 도보가 됐는지 몰랐다.
   → 이제 자연어 층이 `set_mode(walk)`를 **따로 낸다.** 의도가 보인다.
2. **한 값이 두 뜻이었다.** `max_min` 하나가 `walk_advice`의 도보 상한이자 `hard_limit`의 전체 시간 필터였다.
   차량 10분 제한과 노견 도보 10분 제한이 같은 값으로 섞였다.
   → `max_total_min`(수단 무관) / `walk.max_walk_min`(개가 걸어도 되는 시간)으로 분리.
3. **표시가 안 갈렸다.** diff가 이동수단·도보 설정을 "가는 길" 한 덩어리로 쏟았다.
   → `preferred_mode`가 도보가 아니면 도보 설정은 `도보 대안 —` 을 붙여 전면에서 내린다.
   **숨기지는 않는다** — 사용자가 바꿨는데 아무 반응이 없으면 그게 더 나쁘고, 상태에도 남아야 한다.

### 불변식 (테스트로 강제)

```
walk 설정을 바꿔도 car leg 는 변하지 않는다       test_walk_only_settings_change_the_walk_leg_only
도보 툴은 preferred_mode 를 세우지 않는다          test_walk_scoped_tool_never_sets_the_mode
차량으로 바꿔도 도보 설정은 남는다                 test_walk_settings_survive_switching_to_car
전체 상한과 도보 상한은 서로 안 밀어낸다           test_total_and_walk_time_limits_are_separate
```

`mode=car` 인데 도보 판정이 존재하는 것 **자체는 버그가 아니다.** `Transport`는 늘 walk/car/transit 를
다 반환한다(#26). 문제는 범위가 새는 것과 표시가 안 갈리는 것이었다.

### car{} · transit{} 는 아직 없다

유료도로 회피·환승 최소를 넣으려면 **그걸 실제로 반영하는 경로 제공사가 먼저** 있어야 한다.
설정만 받고 무시하면 사용자에게 거짓말이 된다 — `health_flags`의 `heart`·`obesity`가 그렇게 죽어 있다.

## 코드 구조
```
SearchState
├── lat, lng                 공통 기준점
├── target: TargetPrefs      radius_m, open_now, night, emergency, at, require_tags, exclude_ids, pin_ids, limit
├── journey: JourneyPrefs    mode, walk{max_walk_min}, max_total_min, hard_limit
├── sort                     view
└── history                  undo 스택

tools.py   TARGET_TOOLS / JOURNEY_TOOLS / VIEW_TOOLS  (+ policy_of())
diff.py    changes_by_policy() → {target[], journey[], view[]}
nl.py      TOOL_SPECS에 policy 포함 → LLM도 두 정책을 구분해서 고른다
api        find_places(tg.*) / snapshot_for(jn.*) — 서로의 값을 넘기지 않는다
```

## 경계를 넘는 유일한 지점: `journey.hard_limit`
"15분 안"은 두 가지로 읽힌다 — "15분 넘으면 표시해줘" vs "빼줘". 기본은 **표시만**(`advice=avoid`),
사용자가 명시적으로 켤 때만 결과에서 제외하고 응답에 `(N곳은 시간 초과로 제외)`를 남긴다.

## 테스트로 지킨다 (tests/test_refine.py)
- 모든 툴이 정확히 한 정책에 속함 (누락·중복 없음)
- `TOOL_SPECS`가 모든 툴을 덮고 policy가 일치함 — 이 테스트가 `set_origin`·`unrequire`가 LLM 툴 목록에서 빠져 있던 걸 잡았다
- JOURNEY_TOOLS는 `target`을 건드리지 않음 / TARGET_TOOLS는 `journey`를 건드리지 않음
- `max_min`은 hard 없이는 필터가 아님

## 실측 (policy_check)
```
② journey 3연발(도보/계단제외/지하도 피함) → 결과 집합 동일, 순서만 바뀜
③ target '야간에 하는 데'                  → 6곳 → 3곳
⑤ 15분 상한 (표시만)                       → 6곳 유지, 5곳 advice=avoid
⑥ 15분 상한 (hard)                        → 1곳, "5곳은 시간 초과로 제외"
```

## 알려진 느슨한 곳
`set_mode`(journey)가 `sort`(view)를 duration으로 바꾼다 — 편의. view는 결과 집합을 안 바꾸니 정책 위반은 아니지만 유일한 교차점.
