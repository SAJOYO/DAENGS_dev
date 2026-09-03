# 훈련 RAG 답변 가능 범위와 근거 소스 점검 — 2026-09-03

카드: #168 `research/training-rag-knowledge-coverage-audit`. 선행: #161(직접 근거 규칙 v3) · #165(페이지 번호 청크 제외) ·
#167(손 입질 근거 없음). 이 문서는 **사용자 질문(평가 데이터)** 과 **검수 소스(지식)** 를 구분해서, 흔한 훈련 질문마다
"지금 직접 근거로 답할 수 있는가"를 답한다. 소스 인제스트·서빙 변경·검색 구조 변경은 하지 않았다.

## 1. 요약

- 훈련 intent 24개를 감사했다. **지금 답 가능 11 · 직접 근거는 있는데 미서빙 2 · 검수 소스 확보 필요 6 ·
  근거는 있으나 안전성 검수 필요 4 · 훈련 범위 밖 1.**
- 공백의 종류를 구분하는 것이 핵심 산출물이다:
  - **KNOWLEDGE GAP**(검수 근거 자체가 없음): 손 입질 · 분리불안/혼자 두기 · 이리와(리콜) · 사람에게 뛰어오름 · 낯선 사람 공포 · 놀이 과흥분.
  - **SERVING GAP**(같은 출처의 직접 근거가 적재돼 있으나 서빙 allow-list 밖): 마킹·실외 배변만 하는 개 · 편식/간식 의존.
  - **RETRIEVAL GAP**(근거가 서빙 중인데 자연어 질의가 못 찾음): **이번 감사에서 증명된 것 없음.** 손 입질은 검색 문제가 아니라 지식 문제였다(#167).
  - **QUALITY GAP**(근거는 서빙 중이나 검수 없이 믿으면 안 됨): 발·몸 만지기 거부 · 자원 지키기 · 소리 공포 · 두 마리 서열다툼 —
    서열 프레임·제압·쵸크체인·강한 야단이 주된 처방이다.
- 우선순위(리포트 내부 등급, Project Priority 아님): **P0 COVERAGE** 손 입질 · 분리불안 · 발/몸 만지기 거부(품질).
  **P1** 리콜 · 뛰어오름 · 낯선 사람 공포 · 자원 지키기(품질) · 소리 공포(품질). **P2** 과흥분 · 두 마리 다툼(품질) · 마킹/편식 서빙.
- 검색 아키텍처(하이브리드·리랭커·임베딩 교체)는 **일부러 손대지 않았다.** 근거가 없는 intent 는 어떤 검색기도 답을 만들 수 없다.

## 2. 방법과 범위

- 지식 인벤토리: 적재 316문서(`chunks_structure_v1`, 자격 청크 6,433). 이 중 **사람이 검수한 것은 서빙 14문서뿐**이다
  (`serving_corpus_v1.json`). 나머지 302문서는 "적재됨"이지 "검수됨"이 아니다 — 같은 출처(국립축산과학원)라도 서빙 전 검수가 필요하다.
- intent 도출: 동결 평가 `training_api_eval_v1`(25행)·`owner_questions_training`(36행)의 `query_type`
  (potty_training · leash_walking · bite_inhibition · kennel_crate · separation_training · barking_training · socialization ·
  basic_obedience · refuse_boundary · missing) + 서빙 FAQ 항목 제목 13개 + 미서빙 NIAS 훈련 글 제목 + `curriculum_axes.md` 8축.
- 근거 판정: intent 마다 대표 질의 1개로 E5 의미 검색(316문서 전체 top-5, 로컬 재현 — 서버 pgvector 아님) + 서빙 FAQ 항목 본문 확인.
  분류는 #161 경계 그대로 — **DIRECT**(그 문제를 직접 다루고 쓸 수 있는 지도가 있음) / **SUPPORTING**(맥락은 되지만 단독 답 불가) /
  **ADJACENT**(다른 문제) / **UNSUITABLE**(안전·권위·범위·정책·아티팩트 문제). 답을 늘리려고 ADJACENT 를 DIRECT 로 올리지 않았다.
- 소스 본문은 짧은 진단 요약만 적었다. 외부 페이지는 **가져오지 않았다**(fetch 없음) — 제목·안정성은 사람 검수 때 확인한다.

문서 ID 는 `nias_companion-<16hex>` 의 뒤 8자리로 줄여 쓴다. 서빙 문서: `aa3ff602` 사회화 과정 · `3a8d5bc8` 사회화 주의사항 ·
`7eb39284` FAQ(물어뜯기·장난감 집착) · `af87a841` 실내에서 밖으로 · `9c85433b` 칭찬과 야단 · `37f836fa` 화장실 대소변 교육 ·
`533c25a4` FAQ 10항목(이사 후 짖음 · 저밖에 몰라요 · 초인종 · 소리 민감 · 먹이 줄 때 짖음 · 만지면 으르렁/물어요 · 침대·쇼파 지키기 ·
발 만지면 물려고 · 저만 따라다님 · 두 마리 서열다툼) · `74f5cc30` FAQ(손님 짖음) · `fc1f0e5c` 짖는 원인 · `14a5a5c5` 대소변가리기 ·
`d8f2e843` 실외 산책의 시작 · `8edafe52` 짖어요! 물어요! · `8368e6ce` 반려견 예절 · `184b7763` 운동과 산책.

## 3. intent 커버리지 매트릭스

verdict: **AN** ANSWERABLE_NOW · **DNS** DIRECT_NOT_SERVING · **SAR** SOURCE_ACQUISITION_REQUIRED · **SQR** SOURCE_QUALITY_REVIEW_REQUIRED · **OOS** OUT_OF_TRAINING_SCOPE.
D/S = DIRECT / SUPPORTING 소스 수(문서 단위).

| # | intent_id | 표시명 | 예시 질문 | D | S | 최선 소스 | 서빙 | verdict | 품질 메모 | 다음 행동 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | hand_mouthing | 손 입질·놀이 물기 | 강아지가 자꾸 손을 물어요 | 0 | 1 | `7eb39284` #1~#4 (증상에 손가락 물기 언급, 대처는 물건·공간) | 서빙 | **SAR** | 인접 근거가 발 만지기·서열 FAQ 라 잘못 답하면 위험 | P0 소스 확보 (§7) |
| 2 | destructive_chewing | 가구·벽지 물어뜯기 | 집안 물건을 다 물어뜯어요 | 1 | 0 | `7eb39284` #1~#4 | 서빙 | AN | "강하게 야단" 한 줄 | 유지 |
| 3 | resource_guarding | 먹이·장난감 지키기 | 장난감 뺏으려 하면 으르렁거려요 | 2 | 0 | `7eb39284` #5~#6, `533c25a4` #11~#12 | 서빙 | **SQR** | 쵸크체인 착용 후 '놔' 교육, 서열 원인론 | P1 안전성 검수 |
| 4 | barking_doorbell_visitors | 초인종·손님 짖음 | 초인종 소리에 너무 짖어요 | 2 | 1 | `533c25a4` #6, `74f5cc30` #10~#13 | 서빙 | AN | 손님에게 달려들면 '리드줄로 제압' | 유지, 문구 검수 권장 |
| 5 | barking_at_people_outside | 사람·창밖 보고 짖음 | 산책 중 사람 보고 짖어요 | 2 | 1 | `d8f2e843` #2~#4, `fc1f0e5c` | 서빙 | AN | — | 유지 |
| 6 | noise_sensitivity | 천둥·소리 공포 | 천둥 소리를 무서워해요 | 1 | 0 | `533c25a4` #7~#8 | 서빙 | **SQR** | "소리를 들려주면서 교정을 원칙" — 단계 없는 노출(flooding) 여지 | P1 검수 |
| 7 | separation_related | 분리불안·혼자 두기 | 혼자 두면 짖고 물건을 부숴요 | 0 | 2 | `533c25a4` #3/#9 (새 환경 불안→크레이트), `7eb39284` #2 | 서빙 | **SAR** | 동결 세트도 separation 4문항 전부 REWRITE(미승인) — 근거 없음을 이미 알고 있었음. 인접 근거가 "크레이트에 가두시기" | P0 소스 확보 |
| 8 | potty_training_indoor | 배변 패드·화장실 교육 | 패드에 안 하고 아무데나 싸요 | 2 | 0 | `14a5a5c5`, `37f836fa` | 서빙 | AN | — | 유지 |
| 9 | marking_outdoor_only | 마킹·실외에서만 배변 | 여기저기 소변을 봐요 / 밖에서만 싸요 | 2 | 0 | `ad676fc0` 여기저기 소변, `18776fc4` 실내 절대 안 봄 | **미서빙** | **DNS** | 같은 NIAS 출처, 미검수 | P2 검수 후 allow-list |
| 10 | leash_pulling_walk_manners | 줄 당김·산책 예절 | 산책할 때 줄을 너무 당겨요 | 3 | 1 | `d8f2e843`, `184b7763`, `af87a841` (+미서빙 `70f63010` 산책예절 개요) | 서빙 | AN | — | 유지 |
| 11 | jumping_on_people | 사람에게 뛰어오름 | 사람만 보면 뛰어올라요 | 0 | 2 | `74f5cc30` #11 (손님에게 달려들며 짖음), `d8f2e843` #4 | 서빙 | **SAR** | 인접 근거는 짖음 맥락 + 제압 | P1 소스 확보. 동결 oq0028 gold 재검토 |
| 12 | crate_training | 켄넬·크레이트 적응 | 켄넬에 안 들어가려고 해요 | 1 | 1 | `533c25a4` #9 (크레이트 교육·크기), `7eb39284` #2 | 서빙 | AN | "가두시기 바랍니다" 어조, 단계적 적응 서술 없음 | 유지, 보강 후보 |
| 13 | socialization_dogs_places | 사회화·새 장소 | 다른 강아지를 무서워해요 | 2 | 1 | `aa3ff602`, `3a8d5bc8` (+미서빙 `e7cfa3e4` 사회화 정의) | 서빙 | AN | — | 유지 |
| 14 | fear_of_strangers | 낯선 사람 공포·숨기 | 낯선 사람 보면 짖고 숨어요 | 0 | 2 | `533c25a4` #1 (이사 후 짖음), 사회화 문서 | 서빙 | **SAR** | 인접 근거는 애착·이사 FAQ | P1 소스 확보 |
| 15 | recall | 이리와·호출 | 불러도 안 와요 | 0 | 1 | `b8afc793`(미서빙) 반복 교육 원칙 | — | **SAR** | 동결 oq0030 REWRITE(미승인) | P1 소스 확보 |
| 16 | basic_cues_sit_stay_name | 앉아·기다려·이름 반응 | 기다려를 못 해요 | 2 | 1 | `9c85433b` 칭찬과 야단, `533c25a4` #11, `8368e6ce` | 서빙 | AN | 단계·초 단위 서술은 얕음 | 유지 |
| 17 | food_begging_barking | 밥·간식 줄 때 짖고 달려듦 | 밥 먹을 때 짖고 달려들어요 | 1 | 0 | `533c25a4` #10~#11 | 서빙 | AN | 리드줄 제재 언급 | 유지 |
| 18 | picky_eating_treats | 편식·간식만 찾음·사료 거부 | 간식만 먹고 사료를 안 먹어요 | 3 | 0 | `ecddc87d` 간식만 찾을 때, `5723cd0f` 사료 올바르게, `06710105` 식생활습관 | **미서빙** | **DNS** | 미검수. 영양(의료) 경계와 겹침 주의 | P2 검수 후 allow-list |
| 19 | handling_sensitivity | 발·몸 만지기 거부, 발톱 | 발을 만지면 으르렁거려요 | 1 | 1 | `533c25a4` #16~#17, #11~#12 | 서빙 | **SQR** | 서열 가리기·배 보이기·줄 당겨 제압 3~5회·쵸크체인 | **P0 안전성 검수** (물림 위험 맥락) |
| 20 | attachment_one_person | 한 사람만 따라다님·애착 | 저만 계속 따라다녀요 | 1 | 0 | `533c25a4` #4~#5, #18~#19 | 서빙 | AN | "냉정하게 대하기" 처방 | 유지 |
| 21 | multi_dog_fighting | 두 마리 서열다툼 | 두 마리가 너무 싸워요 | 1 | 0 | `533c25a4` #20~#21 | 서빙 | **SQR** | 서로 으르렁거리게 만든 뒤 강하게 야단 | P2 검수 |
| 22 | overexcitement_play | 놀이 과흥분 | 놀다가 너무 흥분해요 | 0 | 1 | `7eb39284` #2 (놀이 시간 정하기) | 서빙 | **SAR** | — | P2 |
| 23 | public_manners_elevator | 엘리베이터·공공장소 펫티켓 | 엘리베이터 탈 때 예절 | 1 | 1 | `8368e6ce` (엘리베이터 펫티켓), 미서빙 `09109759` | 서빙 | AN | — | 유지 |
| 24 | severe_bite_or_injury | 심한 공격·물려서 다침 | 강아지가 물어서 피가 났어요 | 0 | 0 | `kdca_health` 2건(교상 응급처치·광견병, 미서빙) | — | **OOS** | 의료 가드레일 영역. 훈련 답 금지 | 의료/안전 카드에서 문구 |

집계: AN 11 · DNS 2 · SAR 6 · SQR 4 · OOS 1 = 24.

## 4. 고우선 공백

| 등급 | intent | 근거(A 사용자 빈도 · B 제품 중요도 · C 오답 위험 · D 위험한 인접 근거 · E 소스 구하기 쉬움) |
| --- | --- | --- |
| **P0 COVERAGE** | hand_mouthing | A 높음(#167 재현) · B 핵심 · C 중 · **D 높음**(발 만지기·서열 FAQ 가 인접) · E 쉬움(영문 기관 다수) |
| **P0 COVERAGE** | separation_related | A 매우 높음 · B 핵심 · C 중~높음(가두기 처방) · D 높음 · E 중 |
| **P0 QUALITY** | handling_sensitivity | 서빙 중인 처방이 제압·쵸크체인. 물림 위험 맥락에서 그대로 인용되면 안전 문제 |
| P1 | recall · jumping_on_people · fear_of_strangers | A 높음 · D 중 · E 쉬움 |
| P1 | resource_guarding(품질) · noise_sensitivity(품질) | 서빙 중, 처방이 현대 기준과 충돌 |
| P2 | overexcitement_play · multi_dog_fighting(품질) · marking_outdoor_only(서빙) · picky_eating_treats(서빙) | — |

## 5. 질의 커버리지 제안 (평가 데이터, 근거 아님)

형식은 기존 `gate-pass-control-v1`(`query_id` · `split` · `review_status` · `question` · `query_type` · `coverage` · `expected_outcome`)
을 따른다. 픽스처 파일은 연구 저장소 `data/eval/queries/` 에 있어 여기서는 표만 둔다. `expected_outcome` 은 **지금 코퍼스 기준**.

| id | intent | 유형 | 질문 | 지금 기대 |
| --- | --- | --- | --- | --- |
| kc01 | hand_mouthing | formal | 강아지의 입질을 줄이려면 어떻게 훈련해야 하나요? | UNCERTAIN |
| kc02 | hand_mouthing | casual | 강아지가 자꾸 손을 물어요 | UNCERTAIN |
| kc03 | hand_mouthing | short | 손 물어요 | UNCERTAIN |
| kc04 | hand_mouthing | contextual | 놀다가 흥분하면 자꾸 손을 깨물어요 | UNCERTAIN |
| kc05 | separation_related | formal | 혼자 있는 시간을 늘리는 분리 훈련은 어떤 순서로 하나요? | UNCERTAIN |
| kc06 | separation_related | casual | 혼자 두면 계속 짖어요 | UNCERTAIN |
| kc07 | separation_related | contextual | 외출 준비만 해도 불안해해요 | UNCERTAIN |
| kc08 | handling_sensitivity | casual | 발톱 깎으려고 발을 만지면 싫어해요 | ANSWER(현재) — 품질 검수 대상 |
| kc09 | handling_sensitivity | contextual | 쓰다듬으면 으르렁거려요 | ANSWER(현재) — 품질 검수 대상 |
| kc10 | recall | casual | 불러도 안 와요 | UNCERTAIN |
| kc11 | recall | formal | 이리와 훈련은 어떻게 시작하나요? | UNCERTAIN |
| kc12 | jumping_on_people | casual | 사람한테 자꾸 뛰어올라요 | UNCERTAIN |
| kc13 | jumping_on_people | contextual | 손님 오면 달려들어서 뛰어올라요 | 짖음 FAQ 인접 — 뛰어오름으로 확장 금지 |
| kc14 | fear_of_strangers | casual | 낯선 사람 보면 짖고 숨어요 | UNCERTAIN |
| kc15 | resource_guarding | casual | 장난감 뺏으려 하면 으르렁거려요 | ANSWER(현재) — 품질 검수 대상 |
| kc16 | destructive_chewing | casual | 옷을 물어뜯어요 | ANSWER |
| kc17 | potty_training_indoor | casual | 패드에 안 하고 아무데나 싸요 | ANSWER |
| kc18 | leash_pulling_walk_manners | short | 줄을 너무 당겨요 | ANSWER |
| kc19 | negative | — | 사람만 계속 따라다녀요 | 애착 FAQ, 입질·분리불안으로 취급 금지 |
| kc20 | boundary | — | 강아지가 물어서 피가 났어요 | 의료/안전 경계 |

20문항. 손 입질 4문항은 #167 의 17문항 세트와 겹치며, 그 세트를 대체하지 않는다(합치는 것은 카드 B 의 일).

## 6. 후보 외부 소스 (인제스트 없음 · 페이지 미조회 · 최종 승인은 사람)

| 조직 | 문서(제목은 검수 때 확인) | 유형 | 언어 | 커버 intent | 직접성 | 권위 | 안전 정렬 | 인용·출처 적합성 | 저작권·재사용 | 권고 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 농식품부 동물사랑배움터 (apms.epis.or.kr) | 사회화·공공예절 교육 과정 중 물기 예방·분리불안 단원 | 정부 온라인 교육 | ko | 1, 7, 13, 14 | 단원 내용 미확인 | 높음(정부) | 미확인 | 출처 명확 | 교육 영상·교재 재사용 조건 미확인 | NEEDS_COPYRIGHT_REVIEW |
| 농식품부/EPIS 반려동물행동지도사 표준교재·출제범위 | 문제행동(입질·분리불안·공포) 단원 | 국가자격 교재 | ko | 1, 7, 14, 15 | 미확인 | 매우 높음(`SOURCES.md`: "사실상 국가가 정한 정답지") | 미확인 | 출처 명확 | 교재 저작권 미확인 | NEEDS_COPYRIGHT_REVIEW |
| AVSAB (미국수의행동학회) | Position Statement on Humane Dog Training (2021) · Position Statement on the Use of Dominance Theory · Puppy Socialization | 학회 공식 입장 | en | 8절 품질 기준, 13 | "하지 말 것"에 직접 | 매우 높음 | 기준 그 자체 | PDF 출처 명확 | 학회 저작물, 인용 범위 검토 | APPROVE_FOR_HUMAN_SOURCE_REVIEW |
| ASPCA | Dog Care — Mouthing, Nipping and Play Biting in Adult Dogs / 강아지 입질 안내 | 동물복지단체 안내 | en | **1** | 직접(손·피부 입질, 놀이 중단·전환, 하지 말 것) | 높음 | 처벌 반대 | 출처 명확 | © ASPCA, 전문 재게시 불가 — 인용·요약 방식 필요 | APPROVE_FOR_HUMAN_SOURCE_REVIEW (저작권 검토 동반) |
| RSPCA (영국) | Puppy biting / Dog behaviour advice | 동물복지단체 | en | 1, 7 | 직접(입질), 분리불안 안내 있음 | 높음 | 보상 기반 | 출처 명확 | © RSPCA | NEEDS_COPYRIGHT_REVIEW |
| AKC | How to stop puppy biting / Teaching recall | 켄넬클럽 | en | 1, 15 | 직접 | 중(전문가 기고) | 대체로 보상 기반, 글마다 편차 | 출처 명확 | © AKC | NEEDS_COPYRIGHT_REVIEW |
| GOV.UK | Code of practice for the welfare of dogs | 정부 실무 규범 | en | 복지 일반 | SUPPORTING (행동 지도 얕음) | 높음 | — | 이미 적재(`govuk_dog_welfare`) | OGL v3 — 재사용 자유 | REJECT_ADJACENT (입질·분리불안 직접 아님) |
| 학술 (J. Vet. Behav., Appl. Anim. Behav. Sci.) | 분리 관련 문제·입질 관리 문헌 | 동료심사 | en | 7, 1 | 문헌마다 다름 | 매우 높음 | — | 이미 `europe_pmc` 130편 적재됐으나 지도 내용 있는 편 미선별 | 오픈액세스 라이선스별 | NEEDS_COPYRIGHT_REVIEW (선별 필요) |
| 유튜브 훈련 채널 · 블로그 · 지식iN | — | 사례·커뮤니티 | ko | — | — | 낮음/편차 | — | `source_blocklist.md` YouTube 차단, 커뮤니티는 검수 근거 아님 | — | REJECT_POLICY / REJECT_LOW_AUTHORITY |

영문 소스를 쓰면 **답은 한국어**라 번역·인용 표시 방식이 별도 결정이다(#167 에서도 지적). 영어 산문은 #165 이후 근거 자격이 있다.

## 7. 손 입질 상세 판정

- 기존 근거(#167): DIRECT 0. 서빙 중인 물어뜯기 FAQ 는 SUPPORTING — 증상 목록에 손가락·바지 자락 물기가 있지만 처방은
  물건·공간 관리다. 이것을 DIRECT 로 올리면 #161 이 막은 인접 확장을 데이터에서 되살린다. 유지.
- 직접 소스 요건: ① 사람 손·피부를 무는 놀이 입질 자체 ② 놀이/맥락 구분 ③ 구체적 관리·훈련 단계 ④ 전환·강화(장난감 전환, 조용해지면 보상)
  ⑤ 하지 말 것(손으로 밀치기·체벌·입 잡기) ⑥ 월령 구분(이갈이기 자견 vs 성견) ⑦ 심하거나 상처 나는 물기·공격성 의심 시 escalation.
- 최선 후보: **ASPCA 입질 안내**(en, 직접, 요건 ①~⑦ 대부분 커버 예상) + **AVSAB Humane Dog Training**(⑤의 기준) +
  **행동지도사 표준교재 물기 예방 단원**(ko, 내용 미확인 — 있으면 한국어 1순위). RSPCA·AKC 는 보조.
- 거부: 발 만지기 FAQ(핸들링) · 자원 지키기 FAQ · 짖어요!물어요!(서열 원인론) · kdca 교상 처치(의료) · 보듬TV 영상(정책).
- 소스가 들어오면 기대: kc01~kc04 top-4 에 새 근거 진입, "고기를 너무 좋아해서" 는 여전히 UNCERTAIN. 그래도 안 되면 그때 검색 카드.

## 8. 기존 소스 품질 충돌 (삭제하지 않음 · 기록만)

서빙 중인 NIAS FAQ 의 처방이 AVSAB 인도적 훈련·지배이론 입장과 충돌하는 자리:

| 문서·청크 | 내용 요약 | 충돌 유형 |
| --- | --- | --- |
| `533c25a4` #16~#17 발 만지기 | 서열 먼저 가리기, 인위적으로 배 보이기, 으르렁거리면 줄 당겨 제압 3~5회, 쵸크체인 | 지배이론 · 강한 혐오 자극 · 물림 위험 맥락 |
| `533c25a4` #12, #20~#21 | "사람을 물게 되는 것은 서열정리가 안 돼서", 두 마리를 서로 으르렁거리게 만든 뒤 강하게 야단 | 지배이론 · 의도적 갈등 유발 |
| `7eb39284` #6 장난감 집착 | 쵸크체인 걸고 '놔' 교육 | 혐오 도구 |
| `74f5cc30` #11 손님 짖음 | '안돼' 명령과 리드줄로 제압 | 물리적 제압 |
| `533c25a4` #8 소리 민감 | 소리를 들려주면서 교정을 원칙으로 | 단계 없는 노출(flooding) 여지 |
| `533c25a4` #3, #9 | 불안하면 "크레이트 안에 가두시기" | 분리불안 맥락에서 인용되면 역효과 |
| `7eb39284` #3, `9c85433b` | "강하게 야단" 을 기본 교정으로 | 처벌 중심 |
| 미서빙 `9a72286c` 양육상식 | 첫 절이 "서열" | 서빙 검토 시 주의 |

새 소스(AVSAB·ASPCA 등)가 들어오면 같은 질문에 두 처방이 나란히 인용될 수 있다. 이 카드는 서빙 allow-list 를 바꾸지 않는다 —
충돌 해소는 카드 E 의 사람 결정.

## 9. 권고 후속 카드

- **A. `data: 훈련 RAG P0 근거 소스 검수 및 적재`** — hand_mouthing · separation_related. §6 후보 중 사람이 저작권·출처를 승인한 것만,
  기존 인제스트 경로(`pgvector_ingest`)로. 영문이면 인용·번역 방식 결정 포함.
- **B. `test: 훈련 RAG 실사용 질의 커버리지 세트 확정`** — §5 20문항 + #167 17문항을 `gate-pass-control-v1` 형식으로 합쳐 연구 저장소
  픽스처에 넣고, 동결 세트 oq0009/oq0010/oq0028 gold 와 separation·recall REWRITE 행을 재검토.
- **C. `fix: 검수된 근거의 serving allow-list 반영`** — DNS 2건(`ad676fc0` `18776fc4` 마킹/실외 배변 · `ecddc87d` `5723cd0f` `06710105` 편식)
  을 사람이 검수한 뒤 `serving_corpus_v1.json` 에 추가. 편식은 영양(의료) 경계 확인.
- **E. `research: 서빙 NIAS FAQ 안전성 검수`** — §8 표. 삭제·유지·주석 중 사람 결정. P0 는 발 만지기 FAQ.
- **D. `research: DIRECT 근거가 있는데도 검색되지 않는 intent 검색 구조 비교`** — **A·C 이후에만.** 지금은 그런 intent 가 증명되지 않았다.

## 10. 명시적 비결정

- 아무 소스도 인제스트·다운로드하지 않았다. 외부 페이지를 조회하지도 않았다.
- 서빙 allow-list · 청킹 · 임베딩 · top_k · 임계값 · 프롬프트 v3 · 평가 gold · 의료 가드레일 전부 그대로.
- 기존 NIAS 소스를 삭제하지 않았다. 충돌은 기록만.
- 316문서 중 검수된 것은 서빙 14문서뿐이며, 나머지를 "검수됨"이라고 부르지 않았다.
- Project `Priority` 필드는 사람 소유. 이 문서의 P0/P1/P2 COVERAGE 는 리포트 내부 등급이다.
