# 오케스트레이터 비교 v2 (#272 · D-055 ⑥)

> 이 실험은 **같은 DAENGS v1 오케스트레이션 계약** 아래에서 planner-first 결정론 워크플로우와
> 반복 툴 선택 루프를 비교한다. LangGraph 와 LangChain 을 서로 배타적인 런타임 기술로 비교하는
> 것이 아니다 — `create_agent` 자체가 LangGraph 런타임 위에서 돈다.

- 골드: `gold_v1.jsonl` · 채점 케이스 80개 · 반복 3회
- `benchmark_source_sha`: `4260ff62e3167687434f195eec6a9efb690aa008`
- `dev` 소스 SHA: `9ffbdbcb603866dafdde49df6b8973f2509bccd2`
- Agent 구현 SHA: `4260ff62e3167687434f195eec6a9efb690aa008`
- LangGraph 구현 SHA: `4260ff62e3167687434f195eec6a9efb690aa008`
- 패키지: langchain 1.4.0, langchain-core 1.6.1, langchain-google-genai 4.4.0, langgraph 1.2.11, google-genai 2.20.0, pydantic 2.13.4
- 채점기: `tools/router_benchmark/evaluate.py` (v1~v8 · 비교 v1 과 **같은 자**)
- 어댑터: 가짜(즉시 OK) — 재는 것은 능력 선택 · 계약 준수 · 오케스트레이션 오버헤드다.
  Training RAG · Life 답 품질 · Place HTTP · DB 지연 · 운영 end-to-end 지연은 재지 않는다
- 예열(비채점): r1: langgraph ANSWERED 1218.0ms, agent ANSWERED 1985.1ms; r2: agent ANSWERED 2239.0ms, langgraph ANSWERED 1132.2ms; r3: langgraph ANSWERED 1364.5ms, agent ANSWERED 1928.3ms
- 선행 구현 균형(실측): r1: langgraph 40, agent 40; r2: langgraph 40, agent 40; r3: langgraph 40, agent 40

## 통제 설정

| 설정 | langgraph | agent |
| --- | --- | --- |
| model_id | gemini-3.1-flash-lite | gemini-3.1-flash-lite |
| temperature | 0.0 | 0.0 |
| candidate_count | 1 | 1 |
| max_output_tokens_per_call | 256 | 256 |
| provider_timeout_ms | 30000 | 30000 |
| provider_retries | none (google-genai retry_options unset → stop_after_attempt(1)) | none (max_retries=0 → stop_after_attempt(1)) |
| schema_retry | once on schema failure (O-14) | none — tool-call validity is enforced by the API |
| loop_bounds | single call | recursion_limit=25, turn_timeout_ms=60000 |
| selection_surface | structured output (response_json_schema=SemanticRoutingDecision) | function calling over 7 argument-less tools (reply_socially: intent) |
| model_visible_input | policy prompt + ROUTING_METADATA + USER_QUERY | system prompt + ROUTING_METADATA + USER_QUERY (same keys, same validation) |
| prompt_version | semantic-router-ko-v7 | agent-ko-v2 |
| principal | ADMIN comparison-runner | (같음) |
| locale | ko-KR | (같음) |
| requested_capability | None | (같음) |
| adapters | FakeAdapter — immediate OK for every capability (same as v1) | (같음) |
| credential | settings.gemini_api_key — never printed | (같음) |
| execution | sequential; first runner alternates by case index and inverts per run | (같음) |
| warmup | one non-scored invocation per implementation per run, excluded from metrics | (같음) |

구조화 출력 대 툴 호출에서 **피할 수 없는 차이**는 같다고 적지 않는다: 라우터는 JSON 스키마로
강제한 출력 한 번(스키마 실패 시 1회 재시도), 에이전트는 턴마다 function-call 파트와 마무리
문장(재시도 없음, `recursion_limit` 안전장치). 프롬프트는 형태·언어가 다르다. 두 구현이 보는
입력(질문 + 라우팅 메타데이터)과 그 뒤의 게이트·payload·실행·집계는 같은 코드다.

## 실행별 지표

| 지표 | langgraph r1 | agent r1 | langgraph r2 | agent r2 | langgraph r3 | agent r3 |
| --- | --- | --- | --- | --- | --- | --- |
| RoutePlan 완전 일치 | 0.975 | 0.975 | 0.975 | 0.950 | 0.975 | 0.975 |
| 실행 precision | 0.974 | 0.987 | 0.974 | 0.987 | 0.974 | 0.987 |
| 실행 recall | 1.000 | 0.987 | 1.000 | 0.987 | 1.000 | 0.987 |
| 다중 실행 집합 정확도 | 1.000 | 0.941 | 1.000 | 0.941 | 1.000 | 0.941 |
| 핸드오프 precision | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| 핸드오프 recall | 1.000 | 1.000 | 1.000 | 0.952 | 1.000 | 1.000 |
| CLARIFY precision | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| CLARIFY recall | 1.000 | 1.000 | 1.000 | 0.917 | 1.000 | 1.000 |
| 헛 CLARIFY 비율 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 스키마 유효율 | 1.000 | 1.000 | 1.000 | 0.975 | 1.000 | 1.000 |
| 금지 실행 건수 | 0 | 0 | 0 | 0 | 0 | 0 |
| 지어낸 능력 건수 | 0 | 0 | 0 | 0 | 0 | 0 |
| training precision | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| training recall | 1.000 | 0.963 | 1.000 | 0.963 | 1.000 | 0.963 |
| life precision | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| life recall | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| walk precision | 0.958 | 1.000 | 0.958 | 1.000 | 0.958 | 1.000 |
| walk recall | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| place precision | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| place recall | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| LLM 턴 합계 | 80 | 160 | 80 | 157 | 80 | 160 |
| LLM 턴 평균 | 1.000 | 2.000 | 1.000 | 1.960 | 1.000 | 2.000 |
| LLM 턴 최대 | 1 | 2 | 1 | 2 | 1 | 2 |
| 입력 토큰 합계 | 92268 | 175036 | 92268 | 171707 | 92268 | 175036 |
| 출력 토큰 합계 | 2509 | 3289 | 2509 | 3222 | 2509 | 3289 |
| 토큰 합계 | 94777 | 178325 | 94777 | 174929 | 94777 | 178325 |
| 케이스당 토큰 평균 | 1184.700 | 2229.100 | 1184.700 | 2186.600 | 1184.700 | 2229.100 |
| 지연 평균(ms) | 951.500 | 1847.800 | 967.600 | 1855.600 | 947.700 | 1807.200 |
| 지연 p50(ms) | 946.900 | 1819.700 | 951.000 | 1865.700 | 944.600 | 1765.300 |
| 지연 p95(ms) | 1074.800 | 2172.700 | 1128.600 | 2248.500 | 1122.900 | 2009.900 |
| 지연 최대(ms) | 1213.300 | 2681.800 | 1398.300 | 2383.000 | 1351.400 | 2440.600 |
| 러너 오류 | 0 | 0 | 0 | 0 | 0 | 0 |
| 조용한 의도 손실 | 0 | 1 | 0 | 1 | 0 | 1 |
| 먼저 돈 케이스 수 | 40 | 40 | 40 | 40 | 40 | 40 |

## 3회 합산

| 지표 (3회) | langgraph | agent |
| --- | --- | --- |
| RoutePlan 완전 일치 평균 | 0.975 | 0.967 |
| 실행 precision 평균 | 0.974 | 0.987 |
| 실행 recall 평균 | 1.000 | 0.987 |
| 다중 실행 집합 정확도 평균 | 1.000 | 0.941 |
| 핸드오프 precision 평균 | 1.000 | 1.000 |
| 핸드오프 recall 평균 | 1.000 | 0.984 |
| CLARIFY precision 평균 | 1.000 | 1.000 |
| CLARIFY recall 평균 | 1.000 | 0.972 |
| 헛 CLARIFY 비율 평균 | 0.000 | 0.000 |
| 스키마 유효율 평균 | 1.000 | 0.992 |
| 금지 실행 건수 합계 | 0 | 0 |
| 지어낸 능력 건수 합계 | 0 | 0 |
| LLM 턴 합계 | 240 | 477 |
| LLM 턴 케이스 평균 | 1.000 | 1.987 |
| 토큰 합계 | 284331 | 531579 |
| 입력 토큰 합계 | 276804 | 521779 |
| 출력 토큰 합계 | 7527 | 9800 |
| 케이스당 토큰 평균 | 1184.700 | 2214.933 |
| 지연 평균(ms, 240건 합산) | 955.600 | 1836.900 |
| 지연 p50(ms) | 947.300 | 1818.700 |
| 지연 p95(ms) | 1123.600 | 2189.600 |
| 지연 최대(ms) | 1398.300 | 2681.800 |
| 러너 오류 합계 | 0 | 0 |

비용 차이 (agent − langgraph, langgraph 대비 %): 토큰 합계 87.0% ·
입력 88.5% · 출력 30.2% · LLM 턴 98.8% ·
평균 지연 92.2%. **토큰은 측정값이고 턴 수에서 추정하지 않았다.**

## 안정성 — 같은 케이스가 세 번 같은 답을 냈나

| 구현 | 안정 케이스 | 일치율 | 불안정 케이스 |
| --- | --- | --- | --- |
| langgraph | 80/80 | 1.000 | 없음 |
| agent | 78/80 | 0.975 | `handoff_02`, `clarify_07` |

## 두 구현이 갈린 곳

| 반복 | 갈린 케이스 | langgraph 우세 | agent 우세 | 둘 다 틀림 |
| --- | --- | --- | --- | --- |
| r1 | `mixed_09`, `boundary_05` | 1 | 1 | 0 |
| r2 | `handoff_02`, `mixed_09`, `clarify_07`, `boundary_05` | 3 | 1 | 0 |
| r3 | `mixed_09`, `boundary_05` | 1 | 1 | 0 |

- 어느 반복에서든 갈린 케이스: `boundary_05`, `clarify_07`, `handoff_02`, `mixed_09`
- 세 반복 모두에서 갈린 케이스: `boundary_05`, `mixed_09`
- 우세 합계(3회): langgraph 5 · agent 3 · 둘 다 틀림 0

## 조용한 의도 손실

요청한 의도가 사용자 모르게 사라진 케이스(× 반복 횟수). 골드가 CLARIFY 인데 무언가를
실행했거나, 골드 의도의 일부만 낸 경우다. FAILED 나 CLARIFY 로 끝난 것은 조용하지 않다.

- langgraph: 없음
- agent: `mixed_09`×3

## 실패 계약

가짜 어댑터는 항상 OK 라 여기서는 안 보인다. 오류·타임아웃·혼합·계획 동결 전 모델 실패는
`tests/test_orchestrator_failure_contract.py` 가 두 구현에 같은 시나리오를 먹여 결정론으로
검증한다. 이 80케이스 점수와는 섞이지 않는다. 계약이 모호한 자리는 그 테스트의 docstring 에
적어 두었고 새 규칙을 만들지 않았다.

## 실사용 테스터 홀드아웃

**보류.** 저장소에 비식별 실사용 테스터 질의 세트가 없다 (`evals/` 에는 사람이 작성한 골드와
결과만 있고, 실사용 신호는 `docs/life/roadmap.md` 기준 채팅 8턴이다). 만들지 않았다.
기대 스키마와 권장 구성은 `evals/orchestration_router/README.md` 의 v2 절에 있다.

## 지지되는 결론과 지지되지 않는 결론

**지지되는 것** — 같은 계약·같은 모델·같은 설정·같은 어댑터·같은 채점기로 80케이스를 3회
잰 값이다. 위 표의 정확도 차이, 토큰·지연 차이, 안정성, 갈린 케이스는 이 데이터가 말한다.

**지지되지 않는 것** — ⑴ 실사용 질의에서의 우열(홀드아웃 없음). ⑵ 진짜 어댑터를 문
end-to-end 지연·품질. ⑶ 통계적 유의성 — 세 반복은 같은 80케이스의 종속 관측이라 합쳐서
검정하지 않았다. ⑷ LangGraph 와 LangChain 이라는 기술의 우열 — 잰 것은 두 오케스트레이션
패턴이다. ⑸ 에이전트가 툴 결과를 보고 다음 수를 정하는 능력 — 이 계약에서는 그 능력을
쓰지 않는다.

## 권고

`retain_langgraph` — exact match ≥ agent and total tokens ≤ agent in every run

권고 규칙은 `runner_v2.recommendation` 에 적혀 있다. 결론을 못 내면 D-055 ⑥ 대로 기본값인
LangGraph 를 남긴다. **이 PR 에서는 어느 구현도 지우지 않는다.**

## 실측 읽기 (사람이 적음 — `--summarize` 를 다시 돌리면 이 절은 사라지므로 다시 붙일 것)

**정확도는 1~2건 차이이고, 그 차이의 부호는 골드 판본에 달려 있다.** 세 반복 모두에서 갈린
케이스는 둘뿐이다.

- `boundary_05` "오늘 산책 날씨는 말고 목줄 당김 교육만 알려줘" — 골드 training. **에이전트가
  3/3 맞고 LangGraph 가 3/3 틀린다** (부정문을 무시하고 Walk 를 더한다).
- `mixed_09` "리드줄 연습 가능한 날씨인지 보고, 걷는 영상도 보행 분석해줘" — `gold_v1` 은
  training+walk+gait, **에이전트는 3/3 walk+gait** 를 골랐다. 그런데 `gold_v3_corrections.json` 이
  사람 검토로 이 케이스를 walk+gait 로 고쳐 두었다 (README). 즉 이 "감점" 은 동결 골드의 알려진
  오표기다. v1 비교(#252)와 같은 자를 쓰려고 `gold_v1` 그대로 채점했지만, v3 오버레이로 다시 세면:

| 완전 일치 | langgraph r1/r2/r3 | agent r1/r2/r3 |
| --- | --- | --- |
| `gold_v1` (위 표) | 78/80 · 78/80 · 78/80 | 78/80 · 76/80 · 78/80 |
| `gold_v3` 오버레이 | 77/80 · 77/80 · 77/80 | 79/80 · 77/80 · 79/80 |

`gold_v1` 로는 동률에 가깝고(권고 규칙은 "≥" 라 LangGraph 로 떨어진다), v3 로는 에이전트가 두
반복에서 앞선다. **어느 쪽이든 80건 중 1~2건이고, 3회 종속 관측이라 유의성은 말할 수 없다.**

**에이전트가 진 나머지 두 건은 선택이 아니라 프로바이더 실패다.** 반복 2 의 `handoff_02`(턴 0,
206ms — 호출 자체가 예외) 와 `clarify_07`(턴 1 뒤 예외) 은 계획 동결 전 실패라 계약대로 FAILED ·
어댑터 0회가 됐다. 재시도는 두 구현 다 없게 맞췄고 LangGraph 는 240회 호출에서 0건, 에이전트는
477회 호출에서 2건이다 — 케이스마다 두 번 부르는 구조가 노출을 두 배로 만든다. 이것이 안정성
78/80 의 전부이고, 프롬프트나 선택 능력의 문제가 아니다.

**`place precision 0.000` 은 두 구현이 같은 한 케이스(`walk_03`)에서 place 를 더 고른 것이다.**
`gold_v1` 은 v7 의 `place` 목적지 이전에 동결돼 place 를 모른다. 갈림이 아니므로 비교에는
정보가 없다. `walk precision 0.958` 은 위 `boundary_05` 다.

**조용한 의도 손실의 유일한 항목(`mixed_09`×3)도 같은 오표기에서 온다** — v3 기준으로는 손실이
아니다. **계약 정렬은 확인됐다**: 골드 CLARIFY 12건에서 에이전트는 프로바이더 실패 1건을 뺀
모든 반복에서 배타 CLARIFY 를 냈고, 헛 CLARIFY 0, 금지 실행 0, 지어낸 능력 0. v1 에서 계약 차이로
분류됐던 `clarify_07`·`08`·`12` 는 이제 LangGraph 와 같은 답이다.

**비용 차이는 크고 일정하다.** 토큰 +87.0% (입력 +88.5%, 출력 +30.2%), 턴 +98.8%, 평균 지연
+92.2%, p95 2190ms 대 1124ms — 세 반복 전부 같은 방향이다. 이 계약 아래에서 에이전트는 "도구를
고르는 턴 + 마무리 턴" 두 번을 부르고, 툴 결과를 보고 다음 수를 정하는 능력은 쓰지 않는다.
그러니 두 번째 턴은 비용만 내고 정보를 주지 않는다.

**권고 (사람이 확인할 것):** 정확도는 동률 범위 안이고 비용은 에이전트가 약 두 배다. D-055 ⑥ 의
"결론을 못 내면 기본값" 과 비용 축이 같은 쪽을 가리키므로 **LangGraph 유지**를 권한다. 에이전트가
일관되게 이긴 `boundary_05`(부정문) 는 라우터 프롬프트의 후속 카드감이지 아키텍처를 바꿀
근거는 아니다. 이 PR 은 어느 구현도 지우지 않는다.

## 사람의 결정

<!-- 어느 구현을 남길지. 사람이 v2 를 보고 정한 뒤 D-055 ⑥ 에 반영하고, 진 쪽을 지우는 카드를 연다. -->
