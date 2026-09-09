# evals/

여기는 코드가 아니라 **사람이 검토하는 결과·골드 데이터**입니다 (질문·답변·판정·벤치마크
리포트 등의 jsonl/json/md). 재사용되는 평가·벤치마크 코드는 `backend/src/daengs_evals/` 에
있고, 이 폴더는 그 입력(골드 세트)과 출력(수집된 답변·판정·리포트)이 쌓이는 자리입니다.
`src/` 밖에 두는 이유는 데이터라 import 대상이 아니고, 결과 파일이 늘어나도 패키지
설치·배포 대상을 건드리지 않기 위해서입니다.

| 폴더 | 읽고 쓰는 패키지 | 안에 있는 것 |
| --- | --- | --- |
| `answer_quality/` | `daengs_evals.answer_quality` | `/life/ask` 답변 품질용 질문·답변(jsonl)·판정(jsonl)·리포트(md) |
| `orchestration_router/` | `daengs_evals.router_benchmark` · `daengs_evals.orchestrator_comparison` | 시맨틱 라우터 골드 세트·벤치마크 yaml·실행 결과·요약·리포트. 상세는 `orchestration_router/README.md` |
| `training_quality/` | `daengs_evals.training_quality` | 훈련 RAG judge 의 질문·답변·판정 출력 |
| `place_filter_edits/` | `tools/eval_place_filter_edits_20260909.py` (일회성) | 실제 Gemini → 공통 필터 편집 compiler의 합성 문장·응답·판정·실패 기록 |

과거 결과 파일(`*.json`·`*.jsonl`) 안에는 `backend/tools/...` 같은 옛 경로가 그대로 남아
있습니다. 그 파일은 **그때 실제로 쓰인 경로를 적은 기록**이라 일부러 고치지 않습니다 (md 리포트의
경로 인용은 2026-09-08 #336 에서 새 경로로 바꿨습니다).
