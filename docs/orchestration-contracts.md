# 오케스트레이터 공통 계약 (제안)

`/assistant/query` 뒤 LangGraph 오케스트레이션([orchestration-architecture.md](orchestration-architecture.md) §논리
오케스트레이션)이 쓸 공통 계약의 **제안 문서**입니다. 구현은 없습니다 — 이 PR 은 문서만이고,
필드 이름·타입은 구현 카드에서 다듬을 수 있지만 **불변식(§6)은 다듬는 대상이 아닙니다.**

이 계약의 전신은 2026-08-22 멘토링에서 팀이 논의한 v1 도구 계약
(`question, dog_profile → answer/evidence/refused/reason`, `frontend/public/mentoring/0822.html`)입니다.
그때 이미 지적된 문제 — **`refused: true` 하나가 정책 거절·자료 부족·입력 부족·내부 오류
네 가지를 겸한다** — 를 CapabilityResult 의 status 분리(§4)로 풉니다. "도구가 직접 되묻지
않고 오케스트레이터가 후속 질문을 담당한다"는 그때의 원칙은 CLARIFY(§3)로 이어집니다.

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

`context` 의 내용 범위 — 특히 반려견 컨텍스트를 `active_dog_id` 로 줄지 `dog_profile`
스냅샷으로 줄지 — 는 **OPEN** 입니다 (routing 문서 §6).

## 2. RoutePlan

라우터의 산출물. 실행할지, 되물을지, 넘길지를 하나로 표현합니다.

```
RoutePlan:
  mode:      EXECUTE | CLARIFY | HANDOFF
  requests:  list[CapabilityRequest]   # EXECUTE 일 때 1개 이상
  clarify:   {question: str, missing: list[str]} | None   # CLARIFY 일 때
  handoff:   {target: str, reason: str} | None            # HANDOFF 일 때 (예: 대상 UI 플로우)
  router:    deterministic | llm       # 출처 — 어느 경로가 이 판단을 냈는가
  model:     str | None                # router=llm 일 때 사용 모델 (미정 — routing 문서 §4)
```

- `router` 출처 필드는 관측용이자 회귀 판별용입니다. 결정적 경로가 낸 오답과 LLM 이 낸
  오답은 고치는 방법이 다릅니다.
- CLARIFY·HANDOFF 의 의미는 routing 문서 §3 이 소유합니다. 여기서는 모양만 정합니다.

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
  status:     OK | REFUSED | PENDING | ERROR | TIMEOUT
  data:       <능력별 타입> | None      # OK/부분 결과. 도메인 데이터는 능력 소유
  refusal:    {code: str, message: str} | None   # REFUSED 일 때 — 상류 분류를 그대로 보존
  job:        {job_id: str, poll: str} | None    # PENDING 일 때 (미래 Gait 비동기 경로)
  error:      {kind: str, detail: str} | None    # ERROR/TIMEOUT 일 때
  elapsed_ms: int
```

status 다섯 값의 구분이 이 계약의 핵심입니다:

| status | 뜻 | v1 계약에서는 |
| --- | --- | --- |
| OK | 능력이 정상 응답 | `refused: false` |
| REFUSED | **능력의 도메인 판단으로** 답하지 않기로 함 | `refused: true` + `reason: medical` 등 |
| PENDING | 접수됐고 결과는 비동기 | `job_id` 반환 |
| ERROR | 시스템 실패 — 거절이 아님 | `refused: true` + `reason: error` 로 뭉개짐 |
| TIMEOUT | 시간 안에 못 끝남 — 거절이 아님 | (구분 없음) |

- **refusal 은 상류 분류를 보존합니다.** Training 의 SAFETY_REFUSAL / MEDICAL_REFUSAL /
  UNCERTAIN 구분(training-rag-demo.md)이 `refusal.code` 로 그대로 올라옵니다.
  오케스트레이터가 이것을 합치거나 바꿔 말하면, "자료가 늘면 답할 수 있는 것"과
  "자료가 늘어도 답하지 않는 것"의 구분이 사용자 앞에서 사라집니다.
- Life 는 지금 이 봉투에 채울 거절 분류 자체가 없습니다 — **OPEN** (routing 문서 §6).
  문서가 지어내지 않습니다.
- `elapsed_ms` 는 항상 기록합니다. 라우터 벤치마크(routing 문서 §4)와 운영 타임아웃
  정합(architecture §서버 재구축)이 이 값을 씁니다.

## 5. AssistantResponse

`/assistant/query` 가 사용자에게 돌려주는 최상위 응답.

```
AssistantResponse:
  request_id: str
  status:     ANSWERED | PARTIAL | CLARIFY | REFUSED | PENDING | FAILED
  message:    str                      # 사용자에게 보여줄 텍스트
  results:    list[CapabilityResult]   # 근거가 된 능력별 결과 (내부 필드는 노출 전 필터)
  clarify:    RoutePlan.clarify 와 같은 모양 | None
```

**최상위 status 를 능력별 status 들에서 어떻게 접는가(집계 규칙)는 OPEN 입니다.**
예를 들어 2개 능력 중 하나가 OK 하나가 REFUSED 면 PARTIAL 인가 — 그럴듯한 답이 있지만
사람 결정이 남아 있는 자리라 여기서 확정하지 않습니다 (routing 문서 §6). 확정된 것은
표현력뿐입니다: 위 여섯 상태를 **표현할 수 있어야** 하고, REFUSED 를 FAILED 로 뭉개는
집계는 어떤 규칙이 되든 금지입니다 (§6).

## 6. 계약 불변식 (CONFIRMED)

구현이 어떻게 되든 지켜야 하는 것들. 구현 카드는 이 목록을 테스트로 고정해야 합니다.

1. **REFUSED ≠ ERROR.** 오케스트레이터는 상류의 거절을 오류·근거 부족으로 재해석하지
   않고, 그 역도 하지 않습니다.
2. **refusal 메타데이터는 무손실 통과.** 상류의 거절 분류 체계를 오케스트레이터가
   병합·개명하지 않습니다.
3. **그래프 상태에 인증 토큰 금지.** principal 만 들어옵니다. 인증은 그래프 밖,
   그래프 안은 능력별 인가만.
4. **그래프 상태·로그에 업로드 바이너리와 검색 청크 전문 금지.** 인용은 라벨·식별자로.
5. **페이로드는 능력 소유.** 오케스트레이터는 도메인 페이로드를 해석하지 않습니다.
   만능 공용 페이로드를 만들지 않습니다.
6. **elapsed_ms 는 모든 CapabilityResult 에 기록.**
7. **locale 은 상태 필수 필드.** 값이 하나뿐인 지금도 자리를 비우지 않습니다 (§7).
8. **집계 규칙이 미정인 동안 조용히 확정하지 않습니다.** 구현이 임시 규칙을 쓰면
   OPEN 결정을 가리키는 표시를 남깁니다.

## 7. locale 준비

지금은 `locale = "ko-KR"` 하나입니다. 미래에 `"en-US"` 가 들어올 자리를 계약에만
잡아 둡니다 — 라우터 프롬프트·능력 선택·응답 생성이 로케일을 입력으로 받을 수 있는
모양이면 충분합니다. **영어 UI · 영어 코퍼스 · 영어 가드레일 행동은 지금 구현하지
않습니다.** 프롬프트 언어 컨벤션(영어 + Markdown)과 그 전환 순서는 architecture 문서
§프롬프트·로케일 정책이 소유합니다.
