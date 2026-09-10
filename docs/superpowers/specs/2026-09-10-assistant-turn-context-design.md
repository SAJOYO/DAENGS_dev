# 제한된 멀티턴 이어짐과 복구 — 설계

PR #416. 설계 골격은 `docs/orchestration/conversation-quality.md` §7 카드 B 이고, 그 문서는
아직 `dev` 에 없습니다 (#401, `chore/conversation-quality-eval`).

## 1. 무엇을 고치나

대화를 저장하지만 추론은 그것을 못 봅니다. `routers/assistant.py` 의 `_dispatch` 가
`service.run(query=body.query, …)` 로 **현재 질의 하나만** 넘기고, 세션에서 오는 것은
`active_dog_id` 뿐입니다. `services/chat.py:run_persisted_turn` 은 턴을 저장만 하며,
이력을 읽는 유일한 자리는 같은 `client_message_id` 의 정확한 재생입니다.
**저장은 대화 메모리가 아닙니다.**

이 카드는 **가장 작은 이력 기제**를 붙입니다. 범용 대화 요약 · 장기 기억 · 다중 세션 교차
참조는 범위 밖입니다. 첫 턴을 되묻게 만드는 것은 `feat/assistant-ask-mode` (#415) 가
따로 맡습니다.

## 2. 고른 안 — 라우터와 General 에만 이력 블록

완료된 대화 turn 몇 쌍을 `CONVERSATION_HISTORY:` 블록으로 만들어 **시맨틱 라우터 프롬프트와
General 프롬프트에만** 넣습니다. Training · Life · Walk · Place · VetContact 의 payload 는
안 건드립니다.

검토한 대안 둘:

| 안 | 왜 안 골랐나 |
| --- | --- |
| 질의 재작성 경계 하나 (라우팅 전에 `"그거"` 를 풀어서 다시 씀) | 수용 케이스 5건 중 2건만 풉니다. `cq_repeat_after_failure_01` 과 `cq_observed_wellness_repair_01` 턴 7 은 지시어 해소가 아니라 **직전 실패에 대한 항의**라 재작성으로 표현할 수 없습니다. 매 턴 LLM 호출이 하나 늘고 실패 모드도 하나 늡니다 |
| 전 능력에 이력 | `GeneralPayload` 의 "the same trusted facts Life gets, nothing more" 와 Training 이 강아지 사실을 아예 안 보는 설계에 정면으로 부딪힙니다. 범위도 폭발합니다 |

### 배치에 근거가 있습니다

`docs/place/conversation-context-ablation-2026-09-10.md` 는 같은 문제를 Place 쪽에서 실측한
것입니다 (실제 Gemini 호출 102회). 문맥을 넣자 6쌍이 좋아지고 **3쌍이 퇴행**했는데, 퇴행의
모양은 모델이 최신 요청을 놓치고 직전 것을 다시 실행하는 것이었습니다. 같은 정보를 두고
**query 키만 맨 뒤로 옮기니 9/9 통과**했습니다. 과거 발화 중복만 제거한 조건은 6/3 으로
퇴행이 남았습니다 — 원인은 중복이 아니라 배치였습니다.

우리 두 프롬프트는 이미 `USER_QUERY:` 가 마지막입니다 (`semantic.py` `build_semantic_router_prompt`,
`adapters/general.py` `build_general_prompt`). 이력 블록은 그 **앞**에 들어갑니다.

비용도 그 실험에서 옵니다: 입력 토큰 중앙값 2,208 → 2,949 (+34%), 모델 호출 지연 중앙값
2,476ms → 3,075ms (+24%). 우리 쪽 실제 값은 구현 뒤 재서 이 문서에 적습니다.

## 3. 여덟 가지 결정

### ① 개수·종류 — 완료 turn 3쌍, user·assistant 원문 둘 다

`processing_status = 'completed'` 인 최근 3개를 오래된 것부터 실습니다.

- **assistant 턴을 빼면 안 되는 이유**는 케이스가 증명합니다. `cq_pronoun_akka_01` 의
  `"아까 말한 거"` 는 **assistant** 턴 1 을 가리키고, `cq_repeat_after_failure_01` 은
  "직전에 거절당했다" 를 알아야 같은 고정 문구를 두 번 안 뱉습니다.
- **3인 이유**도 케이스에서 나옵니다. 가장 긴 `cq_observed_wellness_repair_01` 의 대상 턴 7 이
  턴 1 까지 닿아야 하는데 그것이 정확히 3쌍입니다. 이 세트는 4 를 요구하지 않습니다.
- **요약하지 않고 원문.** 요약은 LLM 호출과 실패 모드를 하나 더 만드는데, 이 카드의 목표는
  「가장 작은 기제」입니다.
- `failed` · `processing` turn 은 뺍니다. `assistant_content` 가 NULL 이라 실을 것이 없고,
  사용자가 본 대화도 아닙니다.

### ② 누가 보는가 — 시맨틱 라우터와 General

특수 능력은 안 봅니다. 근거는 §2 의 표와 수용 케이스 대응표(§4)입니다 — 다섯 건 전부
라우팅 판단이나 자유 응답에서 갈리지, 특수 능력의 payload 에서 갈리지 않습니다.

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

### 실어 나르는 모양

```python
class PriorTurn(ContractModel):
    user: str          # 원문 그대로
    assistant: str     # 400자에서 자름
```

블록은 오래된 것부터, 한 줄에 한 발화:

```
CONVERSATION_HISTORY:
U1: 심장사상충 예방약은 한 달에 한 번 먹이면 되는거야?
A1: 네, 심장사상충 예방약은 보통 한 달에 한 번 투여합니다. …
U2: 그거 얼마나 오래 해야 해?
A2: 무엇을 얼마나 지속해야 하는지 조금 더 구체적으로 말씀해 주시면 답변드리겠습니다.
```

번호를 붙이는 것은 ⑦-3 의 "현재 질의가 새 주제면 무시한다" 규칙이 무엇을 가리키는지
모델에게 지시할 수 있게 하기 위해서입니다. JSON 이 아니라 줄 단위인 것은 같은 정보를
JSON 으로 실으면 따옴표·이스케이프로 토큰이 눈에 띄게 늘기 때문입니다 — 이 블록은
구조가 아니라 읽을 것입니다.

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
- **랩 파일에도 본문을 안 적습니다.** `SessionDriver` 는 `prior_turns_supplied` 에 **turn
  인덱스만** 적습니다 — 본문은 이미 `cases_v1.jsonl` 에 있고, `drivers._sanitize_route_plan`
  이 `payload` 를 버리는 것과 같은 원칙입니다.

### ⑤ 이력이 없을 때 — 블록을 아예 안 붙입니다

무상태 호출(`persists=False`) · 첫 턴 · 완료 turn 0개 → 프롬프트가 **오늘과 바이트 동일**
합니다. 테스트가 그것을 못박습니다:

```
build_semantic_router_prompt(query=q, context=c)
  == build_semantic_router_prompt(query=q, context=c, prior_turns=())
```

General 도 같습니다. 이 등식은 D-057 ③ / #344 가 `build_general_prompt` 에 걸어 둔
"기존 조합은 같은 리터럴로 재현된다" 규칙의 연장입니다.

### ⑥ `CLARIFY` 응답을 원 요청에 잇는 법 — #415 대기, 절반은 공짜

명시적 대기 상태(Place 의 `pending_proposal` 같은 것)는 **안 만듭니다.** #415 의 계약이
나기 전에 만들면 되돌립니다.

다만 이 안이 절반을 자동으로 해결합니다: `CLARIFY` 는 `chat_turns_assistant_status_check`
의 허용 상태이고 `run_persisted_turn` 은 `FAILED` 만 실패로 닫습니다. 그래서 되묻기 turn 은
`completed` 로 저장되고, **되묻기 문장이 다음 턴 이력에 assistant 원문으로 그냥 들어갑니다.**

### ⑦ 오래된 턴이 라우팅을 오염시키지 않게 — 넷

1. **블록이 `USER_QUERY:` 앞.** Place 실측이 정확히 이 배치입니다 (§2).
2. **창이 3쌍.**
3. **라우터 정책에 한 줄:** 이력은 지시어 해소와 직전 실패 인지에만 쓰고, 현재 질의가 새
   주제면 무시한다.
4. **응급·결정론 경로는 구조적으로 이력을 안 봅니다.** 규칙으로 막는 것이 아니라 자리가
   없습니다 — `general` 은 `_EXECUTE_NAMES` 밖이라 `requested_capability` 로 못 부르고
   (`planner.py:72`), `resolve_emergency_route` 는 `_payload_for` 기계 자체를 우회합니다
   (`planner.py:38` 머리말). `GeneralPayload` 를 만드는 자리는 `assemble_route_plan` →
   `_payload_for` 하나뿐입니다.

### ⑧ 수용 케이스 — 5건

| case_id | 이력이 없어서 난 실패 | 누가 풀어 주나 |
| --- | --- | --- |
| `cq_pronoun_geugeo_01` | `"그거"` 를 못 풀어 되물음 | General — 턴 0 의 심장사상충 예방약 |
| `cq_pronoun_akka_01` | `"확인할 수 없어요"` | General — assistant 턴 1 원문 |
| `cq_correction_explicit_01` | 정정 프레임 유실 | 라우터 + General — 사료 → 구토 |
| `cq_repeat_after_failure_01` | 같은 고정 거절 반복 | 라우터 — 턴 1 의 거절을 보고 다르게 고름 |
| `cq_observed_wellness_repair_01` | 턴 7 이 off-topic | 라우터 — 턴 1·5 를 보고 메타 항의로 인식 |

셋(`cq_correction_explicit_01` · `cq_repeat_after_failure_01` ·
`cq_observed_wellness_repair_01`)은 `expected_mode: "ASK"` 라 `response_mode_fit` 은 #415
것입니다. **이 카드는 `context_continuity` · `repair_success` 두 축으로만 채점받습니다.**

## 4. 이음매 — 코드 세 곳

```
routers/assistant.py     orchestrate 콜백 시그니처 한 줄
        │
        ▼
services/chat.py         run_persisted_turn 이 예약 TX 안에서 읽어 넘김
        │                repositories/chat.py list_recent_completed_turns(limit=3)
        ▼
orchestration/           contracts.PriorTurn
                         service.run(prior_turns=())
                         planner.assemble_route_plan → _payload_for → GeneralPayload
                         semantic.build_semantic_router_prompt
                         adapters/general.build_general_prompt
```

**`graph.py` 는 안 건드립니다.** 이력이 `GeneralPayload` 안에 실려 가므로 엔진은 지금처럼
payload 를 그대로 넘기기만 합니다. 부수 효과로 `_sanitize_route_plan` 이 payload 를 버리는
규칙이 랩 파일 보호를 그대로 해 줍니다 (④).

**읽는 자리가 예약 TX 안인 이유.** D-048 이 「외부 호출 동안 열린 요청 DB 세션과 행 잠금은
0개」를 못박았고, `run_persisted_turn` 은 이미 그 모양입니다 — 예약 TX 가 세션 잠금을 잡았다가
`orchestrate` 전에 닫습니다. 그 안에서 같이 읽으면 TX 를 새로 안 엽니다. 방금 예약한 자기
turn 은 `processing` 이라 자동으로 빠지고, 정확한 재생은 `orchestrate` 를 아예 안 부르니
무관합니다.

## 5. 프롬프트 버전과 기준선

프롬프트 두 개의 본문이 바뀌므로 버전을 올립니다:

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

**⑤ 의 등식이 이 변경의 안전장치입니다** — 이력이 없으면 문자열이 그대로라, v3 의 84-pairwise
승인 본문이 무이력 경로에서 표류하지 않습니다. 이력이 있을 때만 새 버전입니다.

라우터 벤치마크(`daengs_evals/router_benchmark`)의 기준선은 무이력으로 도므로 영향받지
않아야 합니다. 그것을 확인하는 것이 이 카드의 마지막 검증입니다.

## 6. 측정 — SessionDriver

`#401` 의 하네스에 얹습니다. `drivers.py` 에 `StatelessDriver` 옆으로 `SessionDriver` 를
더하고 **`collect.py` · `judge.py` · `report.py` 는 안 고칩니다** — 고쳐야 한다면 그것은
이음매가 샜다는 신호입니다 (§5 가 약속하는 것).

같이 뒤집는 것:

- `transcript.PRIOR_TURNS_REACH_INFERENCE` 를 `True` 로. 그 상수 하나가 두 축의 기대 정답이
  0 이라는 근거였습니다.
- `#401` 리포트가 before 열에 붙이는 「기능 부재」 라벨.

**커밋 위치가 갈립니다.** 이 파일들은 `chore/conversation-quality-eval` 에만 있고 `dev` 에
없습니다. 런타임은 이 PR(#416)에, `SessionDriver` · 상수 · 라벨은
`chore/conversation-quality-eval` 워크트리에 커밋합니다. 두 PR 의 diff 가 안 섞이고,
#401 머지 뒤 after 랩을 바로 돌릴 수 있습니다.

**전후 비교에서 여섯을 고정합니다** — 케이스 파일 해시 · judge 모델 핀 · judge 프롬프트 버전 ·
앵커 세트 · 어댑터 모드 · 미측정 비율 정의. 하나라도 움직이면 리포트가 비교를 거부합니다.

**두 축의 before 는 0 이고 그것은 "모델이 나빴다" 가 아니라 "기능이 없었다" 입니다.**
after 와 맞댈 때 그 차이를 품질 개선으로 읽히게 두지 마세요.

## 7. 이 카드가 하지 않는 것

- 범용 대화 요약 · 장기 기억 · 다중 세션 교차 참조
- 명시적 되묻기 대기 상태 (#415)
- 특수 능력(Training · Life · Walk · Place · VetContact)의 payload 변경
- 무상태 경로의 동작 변경
