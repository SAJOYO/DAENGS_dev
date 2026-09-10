# 제한된 멀티턴 이어짐과 복구 — 설계

PR #416. 설계 골격은 `docs/orchestration/conversation-quality.md` §7 카드 B 입니다.

## 0. 개정 (2026-09-11) — 이력 전달에서 Turn Resolver 로

**초판(2026-09-10)은 "완료 turn 3쌍의 원문을 라우터·General 프롬프트에 얹는" 안이었습니다.
사람 결정으로 그 모양을 바꿉니다** (`2c3d13cc`, 방향 메모). 댕쓰가 되려는 것은 대화 상대이고,
핵심은 능력을 늘리는 것이 아니라 **대화를 책임지는 자리 하나**를 만드는 것입니다. 그래서
#416 은 이력을 전달하는 카드가 아니라 **현재 발화를 가르고 앞 요청에 잇는 pre-routing
Turn Resolver** 입니다.

여덟 가지 결정 중 **여섯은 그대로 살아 있습니다** — 그 여섯은 "이력을 어떻게 다루나" 에 대한
답이지 "이력을 어디에 얹나" 에 대한 답이 아니었기 때문입니다.

| 결정 | 상태 | 무엇이 달라졌나 |
| --- | --- | --- |
| ① 개수·종류 (완료 turn 3쌍, user·assistant 원문) | **유지** | 쓰이는 자리만 바뀝니다 — 프롬프트에 얹는 블록이 아니라 **Turn Resolver 의 후보군**입니다 |
| ② 누가 보는가 | **교체** | 라우터·General 프롬프트에 원문 블록 → **Turn Resolver 만 이력을 보고, 라우터와 선택된 capability 는 구조화 결과만** 봅니다 |
| ③ 길이 한도 (2,000 / 400 / 3,000) | **유지** | 한도가 걸리는 자리가 Turn Resolver 입력으로 옮겨갑니다 |
| ④ 프라이버시·로깅 | **유지 + 추가** | 별도 인자·`LOGGER` 금지·랩 파일 규칙 그대로. 모델이 만든 `standalone_query` 를 사실로 저장 금지하는 조항이 붙습니다 |
| ⑤ 이력 없을 때 (블록 미부착, 바이트 동일) | **유지 + 강화** | `relation=NEW` **fast path** 로 승격됩니다 — 모델 호출 자체를 건너뜁니다 |
| ⑥ `CLARIFY` 이음 | **교체** | "#415 대기로 공란" → **이 카드의 핵심 책임**. #415 가 계약을 확정해 더는 공란이 아닙니다 |
| ⑦ 오염 방지 넷 | **유지 + 강화** | 응급 경계가 「구조적으로 못 본다」에서 「**현재 원문을 직접 검사하고 Resolver 가 못 덮는다**」로 강해집니다 |
| ⑧ 수용 케이스 | **확장** | 5건 → **9건** |

초판이 근거로 삼은 Place 실측(§2)은 여전히 유효합니다 — 다만 그 교훈이 걸리는 자리가
라우터 프롬프트에서 **Turn Resolver 프롬프트**로 옮겨갑니다.

## 1. 무엇을 고치나

대화를 저장하지만 추론은 그것을 못 봅니다. `routers/assistant.py` 의 `_dispatch` 가
`service.run(query=body.query, …)` 로 **현재 질의 하나만** 넘기고, 세션에서 오는 것은
`active_dog_id` 뿐입니다. `services/chat.py:run_persisted_turn` 은 턴을 저장만 하며,
이력을 읽는 유일한 자리는 같은 `client_message_id` 의 정확한 재생입니다.
**저장은 대화 메모리가 아닙니다.**

이 카드는 **대화를 책임지는 자리 하나**를 만듭니다 — 이력을 읽어 현재 발화를 앞 요청에 잇는
pre-routing Turn Resolver 입니다. 범용 대화 요약 · 장기 기억 · 다중 세션 교차 참조는 범위
밖이고, Response Composer 는 이 카드를 측정한 뒤 판단합니다. 첫 턴을 되묻게 만드는 것은
`feat/assistant-ask-mode` (#415) 가 따로 맡습니다.

## 2. 고른 안 — pre-routing Turn Resolver

라우팅 **앞에** 자리 하나를 둡니다. 그 자리가 이력을 읽고, 현재 발화를 다섯 관계 중 하나로
가르고, 앞 요청이나 대기 중인 되묻기에 잇고, **제한된 구조화 컨텍스트**를 만들어 라우터와
선택된 capability 에 넘깁니다. 이력 원문은 거기서 멈춥니다.

검토한 대안 셋:

| 안 | 왜 안 골랐나 |
| --- | --- |
| 이력 원문을 라우터·General 프롬프트에 얹기 (초판) | 관계 판정이 프롬프트 안에 묻힙니다 — `REPEAT` 을 놓쳤을 때 "모델이 이력을 못 읽었나, 읽고도 무시했나" 를 가릴 수 없습니다. 실패가 관측되지 않는 설계입니다 |
| 질의 재작성 경계 하나 | 수용 케이스의 `REPEAT` · `META` 는 지시어 해소가 아니라 **직전 실패에 대한 항의**라 재작성으로 표현할 수 없습니다 |
| 전 능력에 이력 | `GeneralPayload` 의 "the same trusted facts Life gets, nothing more" 와 Training 이 강아지 사실을 아예 안 보는 설계에 정면으로 부딪힙니다 |

**관계를 이름 붙이는 것이 이 안의 값입니다.** 수용 케이스가 관계에 곧바로 떨어집니다 —
`"아니, 산책 말고 밥"` 은 `CORRECTION`, `"그니까 그걸 네가 물어봐야지"` 는 `META`,
실패 뒤 같은 요구는 `REPEAT`. 판정이 값으로 남으면 랩에서 그 값만 보고 어디서 틀렸는지
셀 수 있습니다.

### 배치에 근거가 있습니다

`docs/place/conversation-context-ablation-2026-09-10.md` 는 같은 문제를 Place 쪽에서 실측한
것입니다 (실제 Gemini 호출 102회). 문맥을 넣자 6쌍이 좋아지고 **3쌍이 퇴행**했는데, 퇴행의
모양은 모델이 최신 요청을 놓치고 직전 것을 다시 실행하는 것이었습니다. 같은 정보를 두고
**query 키만 맨 뒤로 옮기니 9/9 통과**했습니다. 과거 발화 중복만 제거한 조건은 6/3 으로
퇴행이 남았습니다 — 원인은 중복이 아니라 배치였습니다.

그래서 **Turn Resolver 프롬프트도 `CURRENT_QUERY:` 를 맨 뒤에** 둡니다 — 후보 이력 블록이
앞, 지금 판정할 발화가 뒤. 집안 관례이기도 합니다: `build_semantic_router_prompt` 와
`build_general_prompt` 가 이미 `USER_QUERY:` 로 끝납니다.

**이 카드에서는 그 퇴행이 덜 위험합니다.** Place 의 퇴행은 문맥에 끌려 *실행*을 잘못한
것이었는데, Turn Resolver 는 실행을 안 합니다 — 틀리면 관계 라벨 하나가 틀리고, 그건
`relation` 값으로 랩에 남습니다. 실행 경계가 뒤에 하나 더 있는 것이 이 배치의 안전망입니다.

비용도 그 실험에서 옵니다: 입력 토큰 중앙값 2,208 → 2,949 (+34%), 모델 호출 지연 중앙값
2,476ms → 3,075ms (+24%). 우리 쪽 실제 값은 구현 뒤 재서 이 문서에 적습니다.

## 3. Turn Resolver 계약

### 책임 넷

1. 현재 발화를 `NEW` · `FOLLOW_UP` · `CORRECTION` · `REPEAT` · `META` 중 하나로 가른다
2. 관련된 이전 사용자 요청 또는 대기 중인 되묻기를 지정한다
3. 라우터와 선택된 capability 가 쓸 **제한된 구조화 컨텍스트**를 만든다
4. 연결이 불확실하면 **추측하지 않고** `CLARIFY` 로 전환한다

### 하지 않는 것

최종 사용자 답변 작성 · 여러 capability 결과의 문장 합성(Response Composer) · 반려견 상태나
관찰 사실의 임의 생성·저장 · 전체 대화 원문을 모든 capability 에 무조건 주입 · 기존 의료·응급
판단 대체.

### 출력

```python
class TurnRelation(StrEnum):
    NEW = "NEW"
    FOLLOW_UP = "FOLLOW_UP"
    CORRECTION = "CORRECTION"
    REPEAT = "REPEAT"
    META = "META"


class ResolvedTurn(ContractModel):
    relation: TurnRelation
    current_query: str                              # 원문. 절대 대체하지 않는다
    referenced_turn_id: UUID | None = None          # 둘 중 하나만 채운다
    pending_clarification_id: UUID | None = None
    referenced_original_request: str | None = None  # 원문
    pending_missing_axes: list[ObservationAxis] = []   # #415 에서 그대로 받는다
    standalone_query: str | None = None             # 모델이 만든 추론용 표현
    resolution_confidence: float                    # 0.0~1.0
    ambiguity: str | None = None
    context_used: list[UUID] = []                   # 실제로 본 turn id
```

**`standalone_query` 는 사실이 아닙니다.** 모델이 추론용으로 만든 표현일 뿐이라 사용자나
반려견의 확정 사실로 저장하지 않습니다. `current_query` 원문과 출처 turn id 를 **항상 같이**
보존해서, 나중에 어느 문장이 사람의 말이고 어느 문장이 모델의 재구성인지 한 눈에 갈리게
합니다.

### `NEW` fast path — 모델을 안 태우는 길

맥락 의존 신호가 없고 대기 중인 되묻기도 없으면 **모델 호출 없이** `relation=NEW` 로 끝냅니다.
신호는 규칙으로 봅니다 — 지시어(`그거` · `저거` · `아까` · `걔`), 정정 표지(`아니` · `말고` ·
`가 아니라`), 반복 표지(`그러니까` · `다시` · `했잖아`), 메타 표지(`물어봐야` · `왜 안`),
그리고 **미해결 `CLARIFY` 의 존재**.

규칙 기반 선별이 집안 관례입니다 — `resolve_emergency_route` 와 `resolve_deterministic_route`
가 이미 모델 앞에서 같은 일을 합니다. 이 길이 ⑤ 의 「이력 없을 때 오늘과 동일」을 비용
0 으로 만듭니다.

### 후보의 경계

최근 완료 turn **3쌍**까지. 미해결 되묻기는 그 3쌍과 **별도로** 유지합니다 — 3쌍 밖으로
밀려나도 대기 중인 질문은 살아 있어야 합니다. 다른 반려견·다른 세션의 맥락은 후보에 넣지
않습니다 (`run_persisted_turn` 이 이미 세션의 pet 을 권위로 삼으므로 구조적으로 갈립니다).

## 4. 여덟 가지 결정

### ① 개수·종류 — 완료 turn 3쌍, user·assistant 원문 둘 다

`processing_status = 'completed'` 인 최근 3개를 오래된 것부터 실습니다. **쓰이는 자리는
Turn Resolver 의 후보군입니다** — 프롬프트에 얹는 블록이 아닙니다 (개정 §0).

- **assistant 턴을 빼면 안 되는 이유**는 케이스가 증명합니다. `cq_pronoun_akka_01` 의
  `"아까 말한 거"` 는 **assistant** 턴 1 을 가리키고, `cq_repeat_after_failure_01` 은
  "직전에 거절당했다" 를 알아야 같은 고정 문구를 두 번 안 뱉습니다.
- **3인 이유**도 케이스에서 나옵니다. 가장 긴 `cq_observed_wellness_repair_01` 의 대상 턴 7 이
  턴 1 까지 닿아야 하는데 그것이 정확히 3쌍입니다. 이 세트는 4 를 요구하지 않습니다.
- **요약하지 않고 원문.** 요약은 LLM 호출과 실패 모드를 하나 더 만드는데, 이 카드의 목표는
  「가장 작은 기제」입니다.
- `failed` · `processing` turn 은 뺍니다. `assistant_content` 가 NULL 이라 실을 것이 없고,
  사용자가 본 대화도 아닙니다.

### ② 누가 보는가 — **이력은 Turn Resolver 만** (교체됨)

```
이력 원문 ──▶ Turn Resolver ──▶ ResolvedTurn ──▶ 라우터
                                      └────────▶ 선택된 capability (context_used 만)
```

- **라우터는 raw history 를 안 받습니다.** `ResolvedTurn` 의 제한된 결과만 받습니다.
- **선택된 capability 에도 필요한 `context_used` 만** 갑니다. 전부 주입하지 않습니다.
- **`relation=NEW` 면 라우터와 capability 는 오늘과 완전히 같게 동작합니다** — 넘길 것이
  없으니 넘기지 않습니다.

초판이 이 자리에서 "라우터·General 프롬프트에 원문 블록" 이었습니다. 바뀐 이유는 §2 의 첫 줄
— 관계 판정이 프롬프트 안에 묻히면 실패가 관측되지 않기 때문입니다.

### ③ 길이 한도

| 자리 | 한도 | 왜 |
| --- | --- | --- |
| user 원문 | 그대로 | `chat_turns_user_content_length_check` 가 이미 ≤2,000자 |
| assistant 원문 | 앞 **400자**, 넘으면 `…` | DB 상한이 8,000자라 안 자르면 그것이 통째로 프롬프트에 옵니다 |
| 블록 전체 | **3,000자**. 넘으면 **오래된 쌍부터** 버리고 최신 쌍은 무조건 남깁니다 | 3쌍이라도 긴 답변 셋이면 한도를 넘습니다 |

**최신 쌍이 한도에 굶지 않습니다.** 한 쌍의 최대치가 user 2,000자 + assistant 400자 =
2,400자라 3,000 아래입니다. 그래서 "최신 쌍은 무조건 남긴다" 는 늘 만족 가능한 규칙이고,
빈 블록으로 떨어지는 경우가 없습니다. 이 부등식이 깨지면(예: assistant 한도를 1,100 이상으로
올리면) 규칙이 모순되므로, 세 숫자는 같이 움직여야 합니다.

### 실어 나르는 모양 — Turn Resolver 의 **입력**

이 블록은 Turn Resolver 프롬프트에만 들어갑니다. 라우터도 capability 도 이것을 못 봅니다 (②).

```python
class PriorTurn(ContractModel):
    turn_id: UUID      # referenced_turn_id 로 되돌릴 근거
    user: str          # 원문 그대로
    assistant: str     # 400자에서 자름
```

블록은 오래된 것부터, 한 줄에 한 발화:

```
CANDIDATE_TURNS:
U1: 심장사상충 예방약은 한 달에 한 번 먹이면 되는거야?
A1: 네, 심장사상충 예방약은 보통 한 달에 한 번 투여합니다. …
U2: 그거 얼마나 오래 해야 해?
A2: 무엇을 얼마나 지속해야 하는지 조금 더 구체적으로 말씀해 주시면 답변드리겠습니다.
```

**번호가 `referenced_turn_id` 의 근거입니다** — 모델이 "U1 을 가리킨다" 고 말할 수 있어야
서버가 그것을 실제 turn id 로 되돌릴 수 있습니다. 번호 없이 문장만 주면 모델이 지목한 것을
행으로 못 잇고, 그러면 §3 이 요구하는 "원문과 출처 turn id 를 항상 같이" 가 성립하지
않습니다. JSON 이 아니라 줄 단위인 것은 따옴표·이스케이프로 토큰이 눈에 띄게 늘기
때문입니다 — 이 블록은 구조가 아니라 읽을 것입니다.

### ④ 프라이버시·로깅

**이력은 `structured_context` 에 섞지 않고 `service.run(..., prior_turns=…)` 별도 인자로
받습니다.** 이유 둘:

- `routing_metadata` 의 `_ROUTING_METADATA_KEYS` 는 문자열 allowlist 이고 D-051 의 좌표
  차단을 담당합니다. 이력은 리스트라 그 길로 못 지나가며, 별도 인자로 받는 쪽이 그 가드를
  **온전히 안 건드립니다.**
- `_structured_context(body)` 는 **클라이언트가 보낸 값**이고 이력은 **서버가 DB 에서 읽은
  값**입니다. 출처가 다른 둘을 한 dict 에 섞으면 나중에 신뢰 경계를 못 가립니다.

지키는 선:

- **트레이스에는 실립니다.** D-054 의 명시적 옵트인 예외 범위 안입니다 — `core/tracing.py`
  는 이미 질문 원문 · 프롬프트 · 답변을 내보내고(기본 꺼짐, 목적지는 우리 GCP),
  `scrub_payload` 는 리스트를 재귀하므로 신원 · 좌표는 자동으로 걸립니다.
- **`LOGGER` 에는 절대 안 실립니다.** D-037 · D-048 이 계속 금지하는 자리입니다.
  `semantic.py` 의 라우터 실패 로그는 지금처럼 `request_id` 만.
- **모델이 만든 표현을 사실로 저장하지 않습니다.** `standalone_query` · `resolved_intent`
  는 추론용이라 반려견 기록이나 사용자 프로필로 새면 안 됩니다. `current_query` 원문과
  출처 turn id 를 항상 같이 들고 다닙니다 (§3).
- **랩 파일에도 본문을 안 적습니다.** `SessionDriver` 는 `prior_turns_supplied` 에 **turn
  인덱스만** 적습니다 — 본문은 이미 `cases_v1.jsonl` 에 있고, `drivers._sanitize_route_plan`
  이 `payload` 를 버리는 것과 같은 원칙입니다.

### ⑤ 이력이 없을 때 — 블록을 아예 안 붙입니다 (fast path 로 강화)

무상태 호출(`persists=False`) · 첫 턴 · 완료 turn 0개 → 프롬프트가 **오늘과 바이트 동일**
합니다. 테스트가 그것을 못박습니다:

```
build_semantic_router_prompt(query=q, context=c)
  == build_semantic_router_prompt(query=q, context=c, prior_turns=())
```

General 도 같습니다. 이 등식은 D-057 ③ / #344 가 `build_general_prompt` 에 걸어 둔
"기존 조합은 같은 리터럴로 재현된다" 규칙의 연장입니다.

**Turn Resolver 에서 이것이 fast path 로 승격됩니다** (§3) — 맥락 의존 신호가 없으면
모델을 아예 안 태우므로, 무이력 경로는 프롬프트가 같은 정도가 아니라 **호출 수까지**
오늘과 같습니다. 초판에서 이 결정의 비용은 0 이었고 지금도 0 입니다.

### ⑥ `CLARIFY` 응답을 원 요청에 잇는 법 — 이 카드의 핵심 책임 (교체됨)

초판은 "#415 대기로 공란" 이었습니다. **#415 가 계약을 확정해 더는 공란이 아닙니다.**
확인한 것 (`origin/feat/assistant-ask-mode`):

- #415 는 §7 카드 A 의 **선택지 ①(`CLARIFY` 재사용)** 로 갔습니다. `AssistantStatus` 는
  안 늘어났습니다 — 새 상태를 처리할 클라이언트 변경이 없습니다.
- `ClarifyRequest` 에 `missing_axes: list[ObservationAxis]` 가 붙었습니다 (`f0625e49`,
  기본 빈 목록, 최대 2).
- `ObservationAxis` 는 닫힌 목록(`APPETITE` · `ENERGY` · `STOOL` · `VOMIT` · 호흡 ·
  `MOBILITY` · `OTHER`)이고 D-068 입니다.

잇는 법:

1. #415 의 `clarify.question` 과 `missing_axes` 를 **그대로 입력으로** 씁니다.
   한국어 문장을 다시 파싱해 축을 복원하지 않습니다 — 그러면 #415 가 스키마를 만든 이유가
   사라집니다.
2. 다음 턴이 그 되묻기에 답한 것으로 **연결됐을 때만** 새로운 사용자 관찰로 취급합니다.
3. **`missing_axes` 를 관찰 결과로 읽지 않습니다.** `APPETITE` 는 "식욕을 물었다" 이지
   "식욕에 문제가 있다" 가 아닙니다 — `ObservationAxis` docstring 이 #416 을 지목해 그렇게
   적어 두었습니다.
4. `missing_axes` 가 **비어 있으면 "축을 모른다"** 로 읽습니다. "물은 것이 없다" 가
   아닙니다 — 같은 docstring 의 경고입니다. 모델이 축을 안 고르면 비어 나옵니다.

저장 쪽은 그대로 공짜입니다: `CLARIFY` 는 `chat_turns_assistant_status_check` 의 허용
상태이고 `run_persisted_turn` 은 `FAILED` 만 실패로 닫으므로, 되묻기 turn 은 `completed`
로 저장되고 다음 턴의 후보에 자연히 들어옵니다.

### ⑦ 오래된 턴이 라우팅을 오염시키지 않게 — 넷

1. **후보 블록이 `CURRENT_QUERY:` 앞.** Place 실측이 정확히 이 배치입니다 (§2).
2. **창이 3쌍.** 그 밖은 후보에 없습니다.
3. **`relation=NEW` 가 오염을 끊는 자리입니다.** 초판은 "새 주제면 무시하라"를 라우터
   프롬프트의 규칙 한 줄로 부탁했는데, 이제는 **판정이 값으로 나오고** `NEW` 면 아무것도
   안 넘어갑니다. 부탁이 계약이 됐습니다.
4. **응급 경계는 Turn Resolver 가 못 덮습니다.** 순서가 `resolve_emergency_route` →
   `resolve_deterministic_route` → **Turn Resolver** → 시맨틱 라우터입니다. 응급은
   **현재 사용자 원문을 직접** 검사하고, Resolver 보다 앞이라 `standalone_query` 로 약해질
   자리가 없습니다. 과거 맥락과 무관하게 현재 발화에 응급 신호가 있으면 기존 경계가
   이깁니다 (수용 케이스 8).
5. **`general` 은 결정론 경로로 못 옵니다** — `_EXECUTE_NAMES` 밖이라
   `requested_capability` 로 부를 수 없고 (`planner.py:72`), `resolve_emergency_route` 는
   `_payload_for` 기계를 통째로 우회합니다 (`planner.py:38`). `GeneralPayload` 를 만드는
   자리는 `assemble_route_plan` → `_payload_for` 하나뿐이라, `context_used` 를 넣을 자리도
   하나뿐입니다.

### ⑧ 수용 케이스 — 9건 (확장됨)

**이 아홉을 구현보다 먼저 고정합니다** (구현 순서 3단계). 관계 라벨이 값으로 나오므로
판정기 없이 코드로 채점할 수 있는 것이 대부분입니다.

| # | 케이스 | 기대 `relation` | 랩 대응 |
| --- | --- | --- | --- |
| 1 | 이력 없는 독립 질문 | `NEW` (fast path, 모델 호출 0) | — |
| 2 | `"그거 얼마나 자주 해?"` 가 앞 요청에 연결 | `FOLLOW_UP` | `cq_pronoun_geugeo_01` |
| 3 | `"아니, 산책 말고 밥"` | `CORRECTION` | `cq_correction_explicit_01` |
| 4 | 실패한 답변 뒤 같은 요구 반복 | `REPEAT` | `cq_repeat_after_failure_01` |
| 5 | #415 가 식욕·활력을 물은 뒤 `"밥은 먹는데 계속 누워 있어"` | `FOLLOW_UP` + `pending_clarification_id` | 신규 |
| 6 | `"그니까 그걸 네가 물어봐야지"` | `META` | `cq_observed_wellness_repair_01` 턴 7 |
| 7 | 오래된 무관한 이력 | `NEW` | 신규 |
| 8 | 현재 발화에 응급 신호 | Resolver 도달 전에 응급이 이김 | 신규 |
| 9 | 확정 못 하는 `"그거"` | `CLARIFY` (임의 연결 금지) | 신규 |

각 케이스가 못박는 것:

- **1** — 기존 단발 라우팅과 **동일**. ⑤ 의 바이트 동일 등식이 이것의 코드 형태입니다.
- **4** — `REPEAT` 판정만으로는 부족합니다. **같은 거절 문구를 다시 재생하지 않는 것**까지
  봅니다. `transcript.check_transcript` 의 `max_repeat_count` 가 이미 세고 있습니다.
- **5** — `missing_axes` 를 관찰로 오해하지 않는지가 여기서 걸립니다 (⑥-3).
- **6** — `off_topic` 거절로 떨어지지 않는 것이 통과 조건입니다.
- **8** — 응급이 Resolver 결과에 **약해지지 않는** 것을 봅니다 (⑦-4).
- **9** — 틀리게 잇느니 되묻습니다. `resolution_confidence` 가 문턱 아래면 `CLARIFY`.

`cq_pronoun_akka_01` 은 랩에 남지만 아홉의 대표는 아닙니다 — 2번과 같은 기제입니다.

넷(`cq_correction_explicit_01` · `cq_repeat_after_failure_01` ·
`cq_observed_wellness_repair_01`, 그리고 5번)은 `expected_mode: "ASK"` 라
`response_mode_fit` 은 #415 것입니다. **이 카드는 `context_continuity` · `repair_success`
두 축과 `relation` 정확도로 채점받습니다.**

## 5. 이음매 — 코드 네 곳

```
routers/assistant.py     orchestrate 콜백 시그니처 한 줄
        │
        ▼
services/chat.py         run_persisted_turn 이 예약 TX 안에서 후보를 읽어 넘김
        │                repositories/chat.py list_recent_completed_turns(limit=3)
        │                                     get_unresolved_clarification(session_id)
        ▼
orchestration/
  resolver.py  (신규)    TurnRelation · ResolvedTurn · fast path 규칙
                         build_turn_resolver_prompt · GeminiTurnResolver
  service.py             _plan_and_execute 에 한 단계: 응급 → 결정론 → Resolver → 라우터
  contracts.py           ResolvedTurn 재수출. AssistantStatus·CapabilityName 은 안 건드림
  semantic.py            select(..., resolved=) — raw history 아님
  planner.py             _payload_for 가 context_used 를 payload 에 실음
```

**새 파일 하나로 나가는 것이 요점입니다.** `orchestration/resolver.py` 는 `emergency.py` ·
`semantic.py` 와 같은 층의 형제입니다 — 관계 판정이 한 파일에 모여 있어야 랩에서 그 파일만
갈아 끼우고 비교할 수 있습니다.

**공유 열거형을 안 넓힙니다.** `TurnRelation` 은 resolver 의 것이고 `AssistantStatus` ·
`CapabilityName` 은 그대로입니다. #415 가 선택지 ①(`CLARIFY` 재사용)로 가서 상태를 안
늘렸으므로, 이 카드도 안 늘립니다 — 열거형을 넓히면 그것을 열거하는 파일이 전부 범위에
들어오고, 그 파일들이 정확히 #415 와 겹치는 자리입니다.

**`graph.py` 는 안 건드립니다.** `context_used` 가 payload 안에 실려 가므로 엔진은 지금처럼
payload 를 그대로 넘기기만 합니다. 부수 효과로 `_sanitize_route_plan` 이 payload 를 버리는
규칙이 랩 파일 보호를 그대로 해 줍니다 (④).

**읽는 자리가 예약 TX 안인 이유.** D-048 이 「외부 호출 동안 열린 요청 DB 세션과 행 잠금은
0개」를 못박았고, `run_persisted_turn` 은 이미 그 모양입니다 — 예약 TX 가 세션 잠금을 잡았다가
`orchestrate` 전에 닫습니다. 그 안에서 같이 읽으면 TX 를 새로 안 엽니다. 방금 예약한 자기
turn 은 `processing` 이라 자동으로 빠지고, 정확한 재생은 `orchestrate` 를 아예 안 부르니
무관합니다.

## 6. 프롬프트 버전과 기준선

프롬프트 본문이 바뀌는 곳과 **새로 생기는 곳**:

`TURN_RESOLVER_PROMPT_VERSION = "turn-resolver-ko-v1"` 이 새로 생깁니다. 아래 다섯은
`ResolvedTurn` 이 실릴 자리가 열려 바뀝니다 — **원문 이력이 아니라 구조화 결과**입니다:

| 상수 | 지금 | 바뀐 뒤 |
| --- | --- | --- |
| `PROMPT_VERSION` (semantic) | `semantic-router-ko-v10` | `semantic-router-ko-v11` |
| `GENERAL_PROMPT_VERSION` | `general-answer-ko-v3` | `general-answer-ko-v6` |
| `GENERAL_CARE_LOG_PROMPT_VERSION` | `general-answer-ko-v4-carelog` | `general-answer-ko-v6-carelog` |
| `GENERAL_VET_PROMPT_VERSION` | `general-answer-ko-v5-vetspend` | `general-answer-ko-v6-vetspend` |
| `GENERAL_CARE_LOG_VET_PROMPT_VERSION` | `general-answer-ko-v5-carelog-vetspend` | `general-answer-ko-v6-carelog-vetspend` |

General 넷을 **같은 `v6` 으로 모으는** 것은 지금 셋(`v3`/`v4`/`v5`)으로 갈린 이유가 조합이
하나씩 붙은 역사이기 때문입니다 — 이번 변경은 넷 전부에 같은 블록을 같은 자리에 넣는 한 번의
변경이라, 세대를 하나로 맞추는 편이 나중에 "어느 세대에 무엇이 들어 있었나" 를 읽기 쉽습니다.

**⑤ 의 등식이 이 변경의 안전장치입니다** — `relation=NEW` 면 실릴 것이 없어 문자열이
그대로라, v3 의 84-pairwise 승인 본문이 그 경로에서 표류하지 않습니다. `NEW` 가 아닐 때만
새 버전의 몸이 나옵니다. **수용 케이스 1번이 이 등식을 테스트로 고정합니다.**

라우터 벤치마크(`daengs_evals/router_benchmark`)의 기준선은 무이력으로 도므로 영향받지
않아야 합니다. 그것을 확인하는 것이 이 카드의 마지막 검증입니다.

## 7. 측정 — SessionDriver

`#401` 의 하네스에 얹습니다. `drivers.py` 에 `StatelessDriver` 옆으로 `SessionDriver` 를
더하고 **`collect.py` · `judge.py` · `report.py` 는 안 고칩니다** — 고쳐야 한다면 그것은
이음매가 샜다는 신호입니다 (#401 §5 가 약속하는 것).

같이 뒤집는 것:

- `transcript.PRIOR_TURNS_REACH_INFERENCE` 를 `True` 로. 그 상수 하나가 두 축의 기대 정답이
  0 이라는 근거였습니다.
- `#401` 리포트가 before 열에 붙이는 「기능 부재」 라벨.

`relation` 정확도는 판정기 없이 셉니다 — 아홉 케이스의 기대 라벨이 값이라 코드로 맞춰
봅니다. 판정기가 필요한 것은 두 축뿐입니다.

**커밋 위치가 2026-09-11 에 바뀌었습니다.** 초판은 「하네스가 `chore/conversation-quality-eval`
에만 있으니 `SessionDriver` 는 그 브랜치에」였습니다. 그 사이 **#401 이 #415 브랜치로
머지됐습니다** (`e3600b97`). 그래서 #415 가 dev 로 들어가면 하네스도 같이 dev 에 들어오고,
`SessionDriver` 는 **이 PR 안에서** 짜면 됩니다. 브랜치를 갈라 둘 이유가 사라졌습니다.

순서는 그래서 **#401 → #415 → #416** 입니다 (§9).

**전후 비교에서 여섯을 고정합니다** — 케이스 파일 해시 · judge 모델 핀 · judge 프롬프트 버전 ·
앵커 세트 · 어댑터 모드 · 미측정 비율 정의. 하나라도 움직이면 리포트가 비교를 거부합니다.

**두 축의 before 는 0 이고 그것은 "모델이 나빴다" 가 아니라 "기능이 없었다" 입니다.**
after 와 맞댈 때 그 차이를 품질 개선으로 읽히게 두지 마세요.

## 8. 이 카드가 하지 않는 것

- **Response Composer** — 여러 capability 결과를 한 답변으로 다시 쓰는 자리. #416 을 측정한
  **뒤에** 별도 PR 로 판단합니다 (방향 메모)
- 최종 사용자 답변 작성 · 문장 합성
- 범용 대화 요약 · 장기 기억 · 다중 세션 교차 참조
- 반려견 상태나 관찰 사실의 임의 생성·저장
- `AssistantStatus` · `CapabilityName` 확장
- 무상태 경로(`persists=False`)의 동작 변경
- 기존 의료·응급 판단 대체

## 9. 순서와 차단

**#401 → #415 → #416.** 앞의 둘이 이 카드의 입력을 소유합니다 — #401 은 하네스와 케이스,
#415 는 `ClarifyRequest.missing_axes` 와 `ObservationAxis` 입니다.

**#415 가 `dev` 에 머지되기 전에는 공유 파일 구현을 시작하지 않습니다.** 겹치는 파일은
`contracts.py` · `planner.py` · `semantic.py` · `service.py` 이고, 지금 손대면 두 PR 이
같은 줄에서 충돌합니다. 머지 전까지는 **스펙과 테스트 설계까지만** 갑니다.

머지 뒤 절차: 최신 `dev` 를 반영 → §4 ⑥ 의 네 항목을 #415 최종 스키마와 다시 대조 →
수용 케이스 아홉을 먼저 고정 → 구현.
