# 문서

| 파일 | 내용 |
| --- | --- |
| [orchestration-architecture.md](orchestration-architecture.md) | CURRENT 물리 토폴로지(실행 주체 3개 · 라우팅 · 배포 · 기동 순서) + **구현 완료된 오케스트레이션 v1 지도**(`/assistant/query` · v1 범위 · **능력 준비도 표(단일 원본)** · Training 토폴로지 — `vectordb.training_rag_*`) |
| [orchestration-contracts.md](orchestration-contracts.md) | 오케스트레이터 공통 계약 (확정) — OrchestratorState · RoutePlan(requests+handoffs+clarify) · CapabilityResult 6상태(**ABSTAINED ≠ REFUSED**) · AssistantResponse 8상태 · 집계 진리표 · 불변식 + **공개 `POST /assistant/query` 진입 경계**(§8) |
| [orchestration-routing.md](orchestration-routing.md) | 승인된 라우팅 정책 — 결정적/의미 경로 경계 · CLARIFY 배타 · 라우터 실패=FAILED · 인가 매트릭스 · 벤치마크 정책 · **사람 결정 이력(O-1~O-14, 전부 해결)** + **production 구현 상태**(§4) |
| [orchestration-router-benchmark.md](orchestration-router-benchmark.md) | Card 2A 의미 라우터 수용 벤치마크 (동결) — 80개 골드 RoutePlan · 결정론적 지표 · 1회 스키마 재시도 · 동결 게이트 · HUMAN FREEZE |
| [decisions.md](decisions.md) | 의사결정 기록 (D-001 ~) |
| [collaboration.md](collaboration.md) | 협업 규칙 — 우선순위 · Iteration · PR 기준 · 데일리 · 회고 |
| [walk-finalize-operating-db-smoke.md](walk-finalize-operating-db-smoke.md) | #140 finalize 운영 DB rollback smoke — 선행 migration 누락 발견, 932점 백업·chunk 이관, 최종 PASS |
| [walk/spatial-diary-api.md](walk/spatial-diary-api.md) | Walk 공간 일기 조회 API — 인증·repeatable-read snapshot·요청/응답·운영 상한·Place/Journey 경계 |

**배치 규칙 — 유닛별 폴더.** 팀 공통(협업 규칙 · 공통/인프라 결정 `D-`)은 이 폴더 루트에,
유닛(코드 경계 — `daengs_life` · `daengs_place` · `daengs_journey` · `daengs_screening` · `gait-analysis` · 오케스트레이션)의
결정 기록과 로드맵은 `docs/<유닛>/` 에 둡니다. 사람이 아니라 코드 경계로 묶는 이유는 담당자가
바뀌어도 폴더가 남기 때문입니다. "어떻게 돌리나"는 코드 옆 README 에, "왜"와 "지금 어디까지"는 여기에.
지금은 `life/` · `training/` · `gait/` 을 옮겼고, `skin/` 은 옮겨 올 문서가 아직 없어
**자리만** 만들어 두었습니다. 나머지(오케스트레이션)는 별도 카드입니다(#82).

### `life/` — 생활 파트 (① 제도·문서 RAG `/ask` · ② 실시간 산책 `/walk`)

생활비서 RAG(①)·실시간 산책(②) 문서는 `choiyc05/daengs-life` 에서 이관했습니다 (D-018).
**ADR 접두사가 `RAG-` · `RT-` 로 갈려 있는 것은 의도입니다** — 위 `decisions.md` 의 `D-` 와
번호가 겹치면서 뜻이 남남이었기 때문입니다 (`D-011` 이 양쪽에서 다른 결정이었습니다).

| 파일 | 내용 |
| --- | --- |
| [life/roadmap.md](life/roadmap.md) | ①+② **생활 파트 로드맵** — 지금 상태 · 경계 · 트랙 A~F · 순서 · 하지 않기로 한 것. 결정의 "왜"는 없고 번호로만 가리킨다 (living doc) |
| [life/decisions-rag.md](life/decisions-rag.md) | ① 설계 결정 기록 (RAG-001 ~ RAG-052, `037` 결번) — 임베딩·청킹·저장 규약·골든셋·적재·검색·서빙·크롤 운영·PDF 약관 |
| [life/decisions-realtime.md](life/decisions-realtime.md) | ② 설계 결정 기록 (RT-) — 실시간 엔진 18결정 (계층·관측 모델·산책 적합도·캐시·부분 실패·응답 계약) |
| [life/data-sources.md](life/data-sources.md) | ① 데이터 소스 수집 체크리스트 — 시드 30개 진행 현황, 키 발급처. **2026-08-30 기준 문서형 23 중 16 수집, 남은 7은 막힘** |
| [life/realtime-apis.md](life/realtime-apis.md) | ② 날씨·대기질 API 정리 + 실측 로그. §1~§5 와 어긋나면 **§6 이 맞습니다** |

저쪽 `docs/workflow.md`(작업 방식)는 **가져오지 않았습니다** — 위 `collaboration.md` 가
같은 규칙을 더 자세히 담고 있어, 두면 같은 규칙의 두 번째 주장이 생깁니다.
이관 노트(`handoff-daengs-dev.md`)도 원본 레포에 남겨 뒀습니다. 결과는 D-018 에 있습니다.

### `training/` — 훈련 파트 (훈련 RAG `/training/chat`)

훈련 RAG 문서는 `frankie516c/dog-training-rag` 에서 **선별 이관**했습니다 (2026-08-31, 파트별
합치는 날). 색인은 [training/README.md](training/README.md) — 서빙 계약([training/rag-demo.md](training/rag-demo.md),
원래 `docs/training-rag-demo.md`) · 소스/수집 · 설계/결정 문서 17건과 근거 리포트
`training/reports/` 12건입니다. GraphRAG→벡터 전환, 환경축 폐기 같은 "왜"가 여기에 있고,
검색 품질 미해결 건은 `training/retrieval-gate/STATUS.md` 가 현재 상태입니다.
발표 대본·폐기된 그래프 설계·실험 과정 리포트는 원본 레포에 남겼습니다 — 무엇을 왜
안 가져왔는지는 색인의 "가져오지 않은 것" 절에 있습니다.

### `gait/` — 보행 분석 (`/gait/*`, 영상에서 관절 움직임 → 같은 개체의 시간 변화 비교)

| | |
| --- | --- |
| [gait/worklog.md](gait/worklog.md) | **어디까지 했고 다음에 뭘 이어야 하나** — 미해결 목록과 각각이 어느 PR·결정으로 이어지는지, 반복해서 부딪힌 자리. 세션이 바뀌면 가장 먼저 사라지는 정보라 파일로 남깁니다 |
| [gait/record-data-design.md](gait/record-data-design.md) | 기록 저장 정책·DB 구조 **설계안** (미확정 — 테이블·migration 은 아직 없습니다). 기록의 주인이 backend 냐 gait 냐가 갈림길이고 그것이 앱이 부르는 URL 을 정합니다 |
| [gait/record-data-design-easy.md](gait/record-data-design-easy.md) | 위 문서를 쉬운 말로 |

**코드 옆에 있는 것**은 여기 없습니다 — `backend/src/daengs_gait/` 의
[API.md](../backend/src/daengs_gait/API.md)(앱이 볼 응답 계약) ·
`README.md`(가중치 배치·운영) · `CLAUDE.md`(임의로 바꾸면 조용히 틀리는 자리).
"어떻게 돌리나"는 코드 옆이 맞다는 규칙입니다.

⚠️ 결정은 `decisions.md` 의 **D-029**(독립 서비스) → **D-038**(소스는 backend 로,
런타임 격리는 유지)에 있습니다. 코드 위치만 보면 `daengs_training`·`daengs_screening`
과 같아 보이지만 **gait 만 런타임을 안 합쳤습니다** — 영상 추론이 분 단위라서입니다.

운영 / 배포 절차는 루트 [README.md](../README.md), 코드 규칙은
[CLAUDE.md](../CLAUDE.md) 에 있습니다.

## 아직 없는 것

- **값 사전** — `documents.subcategory` 는 `CHECK` 제약 없이 문서로 관리하기로
  했는데 그 문서가 아직 없습니다. 데이터 적재를 시작하기 전에 필요합니다.
- **임베딩 모델 기준** — `embedding VECTOR(1024)` 의 1024 가 어떤 모델 기준인지
  적어두지 않으면, 모델을 바꿀 때 판단 근거가 사라집니다.
