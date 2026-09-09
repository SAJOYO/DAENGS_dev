# 개체 적합성 — `pf_v1`

판정 gpt-5.4-2026-03-05 · profile-fitness-diff-ko-v1a · 생성 gemini-3.1-flash-lite (general-answer-ko-v3) · 2026-09-09T02:42:42+00:00

> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.

## 부등식 N < S ≪ P — 이 지표가 작동하는가

| 조건 | 무엇 | changed 비율 [95% CI] |
| --- | --- | --- |
| **N** 잡음 | 같은 프로필 두 번 | 0% [0, 10] (n=34) |
| **S** 특이도 | 다른 프로필 · 법령류 | 100% [51, 100] (n=4) |
| **P** 절제 | 프로필 없음 vs 있음 | 80% [58, 92] (n=20) |
| 본 비교 | 다른 프로필 · reactive | 94% [74, 99] (n=18) |

- N < S: **True** · S < P: **False** · 전체: **False**
- P − N (문항 단위 부트스트랩, 20문항): +0.80 [+0.60, +0.95] — 0 을 안 걸침
- probe 에서 날조: 0% [0, 56] (n=3)

## 항목 (종합 점수는 없다)

| 항목 | 평균 / 만점 | n | 분포 | 게이트 |
| --- | --- | --- | --- | --- |
| responsiveness | 1.66 / 2 | 38 | {2: 30, 1: 3, 0: 5} | not_calibrated |
| invariance | 0.00 / 1 | 4 | {0: 4} | not_calibrated |
| no_fabrication | 1.00 / 1 | 45 | {1: 45} | not_calibrated |

### 계층별

- tier=coarse: responsiveness 1.86 (n=21), no_fabrication 1.00 (n=26), invariance 0.00 (n=3)
- tier=fine: responsiveness 1.41 (n=17), no_fabrication 1.00 (n=19), invariance 0.00 (n=1)
- kind=reactive: responsiveness 1.66 (n=38), no_fabrication 1.00 (n=38)
- kind=invariant: invariance 0.00 (n=4), no_fabrication 1.00 (n=4)
- kind=probe: no_fabrication 1.00 (n=3)

## 실패 종류 (채점된 쌍 45개)

| 실패 | 건수 |
| --- | --- |
| ignored | 5 |
| fabricated | 0 |
| stereotype | 2 |
| over_personalized | 2 |

## 못 잰 것

- 쌍 151 중 판정 90 · 판정 전 제외 {'not_answered': 50, 'out_of_scope': 11} · 위치 뒤집힘 11 · 기권 0 → 미측정 비율 **0.477**
- 패러프레이즈 일치: 0.889 (18쌍)
- 비용: 입력 266,176 / 출력 31,940 토큰 · 1000쌍당 3,312,400

## 측정 대상이 아닌 것

- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)
- Life 경로 — 실서버(pgvector · Redis) 필요. v1 은 `general` 만
- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다

> v1a 전(前) 기록 — 프롬프트 v2 · 질문 v1.1 로 가기 전 상태
