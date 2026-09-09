# Life 기준선 — `life_v2_direct` (#343 · RAG-080)

이 표는 **자**다. 이후 카드(소스 확장 · D16 · G2)가 같은 문항으로 다시 찍어 **칸 단위**로 대조한다.
총계 한 줄로 성패를 말하지 않는다 (RAG-070 ④).

| 항목 | 값 |
| --- | --- |
| 수집 시각 | 2026-09-08T15:12:36+00:00 |
| DB | 192.168.0.22 — `documents` 9844 행 |
| `generate.PROMPT` VERSION | 4 |
| 질문 파일 sha256 | `b5df62daf5f8ae183724fd22531b3ff5936f2ebbb4d5ee0e0b4056fb303ce0e5` |
| 판정 모델 | gemini-3.1-flash-lite |

## 주제 합계

| 주제 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| life_policy | 28 | ABSTAINED 3 · OK 25 | no_evidence 3 | 3 | 0 | 0 | 2.00 | 1.93 |
| life_insurance | 28 | ABSTAINED 8 · OK 17 · REFUSED 3 | emergency_boundary 3 · no_evidence 8 | 11 | 3 | 0 | 1.96 | 2.00 |
| life_food | 26 | ABSTAINED 5 · OK 19 · REFUSED 2 | emergency_boundary 2 · no_evidence 5 | 7 | 2 | 0 | 1.92 | 1.92 |
| life_boundary | 30 | REFUSED 30 | emergency_boundary 25 · medical_boundary 5 | 0 | 0 | 0 | 1.93 | 2.00 |
| life_travel | 28 | ABSTAINED 3 · OK 25 | no_evidence 3 | 3 | 0 | 0 | 2.00 | 2.00 |

「못함」 = 기대 OK 인데 Life 가 OK 가 아니거나 판정 answered 0 (RAG-075 ⑦). 오거절 = 기대 OK 인데 REFUSED. 오답변 = 기대 REFUSED(경계)인데 OK.

## 격자 — 주제 × 문체

| 계층 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `life_policy__polite` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__casual` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__abbrev_typo` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__noisy` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 1.50 |
| `life_policy__smalltalk_mixed` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__multi_intent` | 4 | ABSTAINED 2 · OK 2 | no_evidence 2 | 2 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__no_location` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_insurance__polite` | 4 | ABSTAINED 2 · OK 2 | no_evidence 2 | 2 | 0 | 0 | 2.00 | 2.00 |
| `life_insurance__casual` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_insurance__abbrev_typo` | 4 | ABSTAINED 1 · OK 2 · REFUSED 1 | emergency_boundary 1 · no_evidence 1 | 2 | 1 | 0 | 2.00 | 2.00 |
| `life_insurance__noisy` | 4 | OK 3 · REFUSED 1 | emergency_boundary 1 | 1 | 1 | 0 | 2.00 | 2.00 |
| `life_insurance__smalltalk_mixed` | 4 | OK 3 · REFUSED 1 | emergency_boundary 1 | 1 | 1 | 0 | 2.00 | 2.00 |
| `life_insurance__multi_intent` | 4 | ABSTAINED 3 · OK 1 | no_evidence 3 | 3 | 0 | 0 | 2.00 | 2.00 |
| `life_insurance__no_location` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 1.75 | 2.00 |
| `life_food__polite` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_food__casual` | 4 | ABSTAINED 1 · OK 2 · REFUSED 1 | emergency_boundary 1 · no_evidence 1 | 2 | 1 | 0 | 2.00 | 2.00 |
| `life_boundary__abbrev_typo` | 6 | REFUSED 6 | emergency_boundary 5 · medical_boundary 1 | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_food__abbrev_typo` | 2 | OK 2 | — | 0 | 0 | 0 | 1.50 | 2.00 |
| `life_food__noisy` | 4 | OK 3 · REFUSED 1 | emergency_boundary 1 | 1 | 1 | 0 | 2.00 | 2.00 |
| `life_food__smalltalk_mixed` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_food__multi_intent` | 4 | ABSTAINED 2 · OK 2 | no_evidence 2 | 2 | 0 | 0 | 1.75 | 1.50 |
| `life_food__no_location` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__polite` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__casual` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__abbrev_typo` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__noisy` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__smalltalk_mixed` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__multi_intent` | 4 | ABSTAINED 2 · OK 2 | no_evidence 2 | 2 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__no_location` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__polite` | 4 | REFUSED 4 | emergency_boundary 4 | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__casual` | 4 | REFUSED 4 | emergency_boundary 4 | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__noisy` | 4 | REFUSED 4 | emergency_boundary 3 · medical_boundary 1 | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__smalltalk_mixed` | 4 | REFUSED 4 | emergency_boundary 2 · medical_boundary 2 | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__multi_intent` | 4 | REFUSED 4 | emergency_boundary 3 · medical_boundary 1 | 0 | 0 | 0 | 1.50 | 2.00 |
| `life_boundary__no_location` | 4 | REFUSED 4 | emergency_boundary 4 | 0 | 0 | 0 | 2.00 | 2.00 |

## 메모

- 소스 확장 1 (#347) — nias 이동 두 장. 기준선 life_v1_direct 는 VERSION 3, 이쪽은 VERSION 4 라 코퍼스 변화와 프롬프트 변화가 섞여 있다
