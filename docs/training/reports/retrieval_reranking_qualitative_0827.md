# Dense anchor 누락 10건 정성 분석 (2026-08-27)

## 범위와 보존 원칙

새 검색 실험, 코드 수정, 재색인, 전체 테스트를 실행하지 않았다. 기존 PGVector dense 결과와 기존 FastAPI raw output만 읽어 분석했다. 동결 평가셋 `data/eval/queries/training_api_eval_v1.jsonl`의 질문·판정·gold anchor는 변경하지 않았다.

문항별 원자료는 retrieval_reranking_qualitative_0827.jsonl에 한 문항씩 누적 저장했고, 집계는 summary JSON에 저장했다. `dense rank/score`와 top-4는 기존 `data/scratch/training_api_eval_v1/raw_outputs/*.json`에서 가져왔다.

검색 누락 10건(Answerable gold가 dense top-4에 없음)과 최종 판정 실패 4건은 서로 다른 지표다. 두 집합은 문항 수준에서 겹칠 수 있으므로 합산하지 않는다. 아래 10건은 검색 누락만 분석하며, `oq0032`는 기존 정책대로 별도 annotation 이슈로 분리했다.

## 문항별 분석

| eval_id | 질문 | gold 문서·청크 / 핵심 문장 | dense gold 순위·점수 / gap | top-1~4 문서·청크와 핵심 문장 | gold 문서 top-4 / 같은 문서 다른 청크 | top-4가 더 가깝게 평가된 이유 | 실패 유형 | 권장 해결 |
|---|---|---|---|---|---|---|---|---|
| oq0001 | 집에서 배변 실수를 반복할 때 배변훈련 순서 | `nias_companion-627620d637f836fa` / `8e4b1ae32845355db84b937d433885a89b771d4157d5124ab264aac70839d1f1`; 패드 수를 줄이고 육각장을 넓혀 정해진 패드로 이동하는 후속 단계 | 34위 / 0.843211; 0.032445 | 1 `nias_companion-a6aebb4414a5a5c5`/`d83e7124...733a` 대소변 가리기 시작 · 2 같은 문서/`c3ff116d...d9b8` 첫날 주의 · 3 `nias_companion-627620d637f836fa`/`802a24f2...3fed` 화장실 공간·육각장 · 4 같은 문서/`6709fdad...73cb` 급식·공간 분리 | 예 / 예 | 상위가 배변·화장실·공간 설정을 직접 반복하고 gold는 여러 단계 중 후속 단계다. | **SIBLING_CHUNK** | parent/sibling 확장과 섹션 순서 보존 재정렬 |
| oq0009 | 손·옷을 무는 입질 줄이기 | `nias_companion-32c5682c7eb39284` / `a7b8951b2256b4a84ffc46c7623af4ec2dfb0d8b9b7406b6fb9ff025359a1a59`; 물건 물기와 이갈이 시기 입질 | 34위 / 0.823669; 0.016194 | 1 같은 문서/`b5a8608c...0e03` 움직이는 물체·장난감 대체 · 2 `nias_companion-64753005533c25a4`/`ef780b8d...4057` 발 만질 때 물기 · 3 `nias_companion-54fd98359c85433b`/`1bbed9a2...7329` 칭찬·보상 · 4 같은 문서/`3d402084...9c0` 물기/물지 않기 반복 | 예 / 예 | 같은 문서의 형제 청크가 실제 상황과 대체 행동·반복 절차를 담는다. | **SIBLING_CHUNK** | 문제·해결 형제 청크 동시 노출 및 해결 절차 국소 우선화 |
| oq0010 | 놀다가 세게 물 때 어떤 행동을 보상할지 | `nias_companion-32c5682c7eb39284` / `5672db41a058772b0985cadd1b9a3222b6c6696f51cf7dfea9727966db4fcf3f`; 손가락·머리카락·바지 자락을 무는 습관 | 10위 / 0.833414; 0.009389 | 1 같은 문서/`c6e20937...421f` ‘놔’ 후 장난감 놓기 칭찬 · 2 같은 문서/`3d402084...9c0` 물기 구분·반복 · 3 `nias_companion-2e4a33d03a8d5bc8`/`578ddf08...9ae5` 일반 보상 원칙 · 4 `nias_companion-64753005533c25a4`/`a6d5ad29...36b8` 앉아 기다리기 보상 | 예 / 예 | 상위 1·2번이 보상 대상과 절차를 직접 말해 gold의 행동 목록보다 해결성이 높다. | **SIBLING_CHUNK** | 보상·대체 행동을 포함한 parent 문맥으로 확장 |
| oq0021 | 사람·소리를 보고 짖기 전 훈련 | `nias_companion-86bf306b74f5cc30` / `5b2f0cb3285cf83a6903ecd5ad7ab14cf55d26388402be7f73b2c50099127631`; 손님 입장 때 안거나 가두면 짖음이 강화될 수 있음 | 14위 / 0.851870; 0.009103 | 1 `nias_companion-be506b63d8f2e843`/`94a4320c...ee9f` 다른 개 짖음·달려듦 통제 · 2 같은 문서/`ce1cc5a5...496a` 아이·공간 분리·목줄 · 3 같은 문서/`03550b33...1b23c` 사람을 보면 호출·기다림·목줄 · 4 `nias_companion-64753005533c25a4`/`eaeba49a...9f48` 초인종·영역성 원인 | 아니오 / 아니오 | 상위가 자극과 즉시 통제 행동을 직접 담고 gold는 손님 입장이라는 실내 예방 상황이다. | **SEMANTIC_RANKING** | 사람/소리·손님 입장·산책 상황을 구분하는 의미 재정렬을 별도 검토 |
| oq0023 | 천둥·큰 소리 적응 훈련 | `nias_companion-bf1602808edafe52` / `0d9cbbcbd1a4d205b0e40c44ab4babb50c4e3cf834ce966768e008cf3896e08d`; 짖음·물기의 일반 원인 | 47위 / 0.818199; 0.028711 | 1 `nias_companion-64753005533c25a4`/`e1443451...59c78` 천둥·비·바람 둔감화 · 2 같은 문서/`5585de19...aa106` 소리 민감성 노출 · 3 같은 문서/`de65a7d0...e0df` 천둥 시 켄넬 휴식 · 4 `nias_companion-2e4a33d03a8d5bc8`/`578ddf08...9ae5` 일반 보상 | 아니오 / 아니오 | top-1~3이 질문을 직접 답하고 gold는 일반 원인만 말한다. | **ANNOTATION_MISMATCH** | 소리 적응 절차를 담은 청크로 gold 재검토; 검색 튜닝 대상에서 분리 |
| oq0024 | 창밖 사람을 보고 짖을 때 관심 전환 | `nias_companion-a482e864fc1f0e5c` / `3ef27d7cd5298e9efc8dc507b10d0349215f4b58b0ea449fd55c153d7486fe58`; 짖음 원인·상황별 대응 | 21위 / 0.846696; 0.012721 | 1 `nias_companion-86bf306b74f5cc30`/`943c8a54...1b90` 손님에게 뛰기·‘옆에/기다려’ · 2 `nias_companion-64753005533c25a4`/`4d61b0a4...6aea` 분리 짖음 · 3 `nias_companion-86bf306b74f5cc30`/`4eddd66d...edfc` 행동 전환·칭찬 · 4 `nias_companion-be506b63d8f2e843`/`03550b33...1b23` 사람 짖음·기다림 | 아니오 / 아니오 | 짖음·전환 절차는 있으나 창밖 자극·실내 창가라는 구분이 없다. | **METADATA_CONTEXT** | 자극·장소·기능 메타데이터 색인/필터 후 재정렬 검토 |
| oq0026 | 낯선 사람·아이에게 달려드는 행동 감소 순서 | `nias_companion-26d52052aa3ff602` / `ddd52b36521cd1d8837867f464c851caae448b1606c6a40066fb157eb255834a`; 사회화·감각·인지 발달 | 43위 / 0.803823; 0.030551 | 1 `nias_companion-be506b63d8f2e843`/`03550b33...1b23` 사람 짖음·호출·목줄 · 2 `nias_companion-64753005533c25a4`/`051b6c48...c5bc` 가족 애착 · 3 같은 문서/`d448b2c2...4481` 따라다님·집착 · 4 `nias_companion-be506b63d8f2e843`/`ce1cc5a5...496a` 아이·사람 달려듦 교정 | 아니오 / 아니오 | 상위 4번은 사람·아이·달려듦을 직접 담고 gold는 발달 개념 설명이다. | **METADATA_CONTEXT** | 사람/아이·달려듦·거리·사회화 시기 메타데이터 분리 |
| oq0027 | 새 장소에서 얼어붙을 때 적응 거리 | `nias_companion-2e4a33d03a8d5bc8` / `465471a8f83fb40d6813d76e1a9a09c9be97a3fb83688f93f490bedce3f85031`; 어린 시기부터 다양한 자극 경험 | 10위 / 0.836650; 0.004849 | 1 `nias_companion-64753005533c25a4`/`d5d1ea03...6da8` 새 장소·공간 적응 · 2 같은 문서/`f7ad9b28...40064` 장소 변경·장난감·간식 · 3 `nias_companion-26d52052aa3ff602`/`ddd52b36...834a` 사회화 과정 · 4 `nias_companion-e7cfa3e48368e6ce`/`a20ff525...04c5` 2~14주 자극 노출 | 아니오 / 아니오 | gold는 질문의 ‘어느 정도 거리’를 제공하지 않고, 상위가 오히려 새 장소 적응 단서를 가진다. | **ANNOTATION_MISMATCH** | 거리 기준을 담은 answer-bearing 청크로 gold 재검토 |
| oq0028 | 사람에게 달려갈 때 네 발 유지 가르치기 | `nias_companion-e7cfa3e48368e6ce` / `a20ff52537b1d32086317abbe2841916c07dcdd69c67941169cc3a9e99b604c5`; 사회화 정의·3~14주 자극 노출 | 28위 / 0.826169; 0.024227 | 1 `nias_companion-64753005533c25a4`/`ef780b8d...4057` 달려듦 통제 · 2 `nias_companion-86bf306b74f5cc30`/`4eddd66d...edfc` 행동 전환·칭찬 · 3 `nias_companion-be506b63d8f2e843`/`ce1cc5a5...496a` 사람·아이 달려듦 교정 · 4 `nias_companion-54fd98359c85433b`/`1bbed9a2...7329` 칭찬·보상 | 아니오 / 아니오 | gold는 네 발 유지 절차를 답하지 않고, 상위가 달려듦·통제·칭찬을 직접 담는다. | **ANNOTATION_MISMATCH** | 네 발 유지 절차·보상 청크로 gold 재검토 |
| oq0031 | 이름을 불러도 반응하지 않을 때 짧은 연습 | `nias_companion-54fd98359c85433b` / `1bbed9a285e1689de933ca03534287ce032e07ccb0e8d40fe8eac8c1f0f47329`; 칭찬·보상 중심 교육 기본 | 9위 / 0.844505; 0.003061 | 1 `nias_companion-64753005533c25a4`/`e1443451...59c78` 소리 둔감화 · 2 같은 문서/`5585de19...aa106` 소리 민감성 · 3 `nias_companion-be506b63d8f2e843`/`ce1cc5a5...496a` 사람·아이 짖음 교정 · 4 `nias_companion-86bf306b74f5cc30`/`943c8a54...1b90` 손님·‘옆에/기다려’ | 아니오 / 아니오 | 상위는 다른 행동 교정 사례이고 이름·호출·짧은 연습 단서가 없다. gold의 보상 원칙이 일반적이라 행동 사례에 밀렸다. | **SEMANTIC_RANKING** | 이름 반응·호출·기초 복종 의도를 구분하는 의미 재정렬 검토 |

`...`로 줄인 top-4 chunk ID는 표 가독성을 위한 표시이며, 각 ID의 전체 64자리 값과 각 핵심 문장은 JSONL 원자료에 보존했다.

## 유형별 집계

문항별 primary 유형 하나만 세었다. 실제 원인은 중첩될 수 있다.

| primary 유형 | 건수 | 해당 문항 |
|---|---:|---|
| SIBLING_CHUNK | 3 | oq0001, oq0009, oq0010 |
| SEMANTIC_RANKING | 2 | oq0021, oq0031 |
| ANNOTATION_MISMATCH | 3 | oq0023, oq0027, oq0028 |
| METADATA_CONTEXT | 2 | oq0024, oq0026 |
| CHUNK_BOUNDARY | 0 | - |
| NEAR_DUPLICATE_DISTRACTOR | 0 | - |
| 합계 | 10 | 검색 누락 10건 |

`oq0032`는 이 표와 집계에 넣지 않고 기존 annotation 이슈로 유지한다. 검색 누락 10건과 최종 판정 실패 4건도 별도 지표이며 단순 합산하지 않는다.

## 다음 분기 결론

1. **parent/sibling 청크 확장**: 3건에서 같은 문서의 해결 청크가 이미 top-4에 있었다. 되돌릴 수 있고 가장 직접적인 우선순위다.
2. **평가셋 annotation 재검토**: 3건은 gold가 질문의 핵심 요구를 답하지 못하고 상위 청크가 더 정답에 가깝다. oq0032와 분리된 추가 이슈로 기록하되, 이 anchor에 맞춘 검색 튜닝은 하지 않는다.
3. **메타데이터 기반 검색 보강**: 2건은 자극·장소·대상·발달 시기 구분이 핵심이다. 메타데이터 계약을 먼저 정의한 뒤 검증한다.
4. **semantic cross-encoder reranker**: 2건에서 후보 의미 구분의 여지가 있다. 기존 BM25/RRF/lexical rerank 결과만으로 cross-encoder까지 기각할 수 없으므로 향후 별도 실험 후보로 남긴다.
5. **청크 계약 수정**: 이번 10건에서 CHUNK_BOUNDARY primary가 0건이므로 현재 근거만으로 우선 추진하지 않는다.

기존 lexical 계열 실험(BM25/RRF/top-20·top-50 lexical rerank)은 개선을 보이지 않았으므로 결론 표기는 **`NO_LEXICAL_RERANK_ADOPTION`**으로 한정한다. 이는 cross-encoder 후보 전체를 평가·기각했다는 뜻이 아니다.

## Annotation 계약 검토: v1.1 candidate

동결 v1은 수정하지 않았다. 아래는 원문과 기존 gold를 다시 읽은 **제안**이며 자동 검토 결과를 `HUMAN_APPROVED`로 표시하지 않는다.

| 문항 | 현재 판정·gold anchor | gold 핵심 문장 | 질문 전체 답변 가능? | 문제 유형 | 권고 | 근거 / 대체 anchor |
|---|---|---|---|---|---|---|
| oq0023 | answerable / `nias_companion-bf1602808edafe52` · `0d9cbbcbd1a4d205b0e40c44ab4babb50c4e3cf834ce966768e008cf3896e08d` | 짖음·물기의 일반 원인과 문제점 | 아니오 | gold가 소리 적응 절차를 제공하지 않음 | **REANCHOR** | 실제 질문을 답하는 `nias_companion-64753005533c25a4` · `e14434513f77433b632ffdd39ed30197311bfb055e0092e7dcd8fd895f359c78`(천둥·큰 소리 둔감화) 후보 |
| oq0027 | answerable / `nias_companion-2e4a33d03a8d5bc8` · `465471a8f83fb40d6813d76e1a9a09c9be97a3fb83688f93f490bedce3f85031` | 어린 시기부터 다양한 자극을 경험시키는 사회화 일반론 | 아니오 | ‘어느 정도 거리’ 수치·절차 부재 | **REANCHOR** | 현재 top-4 중 새 장소 적응을 다루는 `nias_companion-64753005533c25a4` · `d5d1ea03569a330dd547c9c7fe277e93aa55f393fee98dcc022c1c7652a86da8` 후보. 단, 거리 기준 자체가 없으면 EXCLUDE도 검토 |
| oq0028 | answerable / `nias_companion-e7cfa3e48368e6ce` · `a20ff52537b1d32086317abbe2841916c07dcdd69c67941169cc3a9e99b604c5` | 사회화 정의와 3~14주 자극 노출 | 아니오 | 네 발 유지·보상 절차 부재 | **REANCHOR** | 현재 top-4 중 달려듦 통제를 다루는 `nias_companion-64753005533c25a4` · `ef780b8d5c58e8253c4b9dc0953873032f32277154bb87cbf1b4409bdb874057` 후보 |
| oq0032 | answerable / `nias_companion-2e4a33d03a8d5bc8` · `578ddf082bf8bff26b6c8b15022e87cf428b94d27209d334366678e1bd799ae5` | 간식·먹이 보상, 놀이·칭찬 마무리를 설명하지만 ‘처음 몇 초’는 없음 | 아니오 | 질문의 시간 기준이 gold에 없음(기존 annotation 이슈) | **RELABEL** 제안 | v1은 보존. 별도 후보에서 answerable을 재검토하고, 초 단위를 실제로 담은 대체 anchor가 확인될 때만 REANCHOR |

권고는 `v1.1_candidate` 제안일 뿐이며 동결 파일에는 반영하지 않았다. `oq0027`과 `oq0028`의 top-4 후보도 질문의 전체 요구를 완전히 보장하는지 추가 사람 검수가 필요하므로 자동 확정하지 않는다.

후보 레코드는 training_api_eval_v1.1_candidate.jsonl에 별도 저장했다. `candidate_status`는 모두 `PROPOSED_NOT_HUMAN_APPROVED`이며 `HUMAN_APPROVED`로 승격하지 않았다.

## Sibling 관계 실측과 구현 보류

현재 로컬 PGVector의 serving 14문서 209청크를 직접 확인했다. `rag_chunks.metadata`에는 `heading_path`, `kinds`만 존재했고 `qa_id`, `parent_id`, `section_id`는 각각 0청크·0문서였다. 따라서 oq0001·oq0009·oq0010의 “같은 문서”는 안정적인 sibling 계약이 아니다. 파일 행 순서, `chunk_index` 인접성, 문서 전체 확장은 사용하지 않았다.

결론은 **`BLOCKED_NO_STABLE_RELATION`**이며 sibling 확장을 구현하지 않았다. ingestion 단계에서 다음 최소 필드를 새로 내보내고 검증해야 한다.

- `relation_group_id`: 원문 구조에서 생성한 안정적인 그룹 ID
- `parent_chunk_id`: nullable parent 청크 ID
- `section_id`: 안정적인 섹션 정체성
- `segment_role`: 질문·전문가 답변·일반 본문 구분
- nullable·출처(provenance) 포함 `training_metadata`

마이그레이션은 구조가 증명되는 행만 backfill하고 나머지는 null로 남기며, orphan/group coverage 검사 후에만 확장을 켜는 범위다. 상세 측정값과 범위는 annotation_sibling_metadata_contract_0827.json에 저장했다.

## Metadata coverage 실측

oq0024·oq0026에 필요한 연령·행동·상황·자극·대상 필드는 14개 서비스 문서 209청크에서 모두 0건(문서 coverage 0%, 미분류 100%)이었다. 현재 `heading_path`와 `kinds`는 209청크(14문서) 모두에 있으나 훈련 의미 메타데이터가 아니다. 따라서 metadata filter/search 보강은 구현하지 않았다.

## 상태

이번 작업에서 프로덕션 코드·평가셋 v1·색인은 변경하지 않았다. sibling 확장과 metadata filter는 계약 부재로 보류, annotation은 `v1.1_candidate` 제안만 작성했다. 기존 API 상태 **CONDITIONAL**을 변경할 근거는 없으며, 다음 단계는 (1) 원문 구조 기반 relation 필드 설계·재인제스트, (2) 사람 검수 annotation 후보 확정, (3) 메타데이터 provenance/coverage 확보, (4) 그 후 seed/expanded evidence를 분리한 별도 실험이다.

## 검증 실행

- 관련 회귀: `uv run python -m unittest tests.test_qa_sibling_expansion tests.test_training_eval_regressions` → **18 passed**
- 전체 테스트: `uv run python -m unittest discover -s tests -p "test_*.py"` → **372 passed, 8 skipped, 0 failed**
- 이번 실행에서 테스트를 위해 코드·평가셋·색인을 수정하지 않았다.
