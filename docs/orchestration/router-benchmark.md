# 의미 라우터 골드 세트와 수용 벤치마크

Card 2A는 이미 선정된 `gemini-3.5-flash-lite`가 DAENGS v1 라우팅 계약을 생산 환경에서
받아들일 수준으로 만족하는지 검증합니다. 모델 비교나 승자 선정이 아닙니다(D-041).
권위 있는 계약은 [라우팅 정책](routing.md),
[공통 계약](contracts.md), [아키텍처](architecture.md),
그리고 실제 `backend/src/daengs_backend/orchestration/contracts.py`입니다.

## 동결 단위와 분류 체계

Phase 1에서 다음 세 자산을 하나로 동결합니다.

- 골드: `backend/evals/orchestration_router/gold_v1.jsonl`
- 프롬프트: `semantic-router-ko-v1` (`backend/tools/router_benchmark/prompt.py`)
- 구성·게이트: `backend/evals/orchestration_router/benchmark_v1.yaml`

EXECUTE는 `training`, `life`, `walk`만 허용합니다. HANDOFF는 `skin`, `gait`만 허용하고
둘을 `requests[]`에 넣을 수 없습니다. `requests[]`와 `handoffs[]`는 공존할 수 있습니다.
CLARIFY는 필요한 구조화 정보가 실제로 빠졌을 때만 쓰며 배타적입니다. 모델 불확실성,
스키마 실패, 짧은 질문, 하위 능력의 기권은 CLARIFY가 아닙니다. 미확정 `medical`,
`emergency`, `place`와 v1 밖 `journey`는 scored truth에서 제외했습니다.

80개 질문은 모두 이 평가를 위해 의도적으로 작성한 자연스러운 한국어입니다.

| 범주 | 수 |
| --- | ---: |
| Training only | 10 |
| Life only | 10 |
| Walk only | 10 |
| Multi EXECUTE | 12 |
| Pure Skin/Gait HANDOFF | 10 |
| EXECUTE + HANDOFF | 10 |
| CLARIFY | 12 |
| Boundary/adversarial wording | 6 |
| **합계** | **80** |

중복 감사는 공백·문장부호를 제거한 질의 쌍의 `SequenceMatcher` 유사도가 0.88 이상인지를
기계적으로 찾고, 모든 문항을 범주별로 수동 검토합니다. 단순 시제·부사만 바꾼 문항은
허용하지 않습니다. 이 감사는 임베딩이나 외부 모델을 사용하지 않습니다.

## 입력과 골드 격리

동결 v1 모델 입력은 고정 정책, 실제 RoutePlan JSON Schema, 질의, 허용된 구조화 컨텍스트뿐입니다.
v1 FAIL 뒤 역할을 분리한 v2/v3 모델 입력은 의미 선택 schema, 질의, 허용된 라우팅
메타데이터뿐이며 payload·좌표·CLARIFY·handoff reason은 결정론적 조립 코드가 소유합니다.
컨텍스트 최상위 키는 `location`, `source`, `action`, 예약된 `active_dog_id`로 제한합니다.
토큰·쿠키·업로드 바이너리·검색 청크·프로필 스냅샷을 넣지 않습니다.

`category`, `gold_route_plan`, `rationale`, 기대 능력과 지표 라벨은 프롬프트 빌더의 인자가
아니며 컨텍스트에서도 거부됩니다. 골드 파일의 rationale은 사람 검토와 평가기 디버깅에만
씁니다. 질문은 생산 로그, 실제 사용자 대화, 팀 채팅, 개인 대화 이력 또는 사용자 텍스트
텔레메트리에서 가져오지 않았습니다.

## RoutePlan 비교와 지표

스키마 검증은 실제 Card 1 Pydantic `RoutePlan`을 사용합니다. exact semantic match는 다음을
정규화해 비교합니다.

- 요청은 `(capability, typed payload, timeout_ms)` 멀티셋
- 핸드오프는 target 멀티셋
- CLARIFY는 존재 여부와 `missing` 키 집합
- 관측 메타데이터 `router`, `model`은 비교에서 제외
- 사용자 표시 문구인 `handoff.reason`, `clarify.question`은 비교에서 제외

따라서 EXECUTE, payload, HANDOFF target, CLARIFY 여부나 누락 필드 차이는 무시되지 않습니다.
precision/recall은 케이스 경계를 보존한 micro 집계입니다. 구현 지표는 다음과 같습니다.

Training/Life payload에는 원 사용자 질의를 그대로 전달합니다. Card 2A는 라우팅을 평가하며
subquery decomposition을 구현하거나 평가하지 않습니다. 다중 의도의 downstream 검색 품질은
end-to-end orchestration evaluation에서 확인합니다.

- 스키마: first-pass valid rate, retry recovery count, one-retry final valid rate, unrecovered count
- 전체: exact RoutePlan semantic match
- EXECUTE: micro precision/recall, Training/Life/Walk별 precision/recall
- 다중 실행: 다중 케이스의 micro recall, exact executable-set accuracy
- HANDOFF: micro precision/recall, Skin/Gait recall, mixed EXECUTE+HANDOFF exact match
- CLARIFY: precision/recall, non-CLARIFY gold 대비 false-positive rate
- 안전: 모든 시도의 Skin/Gait forbidden EXECUTE 수, 허용되지 않은 capability 수
- 관측(Phase 2): latency_ms, warm p50/p95, input/output/total token 수(가용할 때)

분모가 0인 precision은 오탐이 없다는 뜻으로 1.0, 분모가 0인 recall도 해당 골드가 없다는
뜻으로 1.0을 사용합니다. 고정 80문항 전체에는 각 주요 분모가 존재합니다. invalid 출력은
큰 원문이나 provider thought를 보존하지 않고 `invalid_json` 또는
`schema_validation_error` 같은 정규화 오류 메타데이터만 남깁니다.

## 재시도와 수용 게이트

O-14에 따라 1차 출력이 스키마 검증에 실패한 경우에만 정확히 한 번 재시도합니다. 두 번째도
실패하면 `ROUTER_FAILURE`로 취급해 아무 능력도 실행하지 않습니다. 스키마가 유효하지만 골드와
다른 계획은 의미 오류로 채점하며 재시도하지 않습니다.

Phase 1에서 동결한 hard gates는 다음과 같습니다.

| 게이트 | 기준 |
| --- | ---: |
| final schema validity after one retry | = 100% |
| first-pass schema validity | >= 97.5% |
| forbidden Skin/Gait EXECUTE count | = 0 |
| invented/unsupported capability count | = 0 |
| overall exact RoutePlan match | >= 90% |
| executable precision | >= 95% |
| executable recall | >= 95% |
| multi-capability recall | >= 90% |
| exact executable-set accuracy on multi cases | >= 90% |
| Skin HANDOFF recall | = 100% |
| Gait HANDOFF recall | = 100% |
| overall handoff precision | >= 95% |
| exact mixed EXECUTE+HANDOFF match | >= 90% |
| CLARIFY precision | >= 90% |
| CLARIFY recall | >= 90% |

지연과 토큰은 관측값이며 수용 게이트나 대체 모델 선택 규칙이 아닙니다. 모든 게이트를
통과하면 PASS, 하나라도 실패하면 FAIL입니다.

## HUMAN FREEZE와 Phase 2 절차

Phase 1 커밋 시점에는 구성의 `execution_authorized: false`를 유지하며 runner, prediction,
result/winner 파일을 만들지 않습니다. 사람은 각 질문·컨텍스트·골드·rationale과 숫자 게이트를
검토한 뒤 명시적으로 Phase 2를 승인합니다.

승인 뒤 Phase 2 구현은 고정된 80개를 같은 순서로 `gemini-3.5-flash-lite`에 호출하고, 각
케이스에서 첫 시도와 필요시 단 한 번의 스키마 재시도를 기록합니다. 정규화 결과 레코드는
`case_id`, prompt/model, attempt/schema 필드, prediction, deterministic metrics, latency/token,
error category만 저장합니다. API key, Authorization header, 큰 invalid 원문, hidden reasoning,
provider thought는 저장하지 않습니다. cold start는 제외하고 warm p50/p95를 별도 요약합니다.

Phase 2 결과가 FAIL이면 모델을 자동 교체하지 않습니다. 실패 유형을 분석해 사람이 다음 조치를
정합니다. 결과를 본 뒤 프롬프트를 고치면 `semantic-router-ko-v2`, 골드나 게이트를 고치면 새
benchmark version을 만들고 전체 80문항을 다시 실행합니다. v1 파일은 제자리 수정하지 않습니다.

## v2 semantic boundary remediation

v1은 semantic classification과 trusted-data assembly를 분리해야 함을 보여 주었습니다. v2는
LLM의 역할을 Training/Life/Walk와 Skin/Gait의 의미 선택으로 제한합니다. 원 질의 payload,
Walk 좌표, 좌표 누락 CLARIFY, Skin/Gait reason은 benchmark-local 결정론적 assembler가 같은
Card 1 `RoutePlan`으로 만듭니다. 골드, 수용 게이트, 모델과 v1 결과는 변경하지 않습니다.

v2 결과의 사람 검토에서 `mixed_09`는 훈련 방법이 아니라 환경 적합성(Walk)과 영상 분석(Gait)을
묻는 annotation error로 확정했습니다. 원본과 과거 결과를 덮어쓰지 않고 v3 correction overlay에
한 건만 기록합니다. `semantic-router-ko-v3`에는 Walk가 현재 날씨·기온·비·대기질 같은 환경
적합성 요청일 때만 선택되고 Training/Gait의 배경이 산책이라는 이유만으로 선택되지 않는다는
일반 경계 한 줄만 추가합니다. 골드 예시나 키워드 fallback은 추가하지 않습니다.

## 실행 이력과 최종 PASS

- **v1 FAIL** — LLM이 의미 분류뿐 아니라 원문 payload 복사, 좌표, CLARIFY와 handoff reason까지
  직접 작성해 schema validity, exact match, Gait recall과 CLARIFY recall gate를 통과하지 못했습니다.
- **v2 FAIL** — LLM을 EXECUTE/HANDOFF 의미 선택으로 제한하고 trusted data로 실제 Card 1
  `RoutePlan`을 결정론적으로 조립했습니다. exact match는 97.5%였지만 두 gate가 남았습니다.
- **사람 annotation 정정** — `mixed_09`는 Training 방법 요청이 아니라 환경 적합성 Walk와
  Gait 영상 분석 요청임을 확인했습니다. 원본/과거 결과를 덮어쓰지 않고 v3 overlay 한 건으로
  기록했으며, 이는 모델 tuning이나 gate 변경이 아닙니다.
- **최종 PASS** — 일반 Walk 의미 경계만 명확히 한 `semantic-router-ko-v3`를 같은 모델과
  gate로 80문항에 정확히 한 번 실행했습니다.

| 항목 | 최종 결과 |
| --- | ---: |
| model | `gemini-3.5-flash-lite` |
| prompt | `semantic-router-ko-v3` |
| cases / attempts / retries | 80 / 80 / 0 |
| schema validity | 100% |
| exact RoutePlan match | 98.75% |
| executable precision / recall | 98.68% / 100% |
| Skin / Gait HANDOFF recall | 100% / 100% |
| CLARIFY precision / recall | 100% / 100% |
| verdict | **PASS** |

PASS는 100% 의미 정확도를 뜻하지 않습니다. 최종 실행에서 `mixed_09`가 Training을 하나 더
선택한 non-exact 결과 한 건이 남았지만 모든 동결 gate를 통과했고 추가 실행이나 tuning은
하지 않았습니다.

**PASS로 Card 2A tuning은 끝납니다. Card 2B에서 수용할 production 경계는 LLM의 의미 선택과
결정론적 RoutePlan 조립입니다.**

## v4 모델 선정 정정 — `gemini-3.1-flash-lite` (2026-09-01, PR #130)

위 v1~v3의 `gemini-3.5-flash-lite`는 사람의 모델 선정 기억 착오에서 비롯됐습니다. 원래
의도된 팀 라우터 모델은 `gemini-3.1-flash-lite`였습니다. 착오를 프로덕션에 반영하기 전에,
같은 80개 v3 gold·`semantic-router-ko-v3` 프롬프트·동결 gate로 `gemini-3.1-flash-lite`만
바꿔 재실행했습니다(`runner_v4.py`).

| 항목 | v3 (`gemini-3.5-flash-lite`) | v4 (`gemini-3.1-flash-lite`) |
| --- | ---: | ---: |
| cases / attempts / retries | 80 / 80 / 0 | 80 / 80 / 0 |
| schema validity | 100% | 100% |
| exact RoutePlan match | 98.75% | 98.75% |
| executable precision / recall | 98.68% / 100% | 98.68% / 100% |
| Skin / Gait HANDOFF recall | 100% / 100% | 100% / 100% |
| CLARIFY precision / recall | 100% / 100% | 100% / 100% |
| non-exact case | `mixed_09` | `mixed_09` (동일) |
| warm p50 | 784.71 ms | 859.28 ms (+9.5%) |
| warm p95 | 947.53 ms | 1143.53 ms (+20.7%) |
| verdict | PASS | **PASS** |

정확도는 사실상 동등하고 지연은 `gemini-3.1-flash-lite`가 더 느립니다. 이 지연 증가는
의사결정권자가 명시적으로 수용했으며, 이 결과를 근거로 production
`ROUTER_MODEL_ID`를 `gemini-3.1-flash-lite`로 교정했습니다(D-041). v3 결과 파일
(`summary_v3.json`, `results_v3.jsonl`, `phase2_v3_report.md`)은 감사 근거로 보존하며
제자리 수정하지 않았습니다.

## v5 프롬프트 회귀 — `semantic-router-ko-v4` 순수 인사말 분류 (2026-09-03, PR #163)

production 라우터가 순수 인사말(greeting / thanks / goodbye)만 분류하는 `social_intent` 를
얻으면서 프롬프트가 `semantic-router-ko-v3` → **v4** 로 올라갔습니다 (routing 문서 §2).
프롬프트·스키마가 바뀌었으므로 수용된 v1 라우팅 동작을 다시 확인해야 했습니다. 같은 80개
v3 gold·같은 동결 gate·같은 `gemini-3.1-flash-lite` 로 **production 모듈의 프롬프트와
스키마를 그대로** 정확히 한 번 실행했습니다 (`runner_v5.py` — v2~v4 와 달리 벤치마크
사본이 아니라 `daengs_backend.orchestration.semantic` / `planner` 를 씁니다. 인증할 대상이
production 이 실제로 보내는 것이기 때문입니다).

| 항목 | v4 (`ko-v3`, `3.1-flash-lite`) | v5 (`ko-v4`, `3.1-flash-lite`) |
| --- | ---: | ---: |
| cases / attempts / retries | 80 / 80 / 0 | 80 / 80 / 0 |
| schema validity | 100% | 100% |
| exact RoutePlan match | 98.75% | 98.75% |
| executable precision / recall | 98.68% / 100% | 98.68% / 100% |
| Skin / Gait HANDOFF recall | 100% / 100% | 100% / 100% |
| CLARIFY precision / recall | 100% / 100% | 100% / 100% |
| non-exact case | `mixed_09` (Training 추가) | `boundary_05` (Walk 추가) |
| `social_intent` non-null | — | **0 / 80** |
| warm p50 / p95 | 859 ms / 1144 ms | 950 ms / 1117 ms |
| verdict | PASS | **PASS** |

모든 gate 는 v4 와 같은 값으로 통과했고, 80건 어디에서도 social_intent 가 켜지지 않았습니다
(능력 의도 우선 규칙이 gold 전체에서 지켜졌다는 뜻입니다). non-exact 1건이 `mixed_09` 에서
`boundary_05`("오늘 산책 날씨는 말고 목줄 당김 교육만 알려줘" — 자연어 부정, gold 는 Training
만)로 옮겨간 것은 temperature 0 에서도 남는 경계 사례의 흔들림이며, 두 run 모두 1/80
non-exact 로 gate 안입니다. 이 카드는 이 사례를 tuning 하지 않았고 추가 실행도 하지
않았습니다. 별도로 production `GeminiSemanticRouter` 로 순수/혼합 인사말 8건을 1회
프로브해 8/8 기대 분류를 확인했습니다 (PR #163 본문). v1~v4 결과 파일은 제자리 수정하지
않았습니다.

## v6 프롬프트 회귀 — `semantic-router-ko-v5` 일반 돌봄 미지원 경계 (2026-09-03, PR #172) — **FAIL**

production 라우터가 일반 돌봄 질문을 Life 로 보내는 결함이 2건 라이브 프로브로 확인돼(routing 문서
§2 "일반 돌봄") 프롬프트를 v4 → **v5** 로 올렸습니다: Life 정의를 공식 제도·법률·행정·정책·계약
근거로 좁히고, 일반 돌봄 권고(산책·운동 횟수·시간, 급여, 수면, 음수량, 견종·연령·체격별 관리)는
어느 목적지도 아니라고 선언. 모델·스키마·gold·gate·1회 재시도 정책은 v5 run 과 동일하고, run 번호만
**v6** 입니다(`runner_v6.py` — production 모듈 사용). 순서는 규칙대로: 5건 라이브 프로브(5/5 PASS)
→ 80건 회귀 정확히 1회.

| 항목 | v5 (`ko-v4`) | v6 (`ko-v5`) |
| --- | ---: | ---: |
| cases / attempts / retries | 80 / 80 / 0 | 80 / 80 / 0 |
| schema validity (first-pass / final) | 100% / 100% | 100% / 100% |
| exact RoutePlan match | 98.75% | 95.00% |
| executable precision / recall | 98.68% / 100% | 96.10% / 98.67% |
| multi-execute recall | 100% | 97.06% |
| **exact mixed execute+handoff match (gate ≥ 0.90)** | 100% | **80.00% — FAIL** |
| Skin / Gait HANDOFF recall | 100% / 100% | 100% / 100% |
| CLARIFY precision / recall | 100% / 100% | 100% / 91.67% |
| forbidden / invented capability count | 0 / 0 | 0 / 0 |
| `social_intent` non-null | 0 / 80 | 0 / 80 |
| non-exact cases | `boundary_05` | `boundary_05` · `mixed_09` · **`mixed_10`** · **`clarify_08`** |
| verdict | PASS | **FAIL** (14/15 gate 통과) |

non-exact 4건의 성격이 다릅니다:

- `boundary_05`("오늘 산책 날씨는 말고 목줄 당김 교육만") — v5 run 과 같은 Walk 추가. 기존 경계 흔들림.
- `mixed_09`("리드줄 연습 가능한 날씨인지 보고, 걷는 영상도") — v4 run 의 non-exact 가 돌아온 것. 기존
  경계 흔들림.
- **`mixed_10`**("저녁 산책 시간 추천이랑 반려견 철도 탑승 규정 …") gold Life+Walk+Gait → Life+Gait.
- **`clarify_08`**("오늘 산책 시간하고 기차 이동장 규정 같이") gold Walk 선택 → 좌표 없음 CLARIFY 인데
  Life 만 선택.

뒤의 둘은 v5 가 새로 만든 회귀입니다. gold 는 "오늘/저녁 산책 시간" 을 **오늘의 환경 창 = Walk** 로
보는데, v5 의 "walk or exercise frequency or duration … is not Walk merely because it concerns
walking" 이 그 "시간" 을 돌봄 상식으로 읽게 했습니다. 같은 모양의 `walk_03`·`clarify_03` 은 이번 run
에서 맞았으므로 경계가 완전히 무너진 것은 아니고 **의미가 모호해진** 것입니다.

**조치: 규칙대로 STOP.** 프롬프트를 다시 만지지 않았고 재실행도 하지 않았습니다(재조정 루프 금지).
v5 프롬프트는 브랜치에 있으나 **수용되지 않았습니다.** 다음 후보는 미지원 문장에 "오늘/지금의 산책
시간 창은 Walk 그대로" 를 명시하는 한 문장 수정(→ `ko-v6`, run **v7**, 80건 1회)이고 사람 결정 뒤에만
합니다. v1~v5 결과 파일은 제자리 수정하지 않았고 v6 결과 파일(`results_v6.jsonl` · `summary_v6.json` ·
`phase2_v6_report.md`)은 FAIL 기록 그대로 보존합니다. 이 카드의 유료 호출 합계: 2(v4 진단) + 5(v5
프로브) + 80(v6 회귀) = **87건.**

## v7 프롬프트 회귀 — `semantic-router-ko-v6` 일상 운동 권고 vs 오늘의 산책 시간 창 (2026-09-03, PR #172) — **PASS**

v6 run 의 실패 원인은 v5 의 한 문장이었습니다: "walk or exercise frequency or duration … not Walk merely
because it concerns walking" 이 일상 돌봄뿐 아니라 **오늘/저녁의 산책 시간 창**(gold 가 Walk 로 보는 것)까지
눌렀습니다. 사람 결정으로 프롬프트를 v5 → **v6** 으로 올려 그 문장만 보정했습니다 — **일상적·규범적 운동
권고**(하루 몇 번·몇 분, 견종·연령·체격별, 현재 조건과 무관)는 미지원, **지금·오늘·이번 저녁에 걸을지/언제
걸을지**(오늘의 시간 창 고르기 포함)는 날씨·대기질을 명시하지 않아도 Walk, Walk 를 반복 일정으로 넓히지 않음.
Life 제한(v5)·모델·스키마·gold·gate·1회 재시도 정책은 그대로. 순서는 규칙대로: 7건 타깃 프로브(동결 원문
`mixed_10`·`clarify_08`·`walk_03`·`clarify_03` 포함, 7/7 PASS, 유료 7건) → 80건 회귀 정확히 1회(run **v7**).

| 항목 | v5 (`ko-v4`) | v6 (`ko-v5`) | v7 (`ko-v6`) |
| --- | ---: | ---: | ---: |
| cases / attempts / retries | 80 / 80 / 0 | 80 / 80 / 0 | 80 / 80 / 0 |
| schema validity (first-pass / final) | 100% / 100% | 100% / 100% | 100% / 100% |
| exact RoutePlan match (≥0.90) | 98.75% | 95.00% | 97.50% |
| executable precision / recall (≥0.95) | 98.68% / 100% | 96.10% / 98.67% | 97.40% / 100% |
| multi-execute recall (≥0.90) | 100% | 97.06% | 100% |
| exact executable set (multi, ≥0.90) | 100% | 93.75% | 100% |
| exact mixed execute+handoff match (≥0.90) | 100% | **80.00% FAIL** | 90.00% (경계값) |
| Skin / Gait HANDOFF recall (=1.0) | 100% / 100% | 100% / 100% | 100% / 100% |
| handoff precision (≥0.95) | 100% | 100% | 100% |
| CLARIFY precision / recall (≥0.90) | 100% / 100% | 100% / 91.67% | 100% / 100% |
| forbidden / invented capability (=0) | 0 / 0 | 0 / 0 | 0 / 0 |
| `social_intent` non-null | 0 / 80 | 0 / 80 | 0 / 80 |
| warm p50 / p95 | 950 / 1117 ms | — | 1032 / 1207 ms |
| non-exact | `boundary_05` | `boundary_05` · `mixed_09` · `mixed_10` · `clarify_08` | `boundary_05` · `mixed_09` |
| verdict | PASS | FAIL (14/15) | **PASS (15/15)** |

non-exact 2건은 둘 다 이전 run 에서 이미 나타난 온도 0 경계 흔들림입니다: `boundary_05`("오늘 산책 날씨는
말고 목줄 당김 교육만") 은 v5·v6 run 과 같은 Walk 추가, `mixed_09`("리드줄 연습 가능한 날씨인지 보고, 걷는
영상도") 는 v4 run 과 같은 Training 추가(정정 gold 는 Walk+Gait). v6 run 이 새로 잃었던 `mixed_10`·`clarify_08`
은 회복됐습니다. `exact_mixed_execute_handoff_match` 가 정확히 gate 값 0.90 에 걸린 것은 이 10건 범주에
`mixed_09` 하나가 non-exact 이기 때문이며, 규칙대로 80/80 을 쫓지 않고 수용합니다. v1~v6 결과 파일은 제자리
수정하지 않았고(v6 FAIL 산출물은 근거로 보존), v7 산출물은 `results_v7.jsonl` · `summary_v7.json` ·
`phase2_v7_report.md`. 이 카드의 유료 호출 합계: 2(v4 진단) + 5(v5 프로브) + 80(v6 회귀) + 7(v6 프로브)
+ 80(v7 회귀) = **174건.**

## v8 프롬프트 회귀 — `semantic-router-ko-v7` Place 목적지 추가 (2026-09-04, PR #204 · D-051) — **PASS**

바뀐 변수는 **프롬프트 하나**입니다. v7 은 `ExecuteName` 에 `place` 를 더하고 경계 문장 셋을
넣었습니다(Place="어디로 갈까" vs Walk="지금 나가도 될까" · 장소 명사가 배경이면 Place 아님 ·
한 발화가 둘 다 물으면 둘 다). 모델 · gold · gate · 스키마 모양 · 1회 재시도 정책은 그대로입니다.

**이 run 이 재는 것은 "나머지 80건이 안 움직였는가" 입니다.** 동결 gold 에는 Place 케이스가
하나도 없으므로, 이 80건에서 나오는 모든 Place 선택은 정의상 오탐입니다. 그래서 여유가
얼마인지 먼저 적어 두고 시작했습니다 — gold 실행 76건에 `executable_precision` gate 0.95 면
오탐 4건까지가 한계이고, 수용된 v7 run 이 이미 2건(`boundary_05` Walk 추가 · `mixed_09`
Training 추가)을 쓰고 있으므로 **새 Place 오탐은 2건까지 흡수 가능**했습니다.

순서는 규칙대로: 동결 Place gold set 15건 라이브 프로브(15/15 PASS, 유료 15건) → 80건 회귀
정확히 1회(run **v8**, 유료 80건).

| 항목 | v7 (`ko-v6`) | v8 (`ko-v7`) |
| --- | ---: | ---: |
| cases / attempts / retries | 80 / 80 / 0 | 80 / 80 / 0 |
| schema validity (first-pass / final) | 100% / 100% | 100% / 100% |
| exact RoutePlan match (≥0.90) | 97.50% | 96.25% |
| executable precision / recall (≥0.95) | 97.40% / 100% | 96.15% / 100% |
| multi-execute recall (≥0.90) | 100% | 100% |
| exact executable set (multi, ≥0.90) | 100% | 100% |
| exact mixed execute+handoff match (≥0.90) | 90.00% (경계값) | 90.00% (경계값) |
| Skin / Gait HANDOFF recall (=1.0) | 100% / 100% | 100% / 100% |
| handoff precision (≥0.95) | 100% | 100% |
| CLARIFY precision / recall (≥0.90) | 100% / 100% | 100% / 100% |
| walk / training / life precision | 95.83% / 96.30% / 100% | 95.83% / 96.30% / 100% |
| forbidden / invented capability (=0) | 0 / 0 | 0 / 0 |
| `social_intent` non-null | 0 / 80 | 0 / 80 |
| warm p50 / p95 | 1032 / 1207 ms | 930 / 1135 ms |
| non-exact | `boundary_05` · `mixed_09` | `boundary_05` · `mixed_09` · **`walk_03`** |
| verdict | PASS (15/15) | **PASS (15/15)** |

**새로 잃은 것은 `walk_03` 하나이고, 그것이 유일한 Place 오탐입니다.**
"비 그치는 시간 봐서 오늘 걷기 좋은 구간 골라줘" 에 `place` 가 하나 더 붙었습니다(gold 는 Walk
단독). 나머지 두 건은 v7 과 **같은** 기존 흔들림이며, 그 사실은 숫자로 확인됩니다 —
`walk_precision`(95.83%)과 `training_precision`(96.30%)이 v7 과 소수점까지 동일하므로 이번에
추가된 오탐은 Walk 도 Training 도 아닙니다.

`walk_03` 은 쫓지 않고 **경계 사례로 수용합니다.** "좋은 구간 골라줘" 는 실제로 장소 요청과
가까운 문장이고("걷기 좋은 곳 골라줘" 와 형태가 거의 같습니다), gold 가 Walk 단독으로 본 근거는
앞머리의 "비 그치는 시간 봐서" 라는 시간 창 표현입니다. 두 읽기 모두 방어 가능하며, 프롬프트를
더 조여 이 한 건을 잡으려다 v6 이 겪은 것처럼 반대쪽(오늘의 산책 시간 창)을 눌러 버리는 쪽이
더 비쌉니다. 규칙대로 80/80 을 쫓지 않습니다.

`invented_unsupported_capability_count` 가 0 인 것은 `evaluate.ALLOWED_EXECUTE` 에 `place` 를
**회귀를 돌리기 전에** 더했기 때문입니다. 그러지 않았으면 정당한 Place 선택이 "계약에 없는 이름"
으로 집계돼 무관용 gate 에서 거짓 FAIL 이 났을 것입니다. 잘못된 Place 선택은 지금처럼
executable precision 의 감점으로 잡히는 것이 맞습니다.

v1~v7 결과 파일은 제자리 수정하지 않았고, v8 산출물은 `results_v8.jsonl` · `summary_v8.json` ·
`phase2_v8_report.md` 입니다. Place 수용 세트는 `gold_place_v1.jsonl` 로 **별도 파일**이며 동결
80건에 합치지 않았습니다 — 합치면 Place 오탐과 v6 회귀가 같은 숫자로 읽힙니다.
이 카드의 유료 호출 합계: 15(Place 프로브, 정렬 수정 전) + 15(Place 프로브, 재실행)
+ 80(v8 회귀) = **110건.** (첫 프로브는 결정적 실행 순서를 넣기 전이라 gold 와 순서만 달랐고,
그 자체가 순서 결정화가 필요하다는 근거였습니다 — §라우팅 D-051 ④.)

## v9 프롬프트 회귀 — `semantic-router-ko-v8` 배제 문장 (2026-09-07, PR #279) — **PASS**

바뀐 변수는 **프롬프트 한 문장**입니다. v8 은 "사용자가 한 주제를 분명히 뺐으면(하나는 말고 / 하나만)
그 어휘가 보여도 빠진 목적지를 고르지 말라" 를 더했습니다. v7 의 "negation overrides incidental
vocabulary" 가 이미 있었는데도 `boundary_05`("… 날씨는 말고 … 교육만") 는 v5 · v6 · v7 · v8 회귀와
#272 비교(3/3)에서 매번 Walk 를 더 골랐고, 이 문장은 그 패턴을 이름으로 부릅니다. 모델 · gold ·
gate · 스키마 모양 · 1회 재시도 정책은 그대로입니다.

**이 run 이 재지 않는 것도 적어 둡니다.** #279 는 일반 답변 폴백(`CapabilityName.GENERAL`)도 넣었지만
그것은 **플래그 뒤의 planner 규칙**이지 라우터 목적지가 아닙니다. 러너(`runner_v5.run_cases`)는
`assemble_route_plan` 을 기본값(`general_fallback=False`)으로 부르므로 여기서 채점되는 계획은 v1~v8
과 똑같이 **라우터의 결정**이고, 80건 계획에 `general` 은 0건입니다. `evaluate.ALLOWED_EXECUTE` 에
`general` 을 더한 것은 v7 의 `place` 와 같은 이유 — 언젠가 플래그를 켠 계획이 이 채점기에 오면
"지어낸 능력" 이 아니라 precision 감점으로 읽히게 하려는 것입니다. gold 는 손대지 않았습니다.

걱정한 위험은 배제 문장이 평범한 대조를 배제로 과독해 다중 의도의 한쪽을 떨어뜨리는 것이었고,
그래서 볼 숫자는 `executable_recall` 과 `multi_execute_recall` 이었습니다.

| 항목 | v8 (`ko-v7`) | v9 (`ko-v8`) |
| --- | ---: | ---: |
| cases / attempts / retries | 80 / 80 / 0 | 80 / 80 / 0 |
| schema validity (first-pass / final) | 100% / 100% | 100% / 100% |
| exact RoutePlan match (≥0.90) | 96.25% | **97.50%** |
| executable precision / recall (≥0.95) | 96.15% / 100% | **97.40%** / 100% |
| multi-execute recall (≥0.90) | 100% | 100% |
| exact executable set (multi, ≥0.90) | 100% | 100% |
| exact mixed execute+handoff match (≥0.90) | 90.00% (경계값) | 90.00% (경계값) |
| Skin / Gait HANDOFF recall (=1.0) | 100% / 100% | 100% / 100% |
| handoff precision (≥0.95) | 100% | 100% |
| CLARIFY precision / recall (≥0.90) | 100% / 100% | 100% / 100% |
| walk / training / life precision | 95.83% / 96.30% / 100% | **100%** / 96.30% / 100% |
| forbidden / invented capability (=0) | 0 / 0 | 0 / 0 |
| `social_intent` non-null | 0 / 80 | 0 / 80 |
| warm p50 / p95 | 930 / 1135 ms | 897 / 1031 ms |
| 토큰 합계 (`usage_metadata`) | 94,777 | 97,917 (입력 95,468 · 출력 2,449) |
| non-exact | `boundary_05` · `mixed_09` · `walk_03` | `mixed_09` · `walk_03` |
| verdict | PASS (15/15) | **PASS (15/15)** |

**`boundary_05` 가 처음으로 맞았고 잃은 것은 없습니다.** `walk_precision` 이 100% 가 된 것이 그 한 건이고,
`executable_recall` · `multi_execute_recall` 은 100% 그대로라 배제 문장이 다중 의도를 떨어뜨리지 않았습니다.
남은 두 건은 v7 · v8 과 같은 알려진 흔들림입니다 — `walk_03` 의 Place 추가(v8 절에서 경계 사례로 수용)
와 `mixed_09` 의 Training 추가(v4 부터). 규칙대로 80/80 을 쫓지 않습니다.

같은 문장을 에이전트 프롬프트(`agent-ko-v3`)에도 넣었습니다 (D-055 ⑦ 규칙 1). 에이전트 쪽은 이 카드에서
라이브로 재지 않았습니다 — 비교 러너 3회분(240×2 호출)은 이 카드의 예산 밖이고, 두 구현의 결정론 동치는
`tests/test_orchestrator_failure_contract.py` ⓓ 가 잽니다.

v1~v8 결과 파일은 제자리 수정하지 않았고, v9 산출물은 `results_v9.jsonl` · `summary_v9.json` ·
`phase2_v9_report.md` 입니다.

## v10 프롬프트 회귀 — `semantic-router-ko-v9` `general` 추가 목적지, 두 시각 (2026-09-07, PR #279 · D-056) — **stripped PASS · raw FAIL(정보용)**

바뀐 변수는 **프롬프트 하나**입니다. v9(D-056 ①)는 `ExecuteName` 에 `general` 을 더하고, 돌봄·건강
의도가 전문 능력과 섞인 발화에서 그것을 **추가로** 고르되 전문 능력을 대신하지 않으며, 반려견과 무관한
요청에는 아무것도 고르지 않는다고 적었습니다. #277 실측이 이유입니다 — 폴백을 planner 규칙으로만
두었더니 폴백 계층 84건 중 49건이 전문 능력에 가려 폴백에 못 닿았고, 도메인 밖 질문은 place/life/walk
가 집었습니다. 모델 · gold · gate · 1회 재시도 정책은 그대로입니다.

**이 run 부터 시각이 둘입니다** (D-056 ⑤). 동결 gold 에는 `general` 이 없어 라우터가 더 고르는
`general` 은 정의상 전부 오탐이고, D-056 으로는 의도된 정책입니다. 한 숫자로 적으면 둘 중 하나가
숨습니다. 그래서 `runner_v10` 은 한 번의 유료 실행에서 **raw**(planner 를 플래그 on 으로 조립,
`general` 포함 = 켰을 때의 운영 계획)와 **general-stripped**(`general` 요청을 뺀 것 = 플래그 off 의
운영 계획, planner 가 결정에서 `general` 을 떼어 내므로 글자까지 같음)를 같이 냅니다. 회귀를 막는
것은 stripped 시각이고, 그 조건은 "v9 이상(exact ≥ 0.975 · 15/15)" 이었습니다.

| 항목 | v9 (`ko-v8`) | v10 stripped | v10 raw |
| --- | ---: | ---: | ---: |
| cases / attempts / retries | 80 / 80 / 0 | 80 / 80 / 0 | (같은 실행) |
| exact RoutePlan match (≥0.90) | 97.50% | **97.50%** | 61.25% |
| executable precision / recall (≥0.95) | 97.40% / 100% | **97.40% / 100%** | 70.09% / 100% |
| multi-execute recall (≥0.90) | 100% | 100% | 100% |
| exact executable set (multi, ≥0.90) | 100% | 100% | 25.00% |
| exact mixed execute+handoff match (≥0.90) | 90.00% | 90.00% | 40.00% |
| walk / training / life precision | 100% / 96.30% / 100% | 100% / 96.30% / 100% | 100% / 96.30% / 100% |
| CLARIFY · handoff · forbidden · invented | 전부 만점 · 0 | 전부 만점 · 0 | 전부 만점 · 0 |
| warm p50 / p95 · 토큰 | 897 / 1031 ms · 97,917 | 843 / 1059 ms · 107,543 (`usage_metadata`) | (같은 실행) |
| non-exact | `walk_03` · `mixed_09` | `walk_03` · `mixed_09` | 31건, 전부 EXTRA_EXECUTE |
| verdict | PASS (15/15) | **PASS (15/15)** | FAIL (11/15, 정보용) |

**stripped 는 v9 와 소수점까지 같습니다.** 라우터 v9 는 전문 능력의 선택을 하나도 바꾸지 않았습니다 —
recall 100%, walk/training/life precision 동일, 같은 두 흔들림. 조건 충족.

**raw 시각의 발견 — 튜닝하지 않고 보고합니다 (D-056 ③).** 라우터가 `general` 을 **32/80** 에 더
골랐습니다: `training_01~10` **전부**, `multi_*` 9건, `mixed_*` 6건, `boundary_01/04`, `clarify_07/12`,
`handoff_06`, `life_09`, `walk_08`. "섞인 발화에 더하라" 였는데 행동 교정 질문 전부에 붙습니다 —
`execute.general` 설명의 "behavior-as-wellbeing" 이 훈련 질문을 삼킨 것으로 읽힙니다. 플래그를 켜면
훈련 답마다 근거 없는 `[일반]` 절이 하나 더 붙는다는 뜻이라, D-056 ③ 의 재측정(#277 84건 쌍대)에서
답 품질로 어떻게 나오는지 본 뒤 그 문구를 좁히는 것이 첫 후보입니다. 이 run 에서는 규칙대로 더 돌리지
않았습니다.

v1~v9 결과 파일은 제자리 수정하지 않았고, v10 산출물은 `results_v10.jsonl` · `summary_v10.json`
(raw 가 최상위, stripped 는 `general_stripped` 키) · `phase2_v10_report.md` 입니다. 이 카드의 유료 호출
합계: 80(v9) + 5(스모크 v1, 3,335 토큰) + 3(`001bfba` 재확인, 2,174 토큰) + 80(v10, 107,543 토큰)
+ 5(스모크 v2, 4,660 토큰) = **173건.**
