# 문서

| 파일 | 내용 |
| --- | --- |
| [walk/diary-generation.md](walk/diary-generation.md) | 스탬프의 LLM 배경·제목 작성과 기존 생성 예약·완료, 명시적 일기 HTTP 형식 |
| [walk/diary-observations.md](walk/diary-observations.md) | 확정 동선의 체류·상대 속도 후보 공급, 실제 GPS 앵커와 기록 중심 스탬프 연결 |
| [walk/diary-stamps.md](walk/diary-stamps.md) | 사용자 기록 중심 장면 선택·관측 보충·배경 슬롯과 재현 가능한 스탬프 |
| [walk/action-pin-context.md](walk/action-pin-context.md) | 행동 핀 v5 장면·주변 정보 — 추정 위치 출처, 미확정/위치 없음, 버전 협상과 적용 순서 |
| [orchestration/README.md](orchestration/README.md) | 오케스트레이션 유닛 문서 색인 — 아키텍처 · 공통 계약 · 라우팅 정책 · 라우터 벤치마크 |
| [chat-transaction-flow.md](chat-transaction-flow.md) | 제품 대화·AI 요약의 짧은 트랜잭션 경계 — 예약 TX → DB 세션 종료 → 외부 호출 → 조건부 완료 TX |
| [decisions.md](decisions.md) | 의사결정 기록 (D-001 ~) |
| [collaboration.md](collaboration.md) | 협업 규칙 — 우선순위 · Iteration · PR 기준 · 데일리 · 회고 |
| [walk/spatial-diary-api.md](walk/spatial-diary-api.md) | Walk 공간 일기 조회 API — 인증·repeatable-read snapshot·요청/응답·운영 상한·Place/Journey 경계 |
| [place/UPSTREAM.md](place/UPSTREAM.md) | Place 운영 정본의 출처·소유권과 Geo 승격 기준점 |
| [journey/README.md](journey/README.md) | Journey 서비스의 역할·소유 범위와 실행 문서 안내 |
| [territory/visit-attestation.md](territory/visit-attestation.md) | 점령지 방문 인증 워킹 스켈레톤 — 위치·사진·비동기 판정 상태 계약 |
| [territory/owner-summary-api.md](territory/owner-summary-api.md) | 선택한 전봇대 주인의 공개 시즌 점수·점령 수, snapshot·앱 연동·준비 상태 |
| [territory/first-season-rewards.md](territory/first-season-rewards.md) | 첫 시즌 회원별 기본 원장·탈취 보너스·시간 정산, DEV 연결과 적용 순서 |

**배치 규칙 — 유닛별 폴더.** 팀 공통(협업 규칙 · 공통/인프라 결정 `D-`)은 이 폴더 루트에,
유닛(코드 경계 — `daengs_life` · `daengs_place` · `daengs_journey` · `daengs_screening` · `gait-analysis` · Walk · Territory · 오케스트레이션 · 관리자 콘솔)의
결정 기록과 로드맵은 `docs/<유닛>/` 에 둡니다. 사람이 아니라 코드 경계로 묶는 이유는 담당자가
바뀌어도 폴더가 남기 때문입니다. "어떻게 돌리나"는 코드 옆 README 에, "왜"와 "지금 어디까지"는 여기에.
지금은 `orchestration/` · `life/` · `training/` · `gait/` · `place/` · `journey/` · `territory/` · `walk/` 를 옮겼고,
`skin/` 은 옮겨 올 문서가 아직 없어 **자리만** 만들어 두었습니다. 마지막까지 루트에 남아 있던
오케스트레이션 4건도 `orchestration/` 으로 옮겼습니다(#82) — 폴더가 유닛을 말하므로 파일 이름의
`orchestration-` 접두사는 뗐습니다 (`orchestration-contracts.md` → `orchestration/contracts.md`).

### `orchestration/` — `/assistant/query` 뒤 LangGraph 오케스트레이션 (`backend/src/daengs_backend/orchestration/`)

| | |
| --- | --- |
| [orchestration/README.md](orchestration/README.md) | 유닛 색인 — 네 문서의 경계와 읽는 순서 |
| [orchestration/architecture.md](orchestration/architecture.md) | CURRENT 물리 토폴로지(실행 주체 3개 · 라우팅 · 배포 · 기동 순서) + **구현 완료된 오케스트레이션 v1 지도**(`/assistant/query` · v1 범위 · **능력 준비도 표(단일 원본)** · Training 토폴로지 — `vectordb.training_rag_*`) |
| [orchestration/contracts.md](orchestration/contracts.md) | 오케스트레이터 공통 계약 (확정) — OrchestratorState · RoutePlan(requests+handoffs+clarify) · CapabilityResult 6상태(**ABSTAINED ≠ REFUSED**) · AssistantResponse 8상태 · 집계 진리표 · 불변식 + **공개 `POST /assistant/query` 진입 경계**(§8) |
| [orchestration/routing.md](orchestration/routing.md) | 승인된 라우팅 정책 — 결정적/의미 경로 경계 · CLARIFY 배타 · 라우터 실패=FAILED · 인가 매트릭스 · 벤치마크 정책 · **사람 결정 이력(O-1~O-14, 전부 해결)** + **production 구현 상태**(§4) |
| [orchestration/router-benchmark.md](orchestration/router-benchmark.md) | Card 2A 의미 라우터 수용 벤치마크 (동결) — 80개 골드 RoutePlan · 결정론적 지표 · 1회 스키마 재시도 · 동결 게이트 · HUMAN FREEZE |

오케스트레이션은 유닛들의 **앞단**입니다 — 각 능력(Life · Training · Walk · Place · Gait)의 "왜"는
그 유닛 폴더에, 능력을 고르고 합치는 규칙은 여기에 둡니다. 결정 이력은 두 갈래입니다:
공통/인프라 `D-` 는 `decisions.md`, 라우팅 결정 `O-` 는 `orchestration/routing.md` §6.

### `life/` — 생활 파트 (① 제도·문서 RAG `/life/ask` · ② 실시간 산책 `/life/walk-conditions`)

생활비서 RAG(①)·실시간 산책(②) 문서는 `choiyc05/daengs-life` 에서 이관했습니다 (D-018).
**ADR 접두사가 `RAG-` · `RT-` 로 갈려 있는 것은 의도입니다** — 위 `decisions.md` 의 `D-` 와
번호가 겹치면서 뜻이 남남이었기 때문입니다 (`D-011` 이 양쪽에서 다른 결정이었습니다).

| 파일 | 내용 |
| --- | --- |
| [life/roadmap.md](life/roadmap.md) | ①+② **생활 파트 로드맵** — 지금 상태 · 경계 · 트랙 A~G · 순서 · 하지 않기로 한 것. **열린 것만** 담고, 결정의 "왜"는 없고 번호로만 가리킨다 (living doc) |
| [life/roadmap-archive-2026-09.md](life/roadmap-archive-2026-09.md) | 🗄 위 로드맵의 **2026-09-08 까지의 판** — 닫힌 카드의 결과 · 랩 추이 · 닫힌 결정 · 갱신 이력. 같은 §뼈대라 옛 `roadmap.md §N` 인용은 여기를 본다. 갱신하지 않는다 (#341) |
| [life/decisions-rag.md](life/decisions-rag.md) | ① 설계 결정 기록 (RAG-001 ~ RAG-052, `037` 결번) — 임베딩·청킹·저장 규약·골든셋·적재·검색·서빙·크롤 운영·PDF 약관 |
| [life/decisions-realtime.md](life/decisions-realtime.md) | ② 설계 결정 기록 (RT-) — 실시간 엔진 18결정 (계층·관측 모델·산책 적합도·캐시·부분 실패·응답 계약) |
| [life/data-sources.md](life/data-sources.md) | ① 데이터 소스 수집 체크리스트 — 시드 30개 진행 현황, 키 발급처. **2026-08-30 기준 문서형 23 중 16 수집, 남은 7은 막힘** |
| [life/realtime-apis.md](life/realtime-apis.md) | ② 날씨·대기질 API 정리 + 실측 로그. §1~§5 와 어긋나면 **§6 이 맞습니다** |
| [life/assistant-life-gcp-smoke.md](life/assistant-life-gcp-smoke.md) | ① GCP `/assistant/query` 경유 Life 스모크 (#169, 로드맵 A0) — 인프라 PASS · O-9 축소 확인. **산문 물러섬이 OK 로 통과 · `no_evidence` 기권이 안 남** → A3a 근거 |

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

### `place/` — 장소 검색·중립 점령지 읽기 (`/v2/places/*` · `/territory/sites/*`)

| | |
| --- | --- |
| [place/UPSTREAM.md](place/UPSTREAM.md) | 운영 Place 정본의 출처·소유권, Geo에서 승격한 기준점과 포함·제외 범위 |
| [place/discovery-migration.md](place/discovery-migration.md) | 자연어 Place 발견 기능의 운영 이주 계획·런타임 경계·단계별 금지선 |
| [place/facility-tools.md](place/facility-tools.md) | 시설 검색 대화 스켈레톤: 정적 도구·계획·실행·CAS·답변·앱 연결 |
| [place/territory-sites.md](place/territory-sites.md) | 중립 점령지 게임판의 읽기 경계·데이터 세대·적재와 배포 판정 |

실행 명령은 루트 [README.md](../README.md)와 코드·인프라 옆 문서를 따릅니다.
Place는 별도 PostGIS와 Alembic을 소유하며, 그 물리·런타임 경계는
[CLAUDE.md](../CLAUDE.md)와 `decisions.md`의 D-026 · D-027 · D-039에 있습니다.

### `journey/` — 장소 선택 뒤 단발 경로 스냅샷 (`POST /journey`)

| | |
| --- | --- |
| [journey/README.md](journey/README.md) | Journey의 역할·소유 범위와 실행 문서 안내 |
| [journey/UPSTREAM.md](journey/UPSTREAM.md) | Geo 이주 기준점, 유지한 계약과 가져오지 않은 범위 |

로컬 실행과 TMAP provider 설정은 코드 옆
[README.md](../backend/src/daengs_journey/README.md)에 둡니다. Journey는 Place 검색,
Dog/Owner Profile, 산책 기록을 소유하지 않습니다.

### `walk/` — 산책 기록·공간 일기 (`/app/walks/*`)

| 파일 | 내용 |
| --- | --- |
| [walk/entries-and-record-profile.md](walk/entries-and-record-profile.md) | 행동·메모 기록의 동기화 계약과 산책 기록 프로필 |
| [walk/entry-contexts.md](walk/entry-contexts.md) | 행동·글 원본에 연결한 주변 정보 봉투 저장·비동기 수집과 활성화 절차 |
| [walk/public-context.md](walk/public-context.md) | SGIS 행정동·도시공원 실제 배경 공급, 캐시·배포 순서와 Gemini/앱 검증 |
| [walk/area-context.md](walk/area-context.md) | 상권 업종 집계·EGIS 하천 형상 배경, 지역 카탈로그와 실제 생성 검증 |
| [walk/runtime.md](walk/runtime.md) | 산책 공공자료 운영 워커·Beat·공유 캐시, 준비·검사·활성화·복구 |
| [walk/spatial-diary-api.md](walk/spatial-diary-api.md) | 공간 일기 조회 API — 인증·일관된 조회·운영 상한·Place/Journey 경계 |
| [walk/storyboard-live.md](walk/storyboard-live.md) | 실제 산책의 관측 분석·장면 구성과 앱 검토 연결 |
| [walk/diary-titles.md](walk/diary-titles.md) | 스토리보드 대표·장면 제목의 LLM 생성과 저장·실패 처리 |
| [walk/diary-contract.md](walk/diary-contract.md) | 산책 일기 이관 1단계 — 입력·스탬프·분리 서술 계약과 기존 오케스트레이션/생성 서비스 접점 |
| [walk/photo-metadata.md](walk/photo-metadata.md) | 산책 일기 이관 2단계 — 사진 메타데이터 CAS 동기화와 Dev 저장 입력 어댑터 |
| [walk/scene-anchors.md](walk/scene-anchors.md) | 자동 장면을 원본 GPS 관측 위치에 연결하는 v4 계약 |
| [walk/speed-style.md](walk/speed-style.md) | 산책 지도 속도 색상과 앱 표시 정책 |
| [walk/finalize-operating-db-smoke.md](walk/finalize-operating-db-smoke.md) | #140 finalize 운영 DB rollback smoke와 당시 앱 왕복 검증 기록 |

### `territory/` — 점령 방문 증거·상태 (`/app/territory/*`)

| | |
| --- | --- |
| [territory/visit-attestation.md](territory/visit-attestation.md) | 인앱 촬영 시도, 10m 위치 판정, 사진 업로드와 비동기 판정 상태 계약 |
| [territory/claim-foundation.md](territory/claim-foundation.md) | 공유 점유 모델과 상태 전이의 1단계 설계 |
| [territory/ownership-api.md](territory/ownership-api.md) | 온라인 점유 저장·API·동시성 및 배포 계약 |
| [territory/vision-worker.md](territory/vision-worker.md) | 방문 사진의 비동기 판정·재시도와 점유 연결 경계 |

중립 게임판과 점령지 좌표 읽기는 Place가 소유하고, 회원별 촬영 시도와
`VerifiedVisit`과 공유 점유는 backend가 소유합니다. 점유 계약과 후속 정책은
[온라인 점유 API](territory/ownership-api.md)에서 확인합니다.

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

### `console/` — 관리자 콘솔 (`frontend/app/console` · `/admin/*` · `/auth/*`)

| | |
| --- | --- |
| [console/roadmap.md](console/roadmap.md) | **관리자 콘솔 로드맵** — 왜 필요한가(지금 psql · Gmail · SSH 로 하는 일) · **메뉴 9개**와 그 뒤의 API (2026-09-07 실측 · 준비 중은 지식 베이스 하나) · **운영 DB 에 무엇이 적용됐나** · **AI 답변 신고 경로**(서버는 섰고 앱이 남았다) · 트랙 A~E · 순서 · 하지 않기로 한 것 · 열린 결정 (living doc) |

콘솔은 코드 하나(`frontend/app/console`)를 로컬(`daengs.~`, 개발 DB)과 GCP(`daengapp.~`, 운영 DB) 두 곳에
배포합니다. 운영 값이 있는 GCP 쪽이 운영 콘솔이고, "GCP 용 콘솔" 을 따로 만들지 않습니다.
인증·권한의 "왜" 는 `decisions.md` 의 **D-014 · D-015 · D-016** 에 있습니다.

운영 / 배포 절차는 루트 [README.md](../README.md), 코드 규칙은
[CLAUDE.md](../CLAUDE.md) 에 있습니다.

## 아직 없는 것

- **값 사전** — `documents.subcategory` 는 `CHECK` 제약 없이 문서로 관리하기로
  했는데 그 문서가 아직 없습니다. 데이터 적재를 시작하기 전에 필요합니다.
- **임베딩 모델 기준** — `embedding VECTOR(1024)` 의 1024 가 어떤 모델 기준인지
  적어두지 않으면, 모델을 바꿀 때 판단 근거가 사라집니다.
