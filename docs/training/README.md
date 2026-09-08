# 훈련(training) 유닛 문서

훈련 RAG(`POST /training/chat` · `backend/src/daengs_training/`) 유닛의 문서입니다.
`frankie516c/dog-training-rag` 에서 2026-08-31(파트별 문서 합치는 날)에 **선별 이관**했습니다 —
팀이 필요로 하는 "왜"와 "지금 어디까지"만 가져오고, 과정 기록·발표 대본·폐기된 그래프 설계
문서는 원본 레포에 남겼습니다 (life/ 가 `workflow.md` 를 안 가져온 것과 같은 기준).
"어떻게 돌리나"는 코드 옆(`backend/src/daengs_training/`)에 있습니다.

| 파일 | 내용 |
| --- | --- |
| [rag-demo.md](rag-demo.md) | **서빙 계약(현재 유효)** — 모듈러 모놀리스 구조, 승인 매니페스트 14문서/83청크, **배포 과정에서의** 재적재·재임베딩 금지(원문: *"as part of deployment"* — 사람이 손으로 돌리는 적재는 여기 해당하지 않습니다). 원래 `docs/training-rag-demo.md` |

## 소스 · 수집

| 파일 | 내용 |
| --- | --- |
| [SOURCES.md](SOURCES.md) | 코퍼스 소스 지도 — 1층 유튜브·2층 공공지침 2층 구조와 수집 현황 |
| [source_blocklist.md](source_blocklist.md) | 소스 차단·주의 도메인 목록 — 선정 단계 1차 방어선 |
| [acquisition_list.md](acquisition_list.md) | 커버리지 결손 축을 메우기 위한 문서 조달 명세와 해제 픽스처 |
| [data_acquisition_pipeline.md](data_acquisition_pipeline.md) | 원본 바이트·메타데이터만 남기는 수집 단계 경계 설계 |

## 설계 · 결정

| 파일 | 내용 |
| --- | --- |
| [curriculum_axes.md](curriculum_axes.md) | 훈련 커리큘럼 8축 정의 — 코퍼스와 독립이어야 하는 이유 |
| [decision_graphrag_abandoned_0824.md](decision_graphrag_abandoned_0824.md) | **GraphRAG 폐기·벡터RAG 전환 결정**과 하루치 실측 경로 |
| [decision_env_axis_discarded_0824.md](decision_env_axis_discarded_0824.md) | 환경축 폐기 결정 — 핵심 근거 83.1%가 순환 매칭 오염 |
| [document_ingest_design.md](document_ingest_design.md) | 블로그 문서 청킹 설계 결정 — 헤딩 승격을 소스 단위로 차단 |
| [doc_to_md_decision.md](doc_to_md_decision.md) | doc-to-md 추출기 라우팅 확정 — HTML/JATS/PDF 유형별 분기 |
| [design_qa_authority_retrieval.md](design_qa_authority_retrieval.md) | Q&A 소스 권위 분리 설계 — 견주 질문부 인용 금지 계약 |
| [agenda_0825.md](agenda_0825.md) | 데모 이후로 미뤄 둔 결정 안건 모음 — 거절 신호 교체 등 |
| [demo_scenarios.md](demo_scenarios.md) | 데모 4개 시나리오와 질문 매핑, 교체·보류 사유 |

## retrieval-gate/ — 안전 게이트 (진행중)

| 파일 | 내용 |
| --- | --- |
| [retrieval-gate/STATUS.md](retrieval-gate/STATUS.md) | 현재 상태 — 게이트는 수정, **검색 품질은 미해결** |
| [retrieval-gate/TODO.md](retrieval-gate/TODO.md) | 작업 체크리스트와 선후 조건 |
| [retrieval-gate/HISTORY.md](retrieval-gate/HISTORY.md) | 버그 발견부터 수정까지의 작업 일지 |

## reports/ — 실험 리포트 (선별 12건)

위 문서들이 근거로 인용하는 리포트와, 검색 품질 작업(미해결)을 이어받을 때 필요한
0827~0828 증거만 가져왔습니다. 나머지 실험 과정 기록 ~50건은 원본 레포 `reports/` 에 있습니다.

| 파일 | 내용 |
| --- | --- |
| [reports/retrieval_gap_hybrid_vs_vector_0820.md](reports/retrieval_gap_hybrid_vs_vector_0820.md) | 하이브리드 vs 벡터 격차 실측 — 데모 시나리오의 근거 |
| [reports/doc_to_md_evaluation_design.md](reports/doc_to_md_evaluation_design.md) | doc-to-md 병렬 평가 설계 |
| [reports/doc_to_md_html_comparison.md](reports/doc_to_md_html_comparison.md) | 한국어 공공 웹 HTML→Markdown 추출기 비교 |
| [reports/generated/combined_corpus_coverage.md](reports/generated/combined_corpus_coverage.md) | 통합 코퍼스 커버리지 (자동 생성) |
| [reports/generated/medical_guardrail_v1v2_comparison.md](reports/generated/medical_guardrail_v1v2_comparison.md) | 의료 가드레일 v1→v2 비교 (자동 생성) |
| [reports/generated/owner_fixtures_coverage.md](reports/generated/owner_fixtures_coverage.md) | 견주 픽스처 커버리지 (자동 생성) |
| [reports/retrieval_reranking_0827.md](reports/retrieval_reranking_0827.md) | 검색 랭킹 오프라인 비교 결과 (실험 계획서는 원본 레포) |
| [reports/retrieval_reranking_qualitative_0827.md](reports/retrieval_reranking_qualitative_0827.md) | Dense anchor 누락 10건 정성 분석 |
| [reports/training_api_baseline_0827.md](reports/training_api_baseline_0827.md) | 훈련 RAG FastAPI 기준선 |
| [reports/training_api_closeout_0827.md](reports/training_api_closeout_0827.md) | 훈련 RAG 마감 보고서 (중간 진단 체크포인트는 원본 레포) |
| [reports/cross_encoder_rerank_0828.md](reports/cross_encoder_rerank_0828.md) | cross-encoder 리랭킹 오프라인 비교 |
| [reports/retrieval_gate_signal_0828.md](reports/retrieval_gate_signal_0828.md) | 답변 게이트 신호 탐색 — 관련성 신호 부재 확정 |

## 가져오지 않은 것

- `data/` · `experiments/` · `prompts/` — 수집 원본과 실험 산출물. 본문에서 그쪽을
  가리키던 링크는 경로 텍스트만 남기고 링크를 풀었습니다.
- **폐기된 그래프 설계 문서** (schema 2장 · graph_hybrid_retrieval_design · handoff_neo4j) —
  "왜 폐기했나"는 위 decision 2장이 담고 있습니다.
- **GROUNDRULES.md** — 이 레포의 `docs/collaboration.md`·`CLAUDE.md` 와 같은 규칙의
  두 번째 주장이 되므로 두지 않습니다.
- **pgvector_local.md** — e5-base 768차원·청크 7,079개짜리 **옛 로컬 실험 구성**이라,
  현행 서빙(승인 매니페스트 14문서/83청크·`training-rag-pgvector`)과 어긋납니다.
  따라 하면 안 되는 문서라 두지 않습니다. 서빙 기준은 [rag-demo.md](rag-demo.md).
- **YOUTUBE_PIPELINE_TROUBLESHOOTING.md** — 유튜브 자막은 승인 서빙 코퍼스에
  들어가지 못했고, 그 수집 과정의 트러블슈팅 일지라 원본 레포에 남깁니다.
- **발표 대본**(presentation_0825 · mentoring_0822) · **이관 안내서**(TEAM_HANDOFF ·
  BRANCH_MAP · handoff_0822) · **실험 과정 리포트 ~50건** — 원본 레포에 있습니다.
- 문서끼리의 상대 링크는 이관 구조에 맞춰 보정했고, 전수 실존 검사를 통과했습니다.
