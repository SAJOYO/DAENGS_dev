# 오케스트레이션 유닛 문서

`POST /assistant/query` 뒤에서 요청을 능력(Life · Training · Walk · Place · Gait)으로
나눠 보내고 그 결과를 하나의 답으로 합치는 LangGraph 오케스트레이션의 문서입니다.
코드는 `backend/src/daengs_backend/orchestration/` (`contracts.py` · `planner.py` ·
`semantic.py` · `aggregate.py` · `graph.py` · `adapters/`) 에 있습니다.

각 능력이 **무엇을 왜 그렇게 답하는가**는 그 유닛 폴더(`life/` · `training/` · `gait/` ·
`place/`)가 원본이고, 여기에는 **능력을 고르고 합치는 규칙**만 둡니다.
"어떻게 돌리나"는 코드 옆 README 와 루트 [README.md](../../README.md) 입니다.

| 파일 | 내용 |
| --- | --- |
| [architecture.md](architecture.md) | CURRENT 물리 토폴로지(실행 주체 3개 · 라우팅 · 배포 · 기동 순서) + 구현된 오케스트레이션 v1 지도 — v1 범위 · **능력 준비도 표(단일 원본)** · Training 토폴로지 |
| [contracts.md](contracts.md) | 오케스트레이터 공통 계약 (확정) — OrchestratorState · RoutePlan · CapabilityResult 6상태(**ABSTAINED ≠ REFUSED**) · AssistantResponse 8상태 · 집계 진리표 · 불변식 · 공개 진입 경계(§8) |
| [routing.md](routing.md) | 승인된 라우팅 정책 — 결정적/의미 경로 경계 · CLARIFY 배타 · 라우터 실패=FAILED · 인가 매트릭스 · **사람 결정 이력 O-1~O-14**(§6) · production 구현 상태(§4) |
| [router-benchmark.md](router-benchmark.md) | 의미 라우터 수용 벤치마크 (동결) — 80개 골드 RoutePlan · 결정론적 지표 · 1회 스키마 재시도 · 동결 게이트 · HUMAN FREEZE |

**읽는 순서.** 처음이면 `architecture.md` §논리 오케스트레이션 → `contracts.md` →
`routing.md`. 라우터 품질만 볼 때는 `router-benchmark.md` 하나로 충분합니다 —
골드 세트는 `backend/evals/orchestration_router/`, 실행기는
`backend/tools/router_benchmark/` 입니다.

**결정 기록은 두 갈래입니다.** 공통·인프라 결정 `D-` 는 [../decisions.md](../decisions.md)
(오케스트레이션 관련은 D-030 · D-033~D-037 · D-041), 라우팅 자체의 사람 결정 `O-` 는
[routing.md](routing.md) §6. 계약의 권위는 이 문서들과 실제
`backend/src/daengs_backend/orchestration/contracts.py` 가 함께 가집니다 — 어긋난 자리를
발견하면 어느 쪽이 맞는지부터 정하고 양쪽을 같이 고칩니다.
