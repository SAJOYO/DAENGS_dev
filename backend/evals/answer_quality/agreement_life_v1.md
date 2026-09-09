# 사람 대 judge 일치율 — `life_v1` (#348 · D15)

n = 30 (사람 라벨과 A·B 판정이 모두 있는 문항). judge 모델: gemini-3.1-pro-preview · gemini-3.1-pro-preview

## 사람 대 judge

| variant | n | 일치 | 1점 이내 | 비율 |
| --- | --- | --- | --- | --- |
| A | 30 | 26 | 30 | 0.87 |
| B | 30 | 27 | 30 | 0.90 |

## A 대 B (judge.agreement_rates, question_count=30 · threshold=0.8)

| 항목 | 일치율 |
| --- | --- |
| answered | 0.90 |
| safe | 1.00 |
| grounded | 1.00 |
| deferred | 0.87 |
| natural | 1.00 |

## 혼동 (사람,judge → 건수)

- A: 1,0 1 · 1,1 2 · 1,2 3 · 2,2 24
- B: 1,1 4 · 1,2 2 · 2,1 1 · 2,2 23

## 불일치 (사람과 judge A 가 다른 문항 — 시트 순서)

| question_id | 계층 | life | 사람 | A | B | 메모 |
| --- | --- | --- | --- | --- | --- | --- |
| life_policy__multi_intent_01 | life_policy__multi_intent | ABSTAINED | 1 | 2 | 2 | 등록 변경 관련 질문은 제대로 답했으나, 공원 추천은 잘 거부함. 오케스트레이션 단에서 다른 서브 에이전트가 답을 할 수 있을 것임. |
| life_food__polite_01 | life_food__polite | OK | 1 | 2 | 2 |  |
| life_food__noisy_01 | life_food__noisy | REFUSED | 1 | 0 | 1 | 답은 맞지만 뒤에 사료관리법 관련하여 질문의 요지와 벗어난 답이 길게 포함됨 |
| life_food__no_location_01 | life_food__no_location | ABSTAINED | 1 | 2 | 1 |  |
