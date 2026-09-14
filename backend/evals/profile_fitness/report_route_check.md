# 라우터가 고른 담당 vs 기대 (우리 셀, 모델 0회)

생성 2026-09-12T03:10:52+00:00

| 셀 묶음 | 라우터 | 셀 | 일치 | 일치율 [95% CI] |
| --- | --- | ---: | ---: | --- |
| pf_ask_v1 | semantic-router-ko-v10 | 40 | 32 | 80% [65, 90] |
| pf_v6_sub18 | semantic-router-ko-v10 | 46 | 40 | 87% [74, 94] |
| pf_carelog_v1 | semantic-router-ko-v10 | 34 | 31 | 91% [77, 97] |
| pf_life_v1 | semantic-router-ko-v10 | 40 | 36 | 90% [77, 96] |
| **전체** | | 160 | 139 | 87% [81, 91] |

## 불일치 (질문 단위)

| 셀 묶음 | 질문 | 기대 | 라우터가 고른 것 | 셀 | 상태 |
| --- | --- | --- | --- | ---: | --- |
| pf_ask_v1 | 초콜릿이 강아지한테 왜 위험해요? | general | vet_contact | 2 | ANSWERED |
| pf_ask_v1 | 오늘 컨디션 어때 보여? | general | handoff:gait | 2 | HANDOFF |
| pf_ask_v1 | 발을 좀 저는 것 같아요 | general | handoff:gait | 2 | HANDOFF |
| pf_ask_v1 | 숨이 너무 가빠요 어떡해요 | vet_contact | general | 2 | CLARIFY |
| pf_v6_sub18 | 놀아줄 때 어떤 놀이가 좋아요? | general | training | 3 | ANSWERED |
| pf_v6_sub18 | 여름에 산책 나갈 때 뭘 조심해야 해요? | general | walk+general | 3 | ANSWERED |
| pf_carelog_v1 | 오늘 우리 애 상태 어때 보여? | general | handoff:gait | 3 | HANDOFF |
| pf_life_v1 | 사료 살 때 어떤 단계 사료를 골라야 해요? | life | general | 4 | ANSWERED |

## 읽는 법

- 기대 담당은 **질문을 만들 때 정한 것**이다 (돌봄 질문 → general, Life 질문 → life, 응급 어휘 질문 → vet_contact).
  라우터 골드(`evals/orchestration_router/gold_v1.jsonl`, 80건)와는 다른 질문이라 그쪽 수치와 합치지 않는다.
- 불일치가 곧 틀림은 아니다. `walk+general`·`training` 은 그 담당이 같이 도는 것이라 답이 나왔고, 시험 ①은 그 답을 그대로 판정했다.
  `handoff:gait`(컨디션·저는 것 같아요) 와 `vet_contact`(초콜릿이 왜 위험해요) 는 시험 ③ 이 과잉 거절로 센 바로 그것이다.
- 그래서 이 표는 점수가 아니라 **사람이 가를 목록**이다. 틀린 것으로 정해지면 라우터 골드에 그 질문을 더하는 것이 다음 일이다.
