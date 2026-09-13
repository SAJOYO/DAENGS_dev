# 앞 대화 기억 — `tr_v1`

리졸버 gemini-3.1-flash-lite · turn-resolver-ko-v1 · 확신 바닥 0.6 · 판정기 없음 · 2026-09-11T08:19:38+00:00

대본 33 · 맞음 76% [59, 87] · 리졸버 모델 호출 25/33

## 층별

| 층 | 맞음 / n | 비율 [95% CI] |
| --- | --- | --- |
| new_unrelated | 5 / 6 | 83% [44, 97] |
| followup_marker | 5 / 6 | 83% [44, 97] |
| followup_no_marker | 0 / 5 | 0% [0, 43] |
| correction | 5 / 5 | 100% [57, 100] |
| repeat | 3 / 4 | 75% [30, 95] |
| meta | 3 / 3 | 100% [44, 100] |
| pending_answer | 4 / 4 | 100% [51, 100] |

## 혼동행렬 (행 = 기대, 열 = 리졸버가 낸 것)

| 기대 \ 결과 | NEW | FAST_NEW | FOLLOW_UP | CORRECTION | REPEAT | META |
| --- | --- | --- | --- | --- | --- | --- |
| **NEW** | 3 | 3 | 1 |  |  |  |
| **FOLLOW_UP** |  | 5 | 8 |  | 1 |  |
| **CORRECTION** |  |  |  | 5 |  |  |
| **REPEAT** |  |  | 1 |  | 3 |  |
| **META** |  |  |  |  |  | 3 |

## 틀린 것 (8)

- `tr_new_06` [new_unrelated] "말고 다른 건 없어요? 산책 코스 추천 같은 거" 기대 NEW → **FOLLOW_UP** (확신 1.00)
- `tr_fu_02` [followup_marker] "아까 말한 거 다시 설명해줘" 기대 FOLLOW_UP → **REPEAT** (확신 1.00)
- `tr_fun_01` [followup_no_marker] "빗은 어떤 걸 써요?" 기대 FOLLOW_UP → **FAST_NEW** (확신 1.00)
- `tr_fun_02` [followup_no_marker] "비 오는 날은요?" 기대 FOLLOW_UP → **FAST_NEW** (확신 1.00)
- `tr_fun_03` [followup_no_marker] "한 번에 양은요?" 기대 FOLLOW_UP → **FAST_NEW** (확신 1.00)
- `tr_fun_04` [followup_no_marker] "비용은 보통 얼마나 들어요?" 기대 FOLLOW_UP → **FAST_NEW** (확신 1.00)
- `tr_fun_05` [followup_no_marker] "효과 보려면 얼마나 걸려요?" 기대 FOLLOW_UP → **FAST_NEW** (확신 1.00)
- `tr_rep_03` [repeat] "발을 전다고 했잖아 뭘 봐야 하는지 알려줘" 기대 REPEAT → **FOLLOW_UP** (확신 0.95)

## 배선 확인

- 관계를 썼는데 라우터 프롬프트가 `-resolved` 가 아닌 대본: **0** (0 이어야 한다)
- 토큰: resolver 22496/1574 · router 55236/976 · general 42648/2173

## 읽는 법

- `FAST_NEW` 는 표지어 정규식이 모델을 안 태운 것. `followup_no_marker` 층에서 이게 나오면 이어짐을 **구조적으로** 놓친 것이다.
- `LOW_CONFIDENCE` 는 관계는 맞혔을 수 있지만 서비스가 버린 것. 틀린 것과 따로 센다.
- `META` 기대는 리졸버 프롬프트에는 있고 열거형에는 없는 값이다 — 어디로 접히는지가 결과다.
