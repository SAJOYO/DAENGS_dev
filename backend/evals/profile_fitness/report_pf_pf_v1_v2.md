# 개체 적합성 — `pf_v1`

판정 gpt-5.4-2026-03-05 · profile-fitness-diff-ko-v2a · 생성 gemini-3.1-flash-lite (general-answer-ko-v3) · 2026-09-09T03:01:24+00:00

> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.

## 부등식 N < S ≪ P — 이 지표가 작동하는가

| 조건 | 무엇 | changed 비율 [95% CI] |
| --- | --- | --- |
| **N** 잡음 | 같은 프로필 두 번 | 0% [0, 10] (n=34) |
| **S** 특이도 | 다른 프로필 · 법령류 | 29% [8, 64] (n=7) |
| **P** 절제 | 프로필 없음 vs 있음 | 65% [41, 83] (n=17) |
| 본 비교 | 다른 프로필 · reactive | 64% [43, 80] (n=22) |

- N < S: **True** · S < P: **True** · 전체: **True**
- P − N (문항 단위 부트스트랩, 18문항): +0.67 [+0.44, +0.89] — 0 을 안 걸침
- probe 에서 날조: 0% [0, 66] (n=2)

## 항목 (종합 점수는 없다)

| 항목 | 평균 / 만점 | n | 분포 | 게이트 |
| --- | --- | --- | --- | --- |
| responsiveness | 1.13 / 2 | 39 | {2: 19, 0: 14, 1: 6} | not_calibrated |
| invariance | 0.71 / 1 | 7 | {1: 5, 0: 2} | not_calibrated |
| no_fabrication | 1.00 / 1 | 48 | {1: 48} | not_calibrated |

### 계층별

- tier=coarse: responsiveness 1.14 (n=22), no_fabrication 1.00 (n=29), invariance 0.80 (n=5)
- tier=fine: responsiveness 1.12 (n=17), no_fabrication 1.00 (n=19), invariance 0.50 (n=2)
- kind=reactive: responsiveness 1.13 (n=39), no_fabrication 1.00 (n=39)
- kind=invariant: invariance 0.71 (n=7), no_fabrication 1.00 (n=7)
- kind=probe: no_fabrication 1.00 (n=2)

## 실패 종류 (채점된 쌍 48개)

| 실패 | 건수 |
| --- | --- |
| ignored | 14 |
| fabricated | 0 |
| stereotype | 2 |
| over_personalized | 2 |

## 못 잰 것

- 쌍 114 중 판정 90 · 판정 전 제외 {'out_of_scope': 14, 'not_answered': 10} · 위치 뒤집힘 7 · 기권 1 → 미측정 비율 **0.281**
- 패러프레이즈 일치: 0.722 (18쌍)
- 비용: 입력 288,856 / 출력 36,913 토큰 · 1000쌍당 3,619,656

## 측정 대상이 아닌 것

- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)
- Life 경로 — 실서버(pgvector · Redis) 필요. v1 은 `general` 만
- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다

> 같은 답변(pf_v1 셀)을 판정 v2 로. v1a 리포트와 나란히 본다
