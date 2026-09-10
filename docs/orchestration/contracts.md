# 오케스트레이터 공통 계약

`/assistant/query` 뒤 LangGraph 오케스트레이션([architecture.md](architecture.md) §논리
오케스트레이션)이 쓸 공통 계약입니다. **2026-08-30 어드버서리얼 아키텍처 리뷰를 거쳐
사람이 승인한 확정 계약**입니다 (D-033 · D-034). 실제 Pydantic/TypedDict 정의는
`backend/src/daengs_backend/orchestration/contracts.py` 에 있으며, 필드의 Python 표현은
구체화됐지만 **상태 모델과 불변식(§6)은 바뀌지 않았습니다.**

이 계약의 전신은 2026-08-22 멘토링에서 팀이 논의한 v1 도구 계약
(`question, dog_profile → answer/evidence/refused/reason`, `frontend/public/mentoring/0822.html`)입니다.
그때 이미 지적된 문제 — **`refused: true` 하나가 정책 거절·자료 부족·입력 부족·내부 오류
네 가지를 겸한다** — 를 CapabilityResult 의 status 분리(§4)로 풉니다. 특히 **자료 부족
기권(ABSTAINED)과 정책·안전 거절(REFUSED)을 별개 상태로 가릅니다** (D-033). "도구가 직접
되묻지 않고 오케스트레이터가 후속 질문을 담당한다"는 그때의 원칙은 CLARIFY(§2)로 이어집니다.

> 표기는 architecture 문서와 같습니다: CONFIRMED / OPEN / PENDING.
> 아래 코드는 읽기 쉬운 **의사 스키마**이며 실제 정의는 위 구현 경로가 원본입니다.

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
아닙니다.** `context` 의 **예약 키 이름으로만** 문서화합니다. (사실 갱신 2026-08-31:
#88 이 `pets` 테이블·`/app/pets` API·서비스 층 소유자 확인을 넣어 "DB 에 프로필 테이블
없음"은 더 이상 사실이 아닙니다. 그래도 이 결정은 유지됩니다 — **어떤 능력도 아직 이
값을 소비하지 않고**, 오케스트레이션이 그 소유권 해석을 소비하는 것은 여전히 별도
카드(FOLLOW-UP, docs/life/roadmap.md B4)라, 필드를 미리 두면 거짓 확신만 만듭니다.) 규칙:

- dog 식별자는 **소유권 증명이 아닙니다.** 라우팅·개인화 힌트일 뿐입니다.
- 전체 반려견 목록·프로필 스냅샷은 그래프 상태에 넣지 않습니다.
- 대상 반려견이 필요한데 모호하면 → CLARIFY.
- 민감한 반려견별 데이터 접근 전에 권위 있는 소유권/프로필 해석이 선행 조건입니다 (FOLLOW-UP).
- 다견 식별자 모델은 지금 설계하지 않습니다.

**돌봄 사실 (CURRENT — #331)** — B4(#202)가 놓은 `active_dog_id → services/dog_context →
context["dog"] → DogContext` 배관에 세 칸이 더 탑니다: `feeding_style`(free · scheduled) ·
`health_conditions`(자유 텍스트, 200자) · `on_medication`(**`True` 만**). 규칙은 견종·나이와
같습니다 — 서버가 프로필에서 조립하고, planner 가 칸마다 화이트리스트로 옮기며, 모양이 틀린
칸은 그 칸만 떨어지고 요청은 안 깨집니다. 소비자는 `GeneralPayload.dog` 이고 Life 어댑터는
견종·나이만 계속 읽습니다. **약 이름과 급식 시각은 이 경계를 안 넘습니다** — `pets.medications`
는 프로필에 머물고 복약 *여부*만 건너가며, 빈 약 칸은 `False` 가 아니라 모름이라 키 자체가
없습니다. 일반 답변 프롬프트가 약·용량 질문을 거절하는데(D-057), 약 이름이 DOG_CONTEXT 에
있으면 그 거절이 힌트로 바뀝니다. 그 카드가 안전 프롬프트 본문을 안 건드렸다는 뜻으로 여기
버전이 적혀 있었는데, 본문은 이후 #415 에서 되묻기 규칙이 붙어 `general-answer-ko-v6` 이
됐습니다 — **이 절이 말하는 것은 지금도 같습니다: 바뀌는 것은 그 안에 실리는 JSON 뿐이고,
프롬프트 본문은 이 배관과 무관하게 움직입니다.**

**케어 로그 (CURRENT — #344)** — 같은 조건(앱 회원 + `active_dog_id`), 같은 세션에서 오늘의
케어 요약을 읽어 `context["care_log"]` 에 얹습니다 (`services/care_log_context` ←
`services/care_event.day_summary`, #332). 모양은 `CareLogContext` — `day` · 종류별 건수(`meal` ·
`medication` · `snack` · `walk`) · 마지막 시각 셋(`HH:MM`, 서울). planner 가 칸마다 화이트리스트로
옮겨 **`GeneralPayload.care_log` 로만** 보냅니다 — Life 는 조례·보조금 문서로 답하는 자리라 오늘
밥 횟수가 답을 안 가르고, Training 은 반려견 사실을 애초에 안 받습니다. **`note` 와 이벤트 목록은
이 경계를 안 넘습니다** — 사용자가 적은 자유 텍스트가 지시문 옆에 놓이는 자리이고, 요약이 답에
필요한 전부입니다. 오늘 기록이 0건이면 키 자체가 없습니다(빈 로그는 "안 챙겼다" 가 아니라 "안
쓴다" 일 수 있어 어느 쪽으로도 안 읽히게). 표가 아직 없거나 DB 가 아프면(`SQLAlchemyError`)
경고만 남기고 로그 없이 답합니다. 프롬프트는 로그가 있을 때만 `CARE_LOG_TODAY` 블록과 규칙
한 문단이 붙고 버전이 `-carelog` 접미사로 갈립니다(지금은 `general-answer-ko-v6-carelog`) —
로그가 없는 요청은 여전히 기본 본문과 글자까지 같습니다 (`tests/test_assistant_care_log.py`
가 고정). **버전 번호는 네 조합이 함께 움직입니다** — 프롬프트에 박히는 JSON 스키마가 넷에
공통이라, 모델 출력 모양이 바뀌면 넷의 본문이 한꺼번에 달라지기 때문입니다 (#415).

**스크리닝 컨텍스트 (CURRENT — #307)** — `context` 의 두 번째 예약 키가 `screening` 입니다.
사용자가 피부 판정 결과에서 이어 물을 때, 앱이 보내는 것은 **기록 id 하나**(`screening_record_id`,
§8)이고 판정 내용은 서버가 DB 에서 읽습니다 — `screening_records` 소유권을 확인하고
`ScreeningContext {verdict, days_ago}` 로 좁혀 `context["screening"]` 에 넣습니다
(`services/screening_context.py`, `orchestration/contracts.py`). 규칙:

- **판정 본문은 요청으로 받지 않습니다.** `/assistant/query` 응답은 대화 turn 으로 저장되므로
  (D-048), 검증하지 않은 판정이 한 번 들어가면 지난 turn 에서 되돌릴 수 없습니다. `location`·
  `dog` 과 같은 규칙입니다 — 구조화 컨텍스트는 **서버가 명시적으로 조립**합니다.
- **병변 분포 · 계열 · 통제 문구 · `stage1` 확률 · 사진 주소는 안 들어갑니다** (§6 불변식 15).
- **못 채워도 실패가 아닙니다.** 남의 기록 · 없는 기록 · 판정 전 · 앱이 옛 `/screen/v1/screen`
  fallback 으로 찍어 행이 없는 건이 전부 조용히 무시되고, 어시스턴트는 이 기능이 생기기 전과
  똑같이 답합니다.
- **이것은 능력 확장이 아닙니다.** Skin 은 계속 HANDOFF 전용이고(routing 문서 §5) `CapabilityName`
  에 `skin` 이 없습니다 — 여기서 읽는 것은 **이미 끝난 판정의 기록**입니다.
- 라우팅 신호가 아닙니다 — 라우터 프롬프트가 보는 것은 `source`·`action`·`active_dog_id`
  뿐입니다 (`semantic._ROUTING_METADATA_KEYS`).

**스크리닝 이력 (CURRENT — #79 3번)** — 세 번째 예약 키가 `screening_history` 입니다.
같은 아이의 **이전** 판정들이고 모양은 `ScreeningHistory {entries: [ScreeningContext]}` 의
`entries` — 즉 위와 **같은 두 칸이 건수만큼**입니다 (`orchestration/contracts.py`,
`services/screening_context.py::resolve_context`). 최근 순이고 상한은
`SCREENING_HISTORY_LIMIT = 3` 입니다. 규칙:

- **진입 신호는 `screening_record_id` 하나입니다.** "지난번보다 어때요" 에는 앱이 보낼
  참조가 따로 없어, 결과 화면에서 이어 묻는 그 자리에 얹습니다 — 이력 전용 요청 필드를
  만들지 않고, 아이는 지목된 기록의 `pet_id` 에서 옵니다. 그 필드를 안 보낸 요청은
  **이력을 읽지 않고 DB 도 열지 않습니다.** `active_dog_id` 만으로 상시 읽는 것(일반 대화
  전반의 피부 기억)은 실제 수요가 확인된 뒤에 넓힙니다.
- **좁힘은 건수와 무관합니다.** 항목이 `ScreeningContext` 자체라 §6 불변식 15 가 그대로
  걸립니다 — 이력이라고 병변 분포·계열·통제 문구·확률·사진 주소가 필요해지지 않습니다.
- **"나아졌다 / 진행됐다" 를 계산하지 않습니다.** 두 시점의 차이는 강아지의 변화가 아니라
  모델의 잡음일 수 있습니다 (D-023 — 2단계 병변명이 holdout 에서 56.6% 틀리고 `stage1` 은
  보정 전). 하류가 할 수 있는 것은 **이전 기록이 있고 그때는 이런 판정이었다**를 나열·안내
  하는 것까지이고, 판단은 진료 권함으로 끝냅니다. 계약에 추세 칸도 확률도 없어서 애초에
  **비교할 데이터가 없는 것이 의도**입니다.
- **상한이 계약에 있습니다.** 오래 쓴 아이일수록 한 요청이 비싸지는 것도, 저장되는 대화
  turn 이 길어지는 것도 `max_length` 가 막습니다 — 세 번째 판정이 문장을 더 참으로 만들지
  않습니다.
- **못 채워도 실패가 아닙니다.** 첫 기록(이력 없음) · `pet_id` 가 NULL 인 기록(아이를 지우면
  FK 가 SET NULL) · `DONE` 이 아닌 이전 행이 전부 조용히 빠집니다. 빈 목록을 실어 "봤는데
  없더라" 를 말하지도 않습니다 — 키가 아예 없습니다. 기준 기록을 못 읽으면 아이를 알 방법이
  없으므로 이력도 없습니다.
- 기준 기록 자신은 이력에 안 들어갑니다 — 이미 `screening` 에 있고, 두 번 실리면 "기록이
  두 건" 으로 읽힙니다. 거꾸로 **기준 기록이 `FAILED` 여도 이력은 갑니다**: 방금 찍은 판정이
  실패한 자리에서 "지난번엔 어땠지" 는 그대로 유효한 질문이라 `screening` 만 빕니다.
- 라우팅 신호가 아닌 것도 같습니다 (`semantic._ROUTING_METADATA_KEYS`).
- **Life 까지 갑니다** — `LifePayload.screening_history` → 어댑터의 `(판정, 경과일)` 쌍 →
  `SCREENING_HISTORY_BLOCK` (§3 · architecture 문서). `screening` 과 **따로** 흐르므로 한쪽만
  있어도 됩니다.
- **답변에도 절로 붙습니다 — 다만 능력이 답을 못 냈을 때만입니다.** `aggregate_results` 가
  `[이전 기록] …` 절을 **결정적으로** 조립합니다 (§5 · O-9). 조건이 좁은 것은 Life 프롬프트만으로는
  이력이 사용자에게 안 닿기 때문입니다 — Life 는 근거 기반 RAG 라 조례·약관만 답합니다
  (2026-09-07 실측: "예전에 찍어둔 기록 있었나?" 에 수의사법 제13조로 답했습니다). 그런데
  물어본 것에 답이 있으면 이력은 안 물어본 이야기라, **핸드오프뿐 · 전부 기권 · 전부 실패**일
  때만 말합니다. "지난번보다 어때요" 가 정확히 그 자리입니다 — 라우터가 skin 핸드오프만 내고
  능력을 하나도 안 고릅니다. CLARIFY 는 배타적이라(§2) 붙지 않습니다.
- **그 절은 견주지 말라를 사용자에게도 말합니다.** 판정을 나란히 놓으면 사람이 스스로 추세를
  읽는데, 그 차이는 매번 다른 사진에서 나온 것이라 몸이 달라졌다는 근거가 아닙니다 (D-023).
  프롬프트에서 모델에게만 금지하면 코드가 지킨 방어를 화면이 풉니다.

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
  model:     str | None                # router=llm 일 때 사용 모델 — `gemini-3.1-flash-lite` (routing 문서 §4)
  prompt_version: str | None           # router=llm 일 때 프롬프트 버전 — `semantic-router-ko-v8`
```

규칙 (CONFIRMED):

- **`requests[]` 와 `handoffs[]` 는 공존할 수 있습니다.** 예: "오늘 산책 괜찮은지도
  알려주고 피부에 난 것도 봐줘" → Walk 실행 + Skin 업로드 플로우 핸드오프.
- **`RoutePlan.clarify` 는 배타적입니다.** `clarify != None` 이면 그 요청에서는 능력도
  핸드오프도 실행하지 않습니다. 부분 실행 후 되물으면, stateless 재요청(§O-8, routing
  문서 §3)이 이미 실행된 능력을 **다시 실행해 비용을 이중으로** 뭅니다.
- **`AssistantStatus.CLARIFY` 의 생산자는 둘입니다** (#415 · D-068). 위의 계획 시점
  되묻기(좌표 누락, `planner._clarify_question`)와, **답 시점** 되묻기 — General 이
  미명세 질문에 `kind="ask"` 를 내면 `aggregate` 가 그것을 같은 `CLARIFY` 로 옮깁니다.
  **`RoutePlan.clarify` 는 그때도 `None` 입니다** — 계획은 이미 굳었고 배타성 불변식
  (§6-9)은 그대로여야 하므로, 되묻기가 사는 곳은 계획이 아니라 집계입니다. 답 시점
  되묻기는 `results` 가 general **단독**일 때만 성립하고, 그 응답의 `results` 는 비어
  나갑니다(진리표 §5) — General 이 돌았다는 사실은 `route` 트레이스에만 남습니다.
  **`message` 는 두 조각입니다** — 기록으로 지금 말할 수 있는 것(`data["answer"]`)이 앞,
  물을 것이 뒤입니다. `clarify.question` 은 **질문만** 갖습니다: 되묻기를 따로 렌더하는
  클라이언트가 기록 요약까지 질문 자리에 그리지 않게 하려는 분리입니다.
- 요약이 필요하면 mode 는 세 목록에서 **파생**합니다 — 진실 원천이 아닙니다.
- 이 구조를 범용 워크플로 액션 DSL 로 일반화하지 않습니다.
- `router` 출처 필드는 관측용이자 회귀 판별용입니다. 결정적 경로가 낸 오답과 LLM 이 낸
  오답은 고치는 방법이 다릅니다. `model`·`prompt_version` 도 같은 성격이라 세 값은
  **라우팅 의미가 아닙니다** — 벤치마크의 `_semantic_plan_key` 가 셋 다 비교에서 뺍니다.
  결정적 경로에서 뒤의 둘이 `None` 인 것은 "모름"이 아니라 **부른 모델이 없다**는 뜻입니다.
  공개 응답으로 나가는 것은 이 셋뿐이고, 그것도 점검 권한이 있을 때만입니다 (§5 `route`).
- **라우터의 구조화 출력이 스키마 검증에 실패하면 CLARIFY 로 위장하지 않습니다** (O-14,
  routing 문서 §2) — CLARIFY 는 사용자의 정보 부족이고, 스키마 실패는 시스템 실패입니다.
  1회 한정 재시도 후에도 실패면 아무 능력도 실행하지 않고 최상위 FAILED 를 냅니다.
  잘못된 모델 원출력은 사용자에게 노출하지 않습니다.

## 3. CapabilityRequest

```
CapabilityRequest:
  capability: training | life | walk | place | general
      # 실행 registry (place: PR #196, 의미 선택은 PR #204). `general` 은 PR #279 의 일반 답변
      # 폴백 — 실행되고 저장되지만 **라우터가 고르지 못하고** planner 규칙만이 넣는다 (아래 §3 끝)
  payload:    <능력별 타입>                     # 능력이 소유하는 도메인 페이로드
  timeout_ms: int | None                       # 선택 — 능력별 기본값을 덮을 때만
```

`timeout_ms` 는 **오케스트레이션 응답 기한**이지 하위 작업의 강제 취소 보장이 아닙니다.
`asyncio.to_thread()` 로 위임한 블로킹 작업은 TIMEOUT 응답 뒤에도 능력 자체의 provider/domain
타임아웃 안에서 완료 중일 수 있습니다. 따라서 즉시 stateless 재시도는 이전 실행과 잠시
겹치거나 그 뒤에 대기할 수 있으며, 재시도 정책이 이를 고려해야 합니다. 특히 현재 Training
실행은 process-local lock 으로 직렬화되므로 끝나지 않은 실행 뒤에 재시도가 대기할 수 있습니다.

**만능 공용 페이로드를 만들지 않습니다 (CONFIRMED).** `question + dog_profile` 하나로 모든
능력을 덮으려던 v1 계약이 Walk(좌표·시각) 앞에서 이미 안 맞았습니다. 페이로드 타입은
능력이 소유하고, 오케스트레이터는 그 내용을 해석하지 않고 전달만 합니다.

Place의 첫 계약은 `PlacePayload {query, lat, lon}`뿐입니다. `query`는 공백 여부만 검증하고
원문을 보존하며(공백도 다듬지 않습니다 — Place 서비스가 원문의 문자 구간에 해석을
grounding합니다), 좌표는 검증된 `context.location`에서 복사합니다. 반경은 adapter의 서버
정책(3km)이고 `active_dog_id`나 profile 값은 payload에 없습니다. PR #196은
`requested_capability=place`만 결정적으로 열었고, **PR #204(D-051)에서 의미 라우터도 Place를
고릅니다** — `PlacePayload` 자체는 그대로입니다.

**Life payload 는 판정 기록도 받습니다** (#283). `LifePayload {question, dog, screening}` 에서
`screening` 은 §1 의 `context["screening"]` 을 planner 가 화이트리스트로 옮긴 것이고,
`dog` 과 규칙이 같습니다 — 부르는 쪽이 이미 푼 값만 지나가고, 모양이 틀리면 `None` 이지
422 가 아닙니다. **`general` 은 받지 않습니다**: 근거 없이 답하는 자리라(D-057) 판정을 쥐여
주면 자기 `diagnosis` 거절이 막으려던 문장을 부르게 됩니다. Life 가 받는 이유는 그 반대로,
"이런 경우 지원이 있어요" 를 만드는 조례·보조금 문서를 Life 가 검색하기 때문입니다.
`ScreeningContext` 의 두 칸(§1 · 불변식 15)이 여기서도 그대로이고, `daengs_life` 로는
원시값 둘로 건너갑니다 — 도메인이 오케스트레이션 타입을 알면 D-035 가 막은 방향이 됩니다.

**이전 판정들도 같은 규칙으로 받습니다** (#79 3번). `LifePayload.screening_history` 는 §1 의
`context["screening_history"]` 를 planner 가 **항목마다** 같은 화이트리스트로 옮긴 것이라,
좁힘이 건수와 무관하게 걸립니다. `screening` 과 **별개의 칸**인 것은 둘이 따로 없기 때문입니다 —
첫 기록은 이력이 없고, 이번 판정이 실패한 자리에는 이력만 있습니다. `daengs_life` 로는
`(판정, 경과일)` **쌍의 튜플**로 건너갑니다. 상한을 넘는 목록이 오면 잘라서 보냅니다 —
상류가 이미 잘랐으므로 그런 목록은 상류 결함인데, 여기서 422 를 내면 사용자가 보지도 고치지도
못하는 결함 때문에 답할 수 있는 질문이 죽습니다. `general` 이 안 받는 것도 같습니다.

**payload는 능력별 명시 분기로 만듭니다** (D-051). 예전 조립 루프는 Training/Life가 아니면
좌표 payload를 주는 `else` 폴백이었고, 그것은 Walk가 유일한 좌표 능력인 동안에만 맞았습니다 —
`place`가 선택 가능해지는 순간 Place에 `query` 없는 WalkPayload를 주어 검증 실패 → 최상위
FAILED가 됩니다. 이제 새 `ExecuteName`은 자기 payload를 적거나 요청을 소리 나게 세우거나
둘 중 하나이고, 남의 모양을 물려받지 않습니다. 요청 순서도 모델의 나열 순서가 아니라
`CapabilityName` 선언 순서로 고정합니다 — 그 순서가 집계 message의 절 순서로 사용자에게
그대로 보이기 때문입니다.

**`general` 은 두 길로 계획에 들어옵니다** (D-057 ①). ⓐ planner 규칙: 의미 결정이 비어 있고(능력 0 ·
핸드오프 0 · 스몰토크 아님) `DAENGS_GENERAL_FALLBACK` 이 켜져 있으면 `GeneralPayload {question, dog}`
하나 — Life 와 같은 규칙, 좌표 없음 — 를 조립합니다. ⓑ 라우터 목적지(`semantic-router-ko-v9`): 돌봄·
건강 의도가 전문 능력과 섞인 발화에서 라우터가 `general` 을 **추가로** 고릅니다 — 전문 능력을 대신하지
않고, 반려견과 무관한 요청에는 아무것도 고르지 않습니다. `general` 은 실행 순서 맨 뒤, 좌표 불필요,
좌표 게이트(CLARIFY)는 그대로 선택 전체에 하나이며, `requested_capability="general"` 은 풀리지 않는
신호입니다. **플래그가 꺼져 있으면 planner 가 결정에서 `general` 을 떼어 냅니다** — 기본값이 `false` 라
켜기 전까지 계획은 예전과 글자까지 같고, 빈 결정은 FAILED 입니다.

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
(`services/training_rag.py` 의 내부 reason→공개 decision 매핑)가 이미 상류에서 갈라 둔
구분입니다.

### v1 매핑 (CONFIRMED — 상류 사실은 2026-08-31 dev #101 코드로 재검증)

| 상류 결과 | CapabilityResult |
| --- | --- |
| Training `ANSWER` | OK |
| Training `UNCERTAIN` | **ABSTAINED** |
| Training `SAFETY_REFUSAL` | REFUSED (`refusal.code` 에 상류 분류 보존) |
| Training `MEDICAL_REFUSAL` | REFUSED (동일) |
| Training 실제 생성 타임아웃 | TIMEOUT |
| Training 공급자·검색·런타임 실패 | ERROR |
| Life 근거 있는 200 응답 | OK (`ungrounded` 지표는 품질 메타데이터로 전파 가능) |
| Life 무근거 404 (`근거를 찾지 못했다`) | **ABSTAINED** (`abstention.code = no_evidence`) |
| Life 상류/시스템 실패 (502·503) | ERROR |
| Life 타임아웃 (504) | TIMEOUT |
| Walk 정상 판정 — **GOOD·CAUTION·UNSAFE 전부** | OK — **UNSAFE 는 성공한 도메인 판정**이지 REFUSED 가 아닙니다 |
| Walk 판정 불가 (`unknown` — 관측 공급자 부재/실패, 503 + 전체 본문) | ABSTAINED |
| Place 후보 1건 이상(직접 해석 또는 공개된 대안 lens) | OK — 대안·미해결 신호를 notice로 보존 |
| Place 정상 응답이지만 후보 없음·추가 선택 필요·미지원 의미 | ABSTAINED — 공개 projection과 refinement는 `data`에 함께 보존 |
| Place 내부 HTTP/provider 실패 | ERROR 또는 TIMEOUT — provider 본문·원출력은 노출하지 않음 |
| General `kind=answer` (PR #279) | OK — `data.answer` 뿐. 근거 없는 생성이라 인용이 없다 |
| General `kind=refuse` — 진단 · 약/용량 · 응급 · 제도/수치 · 도메인 밖 | **REFUSED** — `refusal.code` 는 사유 범주, `refusal.message` 는 코드가 쓴 고정 안내("수의사에게" / "제도 정보 기능에") |
| General 프로바이더 실패 · 출력 스키마 불일치 | ERROR 또는 TIMEOUT — 라우터 실패 문구가 아니라 능력 하나의 실패로 보인다 |

- **refusal 은 상류 분류를 보존합니다.** Training 의 SAFETY_REFUSAL / MEDICAL_REFUSAL
  구분(공개 decision — `schemas/training.py` · docs/training/rag-demo.md)이 `refusal.code`
  로 그대로 올라옵니다. 오케스트레이터가 이것을 합치거나 바꿔 말하지 않습니다.
- **Training 은 in-process 경계를 씁니다** (#94 이후). 어댑터의 호출 지점은
  `daengs_backend/services/training_rag.py` 게이트웨이(공개 4-decision 계약)이지,
  `daengs_training.service.RAGService`·PGVector·Gemini 내부가 아닙니다.
- **Training 실패 구분은 Card 1 에서 해결했습니다.** 실제 생성 타임아웃은
  `GenerationTimeoutError → TrainingTimeoutError → TrainingRagTimeoutError` 로 경계를 따라
  보존되고 어댑터가 TIMEOUT 으로 번역합니다. 공급자·PGVector·런타임 실패는
  `TrainingRagUnavailableError` 를 거쳐 ERROR 가 됩니다. 공개 `/training/chat` 은 기존 503
  응답 호환성을 유지하므로 공급자 구현 세부가 API 계약으로 새지 않습니다.
- Training 내부의 `REFUSE(no_results)` 만 UNCERTAIN → ABSTAINED 로 번역합니다.
  `safety_boundary_training_harm`, `safety_boundary_medical`, `output_safety_guardrail` 과
  해석되지 않은 다른 REFUSE reason 은 상류 reason 을 보존한 REFUSED 입니다. 알 수 없는
  거절을 자료 부족으로 추측하지 않습니다.
- **Life 는 재설계하지 않습니다** (O-3, D-035). 어댑터가 **이미 있는 기계 신호만**
  매핑합니다 — 무근거 404 는 서빙 층의 명시적 도메인 정책이고(`app/services/ask.py`),
  `ungrounded` 는 품질 신호입니다. **수용된 v1 한계**: Life 가 산문으로만 물러서는
  경우(기계 신호 없음)는 OK 로 통과합니다. Training 급 안전 분류를 Life 에 지어내지
  않으며, Life 안전/거절 분류 신설은 별도 행동 변경 카드입니다 (FOLLOW-UP).
- **Place는 내부 응답 전체를 통과시키지 않습니다.** 대화 turn이 `AssistantResponse` 전체를
  저장하므로 adapter가 최대 3 lens·lens당 3건·전체 9건·48KiB로 다시 제한합니다.
  지도 좌표·표시 사실·KTO/KCISA provenance·refinement는 남기고 search plan, provider 자료,
  원천 raw, 전체 policy receipt는 제거합니다.
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
  route:      {router, model, prompt_version} | None   # 점검 권한이 있을 때만 (CURRENT — #238)
```

**`route` 만 받는 사람이 다릅니다 (CURRENT — #238).** 나머지 필드는 부르는 사람이 누구든 같고,
`route` 는 `search:inspect` 권한을 가진 관리자에게만 실립니다. **앱 회원에게는 항상 `null`
입니다** — 권한 목록 자체가 비어 있어(`routers/assistant.py _principal_context`) 구조적으로
그렇습니다. 키는 늘 있고 값만 없습니다.

- 담기는 것은 **라우터 종류 · 모델 이름 · 프롬프트 버전**뿐입니다. 이 셋은 D-037 이 기본 관측에
  **허용한** 항목 그대로이고, 질문 원문 · 프롬프트 본문 · 공급자 payload 는 응답에도 로그에도
  싣지 않습니다. 선택된 능력은 여기 없습니다 — `results[].capability` 와 `handoffs[]` 로 이미
  공개돼 있습니다.
- **결정론적 경로는 `model`·`prompt_version` 이 `null` 입니다.** 모르는 것이 아니라 부른 모델이
  없다는 뜻이라, 화면은 그 둘을 구분해 그려야 합니다.
- **`RoutePlan` 을 거치지 않는 두 응답에도 붙습니다** — 순수 사교 발화(고정 문구)와 라우터 실패
  FAILED. 실행된 능력이 하나도 없는 것이 정상인 자리라, 경위가 없으면 고장과 구별되지 않습니다.
- **저장되는 대화 turn 에는 실리지 않습니다.** 저장은 앱 회원만 하고(D-048) 라우터는 그 경로에
  플래그를 넘기지 않습니다. `services/chat.py public_response_of` 가 응답 전체를 적재하는
  자리라, 한 번 실리면 지난 turn 에서 되돌릴 수 없습니다.

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
| CLARIFY (계획 시점) | CLARIFY | 아무것도 실행되지 않았음 |
| CLARIFY (답 시점, general 단독 `ask`) | CLARIFY | General 만 돌았고 `results` 는 비어 나감 — `route` 에만 남음 (#415) |

미래 Gait 의 PENDING 이 얽히는 조합(예: PENDING + REFUSED 우선순위)은 지금 필요한 것
이상으로 확정하지 않습니다 — Gait 비동기 도입 카드의 몫입니다.

## 6. 계약 불변식 (CONFIRMED)

구현이 어떻게 되든 지켜야 하는 것들. 구현 카드는 이 목록을 테스트로 고정해야 합니다.

1. **REFUSED ≠ ERROR.** 오케스트레이터는 상류의 거절을 오류로 재해석하지 않고, 그 역도
   하지 않습니다.
2. **ABSTAINED ≠ REFUSED** (D-033). 자료 부족 기권을 정책·안전 거절로 접지 않고,
   그 역도 하지 않습니다. 전부 기권이면 최상위는 UNCERTAIN 이지 REFUSED/FAILED 가 아닙니다.
3. **refusal·abstention 메타데이터는 무손실 통과.** 상류의 분류 체계를 오케스트레이터가
   병합·개명하지 않습니다. 거절 문장이 `[N]` 으로 근거를 지목하면 그 근거도 같이 갑니다 —
   `REFUSED` 가 `data.citations` 를 가질 수 있고, OK 와 같은 규칙(5)으로 줄입니다 (RAG-077).
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
9. **CLARIFY 는 배타적.** `RoutePlan.clarify` 를 낼 요청에서는 능력도 핸드오프도 실행하지
   않습니다 — stateless 재요청의 이중 실행·이중 과금을 막는 규칙입니다 (O-8). 답 시점
   되묻기(#415)도 이 불변식을 안 건드립니다: `RoutePlan.clarify` 를 **사후에 쓰지 않고**,
   응답의 `results` 를 비워 클라이언트가 보는 배타성을 그대로 지킵니다.
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
14. **능력 이름의 사본 세 벌은 항상 같은 값을 가집니다** (#269). 능력을 추가할 때 다음
    셋을 **함께** 넓히세요 — 서로를 참조하지 않고 손으로 적힌 목록이라, 하나만 빠져도
    타입 검사에는 안 걸립니다.

    | 자리 | 이름 | 역할 |
    | --- | --- | --- |
    | `orchestration/contracts.py` | `CapabilityName` | 능력의 원본 정의 |
    | `orchestration/semantic.py` | `ExecuteName` | 라우터가 고를 수 있는 목적지 |
    | `schemas/chat.py` | `AgentCategory` | 저장된 대화를 읽어 줄 때의 꼬리표 |

    `place` 를 넣을 때 앞의 둘만 넓히고 셋째를 놓쳐서, Place 가 답한 대화가 **저장은 되고
    읽을 때만** 응답 검증에 걸려 `/app/chats` 가 500 을 냈습니다. 쓰는 쪽이 읽는 쪽보다
    넓으면 사고가 데이터에 박히고, 코드를 고칠 때까지 그 강아지의 기록이 통째로 죽습니다.
    `tests/test_orchestration_contracts.py::test_capability_names_have_exactly_three_copies_and_they_agree`
    가 셋을 대조합니다.

    `general` (D-057) 은 셋 다에 있습니다 — v9 부터 라우터 목적지이기도 해서입니다 (§3 끝).
    프론트의 `lib/assistant.ts CapabilityName` 도 손으로 맞추는 사본입니다.

15. **스크리닝의 통제 문구와 병변 분포는 어떤 payload · 프롬프트 · 그래프 상태에도 들어가지
    않습니다** (#307). 오케스트레이션이 스크리닝에서 받는 것은 **판정 종류와 경과일**뿐입니다
    (`ScreeningContext`). 불변식 5 의 형제이고, 근거는 D-023 입니다 — 2단계 병변명이 holdout
    에서 56.6% 틀려서 계약에 `top1` 을 아예 두지 않았고, 지금 그 방어가 서 있는 이유는
    **이름을 말하는 코드 경로가 없다**는 사실 자체입니다. 필드가 하나 늘면 그 사실이 사라지는데
    예외도 실패도 안 나므로, 계약이 직접 거절합니다
    (`tests/test_orchestration_contracts.py` · `tests/test_screening_context.py`).
    `headline`·`body`·`action`·`disclaimer` 는 사용자에게 무수정으로 갈 것이지 모델이 읽을
    것이 아닙니다 (PR #79). **합성은 2차 LLM 이 아니라 결정적 절 조립입니다** (O-9 · §5).

## 7. locale 준비

지금은 `locale = "ko-KR"` 하나입니다. 미래에 `"en-US"` 가 들어올 자리를 계약에만
잡아 둡니다 — 라우터 프롬프트·능력 선택·응답 생성이 로케일을 입력으로 받을 수 있는
모양이면 충분합니다. **영어 UI · 영어 코퍼스 · 영어 가드레일 행동은 지금 구현하지
않습니다.** 프롬프트 언어 컨벤션(영어 + Markdown)과 그 전환 순서는 architecture 문서
§프롬프트·로케일 정책이 소유합니다.

## 8. 공개 진입 계약 — `POST /assistant/query` (CURRENT — Card 3, PR #115)

이 절 위의 §1~§6 은 그래프 안쪽 계약입니다. 여기는 그 앞의 **HTTP 경계**가 무엇을
검증하고 무엇을 절대 신뢰하지 않는지를 고정합니다. 원본 코드는
`backend/src/daengs_backend/schemas/assistant.py`(요청 DTO) ·
`backend/src/daengs_backend/routers/assistant.py`(엔드포인트) — 아래는 그 계약의
의사 스키마이지 Pydantic 소스를 그대로 옮긴 것이 아닙니다.

**인증** — `admin_or_app_user(Perm.READ)`, `/life/walk-conditions`·`/life/ask` 와 같은 문입니다. 인증되지
않은 요청은 오케스트레이션에 닿기 전에 401 입니다.

```
AssistantQueryRequest:            # extra="forbid" — 목록에 없는 필드는 전부 422
  query:                  str                     # 필수
  requested_capability:   str | None = None
  source:                 str | None = None
  action:                 str | None = None
  active_dog_id:          str | None = None
  screening_record_id:    UUID | None = None
  location:               LocationIn | None = None

LocationIn:                       # extra="forbid"
  lat: float   # 33..39
  lon: float   # 124..132
```

검증 규칙 (CONFIRMED):

- **`query` 는 `.strip()` 기준으로 비어있으면 422 입니다.** `"   "` 만으로는 통과하지
  못합니다. 검증은 trim 된 값으로 판단하지만 **오케스트레이션으로 넘기는 값은 원문
  그대로**입니다 — 원문 보존은 orchestration 이 소유합니다(§2 위 규칙과 같은 이유).
- `source`·`action`·`active_dog_id` 는 있으면 trim 후 비어있지 않은 문자열이어야
  합니다. dict·list·숫자·bool 같은 다른 모양은 Pydantic 타입 검증에서 이미 422 입니다
  — `semantic.py` 의 내부 fail-fast(`ValueError`)에 닿기 전에 이 경계가 막습니다.
- `location` 이 있으면 `lat`/`lon` 범위는 `/life/walk-conditions` 과 `WalkPayload`(§3)가 이미 쓰는
  범위와 같습니다. 새 지리 정책이 아닙니다.
- **`location` 이 없어도 유효한 요청입니다.** Walk 가 나중에 선택되면 CLARIFY 는
  기존 결정론적 planner 가 냅니다(§2) — HTTP 검증이 미리 막지 않습니다.
- **클라이언트가 보낼 수 있는 임의의 `context` 딕셔너리는 없습니다.** 구조화
  컨텍스트는 위 필드에서만, 서버가 명시적으로 조립합니다.
- **`screening_record_id` 는 참조이지 판정이 아닙니다** (#307). UUID 가 아니면 422 이고,
  내 기록이 아니거나 아직 판정 전이면 **조용히 무시**합니다 — 404 를 주면 "그 기록이
  존재한다" 가 새고, 기록을 못 찾았다는 이유로 답할 수 있는 질문까지 죽습니다. 판정 본문을
  담은 필드는 없고, 보내면 `extra="forbid"` 가 422 로 거부합니다 (§1 스크리닝 컨텍스트).
- **`token`·`authorization`·`credentials`·`user`·`principal`·`permissions` 같은 신원
  필드는 요청 본문에서 받지 않습니다.** `extra="forbid"` 가 422 로 거부하고, 애초에
  `PrincipalContext` 는 본문이 아니라 인증된 의존성에서만 서버가 만듭니다.
- `requested_capability` 는 라우팅 신호일 뿐 인가가 아닙니다(불변식 12) — 새 능력을
  만들지 않고, planner 가 모르는 값이면 의미 라우팅으로 그대로 넘깁니다.
- 응답은 `AssistantResponse` 를 **그대로** 돌려줍니다 — 별도 래퍼도, `FAILED` 를
  포함한 상태 재해석도 없습니다. 의미 상태는 이 문서 §5 가 소유합니다.
