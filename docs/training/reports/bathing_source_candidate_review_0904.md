# 목욕 혐오 학습 소스 후보 검토 (2026-09-04)

**결론: `NO_ELIGIBLE_SOURCE`.** 직접 근거와 재사용 근거를 **동시에** 만족하는 후보가 없습니다.
승인할 소스가 없으므로 인제스트 구현 카드를 열지 않습니다.

- 범위: 소스 검토만. 코드·코퍼스·서빙 매니페스트·평가셋 **변경 없음**.
- 선행 조사: [`bathing_coverage_gap_0904.md`](bathing_coverage_gap_0904.md) — 코퍼스에 목욕 근거 0건.
- 상위 규범: [`../SOURCES.md`](../SOURCES.md) 라이선스·저작권 절, [`../source_blocklist.md`](../source_blocklist.md),
  [`../data_acquisition_pipeline.md`](../data_acquisition_pipeline.md) 소스 정책 절.

---

## 1. 검토 전에 확인한 현행 거버넌스

후보를 찾기 전에 저장소가 실제로 무엇을 강제하는지 먼저 읽었습니다. 아래는 전부 코드/문서 실물입니다.

| 항목 | 실물 | 내용 |
|---|---|---|
| 서빙 허용 목록 | `backend/src/daengs_training/config/serving_corpus_v1.json` | `serving-corpus-v1`, `nias_companion-*` **14건** |
| 허용 목록 적용 | `service.py` `load_serving_document_ids()` → `retrieval/pgvector.py` | `document_id = any(%s)` 로 검색을 그 14건에 가둠 |
| 허용 목록 계약 | `backend/tests/test_training_service.py:130-134` | 개수 14 · 중복 없음 · **모든 ID 가 `nias_companion-` 로 시작**을 단언 |
| 서빙 계약 | [`../rag-demo.md`](../rag-demo.md) | 승인 매니페스트 14문서/83청크. "Do not re-ingest or re-embed as part of deployment" |
| DB 프로비넌스 | `cli/pgvector_ingest.py` | `training_rag_documents(document_id, source_id, content_sha256, metadata)` — **metadata 는 `heading_path` 뿐** |
| 커밋 산출물 계약 | `backend/tests/test_training_committed_artifacts.py` | 추적 파일 안에 청크 본문(>150자) 금지. `KNOWN_LEGACY` 추가는 **사람 승인 필요** |
| 선정 단계 차단 | [`../source_blocklist.md`](../source_blocklist.md) | 후보 조사 **전에** 읽어야 하는 문서. 아래 2절에서 실제로 적용됨 |

### 1-1. 프로비넌스 스키마가 이 작업이 요구하는 필드를 갖고 있지 않습니다

`data_acquisition_pipeline.md` 는 canonical URL · 수집 시각 · robots 스냅샷 해시 · 발행처 ·
권위 등급 · 라이선스 메모를 `data/acquisition/records/` 에 남기는 계약을 정의합니다.
**그 수집 단계는 이 저장소에 없습니다.** `scripts/collect_web_corpus.py`,
`config/acquisition_sources.json`(웹 소스 정본), `scripts/ingest_documents.py`(파서·청커),
`scripts/chunking_config.py`, `scripts/validate_document_parsing.py` 는 전부 원본 레포
`frankie516c/dog-training-rag` 에 남았고 이관되지 않았습니다 (`../README.md` "가져오지 않은 것").

이 저장소에 있는 인제스트 코드는 `cli/pgvector_ingest.py` **한 개**이고, 그것은
이미 만들어진 청크 JSONL(`data/scratch/chunks_structure_v1`)을 읽어 임베딩·upsert 하는
**마지막 단계뿐**입니다. 즉 지금 이 저장소만으로는:

- **결정적 파서 경로가 없습니다.** 새 소스를 HTML/PDF 에서 청크로 만드는 코드가 없습니다.
- **canonical URL · 라이선스 · 개정일을 적을 자리가 없습니다.** DB 스키마에 그 컬럼이 없고
  `metadata` 는 `heading_path`/`kinds` 만 싣습니다.

어떤 소스가 승인되더라도 이 두 가지가 선행 조건입니다. 소스 선택 문제가 아니라 파이프라인 문제입니다.

### 1-2. 기존에 쓰는 기관이라는 사실은 승인 근거가 아닙니다

서빙 코퍼스 14건은 전부 국립축산과학원(NIAS)입니다. NIAS 페이지 실물을 확인한 결과
`Copyright © 2017 National Institute of Animal Science. All rights reserved.` 만 있고
**공공누리(KOGL) 표시가 없습니다.** 담당은 동물복지과, 갱신주기는 "변경시"로 개정일이 특정되지 않습니다.
기존 코퍼스가 그 기관을 쓰고 있다는 사실이 새 문서의 재사용 허가를 뜻하지 않습니다.

---

## 2. 무엇을 '직접 근거'로 요구했는가

목욕 혐오에 대한 **보호자 대상 안전한 응답**을 만들 수 있어야 직접 근거입니다.
목욕 주기·샴푸·털 관리·위생 일반은 직접 근거가 아닙니다.
목욕 맥락이 없는 일반 긍정강화 서술은 **보조 근거**입니다.

요구 항목: ① 목욕 환경 점진 노출 ② 자발적 접근·협조적 핸들링 ③ 환경/단계와 긍정강화 연결
④ 물·세척 단계 점진 도입 ⑤ 강압·플러딩·처벌·제압 회피 ⑥ 스트레스 신호 인지와 중단·난이도 하향
⑦ 도망·물기 시도 시 안전한 대응 ⑧ 드라이기 소리 둔감화 ⑨ 전문가(수의·행동) 의뢰 시점.

---

## 3. 후보 비교

정식 평가 3건입니다. 그 앞뒤로 걸러진 것은 4절에 있습니다.

| | C1 RSPCA Australia KB | C2 VCA / LifeLearn | C3 AAHA 2015 Guidelines |
|---|---|---|---|
| 문서 | "Why and how should I groom my dog?" | "Overcoming Fears with Desensitization and Counterconditioning" | 2015 AAHA Canine and Feline Behavior Management Guidelines |
| URL | `kb.rspca.org.au/categories/companion-animals/dogs/caring-for-my-dog/why-and-how-should-i-groom-my-dog` | `vcahospitals.com/know-your-pet/overcoming-fears-with-desensitization-and-counterconditioning` | `aaha.org/.../2015_aaha_canine_and_feline_behavior_management_guidelines_final.pdf` |
| 권위 등급 | 4 — 확립된 동물복지단체 | 3 — 임상 검토가 있는 수의 조직 | 3 — 인가 수의 조직 (JAAHA 외부 심사) |
| 저자·검토 | 명시 없음 | Lindell VMD DACVB · Feyrecilde LVT VTS · Horwitz DVM DACVB · Landsberg DVM DACVB — **4인 전원 행동 전문** | Hammerle · Levine DACVB · Overall DACVB · Radosta DACVB · Yin 등 7인 태스크포스 |
| 날짜 | Last updated 2022-01-19 | Last updated 2023-10-18 | 2015 (JAAHA 51:205-221) |
| ① 환경 점진 노출 | 부분 (그루밍 전반) | 일반론만 | 정의만 |
| ②③ 협조·긍정강화 | ✅ | 일반론만 | ✅ (원칙) |
| ④ 물·세척 점진 도입 | ❌ | ❌ | ❌ |
| ⑤ 강압·플러딩·처벌 회피 | 간접 | 부분 | ✅ **최강** — 플러딩 "never recommended", 혐오 기법 반대 |
| ⑥ 스트레스 신호·중단 | ✅ | 일반론만 | ✅ |
| ⑦ 물기 시도 대응 | ❌ | ❌ | 공격성 일반만 |
| ⑧ 드라이기 소리 | ❌ | ❌ | ❌ |
| ⑨ 전문가 의뢰 | ✅ (그루머·수의) | ✅ | ✅ (행동전문의 의뢰 기준) |
| **목욕 특정성** | `Bathing` 소제목 1개, 본문의 **약 15%** | **목욕 언급 0** | **"bath" 문자열 0건** (grooming 은 과다그루밍·발톱 문맥) |
| 직접/보조 | **보조** | 보조 | **보조** |
| 안전 정합성 | 양호 | 양호 | 매우 높음 |
| 프로비넌스 | 저자 무명, 참고문헌 없음, 날짜 있음 | 저자·자격·날짜 모두 명확 | 최상 (심사·DOI·저자) |
| robots | 제한 없음 (content-signal 주석만, 지시자 없음) | `/know-your-pet/*/*/` 만 차단 — 대상 URL 은 1단계라 허용 | 허용, `Crawl-delay: 10` |
| **재사용 근거** | **CC BY-NC-ND 4.0** (페이지 HTML 에서 확인) | **명시적 금지** | `© 2015 by American Animal Hospital Association`, 개별 허가 없음 |
| **판정** | `SUPPORTING_ONLY` | **`REJECT`** | `SUPPORTING_ONLY` |

### C1 — RSPCA Australia Knowledgebase

목욕 소제목을 실제로 가진 **유일한** 후보이고, 라이선스를 명시한 유일한 후보입니다.
"짧은 세션으로 시작", "불편·동요를 보이기 **전에** 멈추고 보상", "불안하면 전문 그루머" 취지의
서술이 있어 ②③⑥⑨ 를 덮습니다.

**그런데 라이선스가 `ND`(NoDerivatives) 입니다.** 이 시스템은 검색한 청크를 근거로
**한국어 생성 답변**을 만듭니다. 원문을 번역·요약·재구성한 산출물은 2차적저작물에 해당하고,
BY-NC-ND 는 그 배포를 허용하지 않습니다. `NC` 도 `SOURCES.md` 가 전제로 적은
"개인 학습·포트폴리오용 비공개"가 유지되는 동안만 성립하고, 그 전제가 바뀌면 다시 짜야 합니다.
과제 지침이 금지한 "비공식 한국어 번역을 원본 근거처럼 제시"에 정확히 걸리는 자리이기도 합니다.

직접성도 부족합니다 — ④⑦⑧ 이 전부 없습니다. 물 도입 절차도, 물려는 개에 대한 대응도,
드라이기도 없습니다. 6개 진단 쿼리 중 `물을 묻히면~` · `드라이기 소리~` · `목욕할 때 물려고 해요` 는
이 문서로 답할 수 없습니다.

### C2 — VCA / LifeLearn

행동 전문 자격 4인이 저자인, 후보 중 가장 권위 있는 둔감화·역조건형성 텍스트입니다.
**두 가지 이유로 즉시 탈락합니다.**

1. **재사용 금지가 명시돼 있습니다.** 페이지 하단 저작권 표시가
   `© Copyright 2023 LifeLearn Inc.` 이고, 복제·인쇄·재배포를 LifeLearn 의 명시적 서면 동의 없이
   금지한다고 적고 있습니다. `SOURCES.md` 의 배제 정책에 정면으로 걸립니다.
2. **목욕 내용이 없습니다.** 검색 요약에 목욕 예시가 있는 것처럼 보였으나
   원문을 직접 받아 확인한 결과 목욕·물·건조 관련 서술이 **하나도 없습니다.**
   핸들링 예시로 `grooming, brushing, hugging, lifting` 이 나열될 뿐입니다.
   검색 결과 요약만 믿고 후보로 올렸으면 잘못된 근거로 승인 요청이 갈 뻔했습니다.

### C3 — AAHA 2015 Behavior Management Guidelines

PDF 원문(17쪽)을 받아 전문 검색했습니다. **`bath` 문자열이 0건**입니다.
`grooming` 은 과다그루밍(자기손상)과 "여러 사람이 붙잡아야 하는 발톱 손질" 문맥에만 나옵니다.

대신 안전 경계 근거로는 후보 중 최강입니다 — 플러딩을 권장하지 않는다고 못박고,
혐오적 훈련 기법에 반대하며, 공격성 사례에서 처벌을 쓰지 말라고 하고,
행동전문의 의뢰 기준을 제시합니다. `daengs_training` 의 기존 가드레일·거절 계약과 정확히 같은 방향입니다.

한계는 셋입니다. ⓐ 목욕 직접 근거 0. ⓑ **수의 임상진 대상** 문서라 보호자 대상 문장이 아닙니다.
ⓒ `© 2015 by American Animal Hospital Association` 이고 별도 공개 라이선스가 없습니다.
무료로 읽을 수 있다는 것과 저장·처리·재배포해도 된다는 것은 다릅니다.

---

## 4. 정식 평가 전에 걸러낸 것

| 소스 | 사유 |
|---|---|
| 국립축산과학원 (NIAS) | 서빙 코퍼스의 발행처. 건강관리·예절교육 페이지를 직접 확인했으나 목욕은 **준비물 목록**(`목욕용품(전용샴푸, 린스, 브러시, 타월, 귀 세정제, 드라이기 등)`)에만 등장하고 행동 지침이 없음. **코퍼스 공백이 아니라 발행처 공백**임이 확인됨 |
| 동물사랑배움터 (apms.epis.or.kr) | 농식품부 공식이라 권위는 최상이나 **영상 시청에 로그인이 필요**합니다. `SOURCES.md` 가 배제한 "로그인 구간 자료"에 해당하고, 영상이라 텍스트 인제스트 대상도 아님 |
| GOV.UK 개 복지 실무규범 | 재사용 근거는 최상(OGL). 그러나 복지 규범이라 그루밍은 "적절히 손질" 수준의 일상 관리로만 나오고 **목욕 혐오 행동 지침이 없음** |
| Dogs Trust "How to Handle your Dog" | `Reviewed by: Behaviour team`. 핸들링 둔감화·중단 신호·행동전문가 의뢰가 있어 품질은 좋으나 **목욕·물·드라이기 언급 0**, 저작권은 `© 2026 Dogs Trust` 이고 재사용 허가 문구 없음 |
| Europe PMC CC BY 논문 | 라이선스는 명확(CC BY). 검색된 관련 논문은 **수의 진료 중 핸들링**·그루밍 접근성 연구라 보호자 대상 목욕 절차가 아님 |
| ASPCA · AKC | `data_acquisition_pipeline.md` 가 자동 수집·DB 저장 금지 약관으로 이미 제외 |
| RSPCA (UK) | 같은 문서가 robots 에서 AI crawler 차단으로 기록 |
| a-ha.io · 네이버/티스토리 블로그 · 펫샵/미용실 블로그 | 검색 상위를 이들이 채웁니다. `source_blocklist.md` 차단(a-ha.io) 또는 SEO·제휴·작성자 불명 |

한국어 검색에서 목욕 둔감화를 구체적으로 다루는 것은 사실상 **개인 블로그와 상업 페이지뿐**이었고,
공공기관·수의단체 쪽에는 해당 콘텐츠가 존재하지 않았습니다.

---

## 5. 영어 근거를 쓰는 경로에 대한 별도 경고

가령 라이선스가 풀리더라도 영어 소스는 이 시스템에서 측정되지 않을 가능성이 높습니다.
`source_blocklist.md` 의 "영문 소스 전반" 항목이 이미 실측을 적고 있습니다 —
코퍼스에 AVSAB 영문 3문서 115청크가 들어 있으나 **한국어 질의의 정답으로 잡힌 사례가 gold 13건 중 0건**입니다.
6개 진단 쿼리가 전부 한국어이므로, 영어 문서를 넣고 "검색이 안 된다"를 다시 확인하는 일이 됩니다.

---

## 6. 승인 이전에 해소돼야 하는 선행 조건

어떤 소스가 나중에 승인되더라도 아래는 소스와 무관하게 남습니다.

1. **파서·청커 부재** — 새 소스를 청크로 만드는 결정적 경로가 이 저장소에 없습니다 (1-1절).
2. **프로비넌스 필드 부재** — canonical URL·라이선스·개정일·수집일을 적을 컬럼이 없습니다.
   인용 표기 계약도 `document_id` 기반이라 출처 URL 을 답변에 실을 수 없습니다.
3. **서빙 계약 충돌** — `test_training_service.py` 가 서빙 ID 전부 `nias_companion-` 접두사를 단언합니다.
   비-NIAS 문서를 매니페스트에 넣으면 **기존 테스트가 깨집니다.** 그 단언을 고치는 것은
   서빙 계약 변경이므로 사람 승인 사안입니다.
4. **재적재 금지** — `rag-demo.md` 가 배포 과정의 재인제스트·재임베딩을 금지합니다.
5. **본문 커밋 금지** — `test_training_committed_artifacts.py` 때문에 소스 본문을 저장소에 넣을 수 없습니다.

### 채택하지 않는 우회로

아래는 검색 결과를 성공처럼 보이게 만들 뿐 근거를 만들지 않습니다. 명시적으로 기각합니다.

- `목욕` → 특정 `document_id` 하드코딩 라우팅
- 강제 문서 주입
- 임의 유사도 임계 조정
- 전역 top-k 상향 (`ChatRequest.top_k` 는 `le=4` 이고 그 4 는 측정으로 정해진 값입니다)
- semantic router 변경 (PR #204 소유)
- 새 Care capability 신설
- 직접 근거·거절 규칙 완화
- 미검토 서빙 매니페스트 추가
- 운영 DB 직접 수정

---

## 7. 이 문서가 바꾼 것

없습니다. 코드·코퍼스·서빙 매니페스트·평가 데이터셋·프로덕션 어느 것도 건드리지 않았습니다.
후보 원문에서 인용한 분량은 어느 후보에 대해서도 연속 25단어를 넘지 않습니다.

## 8. 사람이 결정할 것

1. `NO_ELIGIBLE_SOURCE` 를 받아들이고 목욕 축을 **범위 밖으로 남길지** — 지금 동작(`model_reported_insufficient_evidence`)은 정확하므로 이 선택지는 무행동으로 유효합니다.
2. C1(RSPCA Australia)의 **BY-NC-ND 를 감수할지** — 감수한다면 생성 답변이 2차적저작물이 되는 문제를 어떻게 다룰지 함께 정해야 하고, 그래도 ④⑦⑧ 은 여전히 못 덮습니다.
3. 6절의 선행 조건(파서·프로비넌스·서빙 계약)을 **먼저 별도 카드로 처리할지.**
4. 유료 라이선스나 기관에 직접 재사용 허가를 요청할지.
