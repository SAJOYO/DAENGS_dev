# 한국어 공공 웹 HTML → Markdown 병렬 비교

## 결론

현재 수집된 한국어 공공 웹에는 **`BeautifulSoup CMS 선택자 + 밀도 fallback + markdownify`를 1순위로 적용**하는 것이 가장 안전하다. 30문서에서 짧은 본문/한글 소실 실패가 한 건도 없었고, 제목은 전부 보존했으며, 구조가 원본에 존재하는 문서 기준 헤딩 100%, 목록 63.3%, 표 64.3%, 링크 86.7%에서 해당 Markdown 구조를 남겼다. 대신 보일러플레이트 중앙값이 2.67%로 두 범용 추출기보다 높으므로 후처리와 품질 게이트가 필요하다.

범용 fallback은 `trafilatura`가 낫다. `readability-lxml + markdownify`보다 실패가 한 건 적고 표 보존이 강했다. 다만 둘 다 EPIS 정적 안내 4건을 모두 짧게 잘랐고, NIAS 주제형 페이지도 각각 3건과 4건 실패했으므로 어느 하나를 단독 기본값으로 쓰면 안 된다.

권장 실행 순서는 다음과 같다.

1. 알려진 공공 CMS에는 검증된 본문 선택자를 적용한다.
2. 선택자가 없거나 결과가 게이트를 통과하지 못하면 `trafilatura`를 실행한다.
3. 본문 200자 미만, 한글 50자 미만, 제목 소실은 자동 실패로 분류한다.
4. 실패 문서는 버리지 말고 원본 HTML과 함께 재처리 큐에 둔다.

## 표본

콘텐츠 주소가 다른 동일 content-addressed object는 한 번만 세었다. 표본 선택은 URL 해시로 결정적이며 같은 입력에서 다시 실행해도 같은 `sample_id`가 나온다.

| 기관/소스 | 문서 | 층 |
|---|---:|---|
| 국립축산과학원 반려동물 | 16 | 상세글 7, 생애단계 4, 주제형 5 |
| 반려동물행동지도사 자격정보 | 8 | 게시물 상세 4, 정적 안내 4 |
| 농촌진흥청 | 3 | 기사 3 |
| 찾기쉬운 생활법령정보 | 1 | 법령 설명 1 |
| 질병관리청 국가건강정보포털 | 2 | 건강 기사 2 |
| 합계 | 30 | 8개 소스·템플릿 층 |

목록·권리 안내·사이트맵처럼 본문이 아닌 페이지는 표본에서 제외했다.

## 전략

| 전략 | 실제 처리 |
|---|---|
| `trafilatura` | precision 우선 본문 추출, Markdown 출력, 표·링크 포함 |
| `readability_markdownify` | Readability 본문 판정 후 `markdownify`, 문서 제목을 H1으로 보완 |
| `bs_selector_density_markdownify` | 기관별 최소 선택자 레지스트리, 알려지지 않은 템플릿은 텍스트/링크 밀도 점수, `markdownify` 변환 |

세 번째 전략은 `form`을 삭제하지 않고 unwrap한다. EPIS와 KDCA는 본문 전체가 폼 내부에 있어 폼 삭제가 곧 본문 삭제였기 때문이다. EPIS/KDCA의 비시맨틱 footer 컨테이너도 선택 전에 제거한다.

## 전체 결과

| 지표 | trafilatura | readability + markdownify | BS 선택자 + 밀도 |
|---|---:|---:|---:|
| 실패 | 7/30 (23.3%) | 8/30 (26.7%) | **0/30 (0%)** |
| 제목 보존 | 33.3% | 90.0% | **100.0%** |
| 본문 길이 중앙값 | 1,024자 | 936자 | **1,330자** |
| 보일러플레이트 비율 중앙값 | **0.00%** | **0.00%** | 2.67% |
| 평균 실행시간 | 48.3 ms | 49.1 ms | 64.3 ms |
| 헤딩 기회 보존 | 3/22 (13.6%) | **22/22 (100%)** | **22/22 (100%)** |
| 목록 기회 보존 | 10/30 (33.3%) | 7/30 (23.3%) | **19/30 (63.3%)** |
| 표 기회 보존 | **9/14 (64.3%)** | 2/14 (14.3%) | **9/14 (64.3%)** |
| 링크 기회 보존 | 3/30 (10.0%) | 3/30 (10.0%) | **26/30 (86.7%)** |

`readability`와 BS 전략은 HTML 제목을 H1으로 보완하므로 제목·헤딩 수치는 그 정책의 효과를 포함한다. 링크 수가 많다고 곧 좋은 것은 아니다. BS 전략의 링크 보존과 함께 보일러플레이트가 늘어난 이유도 이 때문이다.

## 실패 위치

| 소스 | trafilatura | readability + markdownify | BS 선택자 + 밀도 |
|---|---:|---:|---:|
| EPIS 정적 안내 4건 | 4 | 4 | 0 |
| NIAS 주제형 5건 | 3 | 4 | 0 |
| 나머지 21건 | 0 | 0 | 0 |

범용 추출기의 실패는 예외가 아니라 **본문을 지나치게 짧게 선택한 성공 응답**이었다. 따라서 예외 처리만으로는 잡히지 않고 길이·한글량 게이트가 반드시 필요하다.

## 측정 정의와 한계

- 실패: 추출 예외, 일반 텍스트 200자 미만, 한글 50자 미만 중 하나.
- 제목 보존: HTML title의 4자 이상 의미 구간이 출력 첫 1,000자 안에 존재.
- 구조 기회 보존: 원본 HTML에 해당 요소가 한 개 이상 있는 문서 중 Markdown에도 해당 문법이 한 개 이상 남은 비율.
- 보일러플레이트 비율: 같은 소스 문서 절반 이상에서 반복되는 짧은 줄, 내비게이션 어휘 줄, 링크 밀집 줄의 문자 수를 전체 비공백 텍스트 문자 수로 나눈 휴리스틱.
- 원본 HTML 전체의 구조 개수에는 내비게이션도 포함되므로 구조의 개수 recall은 사용하지 않았다. 표의 기회 보존은 구조가 최소 하나 남았는지만 본다.
- 소스가 한 문서뿐인 생활법령은 반복 줄 기반 보일러플레이트를 측정할 수 없어 어휘·링크 밀도 규칙만 적용된다.
- 이 비교는 추출·형식 복원 평가다. 사실 정확성, 최신성, 검색 적합성, 청크 검색 성능을 대신하지 않는다.

## 산출물

- 실행기: `experiments/doc_to_md/html_compare.py`
- 동일 문서 ID의 전략별 경로·수치: `experiments/doc_to_md/html_manifest.jsonl`
- 집계: `experiments/doc_to_md/html_summary.json`
- 행 단위 지표: `experiments/doc_to_md/html_metrics.csv`
- 원문 Markdown 후보: `data/scratch/doc_to_md_html/<strategy>/` (git ignored)

각 후보 루트의 `manifest.jsonl`에는 `source_id`, `record_path`, `object_path`, 후보 루트 기준 `output_path`, `strategy`, 측정값이 있다. 출력 Markdown 원문은 추적 경로에 두지 않는다.

## 재실행

프로젝트 루트에서 다음 한 줄로 실행한다. `pyproject.toml`은 바뀌지 않는다.

```powershell
uv run --with trafilatura --with readability-lxml --with markdownify --with beautifulsoup4 --with lxml python experiments/doc_to_md/html_compare.py
```

출력은 실행할 때마다 `data/scratch/doc_to_md_html`에 덮어쓴다. 비교 수치는 `experiments/doc_to_md/html_summary.json`과 `html_metrics.csv`에서 확인한다.
