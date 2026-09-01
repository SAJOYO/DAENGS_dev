# Doc-to-Markdown 병렬 평가 설계

## 목적과 원칙

이 평가는 HTML/JATS XML/PDF/JSON 변환기를 같은 계약으로 비교한다. 특정 라이브러리의 내부 점수나 LLM judge를 사용하지 않으며, 같은 입력과 후보 파일에 대해 같은 결과가 나오는 결정적 평가다.

종합 점수 하나로 변환기를 고르지 않는다. 원문 내용 회수, 구조 보존, 출처 보존, 정리 품질은 서로 다른 실패 모드다. `aggregate_screening_score`는 사람 검토 순서를 정하는 보조값이고, 개별 지표와 hard failure가 선택 근거다.

현재 수집 카탈로그를 평가기가 직접 읽은 결과는 레코드 320개, 고유 원본 315개다. 확장자는 HTML 188, JATS/XML 130, PDF 1, JSON 1이다. 동일 원본을 가리키는 레코드는 원본 SHA-256으로 묶는다. HTML 한 개는 내용이 없는 동일 원본을 네 레코드가 가리키고 있어, 변환기 누락과 원본 공백을 구분해야 한다.

## 실행 계약

평가기: `experiments/doc_to_md/eval_doc_to_md.py`

```powershell
.\.venv\Scripts\python.exe experiments/doc_to_md/eval_doc_to_md.py `
  --candidate baseline=experiments/doc_to_md/candidates/baseline `
  --candidate alternative=experiments/doc_to_md/candidates/alternative `
  --output-dir data/scratch/doc_to_md_evaluation
```

후보 디렉터리에는 `.md` 파일을 둔다. 가장 안전한 원본 연결 방법은 후보 루트의 `manifest.jsonl`이다.

```json
{"object_path":"data/acquisition/objects/europe_pmc/pmc1/pmc123.xml","output_path":"pmc123.md"}
```

`source_path`, `content_sha256`, `source_sha256`, `markdown_path`도 대응 키로 허용한다. manifest가 없으면 Markdown 파일 stem을 원본 파일 stem, 레코드 stem 또는 콘텐츠 SHA-256과 대조한다. 연결되지 않은 파일도 버리지 않고 `unmatched_source` hard failure 행으로 남긴다.

기본 산출 경로는 추적하지 않는 작업 영역 `data/scratch/doc_to_md_evaluation`이며, 산출물은 다음 네 개다. tracked `experiments/doc_to_md`에는 평가 코드만 둔다.

- `document_metrics.jsonl`: 문서별 모든 자동 지표와 결정적 해시
- `comparison_summary.json`: 후보별 분포, 원본 커버리지, hard failure 집계
- `human_review_sample.jsonl`: 자동 지표가 포함된 결정적 층화 표본
- `human_review_sample.csv`: 사람이 점수를 기입할 UTF-8 BOM CSV

`output_sha256`은 Markdown 원시 바이트의 SHA-256이다. `output_set_sha256`은 정렬된 출력 경로와 각 출력 해시를 다시 해시한 실행 지문이고, `conversion_manifest_sha256`은 원본-출력 계약의 지문이다. 변환기의 결정성을 검사할 때 동일 경로에 같은 입력으로 두 번 변환하고 두 지문 및 문서별 해시가 같은지 비교한다. 실행 시각은 결과에 넣지 않는다.

## 자동 루브릭

### 1. 문서 완전성

`content_token_recall`은 원문 토큰 multiset과 Markdown 본문 토큰 multiset의 교집합을 원문 토큰 수로 나눈 값이다. 원문의 20% 이상(최소 두 고유 원본)에 정확히 반복되는 15자 이상 행은 사이트 chrome으로 보고 완전성 분모에서 제외한다. 제거 비율은 `source_boilerplate_removed_fraction`으로 함께 기록한다. 이 처리가 없으면 메뉴를 올바르게 제거한 변환기가 불완전하다고 평가되는 역전이 생긴다. 영문/숫자, 한글 어절, 한자를 재현 가능하게 토큰화한다. 출력에서 같은 단어를 반복해도 원문 출현 횟수보다 더 인정하지 않는다.

`content_length_ratio`는 Markdown의 정규화된 가시 문자 수를 원문의 정규화된 가시 문자 수로 나눈 값이다. 1을 넘는다고 좋은 것이 아니다. 지나치게 낮으면 누락 가능성, 지나치게 높으면 보일러플레이트·중복·마크업 누출 가능성이다. 내용 회수율 0.35 미만은 `low_content_recall` hard failure다.

이 회수율은 원문에 있던 문장이 의미를 보존한 채 바뀌었는지, 읽기 순서가 맞는지 판단하지 못한다. 그 판단은 수동 평가로 분리한다.

### 2. 구조 보존

원문과 출력에서 제목, 목록 항목, 표, 표 행을 센다. 각 보존율은 `min(출력 수 / 원문 수, 1)`이다. 원문에 해당 구조가 없으면 점수를 만들지 않는다. `structure_score`는 제목 2, 목록 1, 표 2, 표 행 1의 가중 평균이다.

구조 수가 같아도 제목 계층, 목록의 중첩, 표 셀의 행·열 관계가 틀릴 수 있다. 특히 PDF는 원문 구조 태그를 안정적으로 얻지 못하므로 구조 자동 점수보다 수동 검토를 우선한다.

### 3. 링크와 출처

`canonical_url_present`는 수집 레코드의 canonical URL이 Markdown 어디엔가 그대로 남았는지 확인한다. `source_link_recall`은 원문에 있던 절대 URL 중 Markdown 링크로 보존된 비율이다. 상대 URL의 base 결합이나 링크의 의미 적합성은 아직 자동 채점하지 않는다.

### 4. 보일러플레이트

후보 내 문서의 20% 이상, 최소 두 문서에 반복되는 15자 이상 정규화 행을 코퍼스 전역 보일러플레이트 후보로 본다. 쿠키, 개인정보, 메뉴, 저작권 등 알려진 UI 문구도 별도로 센다. 이 문자 비율이 0.35를 넘으면 `boilerplate_heavy`다.

정당하게 반복되는 기관명·면책문구도 잡힐 수 있으므로 비율은 제거 명령이 아니라 검토 신호다. 후보별로 계산하므로 변환기끼리 보일러플레이트 정의가 섞이지 않는다.

### 5. 비정상 반복

문서 내부에서 20자 이상 문단 블록이 정확히 반복될 때, 첫 블록을 제외한 중복 문자 비율을 `duplicate_block_fraction`으로 낸다. 0.20 초과는 `abnormal_repetition`이다. 같은 제목이나 짧은 표 머리글은 이 지표에서 제외된다.

### 6. 문자 깨짐

Unicode replacement character, 제어 문자, 흔한 UTF-8/Windows 인코딩 깨짐 조각을 센다. 한국어 레코드에서 한글보다 한자가 비정상적으로 많은 경우도 경고량에 더한다. `mojibake_ratio > 0.002` 또는 이상 문자 20개 이상이면 `mojibake`다.

학술 원문의 정당한 한자나 다국어 인용은 false positive가 가능하다. hard failure는 삭제 조건이 아니라 원문과 나란히 볼 우선순위다.

### 7. 지나치게 짧음

정규화된 가시 문자 200자 미만 또는 토큰 40개 미만이면 `too_short`다. 빈 index 페이지처럼 원문 자체가 짧은 경우가 있으므로 `source_visible_char_count`와 함께 본다.

### 8. 청결도와 보조 종합점수

`cleanliness_score`는 1에서 보일러플레이트 비율, 중복 블록 비율, 제한된 문자 깨짐 벌점을 뺀 값이다. 보조 종합점수의 가중치는 내용 회수 5, 구조 2, canonical 출처 1, 청결도 2다. 짧은 문서는 최종 보조점수에 0.5를 곱한다.

가중치는 검색 품질의 진실이 아니라 초기 triage 규칙이다. 후보 선택 시 이 점수의 평균보다 p10, high-risk 수, 원본 커버리지를 먼저 본다.

## 사람 검토 설계

표본은 후보별 `risk_band × source_extension` 조합에서 최소 한 건씩 뽑고, 남은 수는 고정 seed SHA-256 순서로 채운다. 기본값은 후보당 30건이다. 따라서 실행할 때마다 같은 파일이 뽑히며, 위험 문서와 HTML/XML/PDF/JSON 유형이 모두 검토 대상에 들어간다. 비교 후보가 많아도 각 후보를 독립적으로 같은 수만큼 뽑는다.

CSV의 수동 항목은 다음과 같다.

- 의미 충실도 1–5: 주장, 조건, 부정, 수치, 예외가 원문과 같은가
- 구조 충실도 1–5: 제목 계층, 단계 순서, 목록 중첩, 표의 행·열 관계가 같은가
- 유해한 누락 yes/no: 안전 경고, 수의사 의뢰 조건, 금기, 실패 시 대처가 빠졌는가
- 환각 yes/no: 원문에 없는 사실·출처·절차가 생겼는가
- 가독성 1–5: 문자가 정상이고 마크업 누출 없이 읽을 수 있는가
- 전체 수용 yes/no 및 근거 메모

가능하면 한 문서를 두 평가자가 독립 채점하고, yes/no 불일치는 합의한다. 변환기 이름을 가린 파일 뷰를 쓰면 기대 편향을 줄일 수 있다. 1–5 점수는 후보별 평균뿐 아니라 평가자 간 차이와 최저점 문서를 함께 보고한다.

## 선택 규칙

1. 출력 파일 수가 아니라 `candidate_source_coverage`와 `missing_manifest_outputs`로 해당 실행의 완주 여부를 확인한다. `global_source_coverage`는 전체 수집 원본 중 이번 실험 표본의 범위를 나타내므로 둘을 혼동하지 않는다.
2. `unmatched_source`, 문자 깨짐, 비정상 반복, 내용 회수 부족의 원인을 먼저 분류한다.
3. 후보별 p10 내용 회수율과 high-risk 문서 수를 비교한다. 평균만 좋아지고 꼬리가 나빠진 후보는 채택하지 않는다.
4. HTML, XML, PDF를 한 승자로 강제하지 않는다. 유형별 우승 변환기를 조합할 수 있다.
5. 수동 표본에서 유해한 누락 또는 환각이 확인된 문서는 자동점수와 무관하게 실패다.
6. 마지막 선택은 동일한 청킹·임베딩 조건의 retrieval 평가로 검증한다. Doc-to-Markdown 평가는 검색 성능의 대리 지표이지 최종 품질 평가가 아니다.

## 재현성과 알려진 한계

- 네트워크, 시간, 임의 난수, LLM을 사용하지 않는다. 표본 순서도 고정 seed 해시다.
- HTML의 script/style/template을 제외하고 가시 텍스트를 추출한다. 실제 CSS visibility는 계산하지 않는다.
- XML은 ElementTree로 텍스트 순서를 읽으며 복잡한 수식·각주 연결 관계를 완전히 표현하지 못한다.
- PDF 자동 평가는 `pypdf`가 추출한 텍스트에 의존한다. 이 저장소의 `.venv`로 실행해야 한다.
- 토큰 회수율은 단어 순서와 의미를 보지 않으며, 번역·요약형 변환기를 불리하게 평가한다. 이번 목적은 원문 보존형 Markdown 변환이므로 의도된 성질이다.
- 현재 레코드 메타데이터 일부의 한국어 문자열 자체에 깨짐이 보인다. 평가기는 내용 비교에 원본 object를 사용하고, 메타데이터에서는 source id, language, URL, 경로, 해시만 사용한다.

## 검증

```powershell
Push-Location experiments/doc_to_md
..\..\.venv\Scripts\python.exe -m unittest -v eval_doc_to_md_tests.py
Pop-Location
```

테스트는 HTML 숨김 텍스트/구조 추출, 반복으로 내용 회수율을 부풀릴 수 없는지, 문자 깨짐/반복 탐지, 종단 실행의 결과 바이트 결정성을 검증한다.
