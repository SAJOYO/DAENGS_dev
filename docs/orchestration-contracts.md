# 오케스트레이터 공통 계약

`/assistant/query` 뒤 LangGraph 오케스트레이션([orchestration-architecture.md](orchestration-architecture.md) §논리
오케스트레이션)이 쓸 공통 계약입니다. **2026-08-30 어드버서리얼 아키텍처 리뷰를 거쳐
사람이 승인한 확정 계약**입니다 (D-033 · D-034) — 구현은 없습니다. 이 PR 은 문서만이고,
필드 이름·세부 타입은 구현 카드에서 다듬을 수 있지만 **상태 모델과 불변식(§6)은 다듬는
대상이 아닙니다.**

이 계약의 전신은 2026-08-22 멘토링에서 팀이 논의한 v1 도구 계약
(`question, dog_profile → answer/evidence/refused/reason`, `frontend/public/mentoring/0822.html`)입니다.
그때 이미 지적된 문제 — **`refused: true` 하나가 정책 거절·자료 부족·입력 부족·내부 오류
네 가지를 겸한다** — 를 CapabilityResult 의 status 분리(§4)로 풉니다. 특히 **자료 부족
기권(ABSTAINED)과 정책·안전 거절(REFUSED)을 별개 상태로 가릅니다** (D-033). "도구가 직접
되묻지 않고 오케스트레이터가 후속 질문을 담당한다"는 그때의 원칙은 CLARIFY(§2)로 이어집니다.

> 표기는 architecture 문서와 같습니다: CONFIRMED / OPEN / PENDING.
> 아래 코드는 전부 **의사 스키마**입니다 — 실제 Pydantic/TypedDict 정의가 아닙니다.

## 1. OrchestratorState

그래프 한 번의 실행이 들고 다니는 상태. 최소 구성:

```
OrchestratorState:
  request_id:  str            # 요청 추적자. Training 이 이미 X-Request-ID 를 쓰므로 결이 같다
  principal:   Principal      # 그래프 밖 FastAPI 의존성이 인증을 끝낸 주체 (id · 종류 · 권한)
  query:       str            # 사용자 자연어 원문
  locale:      str            # "ko-KR" (§7)
  context:     dict           # 구조화된 컨텍스트만 (예: 화면 출처, 선택된 리소스 식별자)
  route_plan:  RoutePlan      # §2 — 라우터가 채운다
  results:     list[CapabilityResult]   # §4 — 실행 노드들이 append
  response:    AssistantResponse | None # §5 — 마지막에 조립
```

**상태에 넣지 않는 것 (CONFIRMED — 불변식 §6):**

- **인증 토큰** — 인증은 그래프 밖에서 끝났습니다. 토큰이 상태에 들어가면 로그·체크포인트·
  트레이스 어디로든 샙니다. 그래프 안에서 필요한 것은 principal 뿐입니다.
- **업로드 바이너리** — 이미지·영상은 전용 multipart API 의 것입니다 (HANDOFF, routing 문서).
- **검색 청크 전문** — 능력이 내부에서 검색한 원문 텍스트는 능력 안에 머뭅니다. 상태·로그에
  들어가면 "커밋 산출물에 원문 전문 금지" 불변식(architecture §이관 경계)이 우회당합니다.
  상태에는 인용 라벨·식별자 수준만 올라옵니다 (Training 공개 계약의 `citations` 가 선례).

**반려견 컨텍스트 (CONFIRMED — O-4)** — `active_dog_id` 는 v1 상태의 **타입 필드가
아닙니다.** `context` 의 **예약 키 이름으로만** 문서화합니다 — 지금은 권위 있는 반려견
프로필/소유권 원천이 없고(D-029: DB 에 프로필 테이블 없음) 어떤 능력도 이 값을 소비하지
않아서, 필드를 미리 두면 거짓 확신만 만듭니다. 규칙:

- dog 식별자는 **소유권 증명이 아닙니다.** 라우팅·개인화 힌트일 뿐입니다.
- 전체 반려견 목록·프로필 스냅샷은 그래프 상태에 넣지 않습니다.
- 대상 반려견이 필요한데 모호하면 → CLARIFY.
- 민감한 반려견별 데이터 접근 전에 권위 있는 소유권/프로필 해석이 선행 조건입니다 (FOLLOW-UP).
- 다견 식별자 모델은 지금 설계하지 않습니다.

## 2. RoutePlan

라우터의 산출물. **스칼라 mode 하나로 접지 않습니다** (D-034) — "산책은 실행하고 피부는
전용 플로우로 안내"처럼 실행과 핸드오프가 **한 요청 안에 공존**하는 것이 유효한 흐름이라,
단일 mode 는 그것을 표현하지 못합니다 (2026-08-30 리뷰가 계약 결함으로 확정).

```
RoutePlan:
  requests:  list[CapabilityRequest]           # 실행할 능력 — 0개 이상
  handoffs:  list[{target: str, reason: str}]  # 전용 플로우 안내 — 0개 이상
  clarify:   {question: str, missing: list[str]} | None
  router:    deterministic | llm       # 출처 — 어느 경로가 이 판단을 냈는가
  model:     str | None                # router=llm 일 때 사용 모델 (미정 — routing 문서 §4)
```

규칙 (CONFIRMED):

- **`requests[]` 와 `handoffs[]` 는 공존할 수 있습니다.** 예: "오늘 산책 괜찮은지도
  알려주고 피부에 난 것도 봐줘" → Walk 실행 + Skin 업로드 플로우 핸드오프.
- **CLARIFY 는 배타적입니다.** `clarify != None` 이면 그 요청에서는 능력도 핸드오프도
  실행하지 않습니다. 부분 실행 후 되물으면, stateless 재요청(§O-8, routing 문서 §3)이
  이미 실행된 능력을 **다시 실행해 비용을 이중으로** 뭅니다.
- 요약이 필요하면 mode 는 세 목록에서 **파생**합니다 — 진실 원천이 아닙니다.
- 이 구조를 범용 워크플로 액션 DSL 로 일반화하지 않습니다.
- `router` 출처 필드는 관측용이자 회귀 판별용입니다. 결정적 경로가 낸 오답과 LLM 이 낸
  오답은 고치는 방법이 다릅니다.
- **라우터의 구조화 출력이 스키마 검증에 실패하면 CLARIFY 로 위장하지 않습니다** (O-14,
  routing 문서 §2) — CLARIFY 는 사용자의 정보 부족이고, 스키마 실패는 시스템 실패입니다.
  1회 한정 재시도 후에도 실패면 아무 능력도 실행하지 않고 최상위 FAILED 를 냅니다.
  잘못된 모델 원출력은 사용자에게 노출하지 않습니다.

## 3. CapabilityRequest

```
CapabilityRequest:
  capability: training | life | walk           # v1 실행 범위 (architecture §v1)
  payload:    <능력별 타입>                     # 능력이 소유하는 도메인 페이로드
  timeout_ms: int | None                       # 선택 — 능력별 기본값을 덮을 때만
```

**만능 공용 페이로드를 만들지 않습니다 (CONFIRMED).** `question + dog_profile` 하나로 모든
능력을 덮으려던 v1 계약이 Walk(좌표·시각) 앞에서 이미 안 맞았습니다. 페이로드 타입은
능력이 소유하고, 오케스트레이터는 그 내용을 해석하지 않고 전달만 합니다.

## 4. CapabilityResult

능력 하나의 실행 결과 봉투. 멘토링 v1 의 `refused/reason` 겸용 문제를 여기서 풉니다.

```
CapabilityResult:
  capability: str
  status:     OK | ABSTAINED | REFUSED | PENDING | ERROR | TIMEOUT
  data:       <능력별 타입> | None      # OK/부분 결과. 도메인 데이터는 능력 소유
  abstention: {code: str, message: str} | None   # ABSTAINED 일 때 (예: no_evidence)
  refusal:    {code: str, message: str} | None   # REFUSED 일 때 — 상류 분류를 그대로 보존
  job:        {job_id: str, poll: str} | None    # PENDING 일 때 (미래 Gait 비동기 경로)
  error:      {kind: str, detail: str} | None    # ERROR/TIMEOUT 일 때
  elapsed_ms: int
```

status 여섯 값의 구분이 이 계약의 핵심이고, 그중에서도 **ABSTAINED ≠ REFUSED 가
가장 중요합니다** (D-033 — 2026-08-30 사람 승인):

| status | 뜻 |
| --- | --- |
| OK | 능력이 정상 응답 |
| **ABSTAINED** | **자료 부족 기권** — 근거가 없거나 품질 문턱 미달. **자료·근거 상태가 달라지면 나중에 답할 수 있습니다** |
| **REFUSED** | **정책·안전·의료 경계의 의도적 거절** — 능력이 그 방식으로는 답하지 않기로 한 것. **자료가 늘어도 자동으로 바뀌지 않습니다** |
| PENDING | 접수됐고 결과는 비동기 |
| ERROR | 시스템 실패 — 거절도 기권도 아님 |
| TIMEOUT | 시간 안에 못 끝남 — 거절도 기권도 아님 |

**ABSTAINED 를 REFUSED 로 접지 않습니다.** 접으면 "자료가 늘면 답할 수 있는 것"에
"답하지 않기로 했다"는 라벨이 붙습니다 — 멘토링 v1 이 지적했고 Training 게이트웨이
(`services/training_rag.py` 의 `_REFUSE_REASON_DECISIONS`)가 이미 상류에서 갈라 둔 구분입니다.

### v1 매핑 (CONFIRMED)

| 상류 결과 | CapabilityResult |
| --- | --- |
| Training `ANSWER` | OK |
| Training `UNCERTAIN` | **ABSTAINED** |
| Training `SAFETY_REFUSAL` | REFUSED (`refusal.code` 에 상류 분류 보존) |
| Training `MEDICAL_REFUSAL` | REFUSED (동일) |
| Life 근거 있는 200 응답 | OK (`ungrounded` 지표는 품질 메타데이터로 전파 가능) |
| Life 무근거 404 (`근거를 찾지 못했다`) | **ABSTAINED** (`abstention.code = no_evidence`) |
| Life 상류/시스템 실패 (502·503) | ERROR |
| Life 타임아웃 (504) | TIMEOUT |
| Walk 정상 판정 | OK |
| Walk 판정 불가 (`unknown` — 격자 데이터 부재) | ABSTAINED 후보 — 어댑터 구현 카드에서 확정 |

- **refusal 은 상류 분류를 보존합니다.** Training 의 SAFETY_REFUSAL / MEDICAL_REFUSAL
  구분(training-rag-demo.md)이 `refusal.code` 로 그대로 올라옵니다. 오케스트레이터가
  이것을 합치거나 바꿔 말하지 않습니다.
- **Life 는 재설계하지 않습니다** (O-3, D-035). 어댑터가 **이미 있는 기계 신호만**
  매핑합니다 — 무근거 404 는 서빙 층의 명시적 도메인 정책이고(`app/services/ask.py`),
  `ungrounded` 는 품질 신호입니다. **수용된 v1 한계**: Life 가 산문으로만 물러서는
  경우(기계 신호 없음)는 OK 로 통과합니다. Training 급 안전 분류를 Life 에 지어내지
  않으며, Life 안전/거절 분류 신설은 별도 행동 변경 카드입니다 (FOLLOW-UP).
- `elapsed_ms` 는 항상 기록합니다. 라우터 벤치마크(routing 문서 §4)와 운영 타임아웃
  정합(architecture §서버 재구축)이 이 값을 씁니다.

## 5. AssistantResponse

`/assistant/query` 가 사용자에게 돌려주는 최상위 응답.

```
AssistantResponse:
  request_id: str
  status:     ANSWERED | PARTIAL | CLARIFY | HANDOFF | UNCERTAIN | REFUSED | PENDING | FAILED
  message:    str                      # 사용자에게 보여줄 텍스트
  results:    list[CapabilityResult]   # 능력별 결과 전부 보존 (내부 필드는 노출 전 필터)
  handoffs:   list[{target, reason}]   # RoutePlan.handoffs 를 그대로 — 항상 표면화
  clarify:    RoutePlan.clarify 와 같은 모양 | None
```

- `UNCERTAIN` 은 **전부 기권(all-ABSTAINED)** 인 결과의 최상위 표현입니다 — REFUSED 나
  FAILED 로 거짓 라벨링하지 않습니다 (D-033).
- `HANDOFF` 는 **순수 핸드오프**(실행된 능력이 없음)의 최상위 표현입니다. 실행과 핸드오프가
  섞이면 status 는 **실행된 능력의 결과에서만** 계산하고, `handoffs[]` 는 별도로 항상
  렌더합니다 (D-034).
- 집계는 개별 CapabilityResult 를 전부 보존한 채 결정적으로 계산합니다 — **성공/기권/거절/
  실패/타임아웃 판정에 LLM 을 쓰지 않습니다** (O-5).

### 집계 진리표 (CONFIRMED — 기계 status 와 사용자 공지는 별개 관심사)

| 조합 | 최상위 status | 사용자 공지 |
| --- | --- | --- |
| OK + OK | ANSWERED | — |
| OK + ABSTAINED | PARTIAL | 한 능력이 근거 부족이었음을 표시 (자료가 늘면 답할 수 있음이 보이게) |
| OK + REFUSED | PARTIAL | 거절 보존. **안전·의료 거절 공지는 사용자 표시 우선순위가 더 높습니다** — 다른 능력이 성공했다고 숨기지 않습니다 |
| OK + ERROR | PARTIAL | 일부 기능 실패 표시 |
| OK + TIMEOUT | PARTIAL | 동일 |
| ABSTAINED 만 | **UNCERTAIN** | 자료 범위 안내 |
| REFUSED 만 | REFUSED | 거절 code 별 개별 표시 (medical 과 safety 를 합치지 않음) |
| ABSTAINED + REFUSED (OK 없음) | REFUSED (안전·정책이 최상위) | 개별 ABSTAINED 결과는 따로 보존 — 사유를 병합하지 않음 |
| ERROR/TIMEOUT 만 | FAILED | 재시도 안내 |
| PENDING + OK | PARTIAL | pending job 메타데이터 보존 |
| 순수 HANDOFF | HANDOFF | 대상 플로우 안내 |
| EXECUTE + HANDOFF 혼합 | 실행 결과에서 계산 | `handoffs[]` 는 별도 보존·항상 렌더 |
| CLARIFY | CLARIFY | 아무것도 실행되지 않았음 |

미래 Gait 의 PENDING 이 얽히는 조합(예: PENDING + REFUSED 우선순위)은 지금 필요한 것
이상으로 확정하지 않습니다 — Gait 비동기 도입 카드의 몫입니다.

## 6. 계약 불변식 (CONFIRMED)

구현이 어떻게 되든 지켜야 하는 것들. 구현 카드는 이 목록을 테스트로 고정해야 합니다.

1. **REFUSED ≠ ERROR.** 오케스트레이터는 상류의 거절을 오류로 재해석하지 않고, 그 역도
   하지 않습니다.
2. **ABSTAINED ≠ REFUSED** (D-033). 자료 부족 기권을 정책·안전 거절로 접지 않고,
   그 역도 하지 않습니다. 전부 기권이면 최상위는 UNCERTAIN 이지 REFUSED/FAILED 가 아닙니다.
3. **refusal·abstention 메타데이터는 무손실 통과.** 상류의 분류 체계를 오케스트레이터가
   병합·개명하지 않습니다.
4. **그래프 상태에 인증 토큰 금지.** principal 만 들어옵니다. 인증은 그래프 밖,
   그래프 안은 능력별 인가만.
5. **그래프 상태·공개 결과에 업로드 바이너리와 검색 청크 전문 금지.** 특히 Life 어댑터의
   출력은 최소 안전 형태로 축소합니다 (O-9): answer · 안전한 인용/출처 식별자 · 해당 시
   인용 URL · 도메인/품질 플래그까지만. **청크 전문 · 내부 유사도 점수(확신도로 오독됨) ·
   불필요한 내부 청크 식별자는 노출 금지.** Training 의 이미 축소된 공개 계약은 축소된
   채로 둡니다.
6. **페이로드는 능력 소유.** 오케스트레이터는 도메인 페이로드를 해석하지 않습니다.
   만능 공용 페이로드를 만들지 않습니다.
7. **elapsed_ms 는 모든 CapabilityResult 에 기록.**
8. **locale 은 상태 필수 필드.** 값이 하나뿐인 지금도 자리를 비우지 않습니다 (§7).
9. **CLARIFY 는 배타적.** clarify 를 낼 요청에서는 능력도 핸드오프도 실행하지 않습니다 —
   stateless 재요청의 이중 실행·이중 과금을 막는 규칙입니다 (O-8).
10. **라우터 실패 ≠ CLARIFY** (O-14). 스키마 검증 실패는 1회 한정 재시도 후 FAILED 이고,
    아무 능력도 실행하지 않으며, 잘못된 모델 원출력을 사용자에게 노출하지 않습니다.
11. **일반 운영 로그·트레이스에 사용자 질문 원문 금지** (O-13, D-037). 기본 관측에
    허용되는 것: request_id · 안전한 범위의 principal 종류 · 라우터 종류(deterministic/llm) ·
    선택된 능력 · 결과 status · 거절/기권 code · elapsed_ms · 오류 범주. 금지: 질문 원문 ·
    인증 토큰 · 업로드 바이너리 · 검색 청크 전문 · 사적 컨텍스트 원본. Training 게이트웨이가
    이미 지키는 선례(`services/training_rag.py` — 질문 원문 비로깅)를 오케스트레이션
    전체로 확장한 것입니다.
12. **`requested_capability` 는 라우팅 신호일 뿐, 절대 인가가 아닙니다** (D-036).
13. **능력 실패 시 무관한 능력으로 조용히 폴백하지 않습니다** (O-10). 성공한 독립 결과는
    다른 능력이 실패·타임아웃해도 보존합니다.

## 7. locale 준비

지금은 `locale = "ko-KR"` 하나입니다. 미래에 `"en-US"` 가 들어올 자리를 계약에만
잡아 둡니다 — 라우터 프롬프트·능력 선택·응답 생성이 로케일을 입력으로 받을 수 있는
모양이면 충분합니다. **영어 UI · 영어 코퍼스 · 영어 가드레일 행동은 지금 구현하지
않습니다.** 프롬프트 언어 컨벤션(영어 + Markdown)과 그 전환 순서는 architecture 문서
§프롬프트·로케일 정책이 소유합니다.
