# 데이터 소스 조사 — 제도·문서형 RAG

> 조사일: 2026-08-19 · 최종 갱신: 2026-08-27
> 도메인: 동물등록 / 예방접종 / 목줄·입마개(맹견) / 동반 이동 / 지자체 지원 / 펫보험
> 기계용 시드 목록: [`data/manifests/seed_sources.yaml`](../data/manifests/seed_sources.yaml) · 저장 규칙: [`data/README.md`](../data/README.md)

**표기**
- 체크박스 = **수집 완료 여부** (실제 기록은 `data/manifests/crawl_log.jsonl`, 여기는 사람이 보는 요약)
- `id` = seed yaml 의 id, 크롤러 모듈은 `backend/crawler/sources/{domain}/{id}.py` (`-` → `_`, RAG-012)
- ✅확인 = 2026-08-19 URL·제공여부 검증 완료 · ⚠️요확인 = 경로 변동 가능 (수집 전 URL 재확인 필요)
- 🔑 = 키 필요 (§9 발급 체크리스트)

---

## 0. 진행 현황

| 파트 | 시드 | 수집 | Phase 1 즉시 가능 | 1순위 소스 |
|---|---|---|---|---|
| 공통 · 법령 | 4 | **4 ✅ 완료** | — | 국가법령정보 Open API (조문 단위) |
| 동물등록 | 4 | 2 | 0 (animal.go.kr 은 robots 차단) | 정부24 + 국립축산과학원 |
| 예방접종 | 2 | 0 | 0 (⚠️ 2건 URL 확인 필요) | 법령(광견병) + 검역본부 |
| 목줄·입마개·맹견 | 2 | 1 | 1 | 동물보호법·시행규칙 + 보도자료 |
| 동반 이동 | 4 | 0 | 0 (⚠️ 4건 URL 확인 필요) | 운송약관 원문 |
| 지자체 지원 | 4 | 1 | 0 | 보조금24 API + 조례 전수 |
| 펫보험 | 3 | 0 | 0 (⚠️ 3건 URL 확인 필요) | 보험사 약관 PDF |
| **문서형 소계** | **23** | **8** | **1** | |
| 실시간 (저장 X) | 7 | — | — | 기상청 + 에어코리아 + 카카오 |
| **합계** | **30** | | | |

**병목 (2026-08-27 갱신)**
1. ~~키 미발급~~ — **해소.** 키 5개가 전부 `.env` 에 있다 (§9). 실시간 연동 때 발급됐다
2. ⚠️요확인 9건 — 운송약관 4 · 보험 3 · 접종 2. `easylaw-pet` 도 `verified` 였는데 실제 URL 이 죽어 있었음 → **수집 착수 전 URL 확인을 기본 절차로** (§10)
3. PDF 파서 미정 — `pdf-entry` 7건(운송약관·보험약관)은 파싱 전략(RAG-004) 확정 후
4. **robots.txt 차단** — `animal.go.kr` 이 사이트 전체를 막는다 (§2). 새로 생긴 축이다

---

## 1. 공통 · 법령 (4)

> "출처 링크 + 조항 번호 인용" 이 KPI 이므로 법령은 **조문 단위**로 수집한다.
> 웹 원문과 API 를 **둘 다** 둔다 — 웹은 사람이 열 수 있는 출처 링크(답변에 그대로 싣는다),
> API 는 조문 경계가 태그로 확정된 구조화 데이터(청킹·`section`). RAG-011 · RAG-016.

- [x] **`easylaw-pet`** — 법제처 생활법령 "반려동물과 생활하기" · `html` · 키없음 · ✅확인
      → **2026-08-19 수집 완료: 본문 7 + 100문100답 7 = 14건** ([RAG-009](decisions-rag.md))
- [x] **`law-animal-protection`** — 동물보호법 / 시행령 / 시행규칙 웹 원문 · `html` · 키없음 · ✅확인
      → **2026-08-20 수집 완료: 3건** (조문 103 + 45 + 79) ([RAG-011](decisions-rag.md))
- [x] **`law-livestock-epidemic`** — 가축전염병 예방법 / 시행령 / 시행규칙 · `html` · 키없음 · ✅확인
      → **2026-08-20 수집 완료: 3건** (조문 95 + 33 + 97). 요확인이었던 URL 검증도 이때 해소
- [x] **`law-drf-api`** — 국가법령정보 공동활용 Open API (lawSearch / lawService) · `api` · 🔑`LAW_OC` · ✅확인
      → **2026-08-20 수집 완료: 8건** (동물보호법 3종 + 가축전염병 예방법 3종 + 수의사법 + 자연공원법).
      조문 단위 XML + **별표 본문 포함** ([RAG-016](decisions-rag.md))

### 수집 대상 법령

- [x] **동물보호법 / 시행령 / 시행규칙** — 등록 의무(제15조), 안전조치(목줄 2m), 맹견 5종(시행규칙 제2조), 맹견사육허가제(2024.4.27 시행), 맹견 책임보험, 과태료 → 등록·맹견 도메인
- [x] **가축전염병 예방법 / 시행령 / 시행규칙** — 광견병(제2종 가축전염병) 예방접종 명령 → 접종 도메인
- [x] **수의사법** — 진료·처방 → 접종·의료 (`law-drf-api` 로 수집)
- [x] **자연공원법** — 국립공원 등 반려동물 출입 제한 → 이동·산책 (`law-drf-api` 로 수집)
- [ ] **자치법규(조례)** — `target=ordin&query=반려동물` 전수 → 지자체 지원 (§6 에서 체크)

> 조례 전수는 API(🔑`LAW_OC`) 가 필수다 — 검색으로 목록을 얻어야 하는데 웹 원문은 그게 안 된다.

### 노트
- Open API: [open.law.go.kr](https://open.law.go.kr/LSO/openApi/guideResult.do) 로그인 → OPEN API 신청 → **OC(이메일 ID)** 발급, 무료
- 목록: `http://www.law.go.kr/DRF/lawSearch.do?OC={OC}&target=law&type=XML&query=동물보호법`
- 본문: `http://www.law.go.kr/DRF/lawService.do?OC={OC}&target=law&type=XML&ID={법령ID}` — 조문 단위 XML
- `target`: `law`(법령) / `ordin`(자치법규) / `admrul`(행정규칙) / `expc`(법령해석례) / `licbyl`(별표·서식)
- 청킹: XML 조문 구조를 그대로 청크 경계로 → `documents.section` 에 `제16조제2항` 형식 저장 (RAG-004)
- 웹 원문 URL 은 한글 패턴: `https://www.law.go.kr/법령/동물보호법`
- ⚠️ 시행규칙 조문 번호는 개정으로 변동 → 수집 시점의 **시행일자를 meta 에 기록**, 항상 최신본 재수집

### `easylaw-pet` 이 중요한 이유
[생활법령정보 "반려동물과 생활하기"](https://www.easylaw.go.kr/CSP/CnpClsMain.laf?csmSeq=1809&ccfNo=1&cciNo=1&cnpClsNo=1) 는 등록·외출(목줄/입마개)·대중교통·사육관리를 **법령 근거와 함께 해설**한다. 조문+해설이 한 문서에 있어 청킹 품질이 좋고 정적 HTML 이라 크롤 난도 최하 → Phase 1 최우선이었고 실제로 첫 수집 대상이 됐다. trust_level 은 `official`(해설), 인용 조문은 `law`. 100문100답 탭은 Q/A + 관련법령 구조라 **RAG-007 골든셋 재료**로 쓴다. 카드뉴스는 이미지라 제외.

---

## 2. 동물등록 (4)

- [ ] 🚫 **`animal-go-kr`** — 국가동물보호정보시스템 (검역본부) · `html` · 키없음 · **robots.txt 전면 차단**
      → 2026-08-27 정찰: `User-agent: * / Disallow: /`. 본문 품질은 좋았지만(정적 HTML, 카드뉴스도
      `title` 속성에 전문) §12 예절 규칙상 수집하지 않는다. 받으려면 **검역본부에 허가**를 받아야 한다.
      같은 내용이 `nias-pet`·`easylaw-pet` 에 있어 코퍼스 손실은 크지 않다. 시드에 `status: blocked` 로 남김
- [x] **`gov24-registration`** — 정부24 동물등록 민원안내 · `html` · 키없음 · ✅확인
      → **2026-08-27 수집 완료: 2건** (신청·변경신고 / 재발급). 수수료(내장형 10,000원 · 외장형 3,000원)와
      처리기간·구비서류가 여기에만 있다. URL 은 `/mw/AA020InfoCappView.do?CappBizCD=...` 로 교체 —
      옛 `serviceInfo/PTR000051610` 은 200 을 주는 soft-404 였고 robots 도 그 경로는 막는다
- [x] **`nias-pet`** — 국립축산과학원 반려동물 포털 · `html` · 키없음 · ✅확인
      → **2026-08-27 수집 완료: 7건** (행정/법률 정보 5 + 사육 기본사항 + 분실·유기).
      진입점은 `/companion/index.do` — 시드의 `/companion/` 은 soft-404 였다.
      조문 인용이 본문에 그대로 있어 `cites` 가 문서당 2~4건씩 나온다
- [ ] **`data-registration-lookup`** — 동물등록 정보조회 Open API · `api` · 🔑`DATA_GO_KR_KEY` · ✅확인 · https://www.data.go.kr/data/15098913/openapi.do

### 노트
문서량 적고 안정적인 도메인. 등록 대상 / 방법 / 변경신고 / 과태료 + 법령 조문 조합으로 충분하다. 키 없이 3건을 바로 칠 수 있어 `easylaw-pet` 다음 순번으로 적합.

---

## 3. 예방접종 (2)

- [ ] **`qia-rabies`** — 농림축산검역본부 동물방역 안내 · `html` · 키없음 · ⚠️요확인 · qia.go.kr
- [ ] **`kvma-guideline`** — 대한수의사회 접종 가이드라인 · `html` · 키없음 · ⚠️요확인 · kvma.or.kr
- [ ] (참고) WSAVA 백신 가이드라인 — 영문, 종합백신(DHPPL)·코로나·켄넬코프 스케줄. 시드 미등록

### 노트 — trust_level 구분이 이 도메인의 핵심
성격이 다른 두 층위가 섞여 있어 **meta 의 `trust_level`** 로 구분해 저장한다. 챗봇이 "법정 의무" 와 "권장 스케줄" 을 반드시 구분해 답해야 하므로, 수집 단계에서 라벨링해 두면 후처리가 쉽다.

| 층위 | trust_level | 소스 |
|---|---|---|
| 법정 의무 | `law` | 광견병 — 가축전염병예방법. 지자체 무료·지원 접종은 조례/공고와 연계 |
| 공공기관 안내 | `official` | 검역본부(qia), 국립축산과학원, animal.go.kr |
| 수의학 가이드라인 | `guideline` | 대한수의사회, WSAVA |

---

## 4. 목줄·입마개·맹견 (2)

- [ ] **`mafra-press`** — 농식품부 보도자료 검색("맹견", "기질평가") · `html` · 키없음 · ✅확인 · https://www.mafra.go.kr/
- [x] **`korea-kr-policy`** — 정책브리핑 맹견사육허가제 해설 · `html` · 키없음 · ✅확인
      → **2026-08-27 수집 완료: 3건** — 도입(2022 법 통과) · 시행 상세(2024.4.27) · 계도기간(~2025.10.26).
      하나만 받으면 "지금 어떻게 되어 있나"에 답이 안 된다

### 노트
법령 본문은 §1 (`law-animal-protection` / `law-drf-api`) 이 담당하고, 여기는 해설·시행 안내 계층이다.
- 맹견 5종(시행규칙): 도사견, 아메리칸 핏불테리어, 아메리칸 스태퍼드셔 테리어, 스태퍼드셔 불 테리어, 로트와일러 + 잡종
- **맹견사육허가제**(2024.4.27 시행): 시·도지사 허가, 기질평가, 중성화, 책임보험. 일반 견종도 위해 발생 시 기질평가로 맹견 지정 가능
- 안전조치: 목줄 2m 이내

---

## 5. 동반 이동 (4)

> 전부 키 불필요. **운송약관 PDF/HTML** 이 1차 소스, `easylaw-pet`(§1) 이 종합 해설.

- [ ] **`korail-terms`** — 코레일 여객운송약관 · `pdf-entry` · ⚠️요확인 · letskorail.com — 케이지 격납 조건부 허용
- [ ] **`srt-terms`** — 에스알 여객운송약관 · `pdf-entry` · ⚠️요확인 · etk.srail.kr
- [ ] **`seoulmetro-terms`** — 서울교통공사 여객운송약관 · `pdf-entry` · ⚠️요확인 — 용기 격납 + 불쾌감 없을 것. 부산·대구·인천 등 지역 공사도 동일 패턴
- [ ] **`airlines-pet-pages`** — 항공사 9곳 반려동물 안내 · `html` · ⚠️요확인 — 대한항공·아시아나·제주항공·진에어·티웨이·에어부산·에어서울·이스타·에어프레미아. 기내반입/위탁 기준(케이지 포함 무게), 요금

### 다른 파트에서 커버되는 이동 관련 소스
- [ ] 출입국 동물검역 (qia.go.kr) — 국가별 요건: 마이크로칩, 광견병 항체검사 등. 시드 미등록
- [ ] 자연공원법 (국립공원 출입 제한) — §1 법령 API 로 수집
- [ ] 버스 — 지자체별 시내버스 운송약관 / 고속버스 각 운수사. 사업자 재량이 커서 약관 원문 필요, 시드 미등록

### 노트
항공사 딥링크는 사이트 개편 때마다 바뀌므로 manifest 에는 **도메인만 고정**하고 크롤러가 사이트 내 검색으로 탐색하게 설계. JS 렌더링이면 Playwright 폴백.

---

## 6. 지자체 지원 (4)

> 전국 단일 소스가 없는 가장 지저분한 도메인. **3계층**으로 나눠 공략한다.

**① 구조화 · 커버리지**
- [ ] **`benefit24-services`** — 보조금24 / 행안부 대한민국 공공서비스(혜택) 정보 · `api` · 🔑`DATA_GO_KR_KEY` · ✅확인 · https://www.data.go.kr/data/15113968/openapi.do
      중앙+지자체 **7,500여 개** 서비스 목록·상세. "반려" "동물등록" "중성화" "내장형" 키워드 필터링

**② 안정 · 법적 근거**
- [ ] **`ordinance-search`** — 자치법규 API `target=ordin&query=반려동물` · `api` · 🔑`LAW_OC` · ✅확인
      전국 지원 조례 전수. 공고보다 변동이 적어 RAG 기본 코퍼스로 적합

**③ 신선도 · 모니터링**
- [ ] **`seoul-notice-api`** — 서울시 고시공고 정보 API (OA-2482) · `api` · 🔑서울 열린데이터 인증키(무료) · ✅확인 · https://data.seoul.go.kr/dataList/OA-2482/S/1/datasetView.do
- [x] **`seoul-microchip-support`** — 서울시 동물등록 안내 · `html` · 키없음 · ✅확인
      → **2026-08-27 수집 완료: 2건** — 등록 방법·수수료·과태료(544026) + 2026년 자진신고기간(569082).
      시드의 522690 은 2023년 글이라 뺐다. **지원 금액은 해마다 바뀌므로 낡은 값이 코퍼스에 있으면
      `/ask` 가 자신 있게 틀린 금액을 답한다** — 연 1회 사람이 확인할 것.
      `567583`(우리동네 동물병원)은 지자체 지원 카드 몫으로 남겨 뒀다
- [ ] 타 지자체 게시판 크롤 — 후순위, 필요 지역만. 시드 미등록

---

## 7. 펫보험 (3)

- [ ] **`e-insmarket`** — 보험다모아 반려동물보험 비교 · `html` · ⚠️요확인 · https://e-insmarket.or.kr/ — 메뉴 경로 확인 필요
- [ ] **`knia-disclosure`** — 손해보험협회 상품비교공시 · `html` · ⚠️요확인 · https://kpub.knia.or.kr/ — 펫보험 공시 여부 확인 필요
- [ ] **`insurer-terms-pdfs`** — 각 보험사 상품공시실 약관 PDF · `pdf-entry` · ⚠️요확인 — **RAG 핵심 코퍼스**

### 보험사별 약관 (9개사)
- [ ] 삼성화재
- [ ] 메리츠 (펫퍼민트)
- [ ] 현대해상
- [ ] DB손보
- [ ] KB손보
- [ ] NH농협
- [ ] 한화손보
- [ ] 롯데손보
- [ ] 캐롯

### 노트
경로는 각 사 "공시실 > 상품공시 > 판매중 상품". 100p+ PDF 에 보장한도·자기부담금·면책조항(슬개골 탈구 특약 등)이 들어 있고, **약관 조항 번호가 그대로 인용 지표**가 된다 — 법령과 같은 방식으로 `section` 저장. 상품 개정 시에만 갱신되므로 크롤 주기는 분기 1회 수준.

---

## 8. 실시간 조회형 (7) — 저장하지 않고 API 직조회

> 상세: [`docs/realtime-apis.md`](realtime-apis.md). 코퍼스가 아니므로 수집 체크 대상이 아니고, **연동 완료** 체크로 관리한다.

**연동 확인 방법** — 실서버(`uvicorn main:app`)에 `GET /walk` 을 **세 지역**(역삼·해운대·제주)으로
날려 각 provider 가 `sources` 에 `ok` 로 찍히는지 본다. `/walk` 이 안 부르는 오퍼레이션만 따로 찌른다.
마지막 확인 2026-08-25.

- [x] **`kma-vilage-fcst`** — 기상청 단기예보 · 🔑`DATA_GO_KR_KEY` · **3/3 지역 ok**
      오퍼레이션 셋 다: `getUltraSrtNcst`·`getUltraSrtFcst`·`getVilageFcst`.
      ⚠️ **실황(`ncst`)은 해운대에서만 불렸다** — 역삼(AWS 401)·제주(AWS 184)는 같은 격자에 AWS
      지점이 있어 생략됐다. ④-e 1번이 조건부로 발동하는 것이 실측으로 보인 자리다
- [x] **`kma-weather-warning`** — 기상청 특보 · 🔑`DATA_GO_KR_KEY` · **3/3 지역 ok**
      코드에서는 `kma-warning:pwn` 이다 (`getPwnStatus`, 전국 1세트라 조회 키가 하나)
- [ ] **`kma-life-index`** — 생활기상지수 · 🔑**`KMA_HUB_KEY`** (표기 정정 — data.go.kr 이 아니라
      **API허브**다, §6.4) · **부분 연동**
      · `getUVIdxV3` ✅ 실동작 — 시각별 값 20건. **`areaNo` 는 시군구 단위**여야 한다
        (법정동 코드는 `99 검색결과가 없습니다`). 변환은 `kma_life_index.area_code()` (§6.9)
      · `getSenTaIdxV3` ⬜ `03 NO_DATA` — `requestCode` 를 A01~A08·A41·A42 로 훑고 발표시각을
        셋 바꿔도 같다. 파라미터가 아니라 자료가 안 실린 것으로 보인다
      · **판정은 안 막힌다** — ③-b 가 UV 를 축에서 뺐고 ③-c 가 체감온도를 자체 계산으로 확정했다.
        이 provider 는 **대조용 도구**라 여기 미완이어도 `/walk` 은 완결이다
- [x] **`airkorea-realtime`** — 에어코리아 실시간 측정 · 🔑`DATA_GO_KR_KEY` · **3/3 지역 ok**
      `getMsrstnAcctoRltmMesureDnsty`(측정소 실시간) + `getMinuDustFrcstDspth`(권역 예보통보) 둘 다
- [x] **`airkorea-stations`** — 에어코리아 측정소 목록 · 🔑`DATA_GO_KR_KEY` · **3/3 지역 ok**
      최근접 측정소가 세 지역 다 잡힌다 — 강남대로 1.82km · 우동 1.66km · 이도동 0.12km.
      ⚠️ 이 엔드포인트만 간헐적으로 느리다(5초 타임아웃 → 재시도 3회 = 17초, §6.9). 월 1회짜리라
      **요청 예산 밖**(`config.static_budget_sec`)에 둔 근거가 이것이다
- [x] **`kakao-local`** — GPS→행정동, WGS84→TM 좌표 변환 · 🔑`KAKAO_REST_KEY` · **둘 다 ok**
      `coord2regioncode` 3/3 지역(서초2동·우1동·이도2동) · `transcoord` 직접 확인
      (`(202370.9, 443966.0)`). 후자는 §6.5 대로 평시 경로가 아니라 **폴백**이다
- [x] **`kma-apihub`** — 기상청 API허브 · 🔑`KMA_HUB_KEY` · **3/3 지역 ok** — (선택) 아니었다
      `nph-aws2_min`(분 단위 관측) + `stn_inf.php`(지점 745개). 후자가 2026-08-25 승인되면서
      ⑤-d 1순위가 실제로 발동한다 — "선택"으로 적혀 있었지만 **④-e 의 유일한 큰 절감**이 여기 달렸다

> **6/7 연동 완료.** 남은 하나(`kma-life-index`)는 UV 만 되고 체감온도가 `NO_DATA` 인데,
> 둘 다 판정 축이 아니라 **파트② 관통에는 영향이 없다** (`GET /walk` 실서버 확인 완료).

⚠️ 기상청 "체감온도(대상·환경별)" API 가 **2026-05-01 종료** → 체감온도 자체 계산 필요.
→ **§2.3 정정** (§6.8): data.go.kr 쪽만 종료다. API허브에는 `getSenTaIdxV3` 가 살아 있고 활용신청도
된다. 자체 계산 결정(③-c)은 유지되지만 **근거가 "없어서"가 아니라 "행정구역·발표시각 단위라
시각별 판정에 안 맞아서"** 로 바뀐다.

---

## 9. 키 발급 체크리스트

- [x] **`LAW_OC`** ✅ 발급 완료 (2026-08-20) — [open.law.go.kr](https://open.law.go.kr/LSO/openApi/guideResult.do) → OPEN API 신청 · 즉시 발급
      → `law-drf-api` 수집 완료. 남은 것은 `ordinance-search`(조례 전수)
      **IP/도메인 등록은 필요 없다.** 공식 매뉴얼의 샘플 키 `OC=test` 가 등록 없이 그냥 동작하는 것으로
      확인(2026-08-20). 인증 실패 시 나오는 "IP주소 및 도메인주소를 등록해 주세요" 는 원인을 특정하지
      않는 공통 안내문이라, OC 값이 틀렸을 때도 똑같이 나온다
      → `.env` 에 `LAW_OC=발급받은ID` 한 줄. crawler 가 `.env` 를 직접 읽는다
- [x] **`DATA_GO_KR_KEY`** ✅ 발급 완료 — data.go.kr 회원가입 → 각 API "활용신청" · 자동승인, 즉시
      → 이제 `data-registration-lookup` · `benefit24-services` 를 바로 칠 수 있다
- [x] **`KAKAO_REST_KEY`** ✅ 발급 완료 — developers.kakao.com · 즉시
- [x] **서울 열린데이터 인증키** ✅ 발급 완료 — data.seoul.go.kr · 무료 (`SEOUL_OPEN_DATA_KEY`)
- [x] **`KMA_HUB_KEY`** ✅ 발급 완료 — apihub.kma.go.kr

> **2026-08-27 확인: 다섯 개가 전부 `.env` 에 들어 있다.** 실시간(파트②) 연동 때 발급된 것으로 보이는데
> 이 문서가 갱신되지 않아 "키 미발급"이 병목 1번으로 남아 있었다. **더 이상 막고 있는 것이 없다.**

발급 후 `.env` 에 추가, `.env.example` 에는 키 이름만 반영.

---

## 10. ⚠️ 요확인 체크리스트 (수집 착수 전 URL 검증)

- [x] ~~`law-livestock-epidemic`~~ — 2026-08-20 확인 + 수집 완료
- [ ] `qia-rabies` — 검역본부 동물방역 안내 경로
- [ ] `kvma-guideline` — 대한수의사회 접종 가이드라인 경로
- [ ] `korail-terms` — 코레일 약관 PDF 딥링크
- [ ] `srt-terms` — SRT 약관 PDF 딥링크
- [ ] `seoulmetro-terms` — 서울교통공사 약관 PDF 딥링크
- [ ] `airlines-pet-pages` — 항공사 9곳. JS 렌더링이면 Playwright 폴백
- [ ] `e-insmarket` — 보험다모아 반려동물보험 메뉴 경로
- [ ] `knia-disclosure` — kpub 펫보험 공시 여부
- [ ] `insurer-terms-pdfs` — 보험사 9곳 공시실 경로

> `easylaw-pet` 은 `status: verified` 였는데도 시드 URL 이 '페이지 오류' 를 반환했다. **`status` 는 조사 시점 기준일 뿐** — 소스 모듈 작성 전에 항상 재확인한다.

---

## 11. Phase 전략

### Phase 1 — 키 없이 즉시 (소량·고품질)
목표: 6개 도메인 기본 커버 → **RAG 파이프라인 조기 테스트**

- [x] `easylaw-pet` (14건)
- [x] `law-animal-protection` (3건)
- [x] `law-livestock-epidemic` (3건)
- [ ] 🚫 `animal-go-kr` — robots 차단 (§2). 허가를 받지 않는 한 Phase 1 에서 뺀다
- [x] `gov24-registration` (2건)
- [x] `nias-pet` (7건)
- [ ] `mafra-press` — 검색 페이징이 필요하고 성격이 신선도 모니터링이라 Phase 3 으로 미룸
- [x] `korea-kr-policy` (3건)
- [x] `seoul-microchip-support` (2건)

### Phase 2 — 키 발급 후 (구조화 확장)
- [x] `law-drf-api` — 조문 단위 XML (8건, 별표 본문 포함)
- [ ] `benefit24-services` — 보조금24
- [ ] `ordinance-search` — 조례 전수
- [ ] `data-registration-lookup`
- [ ] `insurer-terms-pdfs` — 보험 약관 PDF
- [ ] 운송약관 4건 (`korail` / `srt` / `seoulmetro` / `airlines`)

### Phase 3 — 지속 운영 (Celery Beat, RAG-001)
- [ ] `seoul-notice-api` 고시공고 모니터링 — 신규 지원사업 탐지
- [ ] 법령 개정 체크 — 시행일자 비교
- [ ] 약관 개정 체크 — 분기 1회

---

## 12. 수집 예절 / 법적 주의

- robots.txt 준수, 요청 간격 1~2초(현재 크롤러 기본 1.5s), UA 에 연락처 명시.
  **판정은 표준(RFC 9309 §2.2.2)의 longest-match 다** — `Disallow: /` 아래에 `Allow: /특정경로` 를
  적는 사이트(정부24)가 있어서, 먼저 적힌 규칙이 이기는 `urllib.robotparser` 로는 열려 있는 경로를
  스스로 막는다. `crawler/core/fetch.py` 의 `Robots` 가 이것을 구현한다 (2026-08-27)
- 공공저작물은 대부분 **공공누리 제1유형(출처표시)** — 페이지별 유형을 `.meta.json` 에 기록
- 약관·항공사 안내는 사실정보 위주라 내부 RAG 활용은 무리 없으나, **서비스 표출 시 출처 표기 필수** (KPI 와도 일치)
- 원본은 git 미추적, `.meta.json` 필수, meta 없으면 인덱싱 금지 — [RAG-008](decisions-rag.md)
