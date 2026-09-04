# GCP `/assistant/query` 경유 Life 스모크 (A0)

> ⚠️ **아래 경로 표기는 실행 시점의 것이다.** Life 직접 API 는 #176(A4)으로 `/life/ask` · `/life/walk-conditions` 가 됐다.
> 실측 기록이라 본문은 찍힌 대로 둔다.

- 실행일: 2026-09-03 14:33 (KST)
- 대상: `https://daengapi.weareithero.cloud` — GCP VM 이 서빙하는 `main` `ba4136f` (#153 스냅샷). DB 는 09-02 덤프(코퍼스 8,990문서)
- 실행 쪽 코드: `dev` `c45bfdd` (#169 브랜치) — 요청은 curl, 판독은 응답 JSON 만
- 인증: 관리자 계정 `POST /auth/login` 쿠키 → `POST /auth/refresh` 로 access 갱신 뒤 실행. 계정·비밀번호·쿠키는 기록하지 않는다
- 최종 결과: **인프라 PASS** (4/4 HTTP 200 · Life 능력 OK · 응답 축소 확인). **답변 품질에서 A3a 근거 둘** (§3)

로드맵 행: `docs/life/roadmap.md` A0. 왜 돌렸는지는 그 행과 PR #169 본문에 있다.

## 1. 요청과 결과

질문은 전부 스모크용 합성 문장이다 (D-037 이 금하는 것은 사용자 원문).

| # | 요청 | 기대 | HTTP | 총 시간 | `status` | `results[0]` | 판정 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ①-1 | `{"query":"목줄 안 하면 과태료 얼마야","requested_capability":"life"}` | ANSWERED · OK · citations ≥ 1 (첫 요청은 예열 503 가능) | 200 | 3.0s | ANSWERED | `life` OK, 2,879ms, citations 5 | PASS — 예열 503 없음 |
| ①-2 | 같은 요청 | 같음 | 200 | 2.4s | ANSWERED | `life` OK, 2,328ms, citations 5 | PASS |
| ② | `{"query":"목줄 안 하면 과태료 얼마야"}` (힌트 없음) | 라우터가 `life` 선택 | 200 | 4.1s | ANSWERED | `life` OK, 2,585ms, citations 5 | PASS — 의미 라우팅이 `life` 를 골랐다 (라우터 종류는 응답에 없고 서버 로그에만 남는다) |
| ③ | `{"query":"우주선에 강아지 태우는 규정 알려줘","requested_capability":"life"}` | UNCERTAIN · `abstention.code == "no_evidence"` | 200 | 3.7s | **ANSWERED** | `life` **OK**, 3,602ms, citations 6 | **인프라로는 PASS, 기대와 다름** — §3-2 |
| ④ | ①-2 응답의 `results[0].data` 검사 | `content` 전문 · `score` · `chunk_id` 없음 (O-9) | — | — | — | data 키 = `answer` · `citations` · `quality`. citation 키 = `label` · `url` · `document_title` | PASS |

라우터를 지나는 요청(②)이 결정적 경로(①)보다 약 1.5초 느리다 — Gemini 의미 선택 한 번의 값이고 D-041 의 warm p50 859ms 와 결이 맞는다.

## 2. 인프라로 확인된 것

- GCP backend 의 `ml` 그룹 · 임베딩 모델 · `GEMINI_API_KEY` 가 전부 살아 있다. 셋 중 하나라도 빠졌으면 503→ERROR 또는 FAILED 가 났다 (runbook §2 표 · CLAUDE.md `uv sync` 함정).
- 예열 503 이 안 난 것은 인코더가 이미 상주 중이라서다. 컨테이너 재기동 직후에는 ①-1 이 ERROR 일 수 있고 그때는 ①-2 가 정답이다.
- `/auth/refresh` 회전이 GCP 에서 동작한다. access 쿠키 수명이 5분이라 스모크는 refresh 뒤에 바로 돌려야 한다.
- 어댑터의 O-9 축소가 배포본에 반영돼 있다 (④). 직접 `/ask` 는 전문을 그대로 낸다 (로드맵 A4 · §5).

## 3. 답변 품질 — A3a 의 근거

인프라와 별개로, 두 응답이 로드맵 §2 "이 파트가 내야 할 신호" 표의 **현재 칸이 왜 문제인지**를 실물로 보여 줬다.
둘 다 이 카드의 FAIL 이 아니라 **A3a(Life 경계 신호) 카드의 입력**이다.

### 3-1. 산문 물러섬이 OK 로 통과한다 (D-035 가 수용한 v1 한계의 실물)

①·② 세 번 모두 답변 첫 줄이 *"제시해주신 [참고자료]에는 … 과태료 규정이 포함되어 있지 않습니다"* 였고 그 뒤에 무관한
과태료(보험 미가입 · 가축전염병 명령 위반 · 화물 목록 미제출)를 나열했다. 그런데 `status=OK` · 최상위 `ANSWERED` 다.
앱은 이것을 정상 답변으로 보여 준다.

- 기계 신호는 **이미 있다**: `quality.cited == []` (답변에 조항 번호가 하나도 없음). ①·② 전부 `[]` 였다.
- 검색이 준 5건은 `동물보호법 시행령 별표 4` 의 다른 행(보험 60일 초과 · 기질평가 거부)과 `가축전염병 예방법 시행령 별표 3` 이다.
  **목줄(안전조치 의무 위반) 과태료 행이 코퍼스에 없거나 검색이 못 잡았다** — 어느 쪽인지는 이 카드가 안 본다. 로컬 서버 DB 에서
  `별표 4` 행을 세는 것은 별도 조사 (D5 자리).
- A3a 에서 볼 것: `cited == []` 이고 답변이 "자료에 없다" 형이면 ABSTAINED(`no_evidence`)로 접는 것이 D-035 "기존 기계 신호만 매핑"의
  연장선인지, 아니면 새 분류인지. 전자면 어댑터 한 줄이고 후자면 Life 카드다.

### 3-2. `no_evidence` 기권은 이 코퍼스에서 사실상 안 난다

③ "우주선에 강아지 태우는 규정" 은 근거 0건을 노린 문장인데, 하이브리드 검색이 이스타항공 동반 기준 5건과 동물보호법 제16조를
돌려줬고, 생성 모델이 *"'우주선'은 반려동물 운송 용기를 의미하는 것으로 이해하여"* 라고 **스스로 재해석한 뒤** 항공 규정을 답했다.
`quality.cited == ["제16조"]` 라 3-1 의 신호로도 안 걸린다.

- 서빙의 404(→ABSTAINED)는 hits 가 0건일 때만 난다 (`services/ask.py`). dense + FTS + RRF 는 무엇이든 상위 k 를 돌려주므로
  **근거 0건은 빈 코퍼스에서나 난다.** 로드맵 §2 의 "약한 근거 기권" 목표가 필요한 이유가 이것이다.
- A3a 에서 볼 것: 점수 문턱(코사인/RRF)이든 생성 단계의 자기보고("재해석하여")든, **어느 신호로 약한 근거를 가를지**. 골든셋에
  `expect: abstain` 문항이 있어야 어느 쪽이 맞는지 잴 수 있다 (RAG-049 ④).

## 4. 이 카드가 하지 않은 것

- 원인 조치 — 3-1 · 3-2 는 기록만 하고 A3a 로 넘긴다.
- 코퍼스 조사 — 별표 4 안전조치 행의 유무는 로컬 서버 DB 에서 따로 센다.
- Walk 스모크 — 앱의 CAUTION 문구 버그(859a691)로 이미 GCP 경로가 검증됐다.
- 벤치마크 갱신 — 라우터 선택 1건은 스모크지 측정이 아니다 (D-041).

## 5. 다시 돌리는 법

쿠키는 별도 터미널에서 관리자 로그인으로 만든다 (비밀번호가 세션 기록에 남지 않게). 그다음:

```bash
J=<cookie jar>; API=https://daengapi.weareithero.cloud
curl -s -b "$J" -c "$J" -X POST "$API/auth/refresh" -o /dev/null -w '%{http_code}\n'     # 200
curl -s -b "$J" -X POST "$API/assistant/query" -H "Content-Type: application/json" \
  -d '{"query":"목줄 안 하면 과태료 얼마야","requested_capability":"life"}'
```

`status` · `results[0].status` · `results[0].data.quality.cited` 셋만 보면 된다. 배포 직후면 두 번 보낸다.
