# 대화 품질 평가 (#401)

`#277`(`daengs_evals.answer_quality`)은 질문 하나와 답변 하나만 봅니다. 실제로 관찰된
대화의 실패는 그 모양으로는 안 보입니다 — 미명세 질문을 되묻지 않고 닫아 버리거나, 사용자가
여러 번 정정했는데 다음 턴 행동이 안 바뀌거나, 지시대명사가 걸린 발화를 앞 턴 없이 off-topic
으로 처리하는 것들은 전부 **턴이 여럿이어야만 보이는 실패**입니다. `daengs_evals.
conversation_quality`(코드는 `backend/src/daengs_evals/conversation_quality/`, 데이터는
`backend/evals/conversation_quality/`)가 그 자리를 잽니다.

**이 패키지는 런타임을 바꾸지 않습니다.** 라우팅·General 프롬프트·거절 규칙·API 계약·집계·
대화 메모리는 전부 이 패키지 밖입니다 — 이 문서 뒷부분(§4)이 그 바깥에 필요한 것을 후속
카드 둘로 나눠 설계만 적습니다.

> 표기는 [architecture.md](architecture.md)·[contracts.md](contracts.md)와 같습니다.
> CONFIRMED = 이 카드가 확정한 것 · FOLLOW-UP = 별도 카드로 후속 · OPEN = 사람 결정 대기.

## 1. 세 축 — 각 축이 보는 것이 다릅니다

한 번 불러서 세 점수를 받는 편이 쌉니다. 그런데 그렇게 하면 한 축의 입력이 다른 축의 판정을
물들입니다. 그래서 판정기를 셋으로 가르고, 축마다 **다른 재료**를 줍니다
(`judge.py`의 `PROMPTS`·`build_payload`).

| 축 | 무엇을 재나 | 보는 것 | 안 보는 것 — 이유 |
| --- | --- | --- | --- |
| `response_mode_fit` | 정보 상황에 맞는 상호작용 모드를 골랐나(관찰 항목 나열은 질문이 아니다) | 현재 발화, 답변, `user_input_needed`(라벨이 아니라 상황의 사실) | 프로필·상태 — 있으면 "근거 없는 개인화가 그럴듯하다"로 판정이 미끄러진다 |
| `context_continuity` | 앞 턴과 실제로 있던 상태를 맞게 썼나 | 이전 턴들, 공급된 상태, 답변 | `expected_mode`(우리 정답지) — 주면 정확도 채점기가 되어 잴 것이 안 남는다 |
| `repair_success` | 정정·반복·항의 다음 턴에서 행동이 바뀌었나 | 정정 전 턴 쌍, 정정 발화, 그다음 답변 | 상태 — 주면 "그래도 견종에 맞는 말은 했다"로 정정 실패를 덮는다 |

각 축은 0~2점이거나, 그 턴에서 잴 수 없으면 `None`(N/A)입니다. `rubric.applicability`가
케이스 단위가 아니라 **턴 단위**로 이를 정합니다 — 관찰 케이스(`target_turns=[1, 5, 7]`)의
턴 1은 `response_mode_fit`의 대상이지만 앞에 복구할 assistant 턴이 없어 `repair_success`의
대상은 아닙니다.

`expected_mode`는 `ANSWER`/`ASK`/`REDIRECT` 세 값을 갖는 **상호작용 모드**이지, `ANSWERED`·
`CLARIFY` 같은 API 계약 상태 이름이 아닙니다(`cases.py`). `ASK`가 계약에서 어느 상태로
나갈지는 아직 미정이므로(§4-A), 케이스가 그 결정보다 오래 살도록 일부러 갈라 적습니다.

## 2. 총점은 없습니다 — 게이트 + 벡터로 읽습니다

`AxisScores`에는 `total` 칸이 없습니다. 설계입니다 — 세 축을 하나로 더하면 안전 실패가
말투 점수에 묻힙니다. 대신 `rubric.derive_usability`가 두 층으로 읽습니다:

1. **사용성 게이트** — `usable` / `unusable(response_mode_fit)` / `unusable(repair_success)`.
   `response_mode_fit == 0` 이거나(`repair_success`가 해당되는 턴에서) `repair_success == 0`
   이면 그 턴은 못 씁니다. `safety`는 이 카드가 재지 않는 축이라 항상 0으로 부르고, 그
   0을 "안전 문제 없음"으로 읽지 않습니다 — 이 카드는 안전을 아예 안 잽니다(`report.py`
   모듈 docstring).
2. **축별 벡터** — 게이트를 통과·실패한 턴들의 세 축 평균·분포. 벡터를 다시 합쳐 한 숫자로
   만들지 않습니다.

`StateAudit`(`relevant_state_available`·`relevant_state_used`·`state_used_correctly`·
`unsupported_or_superficial_personalization`)도 같은 이유로 점수가 아니라 **사실 기록**
넷입니다. 이진 `used_state`를 품질 점수로 쓰면 "일반론 강의에 견종 이름 하나 끼워 넣은 것"이
상태를 쓴 것으로 잘못 셈해집니다.

## 3. `context_continuity`·`repair_success` — 더는 바닥이 고정돼 있지 않습니다

`transcript.py`의 한 줄이 이 패키지에서 가장 중요합니다. 지금 값은:

```python
PRIOR_TURNS_REACH_INFERENCE = True
```

`#416`(`docs/superpowers/specs/2026-09-10-assistant-turn-context-design.md`)이 pre-routing
**Turn Resolver**를 놓으면서 이 값이 뒤집혔습니다. `routers/assistant.py`가 현재 질의 하나만
넘기고 `services/chat.py:run_persisted_turn`이 저장만 하던 것은 지금도 사실이지만, 그 사이에
새 자리가 하나 생겼습니다 — `run_persisted_turn`이 예약 TX 안에서 최근 완료 turn 3쌍과 대기
중인 `CLARIFY`를 읽어 `AssistantOrchestrationService.run`에 `prior_turns`·
`pending_clarification`으로 넘기고, `orchestration/resolver.py`의 `GeminiTurnResolver`가 그것을
받아 현재 발화를 `NEW`·`FOLLOW_UP`·`CORRECTION`·`REPEAT`·`META` 중 하나로 가른 뒤 **제한된
구조화 컨텍스트**(`ResolvedTurn`)를 라우터와 선택된 capability에 넘깁니다. 이력 원문은 거기서
멈춥니다 — 라우터도 capability도 대화 원문을 직접 보지 않습니다.

그래서 **이제 이 두 축의 옳은 점수는 코드로 0으로 확정돼 있지 않습니다.** 다만 이 뒤집힘은
**어느 드라이버로 모았는가에 매여 있습니다** — `StatelessDriver`(턴마다 독립 호출, 세션도
이력도 없음)로 모은 랩은 여전히 `prior_turns`를 안 실어 보내므로 두 축의 정답이 그대로 0이고,
`report.py`의 `FLOORED_AXES`가 그 랩을 그렇게 고정해 둡니다 — before 열의 `기능 부재` 라벨은
"그 랩은 기능을 안 물었다"는 사실이지 "모델이 나빴다"가 아닙니다. `drivers.SessionDriver`로
모은 랩(`prior_turns`·`pending_clarification`을 실제로 싣는 드라이버)부터 두 축을 판정기가
실제로 재고, 그 랩의 before 열에는 `기능 부재`가 붙지 않습니다. 어느 드라이버로 모았는지가
랩 헤더에 남으므로(`SessionDriver` 로 모았다고 자칭한 랩이 실제로는 `prior_turns`를 안 실었는지)
`render_compare`가 그 값으로 못박습니다.

Turn Resolver 자체의 설계·계약·수용 케이스는 이 문서가 아니라 스펙 문서가 정본입니다:
[`docs/superpowers/specs/2026-09-10-assistant-turn-context-design.md`](../superpowers/specs/2026-09-10-assistant-turn-context-design.md).
랩 설계(before 랩으로 무엇을 쓰는지, 무엇이 미측정인지)는
[`backend/evals/conversation_quality/README.md`](../../backend/evals/conversation_quality/README.md)에 있습니다.

## 4. 숫자는 지표가 아닙니다

세 축 전부 `not_calibrated`입니다. 이 프로젝트는 사람 라벨 캘리브레이션을 하지 않기로
정했습니다(D-060 ⑦, RAG-075 — "판단하는 자 없이 판단했다"). 사람 라벨 없이는 "판정기가
맞았다"를 확인할 길이 없으므로, 이 하네스가 하는 일은 **채점이 아니라 사람이 볼 자리를
고르는 것**입니다. judge를 믿을 근거는 결국 앵커 세트(`anchors.py`, dev 12건 + holdout
9건, "의견이 아니라 확인 가능한 사실"로만 지음)뿐이고, `score`는 그 앵커를 통과해야만
돌아갑니다(`judge.require_anchor_pass`). `run_score`는 `anchors_sha256`을 기본값 없는
필수 키워드로 받습니다 — 넘길 수 있게만 해 두면 안 넘기는 길이 기본 경로가 되어 "어느
앵커로 통과했는지"를 안 남기는 실패가 조용히 자리 잡기 때문입니다(`__main__.py`가 항상
`anchors.anchors_sha256()`으로 지금 앵커 파일의 해시를 계산해 그대로 넘깁니다). 판정
프롬프트는 지금 `PROMPT_VERSION = 3`이고, 프롬프트를 고치면 이 값을 올리고 앵커를 다시
통과해야 합니다. 리포트·비교 어디에도 합계·종합 점수 칸이 없는 것은 이
지위를 코드 모양으로 못박은 것입니다.

## 5. 드라이버 이음매와 전후 비교

`drivers.py`의 `ConversationDriver` 이음매가 같은 하네스로 런타임 변경 전후를 재게
합니다. 오늘은 `StatelessDriver` 하나뿐(오늘 런타임과 같은 모양 — 턴마다 독립 호출)이고,
이력 기제가 생기면 `SessionDriver`를 더하되 **하네스는 안 고칩니다** — 그래야 두 랩에서
케이스·판정·리포트가 같은 물건으로 남습니다.

`report.render_compare`는 다음 여섯이 안 움직여야 비교를 만듭니다(하나라도 다르면
`ValueError`로 거부) — `cases_sha256`·`judge_model`·`prompt_version`·`anchor_set`·
`adapter_mode`·`general_fallback`. `general_fallback`은 실측(2026-09-10)으로 더해졌습니다
— 프로세스 기본값(꺼짐)으로 돌리면 라우터가 전문 capability를 하나도 못 고른 턴마다
General이 아예 안 조립되고 빈 계획 그대로 `FAILED`로 끝나는데, 서버 값(켜짐,
`docs/decisions.md:3252`)으로 돌리면 그 자리에 Gemini 생성 답변이 붙습니다 — 나머지
다섯 핀이 전부 같아도 이 스위치 하나로 랩의 성격이 달라집니다. 일곱째로 **미측정 비율의
정의**(판정 전 제외 + 가짜 어댑터 셀 + 해당 없는 축, 분모=턴수×3)를 두 랩에서 같은
계산으로 고정합니다 — 이것은 값이 아니라 계산 방법이라 `PINNED_FIELDS`에는 안 들어가지만,
`report.py` 모듈 하나가 그 정의를 유일하게 갖고 있어 두 번 다른 방식으로 계산될 수
없습니다.

`dead_end`는 판정 축이 아니라 **부분** 파생 진단입니다 — 답이
`daengs_backend.orchestration.redirects.SCOPED_REDIRECT_MESSAGES`의 고정 리다이렉트
문구와 글자 그대로 같고, 동시에 `response_mode_fit`이 0이 아닌 경우만 셉니다. 병원
안내처럼 형식상 다음 행동이 있어 모드는 통과했지만 실제로는 막다른 길인 자리를 겨눕니다.
모델이 매번 다른 말로 같은 벽을 세우는 경우(고정 문구가 아닌 막다른 길)는 이 신호로
못 잡습니다 — 그 부분은 여전히 사람이 대화를 읽어야 합니다.

## 6. 쓰는 법

```bash
cd backend
uv run python -m daengs_evals.conversation_quality collect --lap before --out-dir <dir> --adapter-mode real
uv run python -m daengs_evals.conversation_quality check-anchors --anchor-set dev
uv run python -m daengs_evals.conversation_quality score --lap-file <dir>/lap_before.jsonl
uv run python -m daengs_evals.conversation_quality report --lap-file <dir>/lap_before.jsonl --judgments <judgments.jsonl>
uv run python -m daengs_evals.conversation_quality compare \
  --before-lap <lap_before.jsonl> --before-judgments <judgments_before.jsonl> \
  --after-lap <lap_after.jsonl> --after-judgments <judgments_after.jsonl>
uv run python -m daengs_evals.conversation_quality case-report \
  --before-lap <lap_before.jsonl> --after-lap <lap_after.jsonl>
```

`case-report`(#415)는 **판정기를 안 부르고 판정 파일도 안 받습니다** — 랩 행에서만 뽑으므로
공짜이고 `score` 전에도 돌릴 수 있습니다. `compare`가 집계를 내는 자리라면 이쪽은 **케이스마다
두 랩의 실제 답변을 나란히** 놓고, `response mode`(계약 상태에서 파생한 라벨) · `elicited` ·
`clarify.question` · `clarify.missing_axes` · `dead_end`를 같이 찍습니다. 뒤에 **안전 회귀
sentinel** 일곱이 붙고, 신호가 하나라도 있으면 종료 코드 1입니다.

⚠ sentinel은 **종합 안전성 평가가 아닙니다.** `#415` 범위의 명시적 안전 계약에 회귀 신호가
있는지만 봅니다 — 통과를 "안전성이 검증됐다"로 쓰지 마세요. 케어 로그를 실어 보내는 케이스가
`cases_v1.jsonl`에 없어서 기록 관련 둘(④⑤)은 **미측정**으로 나옵니다. 그 둘은
`tests/test_orchestration_ask_mode.py`가 유닛으로 봅니다.

`collect`·`check-anchors`는 실제 모델을 부릅니다(`--adapter-mode real`이거나 세만틱
라우터가 Gemini를 물기 때문에 유료 호출입니다). **랩 실행 자체는 이 카드 밖이고, 하네스가
끝난 뒤 사람이 명시적으로 승인할 때 돕니다.** `report`·`compare`는 판정기를 다시 안 부르고
이미 있는 파일만 읽는다는 점은 같지만, **완전히 설정 없이 도는 것은 아닙니다** —
`summarize`가 `dead_end` 진단·코드 기반 검사를 내려고
`daengs_backend.orchestration.redirects`를 늦게 물어서, `backend/.env`가 없는
체크아웃에서는 그 두 자리가 죽는 대신 "측정 불가"로 표시될 뿐 값을 내지는 못합니다.
판정 파일을 다시 만들거나 judge를 부르지는 않습니다.

---

## 7. 후속 런타임 카드 — 설계만, 구현은 여기서 하지 않습니다

> **어디로 가는지** (사람 결정, 2026-09-10). 댕쓰가 되려는 것은
> *"반려견 생활 · 훈련 · 산책 질문에서 문맥을 기억하고, 필요한 정보를 자연스럽게 되물으며,
> 앱에 기록된 내 강아지의 상태를 활용해 답하는 대화형 비서"* 입니다.
>
> **지금은 "안전하고 정교한 기능 라우터" 에 가깝지 "대화 상대" 가 아닙니다.** 아래 두 카드가
> 그 전환의 시작이고, **핵심은 Agent 를 늘리는 것이 아니라 대화 책임자를 하나 만드는 것**
> 입니다. 능력을 더 붙이는 방향의 제안이 올라오면 이 문단을 근거로 되물으세요 — 이 대화가
> 실패한 이유는 능력이 모자라서가 아니라 대화를 책임지는 자리가 없어서였습니다.

이 카드가 잰 실패 둘은 런타임을 고쳐야 없앨 수 있습니다. **일부러 둘로 가릅니다** — 하나는
지금 라운드(단발 질의)에서 끝나고, 다른 하나는 여러 라운드에 걸친 상태를 요구해 비용·범위가
전혀 다릅니다. 아래는 결정을 대신하는 문서가 아니라 **결정에 필요한 선택지와 그 대가**를
적은 것입니다 — 문구·정책·구현은 팀의 계약 결정이 난 뒤의 일입니다.

### A. 단일 턴 응답 모드 — `ASK`

**잡으려는 실패.** `오늘 건강 상태는 어때?` 같은 미명세 질문에 오늘은 진단·거절·일반론
중 하나로 응답이 닫힙니다. 원하는 모양은 짧고 구체적인 질문 하나입니다. 예시(승인된 문구
아님, 상호작용 모양만):

```text
오늘 평소와 달라 보이는 점이 있나요? 우선 식욕·활력·배변·구토/설사·호흡 중 가장 달라진 것
하나를 알려주세요.
```

> **결정됐습니다 (2026-09-10, `#415` · D-068) — ①′.** 아래 표의 ①을 골랐지만, 거기 적힌
> "General이 `route_plan.clarify`를 채운다"는 모양으로는 **안 됩니다**: 계획은 어댑터가 돌기
> 전에 굳고, 그 계획에는 이미 `general` 요청이 들어 있어 `clarify_is_exclusive`에 걸립니다.
> 실제 구현은 **집계**에서 옮깁니다 — General이 `kind="ask"`를 내면 어댑터가 `data["ask"]`에
> `ClarifyRequest`를 담고, `aggregate`가 **general 단독일 때만** `CLARIFY`로 냅니다.
> `RoutePlan.clarify`는 끝까지 `None`이라 배타성 불변식이 그대로 서고, 그 응답의 `results`는
> 비워 나갑니다. 아래 표의 ①에 적힌 대가("다른 능력 결과를 같이 못 준다")는 이 카드에서는
> 물지 않습니다 — 폴백은 규칙상 라우터가 아무것도 안 골랐을 때만 조립되므로(`planner.py`),
> 같이 낼 결과가 애초에 없습니다.

**결정할 것 — `ASK`가 어디 사는가.** `contracts.py`의 `RoutePlan.clarify`는 이미 있는
계약이고 정확히 "도구가 직접 되묻지 않고 오케스트레이터가 후속 질문을 담당한다"는 원칙을
구현합니다(`contracts.md` §서문). 그런데 오늘 `CLARIFY`는 **배타적**입니다 —
`aggregate.py:83`이 `route_plan.clarify is not None`이면 `results=[]`로 즉시 돌아가고,
`RoutePlan.clarify_is_exclusive`(`contracts.py:302`)가 `clarify`와 `requests`/`handoffs`의
동시 존재를 아예 막습니다 — `graph.py:143`(`_validate_route_plan`)은 같은 규칙을 그래프
쪽에서 한 번 더 거는 별도의 중복 검사이지, 같은 검증기가 아닙니다. 지금 이 값을 채우는
유일한 자리는
`planner.py`의 규칙 기반 좌표 누락 검사(`_clarify_question`)이고, General 어댑터나
세만틱 라우터는 채우지 않습니다.

선택지와 대가:

| 옵션 | `ASK`가 사는 곳 | 대가 |
| --- | --- | --- |
| ① `CLARIFY` 재사용 | General이 `route_plan.clarify`를 채우거나, 별도 노드가 채움 | 계약을 안 늘린다. 그런데 배타성 때문에 "질문 하나 던지면서 다른 능력 결과는 같이 못 준다" — 오늘의 좌표 누락 케이스와 성격이 다른데 같은 상태를 씀 |
| ② `ANSWERED` 안에 질문 얹기 | `AssistantResponse.message`에 질문 문장을 담아 `ANSWERED`로 내보냄 | 계약이 오해를 부른다 — `ANSWERED`는 "답했다"는 뜻인데 실제로는 안 답하고 되물은 것. 클라이언트가 `status`만 보고 분기하면 놓친다 |
| ③ 새 상태 추가 | `AssistantStatus`에 세 번째 되묻기 상태(가칭 `NEEDS_INPUT`)를 추가 | 계약을 늘린다 — 프론트·앱 클라이언트가 새 상태를 처리해야 하고, D-033/D-034 리뷰를 다시 거쳐야 할 만큼 계약 변경이다 |

이 표는 팀이 고를 자리를 만드는 것이지 답을 고르지 않습니다. 어느 쪽이든 결정이 나야
`cases_v1.jsonl`의 `expected_mode: "ASK"`를 실제 계약 상태로 옮겨 채점할 수 있습니다 —
그 전에는 케이스가 상호작용 모드로만 적혀 있어 이 미정에 흔들리지 않습니다(§1).

**수용 케이스.** `cq_wellness_vague_01`처럼 `expected_mode == "ASK"`이고
`user_input_needed == True`인 케이스들이 이 카드의 통과 기준입니다 — 위 결정이 난 뒤
after 랩에서 이 케이스들의 `response_mode_fit`이 오르는지를 봅니다.

before 랩과 케이스 파일을 맞춰 보면 대상은 **턴 여섯**입니다(`#415`가
`tests/test_orchestration_ask_mode.py`로 그 수를 고정합니다). 그 여섯이 전부 `diagnosis`
거절인 것은 **아닙니다** — 다섯이 `diagnosis`이고 하나(`cq_symptom_missing_triage_01`, 반복
구토)는 `emergency`입니다. 그래서 프롬프트에서 좁혀야 할 규칙이 하나가 아니라 **둘**입니다.
반대로 `cq_explicit_diagnosis_request_01`(병명 확답 요구)과 `cq_emergency_immediate_01`(실제
응급)은 지금 동작이 정답이라 **움직이면 안 됩니다** — 그 둘이 이 카드의 과잉 수정 경보입니다.

> **케이스 파일은 `#415`가 건드리지 않았습니다.** `cases_v1.jsonl`은 `cases_sha256`으로
> 핀 박혀 있어 한 줄만 더해도 before 랩과의 `compare`가 거부됩니다(`report.render_compare`).
> `#415` 착수 뒤 사람이 지정한 케이스 여덟(기록 있음/없음의 상태 질문, `오늘 힘이 없어 보여`,
> 대화 자체에 대한 항의, 되묻기에 답한 후속 발화, 미기록 오독, 비응급 증상, 응급 신호)은
> `backend/tests/test_orchestration_ask_mode.py`에 유닛으로 들어가 있고, **케이스 파일에는
> 다음 판에서** 실립니다.

### B. 제한된 멀티턴 연속성·복구 — Turn Resolver 로 구현됨

> **구현됐습니다 (`#416`, 2026-09-11).** 이 절이 열어 둔 여덟 질문은 전부 답이 났고, 설계·계약·
> 배선의 정본은 이 문서가 아니라
> [`docs/superpowers/specs/2026-09-10-assistant-turn-context-design.md`](../superpowers/specs/2026-09-10-assistant-turn-context-design.md)
> 입니다. 아래는 그 문서로 넘어가기 전 요약이고, 자세한 것은 링크를 따라가세요.

`#416`은 "최근 대화 전달" 기능이 아니라 pre-routing **Turn Resolver**로 구현됐습니다 — 최근
턴 원문을 프롬프트에 얹어 모든 capability에 흘려보내는 안은 사람이 검토 뒤 버렸습니다(스펙
§2, 검토한 대안 표). 대신 라우팅 **앞**에 자리 하나(`orchestration/resolver.py`)가 이력을
읽고, 현재 발화를 `NEW`·`FOLLOW_UP`·`CORRECTION`·`REPEAT`·`META` 다섯 관계 중 하나로 가르고,
앞 요청이나 대기 중인 `CLARIFY`에 이어 **제한된 구조화 컨텍스트**(`ResolvedTurn`)를 라우터와
선택된 capability에 넘깁니다. 이력 원문은 거기서 멈춥니다 — 라우터도 capability도 대화 원문을
직접 못 봅니다.

```
"오늘 건강 상태는?"  →  "식욕과 활력은 어떤가요?"  →  "밥은 먹는데 계속 누워 있어."
     NEW                    CLARIFY(#415)                  FOLLOW_UP ← 앞 질문에 묶인다
```

여덟 질문이 답이 난 자리(스펙 §4의 번호와 같습니다):

| 항목 | 답 |
| --- | --- |
| 개수·종류 | 완료 turn **3쌍**, user·assistant 원문 그대로(요약 안 함) — Turn Resolver의 후보군으로만 쓰인다 |
| 누가 보는가 | **Turn Resolver만** 이력을 본다. 라우터와 선택된 capability는 `ResolvedTurn`의 제한된 결과만 받는다 |
| 길이 한도 | user 그대로(DB 제약 ≤2,000자) · assistant 400자 절단 · 블록 전체 3,000자, 넘으면 오래된 쌍부터 버림 |
| 프라이버시·로깅 | `structured_context`와 안 섞고 별도 인자(`prior_turns`)로 받는다. `LOGGER`에는 안 실림(D-037·D-048). `standalone_query`는 사실로 저장 안 함 |
| 이력 없을 때 | `relation=NEW` **fast path**로 승격 — 맥락 의존 신호가 없으면 모델 호출 자체를 건너뛴다. 프롬프트는 오늘과 바이트 동일 |
| `CLARIFY` 이음 | 가장 최근 완료 turn의 `assistant_status == 'CLARIFY'`를 대기 중인 되묻기로 읽는다. 새 테이블·새 칸 없음 — `chat_turns.public_response`에 이미 있다 |
| 오래된 턴 오염 방지 | 후보 블록이 `CURRENT_QUERY:` 앞, 창이 3쌍, `relation=NEW`가 판정으로 끊고, 응급 경계는 Resolver보다 앞(구조적으로 못 덮음) |
| 수용 케이스 | 5건 → **9건**으로 확장(스펙 §3-⑧) |

**`CLARIFY` 생산자는 둘로 유지됩니다.** Turn Resolver는 `CLARIFY`를 만들지 않습니다 — 연결이
불확실하면 붙임을 버리기만 하고, General의 기존 ask 경로(`#415`, D-068)가 오늘처럼 되묻습니다.
세 번째 생산자를 만들지 않기로 한 것은 사람 결정입니다 — 열거형(`AssistantStatus`)을 넓히지
않는다는 D-068의 선택을 그대로 잇습니다.

**Response Composer는 여전히 이 카드가 아닙니다.** 여러 capability 결과를 한 답변으로 다시
쓰는 자리는 `#416`을 측정한 뒤 별도 PR로 판단합니다.

**받아들이는 법.** `transcript.PRIOR_TURNS_REACH_INFERENCE`가 `True`로 뒤집혔고(§3),
`drivers.SessionDriver`가 `StatelessDriver` 옆에 더해졌습니다. `collect.py`·`judge.py`·
`report.py`는 약속대로 손대지 않았습니다.

**이 카드가 하지 않은 것.** 범용 대화 요약·장기 기억·다중 세션 교차 참조, 반려견 상태·관찰
사실의 임의 생성·저장, `AssistantStatus`·`CapabilityName` 확장, 무상태 경로의 동작 변경,
기존 의료·응급 판단 대체 — 전부 스펙 §8과 같습니다.
