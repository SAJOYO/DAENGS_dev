# 오케스트레이션 유닛 문서

`POST /assistant/query` 뒤에서 요청을 능력(Life · Training · Walk · Place · Gait)으로
나눠 보내고 그 결과를 하나의 답으로 합치는 LangGraph 오케스트레이션의 문서입니다.
코드는 `backend/src/daengs_backend/orchestration/` (`contracts.py` · `planner.py` ·
`semantic.py` · `aggregate.py` · `graph.py` · `runtime.py` · `adapters/`) 에 있습니다.

**오케스트레이터 구현은 LangGraph 하나입니다** (D-072, D-055 개정). 한때 자유도가 필요한
질의에 LangChain 에이전트가 나은지 재 보려고 둘을 병존시켰지만(D-055), 비교 v2 는
정확도 우위를 못 보였고 토큰·지연은 에이전트가 약 두 배였으며, 승인된 후속 기능
어디에도 "툴 결과를 보고 다음 수를 정하는 선택" 이 필요하지 않아 그 조건이 끝내 채워지지
않았습니다. 그래서 LangGraph 를 유일한 지원 런타임으로 확정하고 `agent/` 와 `agent`
extra 를 지웠습니다. 만드는 곳은 `runtime.py` 의 `build_orchestrator()` 하나이고
(`routers/assistant.py` 는 `run(...) -> AssistantResponse` 만 봅니다), 두 번째 구현이
다시 필요해지면(D-072 재검토 조건) 그 자리 하나만 고치면 됩니다. 비교 근거(리포트·결과
데이터)는 `backend/evals/orchestration_router/` 에 그대로 남아 있습니다.

`contracts.py` · `adapters/` · `aggregate.py` 는 `planner.py` · `semantic.py` · `graph.py`
와 이미 한 몸입니다 — 지킬 두 번째 구현이 없어도 계약 하나·집계 진리표 하나로 두는 이유는
그대로 유효합니다. 자세한 것은 D-055 · D-072.

각 능력이 **무엇을 왜 그렇게 답하는가**는 그 유닛 폴더(`life/` · `training/` · `gait/` ·
`place/`)가 원본이고, 여기에는 **능력을 고르고 합치는 규칙**만 둡니다.
"어떻게 돌리나"는 코드 옆 README 와 루트 [README.md](../../README.md) 입니다.

앱의 공통 채팅에서 같은 시설 세션을 이어 쓰는 선택 계약은
[시설 대화 연결](../place/assistant-conversation.md)을 따른다. `facility`가 있는 요청의
Place만 기존 시설 v2 실행기로 연결하며, 다른 capability와 집계 규칙은 공통 구현을 사용한다.

| 파일 | 내용 |
| --- | --- |
| [architecture.md](architecture.md) | CURRENT 물리 토폴로지(실행 주체 3개 · 라우팅 · 배포 · 기동 순서) + 구현된 오케스트레이션 v1 지도 — v1 범위 · **능력 준비도 표(단일 원본)** · Training 토폴로지 |
| [contracts.md](contracts.md) | 오케스트레이터 공통 계약 (확정) — OrchestratorState · RoutePlan · CapabilityResult 6상태(**ABSTAINED ≠ REFUSED**) · AssistantResponse 8상태 · 집계 진리표 · 불변식 · 공개 진입 경계(§8) |
| [routing.md](routing.md) | 승인된 라우팅 정책 — 결정적/의미 경로 경계 · CLARIFY 배타 · 라우터 실패=FAILED · 인가 매트릭스 · **사람 결정 이력 O-1~O-14**(§6) · production 구현 상태(§4) |
| [router-benchmark.md](router-benchmark.md) | 의미 라우터 수용 벤치마크 (동결) — 80개 골드 RoutePlan · 결정론적 지표 · 1회 스키마 재시도 · 동결 게이트 · HUMAN FREEZE |

**읽는 순서.** 처음이면 `architecture.md` §논리 오케스트레이션 → `contracts.md` →
`routing.md`. 라우터 품질만 볼 때는 `router-benchmark.md` 하나로 충분합니다 —
골드 세트는 `backend/evals/orchestration_router/`, 실행기는
`backend/src/daengs_evals/router_benchmark/` 입니다.

**결정 기록은 두 갈래입니다.** 공통·인프라 결정 `D-` 는 [../decisions.md](../decisions.md)
(오케스트레이션 관련은 D-030 · D-033~D-037 · D-041 · D-055 · D-072), 라우팅 자체의 사람 결정 `O-` 는
[routing.md](routing.md) §6. 계약의 권위는 이 문서들과 실제
`backend/src/daengs_backend/orchestration/contracts.py` 가 함께 가집니다 — 어긋난 자리를
발견하면 어느 쪽이 맞는지부터 정하고 양쪽을 같이 고칩니다.

**산책 일기 이관**의 입력·장면 계약은 도메인 소유인
[../walk/diary-contract.md](../walk/diary-contract.md)에 있습니다. 현재 Walk adapter의
산책 조건 판단을 바꾸지 않고, 기존 `walk_storyboard` 생성 수명주기에 붙이는 접점을 정의합니다.
일기 작성은 `runtime.build_diary_orchestrator()` → `diary.py` LangGraph로 연결됩니다.
채팅 `graph.py`와 일기 그래프가 `execution.py:JobExecutor`를 공유합니다. 기존 산책 조건
capability나 채팅 응답 계약을 일기로 바꾸지 않습니다. 본문 고정·조건부 행동·제목 배치와
기존 발행 서비스의 권한은 [카드 오케스트레이션](../walk/card-orchestration.md)을 따릅니다.
