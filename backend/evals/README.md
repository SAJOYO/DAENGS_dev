# evals/

여기는 코드가 아니라 **사람이 검토하는 결과·골드 데이터**입니다 (질문·답변·판정·벤치마크
리포트 등의 jsonl/json/md). 재사용되는 평가·벤치마크 코드는 `backend/src/daengs_evals/` 에
있고, 이 폴더는 그 입력(골드 세트)과 출력(수집된 답변·판정·리포트)이 쌓이는 자리입니다.
`src/` 밖에 두는 이유는 데이터라 import 대상이 아니고, 결과 파일이 늘어나도 패키지
설치·배포 대상을 건드리지 않기 위해서입니다.

| 폴더 | 읽고 쓰는 패키지 | 안에 있는 것 |
| --- | --- | --- |
| `diary_slots/` | `tools/run_diary_slots.py` · `daengs_evals.diary_slots_demo` | 파트 슬롯 미리보기의 합성 입력 기반 Gemini 실제 문장·근거·정책 버전과 알려진 한계 |
| [place_conversation/](place_conversation/README.md) | `daengs_evals.place_conversation` | 시설 검색 대화 23개 명세·실제 출력·구조 비교·검토 기록 |
| `walk_diary_stamps/` | `tools/preview_diary_stamps.py` | 사용자 기록 중심 스탬프의 합성 입력·선택 사유·읽기용 결과. LLM 호출 없음 |
| `answer_quality/` | `daengs_evals.answer_quality` | `/life/ask` 답변 품질용 질문·답변(jsonl)·판정(jsonl)·리포트(md) |
| `orchestration_router/` | `daengs_evals.router_benchmark` · `daengs_evals.orchestrator_comparison` | 시맨틱 라우터 골드 세트·벤치마크 yaml·실행 결과·요약·리포트. 상세는 `orchestration_router/README.md` |
| `training_quality/` | `daengs_evals.training_quality` | 훈련 RAG judge 의 질문·답변·판정 출력 |
| `conversation_quality/` | `daengs_evals.conversation_quality` | 동결 멀티턴 케이스(`cases_v1.jsonl`)·랩·판정·전후 비교 리포트. `#277`과 달리 질문·답변 한 쌍이 아니라 대화 여러 턴을 본다. 상세는 `conversation_quality/README.md`, 축 정의와 후속 설계는 `docs/orchestration/conversation-quality.md` |

과거 결과 파일(`*.json`·`*.jsonl`) 안에는 `backend/tools/...` 같은 옛 경로가 그대로 남아
있습니다. 그 파일은 **그때 실제로 쓰인 경로를 적은 기록**이라 일부러 고치지 않습니다 (md 리포트의
경로 인용은 2026-09-08 #336 에서 새 경로로 바꿨습니다).
