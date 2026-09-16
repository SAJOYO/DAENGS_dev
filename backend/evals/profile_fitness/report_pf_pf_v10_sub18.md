# 개체 적합성 — `pf_v10_sub18`

판정 gpt-5.4-2026-03-05 · profile-fitness-diff-ko-v2.1a · 생성 gemini-3.1-flash-lite (general-answer-ko-v10) · 2026-09-16T00:45:27+00:00

> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.

## 부등식 N < S ≪ P — 이 지표가 작동하는가

| 조건 | 무엇 | changed 비율 [95% CI] |
| --- | --- | --- |
| **N** 잡음 | 같은 프로필 두 번 | — |
| **S** 특이도 | 다른 프로필 · 법령류 | 62% [31, 86] (n=8) |
| **P** 절제 | 프로필 없음 vs 있음 | 50% [19, 81] (n=6) |
| 본 비교 | 다른 프로필 · reactive | 71% [36, 92] (n=7) |

- N < S: **None** · S < P: **False** · 전체: **None**
- P − N (문항 단위 부트스트랩, 0문항): —
- probe 에서 날조: —

## 항목 (종합 점수는 없다)

| 항목 | 평균 / 만점 | n | 분포 | 게이트 |
| --- | --- | --- | --- | --- |
| responsiveness | 1.15 / 2 | 13 | {2: 7, 0: 5, 1: 1} | not_calibrated |
| invariance | 0.38 / 1 | 8 | {1: 3, 0: 5} | not_calibrated |
| no_fabrication | 1.00 / 1 | 21 | {1: 21} | not_calibrated |

### 계층별

- tier=coarse: responsiveness 1.15 (n=13), no_fabrication 1.00 (n=19), invariance 0.33 (n=6)
- tier=fine: invariance 0.50 (n=2), no_fabrication 1.00 (n=2)
- kind=reactive: responsiveness 1.15 (n=13), no_fabrication 1.00 (n=13)
- kind=invariant: invariance 0.38 (n=8), no_fabrication 1.00 (n=8)

## 실패 종류 (채점된 쌍 21개)

| 실패 | 건수 |
| --- | --- |
| ignored | 5 |
| fabricated | 0 |
| stereotype | 0 |
| over_personalized | 0 |

## 못 잰 것

- 쌍 28 중 판정 21 · 판정 전 제외 {'not_answered': 3, 'out_of_scope': 4} · 위치 뒤집힘 0/0 (양방향 본 쌍 중) · 기권 0 → 미측정 비율 **0.25**
- 패러프레이즈 일치: None (0쌍)
- 비용: 입력 38,999 / 출력 5,797 토큰 · 1000쌍당 2,133,143

## 측정 대상이 아닌 것

- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)
- Life 경로 — 이 수집에선 가짜 어댑터(자리표시). 팀 DB(pgvector) 가 있어야 진짜로 돈다 — `--adapters life`
- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다
