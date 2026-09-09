# 개체 적합성 — `pf_v1_1`

판정 gpt-5.4-2026-03-05 · profile-fitness-diff-ko-v2a · 생성 gemini-3.1-flash-lite (general-answer-ko-v3) · 2026-09-09T03:33:52+00:00

> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.

## 부등식 N < S ≪ P — 이 지표가 작동하는가

| 조건 | 무엇 | changed 비율 [95% CI] |
| --- | --- | --- |
| **N** 잡음 | 같은 프로필 두 번 | 0% [0, 10] (n=35) |
| **S** 특이도 | 다른 프로필 · 법령류 | 43% [16, 75] (n=7) |
| **P** 절제 | 프로필 없음 vs 있음 | 59% [36, 78] (n=17) |
| 본 비교 | 다른 프로필 · reactive | 67% [45, 83] (n=21) |

- N < S: **True** · S < P: **True** · 전체: **True**
- P − N (문항 단위 부트스트랩, 17문항): +0.59 [+0.35, +0.82] — 0 을 안 걸침
- probe 에서 날조: 0% [0, 56] (n=3)

## 항목 (종합 점수는 없다)

| 항목 | 평균 / 만점 | n | 분포 | 게이트 |
| --- | --- | --- | --- | --- |
| responsiveness | 1.18 / 2 | 38 | {2: 21, 0: 14, 1: 3} | not_calibrated |
| invariance | 0.57 / 1 | 7 | {1: 4, 0: 3} | not_calibrated |
| no_fabrication | 1.00 / 1 | 48 | {1: 48} | not_calibrated |

### 계층별

- tier=coarse: responsiveness 1.20 (n=20), no_fabrication 1.00 (n=27), invariance 0.60 (n=5)
- tier=fine: responsiveness 1.17 (n=18), no_fabrication 1.00 (n=21), invariance 0.50 (n=2)
- kind=reactive: responsiveness 1.18 (n=38), no_fabrication 1.00 (n=38)
- kind=invariant: invariance 0.57 (n=7), no_fabrication 1.00 (n=7)
- kind=probe: no_fabrication 1.00 (n=3)

## 실패 종류 (채점된 쌍 48개)

| 실패 | 건수 |
| --- | --- |
| ignored | 14 |
| fabricated | 0 |
| stereotype | 2 |
| over_personalized | 2 |

## 못 잰 것

- 쌍 116 중 판정 93 · 판정 전 제외 {'out_of_scope': 15, 'not_answered': 8} · 위치 뒤집힘 10/93 (양방향 본 쌍 중) · 기권 1 → 미측정 비율 **0.293**
- 패러프레이즈 일치: 0.778 (18쌍)
- 비용: 입력 298,420 / 출력 38,466 토큰 · 1000쌍당 3,622,430

## 측정 대상이 아닌 것

- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)
- Life 경로 — 실서버(pgvector · Redis) 필요. v1 은 `general` 만
- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다

## 프롬프트 변형 A/B 일치

공유 쌍 20

- changed: 일치 0.95 · κ 0.89
- profile: 일치 0.95 · κ 0.83
- fabricated: 일치 1.00 · κ (single_category)
- stereotype: 일치 1.00 · κ (single_category)

> 확정 판정 v2 · 변형 B 20쌍 일치율 포함
