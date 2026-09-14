# 개체 적합성 — `pf_v1_1`

판정 gpt-5.4-2026-03-05 · profile-fitness-diff-ko-v3a · 생성 gemini-3.1-flash-lite (general-answer-ko-v3) · 2026-09-09T03:22:10+00:00

> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.

## 부등식 N < S ≪ P — 이 지표가 작동하는가

| 조건 | 무엇 | changed 비율 [95% CI] |
| --- | --- | --- |
| **N** 잡음 | 같은 프로필 두 번 | 0% [0, 28] (n=10) |
| **S** 특이도 | 다른 프로필 · 법령류 | — |
| **P** 절제 | 프로필 없음 vs 있음 | 57% [25, 84] (n=7) |
| 본 비교 | 다른 프로필 · reactive | 50% [22, 78] (n=8) |

- N < S: **None** · S < P: **None** · 전체: **None**
- P − N (문항 단위 부트스트랩, 6문항): +0.67 [+0.33, +1.00] — 0 을 안 걸침
- probe 에서 날조: —

## 항목 (종합 점수는 없다)

| 항목 | 평균 / 만점 | n | 분포 | 게이트 |
| --- | --- | --- | --- | --- |
| responsiveness | 0.93 / 2 | 15 | {2: 6, 0: 7, 1: 2} | not_calibrated |
| invariance | — | 0 | — | not_calibrated |
| no_fabrication | 1.00 / 1 | 15 | {1: 15} | not_calibrated |

### 계층별

- tier=coarse: responsiveness 0.91 (n=11), no_fabrication 1.00 (n=11)
- tier=fine: responsiveness 1.00 (n=4), no_fabrication 1.00 (n=4)
- kind=reactive: responsiveness 0.93 (n=15), no_fabrication 1.00 (n=15)

## 실패 종류 (채점된 쌍 15개)

| 실패 | 건수 |
| --- | --- |
| ignored | 7 |
| fabricated | 0 |
| stereotype | 1 |
| over_personalized | 0 |

## 못 잰 것

- 쌍 55 중 판정 32 · 판정 전 제외 {'out_of_scope': 15, 'not_answered': 8} · 위치 뒤집힘 6 · 기권 1 → 미측정 비율 **0.545**
- 패러프레이즈 일치: None (0쌍)
- 비용: —

## 측정 대상이 아닌 것

- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)
- Life 경로 — 실서버(pgvector · Redis) 필요. v1 은 `general` 만
- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다

> v1.1 질문 × 판정 v3 (대칭 관찰 · 기권)
