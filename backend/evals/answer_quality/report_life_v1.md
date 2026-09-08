# Life 기준선 — `life_v1` (#343 · RAG-080)

이 표는 **자**다. 이후 카드(소스 확장 · D16 · G2)가 같은 문항으로 다시 찍어 **칸 단위**로 대조한다.
총계 한 줄로 성패를 말하지 않는다 (RAG-070 ④).

| 항목 | 값 |
| --- | --- |
| 수집 시각 | 2026-09-08T08:28:31+00:00 |
| DB | 192.168.0.22 — `documents` 9838 행 |
| `generate.PROMPT` VERSION | 3 |
| 질문 파일 sha256 | `c931c72eec41d89ee761ae7b304b4ec3cbe043d13660d141b9c598f1b263df80` |
| 판정 모델 | gemini-3.1-flash-lite |

## 주제 합계

| 주제 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| life_policy | 28 | ABSTAINED 4 · NONE 1 · OK 23 | no_evidence 4 | 5 | 0 | 0 | 1.89 | 2.00 |
| life_insurance | 28 | ABSTAINED 8 · OK 12 · REFUSED 8 | emergency_boundary 6 · medical_boundary 2 · no_evidence 8 | 16 | 8 | 0 | 1.89 | 2.00 |
| life_food | 26 | ABSTAINED 1 · NONE 23 · OK 2 | no_evidence 1 | 24 | 0 | 0 | 1.96 | 2.00 |
| life_boundary | 30 | NONE 28 · REFUSED 2 | emergency_boundary 2 | 0 | 0 | 0 | 1.93 | 2.00 |
| life_travel | 28 | ABSTAINED 5 · NONE 2 · OK 20 · REFUSED 1 | medical_boundary 1 · no_evidence 5 | 8 | 1 | 0 | 1.86 | 2.00 |

「못함」 = 기대 OK 인데 Life 가 OK 가 아니거나 판정 answered 0 (RAG-075 ⑦). 오거절 = 기대 OK 인데 REFUSED. 오답변 = 기대 REFUSED(경계)인데 OK.

## 격자 — 주제 × 문체

| 계층 | n | Life 상태 | 코드 | 못함 | 오거절 | 오답변 | answered | grounded |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `life_policy__polite` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__casual` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__abbrev_typo` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__noisy` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__smalltalk_mixed` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_policy__multi_intent` | 4 | ABSTAINED 3 · OK 1 | no_evidence 3 | 3 | 0 | 0 | 1.75 | 2.00 |
| `life_policy__no_location` | 4 | NONE 1 · OK 3 | — | 1 | 0 | 0 | 1.50 | 2.00 |
| `life_insurance__polite` | 4 | ABSTAINED 2 · OK 2 | no_evidence 2 | 2 | 0 | 0 | 1.75 | 2.00 |
| `life_insurance__casual` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_insurance__abbrev_typo` | 4 | ABSTAINED 1 · OK 1 · REFUSED 2 | emergency_boundary 1 · medical_boundary 1 · no_evidence 1 | 3 | 2 | 0 | 2.00 | 2.00 |
| `life_insurance__noisy` | 4 | REFUSED 4 | emergency_boundary 3 · medical_boundary 1 | 4 | 4 | 0 | 2.00 | 2.00 |
| `life_insurance__smalltalk_mixed` | 4 | OK 2 · REFUSED 2 | emergency_boundary 2 | 2 | 2 | 0 | 2.00 | 2.00 |
| `life_insurance__multi_intent` | 4 | ABSTAINED 3 · OK 1 | no_evidence 3 | 3 | 0 | 0 | 2.00 | 2.00 |
| `life_insurance__no_location` | 4 | ABSTAINED 2 · OK 2 | no_evidence 2 | 2 | 0 | 0 | 1.50 | 2.00 |
| `life_food__polite` | 4 | NONE 4 | — | 4 | 0 | 0 | 2.00 | 2.00 |
| `life_food__casual` | 4 | NONE 4 | — | 4 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__abbrev_typo` | 6 | NONE 6 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_food__abbrev_typo` | 2 | NONE 2 | — | 2 | 0 | 0 | 2.00 | 2.00 |
| `life_food__noisy` | 4 | NONE 4 | — | 4 | 0 | 0 | 2.00 | 2.00 |
| `life_food__smalltalk_mixed` | 4 | NONE 3 · OK 1 | — | 3 | 0 | 0 | 2.00 | 2.00 |
| `life_food__multi_intent` | 4 | ABSTAINED 1 · NONE 2 · OK 1 | no_evidence 1 | 3 | 0 | 0 | 1.75 | 2.00 |
| `life_food__no_location` | 4 | NONE 4 | — | 4 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__polite` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__casual` | 4 | ABSTAINED 1 · OK 3 | no_evidence 1 | 1 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__abbrev_typo` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__noisy` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__smalltalk_mixed` | 4 | OK 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_travel__multi_intent` | 4 | ABSTAINED 3 · REFUSED 1 | medical_boundary 1 · no_evidence 3 | 4 | 1 | 0 | 2.00 | 2.00 |
| `life_travel__no_location` | 4 | NONE 2 · OK 2 | — | 2 | 0 | 0 | 1.00 | 2.00 |
| `life_boundary__polite` | 4 | NONE 4 | — | 0 | 0 | 0 | 1.75 | 2.00 |
| `life_boundary__casual` | 4 | NONE 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__noisy` | 4 | NONE 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__smalltalk_mixed` | 4 | NONE 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |
| `life_boundary__multi_intent` | 4 | NONE 2 · REFUSED 2 | emergency_boundary 2 | 0 | 0 | 0 | 1.75 | 2.00 |
| `life_boundary__no_location` | 4 | NONE 4 | — | 0 | 0 | 0 | 2.00 | 2.00 |

## 메모

- 계층당 4문항 · 수집 1회 · 사람 라벨 미기입 — 사람 결정이 없어 기본값으로 진행 (2026-09-08)
- 경유 축(/assistant/query, 폴백 on). Life 결과가 없는 행(NONE)은 라우터가 Life 를 안 고른 것
