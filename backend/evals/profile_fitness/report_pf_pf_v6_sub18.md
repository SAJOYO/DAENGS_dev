# 개체 적합성 — `pf_v6_sub18`

판정 gpt-5.4-2026-03-05 · profile-fitness-diff-ko-v2.1a · 생성 gemini-3.1-flash-lite (general-answer-ko-v6) · 2026-09-11T08:38:10+00:00

> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.

## 부등식 N < S ≪ P — 이 지표가 작동하는가

| 조건 | 무엇 | changed 비율 [95% CI] |
| --- | --- | --- |
| **N** 잡음 | 같은 프로필 두 번 | — |
| **S** 특이도 | 다른 프로필 · 법령류 | 38% [14, 69] (n=8) |
| **P** 절제 | 프로필 없음 vs 있음 | 57% [25, 84] (n=7) |
| 본 비교 | 다른 프로필 · reactive | 71% [36, 92] (n=7) |

- N < S: **None** · S < P: **True** · 전체: **None**
- P − N (문항 단위 부트스트랩, 0문항): —
- probe 에서 날조: —

## 항목 (종합 점수는 없다)

| 항목 | 평균 / 만점 | n | 분포 | 게이트 |
| --- | --- | --- | --- | --- |
| responsiveness | 1.07 / 2 | 14 | {2: 6, 1: 3, 0: 5} | not_calibrated |
| invariance | 0.62 / 1 | 8 | {1: 5, 0: 3} | not_calibrated |
| no_fabrication | 1.00 / 1 | 22 | {1: 22} | not_calibrated |

### 계층별

- tier=coarse: responsiveness 1.07 (n=14), no_fabrication 1.00 (n=20), invariance 0.83 (n=6)
- tier=fine: invariance 0.00 (n=2), no_fabrication 1.00 (n=2)
- kind=reactive: responsiveness 1.07 (n=14), no_fabrication 1.00 (n=14)
- kind=invariant: invariance 0.62 (n=8), no_fabrication 1.00 (n=8)

## 실패 종류 (채점된 쌍 22개)

| 실패 | 건수 |
| --- | --- |
| ignored | 5 |
| fabricated | 0 |
| stereotype | 1 |
| over_personalized | 1 |

## 못 잰 것

- 쌍 28 중 판정 22 · 판정 전 제외 {'out_of_scope': 4, 'not_answered': 2} · 위치 뒤집힘 0/0 (양방향 본 쌍 중) · 기권 0 → 미측정 비율 **0.214** · 계획에 없던 조건의 쌍 {'noise': 18} 은 분모에서 뺌
- 패러프레이즈 일치: None (0쌍)
- 비용: 입력 38,847 / 출력 5,902 토큰 · 1000쌍당 2,034,045

## 측정 대상이 아닌 것

- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)
- Life 경로 — 이 수집에선 가짜 어댑터(자리표시). 팀 DB(pgvector) 가 있어야 진짜로 돈다 — `--adapters life`
- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다

> general v3→v6 전후 비교 (benchmark_pf_v6_sub18.yaml). 같은 18문항, 판정 v2.1a 한 방향
