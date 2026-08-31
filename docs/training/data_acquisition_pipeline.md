# 원본 데이터 수집 파이프라인

이 단계의 산출물은 **원본 응답 바이트와 출처 메타데이터**다. HTML 정제, PDF 텍스트화,
doc-to-md, 청킹, 임베딩, PGVector 적재는 아직 하지 않는다. 후속 전략이 바뀌어도
재수집하지 않기 위한 경계다.

## 저장 구조

`data/acquisition/`은 `.gitignore` 대상이며 로컬 또는 비공개 object storage에만 둔다.

```text
data/acquisition/
  objects/<source>/<sha-prefix>/<content-sha>.<ext>  원본 응답 바이트
  records/<source>/<url-hash-prefix>/<url-hash>.json URL별 최신 메타데이터
  runs/<timestamp>_<source>.jsonl                     실행별 append 로그
  candidates/<source>.jsonl                          discover-only 결과
```

레코드에는 canonical URL, HTTP 메타데이터, 수집 시각, 원본 SHA-256, robots 스냅샷
해시, 발행처, 권위 등급, 주제, 라이선스/권리 메모가 들어간다. 같은 URL은 안정적인
URL 해시로 재개되고, 서로 다른 URL이 같은 원본을 반환하면 content SHA-256으로
중복을 셀 수 있다. 원본은 삭제하지 않는다.

## 소스 정책

웹 소스 정본은 `config/acquisition_sources.json`이다. `enabled=false`인 후보는
robots 또는 약관에 걸렸거나 재사용 허가가 아직 없는 소스다. robots 허용,
공개 열람, RAG/DB 재사용 허가는 서로 다른 상태로 취급한다.

현재 자동 본문 수집 우선순위는 다음과 같다.

- 한국어: 국립축산과학원, 생활법령정보, 질병관리청, 행동지도사 자격정보, 농촌진흥청
- 영어: GOV.UK OGL 자료. FDA·CDC는 권리 근거는 좋지만 현재 실행 환경에서 각각
  HTTP 404·403으로 직접 수집이 거부돼 자동 대상에서 일시 제외
- 연구: Europe PMC 공식 API의 문서별 CC0/CC BY JATS XML

ASPCA·AKC는 자동 수집/DB 저장 금지 약관 때문에 제외한다. RSPCA는 AI crawler를
robots에서 차단한다. AVSAB, Dogs Trust, VCA, PDSA 등은 전문성은 높지만 자동
확대 전에 DB/RAG 재사용 허가를 확인한다. 기존 로컬 연구 사본이 있다는 사실은
신규 수집 허가를 뜻하지 않는다.

## 실행

```powershell
# 소스와 차단 사유 확인
uv run python scripts/collect_web_corpus.py --list-sources

# 한국어 공공자료 수집
uv run python scripts/collect_web_corpus.py `
  --source nias_companion `
  --source easylaw_animal `
  --source kdca_health `
  --source epis_behavior_exam `
  --source rda_dog_guidance

# 재사용 조건이 명확하고 현재 접근 가능한 영어 공공자료
uv run python scripts/collect_web_corpus.py `
  --source govuk_dog_welfare `
  --source govuk_dog_manual_pdf

# Europe PMC: CC0/CC BY만 저장
uv run python scripts/collect_europe_pmc.py --max-records 1000 --page-size 1000

# 원본 무결성·정확 중복 검사
uv run python scripts/report_acquisition.py --verify-hashes `
  --json data/scratch/acquisition_report.json
```

웹 수집기는 기본적으로 기존 URL 레코드와 원본 파일이 있으면 건너뛰고, 저장된 HTML의
링크를 다시 읽어 중단 지점부터 탐색을 이어간다. 원문이 갱신됐는지 다시 받으려면
`--refresh`를 명시한다. 소스별 지연시간과 robots의 crawl-delay 중 더 긴 값을 쓴다.

Europe PMC 수집기는 PMC HTML을 크롤링하지 않고 공식 REST API의 `fullTextXML`만
사용한다. `OPEN_ACCESS:Y`만으로 재사용을 허용하지 않으며, JATS 문서 안의 라이선스가
CC0 또는 CC BY로 식별된 경우만 object 저장소에 쓴다.

## 후속 단계에 넘길 계약

doc-to-md 단계는 원본을 덮어쓰지 않고 새 파생 레코드를 만든다. 최소 연결 키는 다음과 같다.

```text
source_id
canonical_url
content_sha256
object_path
derived_text_sha256
extractor_name
extractor_version
derived_at
```

청킹과 임베딩은 그 다음 별도 실행으로 두고, PGVector에는 `source/document/chunk/embedding_run`
정체성을 분리한다. 그래야 청킹이나 임베딩 모델을 바꿀 때 원문과 출처 계약을 보존할 수 있다.
