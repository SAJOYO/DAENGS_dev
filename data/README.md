# data/

크롤링 원본과 가공 산출물 저장 폴더. **원본은 불변**, 가공물은 언제든 재생성 가능해야 한다.

## 구조

```
data/
├── manifests/        # 시드 소스 목록(seed_sources.yaml), 크롤 로그(crawl_log.jsonl)
├── raw/              # 크롤링 원본 (PDF/HTML/XML/JSON) — 도메인별 하위 폴더
│   ├── law/          # 법령 원문 (동물보호법, 가축전염병예방법 …)
│   ├── registration/ # 동물등록
│   ├── vaccination/  # 예방접종 (의무 + 스케줄)
│   ├── leash-muzzle/ # 목줄·입마개·맹견
│   ├── transport/    # 대중교통·항공 동반 이동
│   ├── subsidy/      # 지자체 지원사업 (공고문·조례·보조금24)
│   └── insurance/    # 펫보험 약관
├── processed/        # 가공 산출물 — 단계별 분리
│   ├── parsed/       #   구조 추출 (jsonl) — 원본 1건 = 1파일, 1행 헤더 + 요소 (RAG-019)
│   ├── chunks/       #   청킹 (jsonl) — DB 적재 입력
│   ├── embeddings/   #   벡터 (parquet)
│   └── eval/         #   6단계 3파전 문항별 top-k 덤프 (jsonl) — 기계 산출물이라 무시 (RAG-024 ④)
├── eval/             # 손으로 만드는 평가 자산 자리. git 관리 대상이나 **지금은 비어 있다** —
│                   #   골든셋은 RAG-022 ⑥으로 backend/rag/stages/goldenset.yaml 로 갔다
└── reference/        # 정적 참조 데이터 (에어코리아 측정소 목록, 기상청 격자 변환표 등)
```

### git 추적 범위 (RAG-008 → RAG-017)

**받은 결과물은 어느 것도 커밋하지 않는다.** 추적하는 것은 "무엇을 받을지"와 폴더 구조뿐이다.

| 대상 | 추적 |
|---|---|
| `manifests/seed_sources.yaml` | ✅ 커밋 — 무엇을 받을지 |
| `**/.gitkeep` | ✅ 커밋 — 폴더 구조 |
| `raw/` 원본 파일 (pdf/html/xml/json) | ❌ 무시 |
| `raw/**/*.meta.json` | ❌ 무시 (RAG-017 에서 변경) |
| `manifests/crawl_log.jsonl` | ❌ 무시 |
| `processed/**` | ❌ 무시 |
| `eval/`, `reference/` | ✅ 커밋 — 손으로 만드는 것이라 성격이 다르다. **기계 산출물은 여기 오지 않는다** (RAG-024 ④) |

**원본을 커밋하지 않는 이유** — 이 레포는 public이다. 정부 자료는 공공누리로 재배포가 되지만
**펫보험 약관은 보험사 저작물**이라 그대로 올리는 것은 리스크가 있다.

**meta 도 커밋하지 않는 이유 (RAG-017)** — meta 는 "이미 받은 것을 또 받지 않기" 위한 **로컬 상태**다.
`crawl_log.jsonl` 과 성격이 같아 같이 취급한다. 커밋해도 실행에는 아무 도움이 안 되고
(새 PC 에서는 어차피 원본이 없어 전부 다시 받는다), 오히려 meta 만 있고 원본이 없는 어긋난 상태를
만든다. 수집 현황은 사람이 읽는 `docs/data-sources.md` 체크리스트가 담당한다.

**상태가 로컬이라는 것의 뜻** — `data/` 를 지우면 변경 감지 근거가 사라져 전부 다시 받는다.
Celery 워커를 컨테이너로 띄울 때 `data/` 는 **반드시 영속 볼륨**이어야 한다. 아니면 배치가 돌 때마다
같은 문서를 다시 내려받는다 (RAG-017).

**대신 감수하는 것** — 공고문이 내려가면 원본을 다시 못 구한다. 로컬 `raw/`는 지우지 말 것.

### 단계를 파일로 끊는 이유

청킹 전략과 임베딩 모델은 여러 번 갈아엎게 된다. 한 덩어리로 짜두면 청크 크기 하나 바꿀 때마다
정부 사이트를 다시 긁어야 한다. `parsed/`를 따로 남기면 **D-006의 구조 파싱 성공률·스캔 PDF 비율**도
그 산출물에서 바로 측정된다.

| 무엇을 바꾸면 | 어디부터 다시 돌리나 |
|---|---|
| 청크 크기·분할 기준 | `chunks/` |
| 임베딩 모델 | `embeddings/` |
| 파서 (HWP 깨짐 수정 등) | `parsed/` |
| — | 크롤링은 다시 하지 않는다 |

텍스트 단계가 jsonl인 이유는 디버깅·diff가 쉬워서다. 용량이 문제되는 것은 1024차원 벡터뿐이라
`embeddings/`만 parquet을 쓴다 (`pyproject.toml`의 `ml` 그룹에 `pyarrow` 포함).

---

## 규칙

1. **원본 불변** — `raw/` 파일은 수정 금지. 재크롤 시 날짜가 다른 새 파일로 저장.

2. **파일명**: `{slug}__{YYYYMMDD}.{ext}` (예: `animal-protection-act__20260819.xml`)
   - `slug`는 `seed_sources.yaml`의 `id`와 대응시킨다.
   - 지자체 공고처럼 **한 소스에서 문서가 수십 개 쏟아지는 경우**는 slug를 개별로 정할 수 없으므로
     `{source_id}__{YYYYMMDD}__{urlhash8}.{ext}` 를 쓴다. `urlhash8` = `sha256(url)[:8]`.
     URL에 한글·쿼리스트링이 섞여 원래 파일명을 그대로 쓸 수 없고, 공공기관 첨부는
     `공고문.pdf`·`붙임1.pdf` 처럼 이름이 겹쳐 덮어쓰기 때문이다.

3. **출처 메타 필수** — 원본 1개당 같은 이름의 `.meta.json` 1개.

   ```json
   {
     "source_id": "easylaw-pet",
     "source_url": "https://www.easylaw.go.kr/CSP/CnpClsMain.laf?csmSeq=1809&ccfNo=2&cciNo=2&cnpClsNo=1",
     "document_title": "반려동물과 생활하기 > … > 반려견과 외출 시 이것만은 챙기세요. (본문)",
     "source": "법제처 찾기쉬운 생활법령정보",
     "source_type": "web",
     "format": "html",
     "category": "policy",
     "subcategory": "pet-life-guide",
     "trust_level": "official",
     "published_at": "2026-07-15",
     "fetched_at": "2026-08-19T23:41:43+09:00",
     "sha256": "aca76bff…",
     "license": "공공누리 제1유형",
     "notes": "",

     "raw_file": "law/easylaw-pet-2-2-1__20260819.html",
     "run_id": "20260819-234132",
     "http_status": 200,
     "content_type": "text/html;charset=UTF-8",
     "bytes": 121675,
     "cites": ["동물보호법 제16조제2항제1호", "동물보호법 시행규칙 제11조", "…"],
     "previous_sha256": null
   }
   ```

   위 블록은 `documents` 컬럼 대응 필드, 아래 블록은 추적용 필드다.
   `crawler/core/store.py` 가 쓰므로 손으로 만들지 않는다.

   - `sha256`은 **콘텐츠 지문**이다. RAG-001 원칙2의 변경 감지용으로, 이전 값과 같으면 재파싱·재임베딩을 건너뛴다.
     - 단, sha256이 같아도 **`raw_file`이 가리키는 원본이 디스크에 없으면 다시 받는다** (RAG-010).
       meta는 "받은 적 있다"는 기록일 뿐이고 파이프라인이 소비하는 것은 원본 파일이라, 둘이 어긋난
       상태는 정상이 아니라 복구 대상이다 (`data/`를 옮기거나 원본만 지웠을 때)
     - `html` → 소스 모듈 `extract()` 가 뽑은 **본문 텍스트**의 해시. 조회수·세션값 같은 노이즈 때문에
       원본 바이트를 그대로 해시하면 매번 "변경됨"이 뜨기 때문
     - `pdf`/`xml`/`json` 등 → 원본 바이트 해시
     - 청크 텍스트 해시인 `documents.content_hash`와는 층위가 다르다 (그쪽은 중복 적재 방지용)
   - `cites` — 본문이 인라인 인용한 조항. RAG-004 `section` 형식(`제16조제2항제1호`)으로 정규화.
     해설 문서(`trust_level: official`)가 어떤 법령(`law`)에 근거하는지의 연결고리
   - **meta 없는 파일은 인덱싱 금지.** "출처 링크 + 조항 번호를 함께 응답"이 성능 지표라
     출처 없는 문서는 가치가 없음.

4. **크롤 로그** — `manifests/crawl_log.jsonl`. 한 줄에 JSON 객체 하나(JSON Lines), append-only.

   ```json
   {"run_id":"20260819-234132","source_id":"easylaw-pet","slug":"easylaw-pet-1-1-1","url":"https://…","raw_file":"law/easylaw-pet-1-1-1__20260819.html","status":200,"sha256":"4bc3…","changed":true,"fetched_at":"2026-08-19T23:41:34+09:00","error":null}
   ```

   `changed:false` 면 `raw_file` 은 null (아무것도 안 씀). 실패면 `status`/`error` 만 채워진다.

   `reason` — 왜 받았는지: `new`(첫 수집) / `changed`(내용 변경) / `raw-missing`(meta는 있는데 원본이
   없어 복구, RAG-010) / `forced`(`--force`) / `same`(스킵). `changed:true` 만으로는 내용이 바뀐 것인지
   원본이 없었던 것인지 구분되지 않아 사후 분석이 안 된다.

   `.meta.json`이 **문서의 출처 정보**라면 이쪽은 **실행 이력**이다. 역할이 다르므로 둘 다 둔다.
   특히 404·타임아웃은 파일 자체가 안 생겨서 `.meta.json`이 없으므로, 실패를 기록할 곳은 여기뿐이다.
   남기지 않으면 재실행할 때마다 죽은 URL을 계속 두드린다.

   - JSON 배열이 아니라 JSONL인 이유: 배열은 파일을 닫아야 완성돼서 크롤 도중 중단되면 통째로 깨진다.
   - `run_id` 단위 요약은 Postgres `crawl_runs` 테이블에 별도 기록한다 (RAG-001 원칙3, 관리자페이지용).
     이 파일은 **문서 단위**, `crawl_runs`는 **실행 단위**로 층위가 다르다.

5. **realtime(날씨·대기질)은 저장하지 않는다** — API 직조회 대상. 10분 뒤면 틀릴 값을 벡터로 박아두면
   계속 오답을 뱉는다. 단, 측정소 목록·격자 변환표 같은 정적 메타는 `reference/`에 캐시
   (월 1회 갱신 권장).

---

## 값 사전

DB CHECK 제약과 반드시 일치해야 한다. 표기는 **kebab-case로 통일**한다
(`raw/` 하위 폴더명과 맞추기 위함). 흔들리면 `(category, subcategory)` 복합 인덱스 필터가 조용히 실패한다.

### category — 대분류 (DB CHECK 있음)

`policy` · `travel` · `food`

> **`care`·`emergency`는 제외했다 (RAG-008).** 진단 성격이 강해 별도 에이전트로 분리한다.
> 필요해지면 테이블 재생성 없이 CHECK 제약만 교체하면 된다.

### source_type — 매체 유형 (DB CHECK 있음)

| 값 | 대상 |
|---|---|
| `document` | 파일로 배포된 문서 (공고문 PDF/HWP/HWPX, 약관) |
| `web` | HTML 본문 (기관 웹페이지, 찾기쉬운 생활법령 해설) |
| `api` | API 응답 (법제처 DRF XML, 공공데이터포털 JSON) |
| `manual` | 직접 작성·정리 (FAQ, 보정 데이터) |

기존 `pdf`가 `document`로 바뀌었다. `pdf`는 포맷 축이라 `web`·`api`와 기준이 섞여
"웹에서 받은 PDF"를 어디에 넣을지 답이 안 나왔다. 세부 포맷은 `format`이 담당한다.

### format — 파일 포맷

`pdf` · `hwp` · `hwpx` · `html` · `xml` · `json`

> **HWP와 HWPX는 반드시 구분한다.** HWP는 바이너리(OLE)라 `pyhwp`/`hwp5`가 필요하고 파싱 실패가 잦은 반면,
> HWPX는 ZIP+XML(국가표준 OWPML)이라 압축을 풀어 파싱하면 되고 훨씬 안정적이다. 파서 코드 경로가 다르다.

### trust_level — 신뢰 등급

`law`(법령) > `official`(공공기관 안내) > `guideline`(협회·수의학 가이드)

법령 원문과 기관 안내페이지를 구분하는 것이 "조항 번호 인용" KPI에 직결된다.
블로그·유튜브를 넣게 되면 `community`를 추가한다 (현재 시드 30개는 전부 공공·기관 소스).

### subcategory — 세부 분류 (CHECK 없음, 수집하면서 늘려간다)

확정 목록이 아니라 **"지금까지 쓴 값의 기록"** 이다. 값을 추가할 때 이 표를 갱신한다.
재분류는 `UPDATE` 한 줄이고 `content`가 바뀌지 않으므로 **임베딩 재계산이 필요 없다.**

| RAG 도메인 | category | subcategory | trust_level |
|---|---|---|---|
| 반려동물 생활 종합 해설 (easylaw) | `policy` | `pet-life-guide` | `official` (인용 조문은 `cites`) |
| 동물보호법 3종 (법·시행령·시행규칙) | `policy` | `animal-protection-act` | `law` |
| 가축전염병 예방법 3종 | `policy` | `livestock-epidemic-act` | `law` |
| 동물등록 | `policy` | `registration` | `law` / `official` |
| 예방접종 — 법정 의무(광견병) | `policy` | `vaccination` | `law` |
| 예방접종 — 권장 스케줄 | `policy` | `vaccination-schedule` | `guideline` |
| 목줄·입마개·맹견 | `policy` | `leash-muzzle` | `law` |
| 동반 이동 (철도/지하철/버스/항공/검역) | `travel` | `transport-rail` 등 | `official` |
| 지자체 지원사업 | `policy` | `subsidy` | `official` |
| 펫보험 | `policy` | `insurance` | `official` |

법령 원문은 한 문서가 여러 도메인을 걸친다 (동물보호법 = 등록 + 맹견). 그래서 문서 단위 subcategory는
법령 이름으로 두고, `registration`·`leash-muzzle` 같은 세부 분류는 **조문 단위로 쪼갠 뒤**(RAG-004) 붙인다.

접종 스케줄이 `care`에서 `policy`로 옮겨진 이유 — `care`를 없앤 대신 **`trust_level`이 그 구분을 대신한다.**
`guideline`은 원래 "접종 스케줄처럼 법정 근거가 없는 문서" 구분용으로 만든 등급이라 역할이 겹쳤다.

---

## PDF 파서 후보 (미결정 — 파싱 단계 착수 시 결정)

셋 다 pip으로 설치하는 Python 라이브러리다.

| | 형태 | 텍스트 품질 | 표 추출 | 속도 | 라이선스 |
|---|---|---|---|---|---|
| `pypdf` | 순수 Python | 낮음 (레이아웃 무시) | ❌ | 보통 | BSD |
| `pdfplumber` | 순수 Python (pdfminer.six 기반) | 높음 | ✅ 강함 | 느림 | MIT |
| `PyMuPDF` | C 라이브러리(MuPDF) 바인딩 | 높음 | 보통 | 압도적으로 빠름 | ⚠️ AGPL-3.0 |

- 정부 공고문은 **표가 많다** (지원사업 금액표, 접종 일정표, 맹견 목록). 표를 못 뽑으면 셀 값이
  한 줄로 뭉개져 청크 품질이 무너진다.
- 속도는 크롤링이 더 느려서 대체로 병목이 아니다.
- `PyMuPDF`의 AGPL-3.0 — 개인·내부용은 무관하지만 웹 서비스로 배포하면 소스 공개 의무가 생긴다.
- **스캔 PDF**는 텍스트 레이어가 없어 어떤 파서로도 0자가 나온다. D-006이 비율을 측정하고,
  RAG-007 논의에서 나온 Gemini vision OCR 폴백 범위를 그 수치로 정한다.
- HWP는 `pyhwp`(hwp5) 설치가 까다롭고 실패율이 높다. HWPX는 표준 라이브러리(`zipfile`+`xml.etree`)로 처리된다.

`parsed/` 산출물에 파서 이름·버전을 기록해두면, 파서를 갈아탄 뒤 구버전으로 처리된 문서만 골라 재파싱할 수 있다.

---

시드 소스 목록: [manifests/seed_sources.yaml](manifests/seed_sources.yaml)
소스 조사 문서: [../docs/data-sources.md](../docs/data-sources.md)
결정 기록: [../docs/decisions-rag.md](../docs/decisions-rag.md)
