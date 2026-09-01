# 의미 라우터 골드 세트와 수용 벤치마크

Card 2A는 이미 선정된 `gemini-3.5-flash-lite`가 DAENGS v1 라우팅 계약을 생산 환경에서
받아들일 수준으로 만족하는지 검증합니다. 모델 비교나 승자 선정이 아닙니다(D-041).
권위 있는 계약은 [라우팅 정책](orchestration-routing.md),
[공통 계약](orchestration-contracts.md), [아키텍처](orchestration-architecture.md),
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
