# 결정: GraphRAG 폐기, 벡터RAG 단독으로 전환 — 2026-08-24

## 결정

이 저장소의 그래프 검색(엔티티/관계 추출 + Neo4j/로컬 파일 기반 그래프 확장)을
**폐기한다.** 앞으로는 벡터 검색 단독(`scripts/evaluate_youtube_retrieval.py`,
`run_combined_retrieval_eval.py --graph-off` 경로)으로 간다.

기존 그래프 관련 코드·문서·동결 스냅샷(`scripts/extract_entities.py`,
`scripts/load_graph_neo4j.py`, `frozen/`, `docs/schema_*.md`,
`data/graph/prompts_stage2_v3_draft.md` 등)은 **삭제하지 않는다** — 이 결정에
이르기까지의 실측 과정 자체가 이 프로젝트의 방법론적 증거이기 때문이다(견종 축 v1
가드레일 코드를 남겨둔 것과 같은 이유, `docs/decision_env_axis_discarded_0824.md`도
같은 원칙).

## 결정 경로 — 오늘 하루의 실측 요약

1. **견종 축 기각** (이전 세션, `reports/breed_conditionality_0822.md`) — 처치를
   가르는 문장 0건, 관계 후보 0건.
2. **그래프 DB 전체 존속 여부 리서치** (`reports/research_graph_viability_0824/`)
   — 실측(밀도 0.13~0.25%, 고립노드 56%)과 문헌이 같은 방향으로 수렴, 다만
   검색 성능 축이 없어 "조건부" 판정으로 유보.
3. **환경축(absence/numeric) 폐기** (`docs/decision_env_axis_discarded_0824.md`)
   — flagship 근거 83.1%가 순환 매칭 오염으로 판명.
4. **그래프 vs 벡터 검색 성능 실측** (`reports/retrieval_perf_graph_vs_vector_0824.md`)
   — 32개 질의 전수에서 랭킹 지표 완전 동일, 이득 0건. 원인은 표본이 아니라
   `hybrid_merge()`가 그래프 후보를 벡터 순위 뒤에 이어붙이기만 하는 코드 구조.
5. **절차/연령대 축 1차 실측** (`reports/procedure_agegroup_axis_measurement_0824.md`)
   — 절차축은 "그래프가 리스트보다 나은가" 1차 신호 부정적(문서 간 재사용이
   단일 저자 문체 반복), 연령대축은 표본상 유망하나 미확정.
6. **소스 진단** (`reports/procedure_axis_source_diagnosis_0824.md`,
   `reports/agegroup_axis_corpus_diagnosis_0824.md`) — **821문서 크롤 풀 전체가
   실질 저자 2명뿐**(yoonsu3454 70.9%, africaamc 29.1%)임을 발견. 절차축은 새
   소스 없이는 테스트 자체가 성립 불가, 연령대축은 처치분화 추출법 자체가
   REQUIRES를 기각시켰던 것과 같은 결함(논항 불명)이라 표본을 늘려도 해소 안 됨.
7. **"마지막 투자 시도"** (`reports/graphrag_final_attempt_stage1_sourcing_0824.md`)
   — 사용자 소유 스크레이퍼(`workspace/scrapper/mini_blog_scraper.py`)로 핏펫
   (Fitpet) 블로그 8건을 신규 확보(로그인/유료/robots.txt 문제 없음, 제3의
   독립 저자). 추출 프롬프트 v3 초안(`claim_type: directive/descriptive` +
   `훈련단계`/`중증도단계` 분리)이 dry-run 2건에서는 의도대로 작동.
8. **본 추출 승인 단계에서 중단** — 실제 재추출(`scripts/extract_entities.py`)이
   OpenAI API를 쓰는 것으로 확인되자 사용자가 OpenAI 비용 지출을 명시적으로
   거부. Claude(Anthropic) API로 전환하는 방안(`.env.example`에 키 자리조차
   없음, 파이프라인 개조 필요)을 제시했으나, 그 전에 **비용 0인 추가 검증**을
   먼저 시도했다.
9. **로컬 임베딩 기반 의미적 재사용 검증(비용 0)** — 핏펫 8건의 STEP 문장과
   기존 재사용 후보 문서의 STEP 문장을 `intfloat/multilingual-e5-base`(로컬)로
   비교. 최고 유사도(0.92)조차 진짜 절차 재사용이 아니라 "분리불안 글은 증상을
   먼저 설명한다"는 **글쓰기 템플릿 유사성**이었고, 나머지 상위 매칭 다수가
   yoonsu3454의 문장 하나("먼저 강아지만의 안전하고 편안한 휴식 공간을
   만들어주세요")로 쏠리는 임베딩 허브 아티팩트였다. **서로 다른 저자 간 진짜
   같은 훈련 스텝이 재사용된다는 신호는 끝까지 나오지 않았다.**

## 왜 여기서 멈추는가

사용자가 사전에 정한 기준("두 축을 해보고 순수 GraphRAG에 메리트가 전혀 없고
구축조차 안 된다면 포기")에 부합한다:

- **구축조차 안 됨**: 절차축은 코퍼스(저자 2~3명)만으로 테스트가 성립하지
  않았고, OpenAI 비용 거부로 재추출 경로도 막혔다.
- **메리트 없음**: 비용을 들이지 않고 시도 가능한 마지막 대안(로컬 임베딩
  의미 비교)까지 포함해 모든 검증에서 그래프가 벡터 대비 이득을 낸다는 신호가
  한 번도 나오지 않았다(4·9번). 하이브리드 검색은 코드 구조상 필연적으로
  벡터와 동일한 결과를 낸다(4번).

## 이번에 하지 않은 것

- 그래프 관련 코드·문서·frozen 스냅샷 삭제 — 하지 않음(증거 보존)
- README.md의 "그래프 DB" 관련 서술 갱신 — 이번 결정 문서만 추가했고 README
  자체는 아직 안 고쳤다. 다음 세션에서 필요시 반영.
- 신규 확보한 핏펫 8건(`data/raw/documents_candidate_0824/`,
  `data/blog_raw_fitpet/`)의 처리 — 그래프용으로는 안 쓰지만, **벡터RAG
  코퍼스로는 여전히 유효한 자료**다(절차형 훈련 콘텐츠, 제3의 독립 저자). 벡터
  코퍼스에 편입할지는 별도 결정 사항으로 남긴다.

## 다음

벡터RAG 단독 경로로 계속한다. `docs/TEAM_HANDOFF.md`의 "그대로 이관하면 안
되는 것" 목록에 그래프 관련 스크립트 전체를 추가하는 것을 고려할 것.

## 참조

- `reports/research_graph_viability_0824/00_SYNTHESIS.md`
- `reports/retrieval_perf_graph_vs_vector_0824.md`
- `reports/procedure_axis_source_diagnosis_0824.md`
- `reports/agegroup_axis_corpus_diagnosis_0824.md`
- `reports/graphrag_final_attempt_stage1_sourcing_0824.md`
- `docs/decision_env_axis_discarded_0824.md`
- `data/scratch/semantic_step_reuse_check.py` (최종 무비용 검증, gitignore 대상 —
  재현하려면 `C:/backup/dogtraining_0821/scrapper/data/blog_raw_fitpet/`가 있어야 함)
