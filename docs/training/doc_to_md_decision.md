# doc-to-md 병렬 비교 결과와 현재 라우팅

## 결론

문서 전체에 하나의 추출기를 강제하지 않는다. 현재 라우팅은 HTML·JATS·PDF·JSON
유형별로 다르게 둔다. 수치는 [HTML 비교 보고서](reports/doc_to_md_html_comparison.md),
[평가 설계](reports/doc_to_md_evaluation_design.md),
PDF/JATS 실행 요약에 기록돼 있다.

### HTML

한국어 공공 웹 30건에서 `BeautifulSoup CMS 선택자 + 텍스트/링크 밀도 fallback +
markdownify`가 0/30 실패, 제목 보존 100%, 본문 토큰 회수율 중앙값 0.642를 기록했다.
trafilatura는 7/30, readability는 8/30이 길이·한글량 게이트에서 실패했다.

따라서 알려진 기관 템플릿에는 선택자 레지스트리를 먼저 적용하고, 결과가 200자 미만,
한국어 문서에서 한글 50자 미만, 제목 소실이면 trafilatura로 재시도한다. 그래도 실패하면
원본을 버리지 않고 수동 검토 큐로 보낸다. 선택자 방식은 링크·메뉴를 더 남길 수 있으므로
보일러플레이트 비율 0.35 초과도 검토 신호로 둔다.

### JATS XML

Europe PMC CC0/CC BY 130건에서는 namespace-aware 직접 구조 파서가 0건 실패,
총 2.62초였고, Pandoc JATS reader는 총 31.74초였다. 표본의 토큰 회수율도 직접
파서가 약 0.908, Pandoc이 약 0.774였다. 직접 파서를 기본값으로 채택하고, XML 파싱
실패나 지나치게 짧은 결과만 Pandoc fallback으로 보낸다.

직접 파서는 섹션·표·목록·그림 캡션·참고문헌·인라인 링크를 Markdown으로 보존하지만,
복잡한 수식·각주 관계는 후속 수동 검토 대상이다.

### PDF

현재 PDF는 GOV.UK 문서 1건뿐이어서 결론은 잠정적이다. 같은 52쪽 문서에서
PyMuPDF4LLM은 82개 헤딩, 101개 목록 항목, 67개 표 행을 복원했다. PyMuPDF block
추출은 24개 헤딩, pypdf plain은 헤딩을 복원하지 못했다. 따라서 구조가 있는 PDF에는
PyMuPDF4LLM을 우선하고, 실패하면 PyMuPDF block, 마지막으로 pypdf plain을 사용한다.
PDF 표본을 더 모은 뒤 이 순서를 재검증해야 한다.

### JSON

GOV.UK API 응답처럼 이미 구조화된 JSON은 HTML 추출기를 거치지 않는다. title,
description, details, documents, links를 구조적으로 Markdown에 렌더링하고 원본 URL·
관할권·라이선스를 front matter에 보존하는 별도 변환기를 둔다.

## 공통 품질 게이트

변환 결과가 짧거나 출처가 사라졌다고 자동 삭제하지 않는다. 원본 object와 함께 실패
사유를 기록한다.

- visible text 200자 미만
- 한국어 문서의 한글 문자 50자 미만
- 제목 소실
- 보일러플레이트 비율 0.35 초과
- 비정상 반복 또는 문자 깨짐
- canonical URL·source record 연결 실패

자동 점수는 변환기 선택의 대리 지표일 뿐 최종 검색 품질이 아니다. 다음 단계에서 동일한
청킹·임베딩 조건으로 retrieval 평가를 다시 실행하고, 위험 문서는 사람이 의미 충실도·
유해한 누락·환각을 확인한다.

## 재현 명령

```powershell
uv run --with trafilatura --with readability-lxml --with markdownify --with beautifulsoup4 --with lxml `
  python experiments/doc_to_md/html_compare.py

uv run python experiments/doc_to_md/eval_doc_to_md_tests.py -v

uv run python scripts/report_acquisition.py --verify-hashes `
  --json data/scratch/acquisition_report_after_doc2md.json
```

후보 Markdown은 모두 `data/scratch/`에만 생성한다. 저장소에는 변환기 코드, 원문 없는
매니페스트, 수치 요약만 남긴다.
