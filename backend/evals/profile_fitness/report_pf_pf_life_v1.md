# 개체 적합성 — `pf_life_v1`

판정 gpt-5.4-2026-03-05 · profile-fitness-diff-ko-v2.1a · 생성 gemini-3.1-flash-lite (general-answer-ko-v3) · 2026-09-09T08:00:48+00:00

> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.

## 부등식 N < S ≪ P — 이 지표가 작동하는가

| 조건 | 무엇 | changed 비율 [95% CI] |
| --- | --- | --- |
| **N** 잡음 | 같은 프로필 두 번 | 0% [0, 26] (n=11) |
| **S** 특이도 | 다른 프로필 · 법령류 | 33% [10, 70] (n=6) |
| **P** 절제 | 프로필 없음 vs 있음 | 67% [21, 94] (n=3) |
| 본 비교 | 다른 프로필 · reactive | 100% [44, 100] (n=3) |

- N < S: **True** · S < P: **True** · 전체: **True**
- P − N (문항 단위 부트스트랩, 3문항): +0.67 [+0.00, +1.00] — 0 을 걸침
- probe 에서 날조: —

## 항목 (종합 점수는 없다)

| 항목 | 평균 / 만점 | n | 분포 | 게이트 |
| --- | --- | --- | --- | --- |
| responsiveness | 1.67 / 2 | 6 | {2: 5, 0: 1} | not_calibrated |
| invariance | 0.67 / 1 | 6 | {1: 4, 0: 2} | not_calibrated |
| no_fabrication | 1.00 / 1 | 12 | {1: 12} | not_calibrated |

### 계층별

- tier=coarse: invariance 0.75 (n=4), no_fabrication 1.00 (n=8), responsiveness 2.00 (n=4)
- tier=fine: invariance 0.50 (n=2), no_fabrication 1.00 (n=4), responsiveness 1.00 (n=2)
- kind=invariant: invariance 0.67 (n=6), no_fabrication 1.00 (n=6)
- kind=reactive: responsiveness 1.67 (n=6), no_fabrication 1.00 (n=6)

## 실패 종류 (채점된 쌍 12개)

| 실패 | 건수 |
| --- | --- |
| ignored | 1 |
| fabricated | 0 |
| stereotype | 0 |
| over_personalized | 2 |

## 못 잰 것

- 쌍 28 중 판정 25 · 판정 전 제외 {'not_answered': 3} · 위치 뒤집힘 2/11 (양방향 본 쌍 중) · 기권 0 → 미측정 비율 **0.179**
- 패러프레이즈 일치: None (0쌍)
- 비용: 입력 74,436 / 출력 6,476 토큰 · 1000쌍당 3,236,480

## 측정 대상이 아닌 것

- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)
- Life 경로 — **진짜 RAG 로 쟀다** (팀 DB `documents`). 질환은 Life 계약상 안 받으므로 견종·나이만
- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다

> Life 경로 v1 — general+Life 진짜, 사전 등록 benchmark_pf_life_v1.yaml
