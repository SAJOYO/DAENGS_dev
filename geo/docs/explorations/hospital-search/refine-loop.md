---
status: parked
implementation: working-skeleton
last_verified: 2026-08-24
---
# 대화로 조건을 편집하는 루프

> 상태 편집·툴 검증·턴 단위 undo·Fake/OpenAI 경계까지 구현돼 있다. 그러나 실제 장소 데이터로
> 편집할 수 있는 조건이 부족해 결정 #51 이후 제품 코어에서는 parked다.

첫 검색은 **필터 없는 초안**. 마음에 안 들면 조건을 바꿔 재검색 — 사람이 원래 그렇게 함.

```
[초안] 좌표만 → search() → 화면
[대화] "너무 멀어" / "여기 말고" / "밤에 하는 데"
[LLM]  현재 상태 + 발화 → 툴 호출로 상태 변경
[검색] 바뀐 상태로 search()
[화면] 갱신 + changes 한 줄
```

LLM은 **검색을 하지 않는다. 조건을 편집한다.** 검색은 결정론적 함수 하나.

## 툴 (현재 계약)
`set_origin`, `set_radius`, `set_open_now`, `set_night_service`, `set_emergency_service`,
`set_urgency`, `set_time_intent`, `note_symptoms`, `require`, `exclude`, `pin`,
`set_mode`, `set_max_total_min`, `set_walk_max_min`,
`set_sort`, `undo`, `reset`, `ask`
- `set_specialty` 없음 — 결정 #64. 한국 수의 진료에 과목 제도가 없다
- `set_walk_option`·`set_walk_avoid` 없음 — 결정 #66. 계단은 288경로에서 0회였다
- `min_rating` 없음 — 의도적. 데이터 없는 병원이 불리해지는 왜곡
- `exclude`의 "두 번째"는 사용자가 본 화면 기준 → 요청에 `shown_ids` 순서 포함

## 상태
`EditableState(state_version=4)`가 context / target / journey / view와 `history`를 묶는다. 서버는
무상태이며 클라이언트가 현재 state를 왕복한다. 알 수 없는 버전·필드·툴·인자는 422다.

## 응답
`changes: string[]`는 **서버가 상태 diff로 생성.** LLM이 "좁혔어요"라 말했는데 안 바뀐 상황 방지.

## `ask()`가 실제로 쓰이는 지점
할매(안과·걸어서·낮) 케이스: 안과 특화는 멀리(2차 센터), 걸어서는 가까이 → 충돌. 검색이 못 풀고 "가까운 일반 병원 vs 멀지만 안과 있는 센터" 되물어야 함.
