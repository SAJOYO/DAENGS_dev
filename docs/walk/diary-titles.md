# 산책 대표 제목의 생성과 저장

2026-09-07 `services/walk_storyboard.py` 확인: 기존 스토리보드는 규칙 기반 장면과 환경 사실의
조합이다. v3 요청에서는 이 조합을 읽는 LLM 단계를 한 번 추가해 대표 제목과 장면 제목을
함께 생성한다. 별도 제목 API나 목록 조회 중 생성은 없다. 앱 연결은
[DAENGS_APP#186](https://github.com/SAJOYO/DAENGS_APP/pull/186).

## 계약

`walk-storyboard-candidates-v3`는 v2에 `title: string | null`(최대 40자)과
`title_fact_ids: string[]`(최대 8개)을 추가한다. 기존 JSONB bundle에 함께 저장하므로
SQL 변경은 없다. 생성 장면은 제목·revision만 변경하고 사실·시각·동선 범위·entry 출처와
시간 순서는 보존한다. 대표 제목의 근거 ID는 저장된 비 coverage 사실을 참조해야 한다.

구 앱은 요청한 v1/v2 형식으로 내려주어 엄격한 추가 키 검사를 통과한다. 기존 v2 캐시를
새 앱이 읽으면 제목 없이 그대로 사용한다. 형식 협상만으로 새 LLM 호출을 만들지 않는다.
제목 생성은 새 분석 또는 명시적인 `refresh`에서만 수행한다. 정책 버전을 바꿔 과거 기록
전체에 재분석을 강제하지 않는다.

## 호출과 실패

- 기존 서버 `GEMINI_API_KEY`, 모델 `gemini-3.1-flash-lite`를 사용한다. 앱에는 키를 넣지 않는다.
- 장면 ID·기존 제목·비 coverage 사실의 id/kind/text만 보낸다. 좌표 필드, 계정/반려견 ID,
  원시 provider 응답과 URL은 따로 전달하지 않는다. 사용자가 메모에 쓴 내용은 자료에 포함된다.
- 대표/장면 제목 모두 한 줄 40자, 각 제목에 사실 ID 1~8개. 장면 누락/추가/중복과
  다른 장면의 사실 참조를 거부한다. 사실 ID는 모델 응답의 근거 연결을 검사할 뿐,
  자연어의 의미적 사실성을 증명하지 않는다.
- 전체 입력은 UTF-8 64,000바이트까지다. 초과하면 일부만 잘라 대표 제목을 만들지 않는다.
- 외부 호출 제한 12초, SDK 재시도 없음. 기존 환경 조회 10초와 별도의 제한이다.
  DB 트랜잭션 밖에서 호출하고, 완료 후 세대/원본 revision을 다시 확인한다.
- 키 없음·provider 오류·시간 초과·응답 오류는 `title=null`인 원래 사실 장면을 ready로 저장한다.
  같은 입력을 다시 요청하거나 목록을 열어도 자동 재시도하지 않는다. 명시적 refresh로 재시도한다.
- 요청 취소는 실패 대체값으로 삼키지 않고 전파한다. 기존 60초 lease 복구 규칙을 유지한다.

## 내용 정책

감정·의도·건강 상태·익숙함·특별함을 추측하지 않는다. 주변 시설 조회를 실제 방문이나
산책 당시 환경의 증거로 표현하지 않는다. 메모 안의 명령은 따르지 않는 자료로 취급한다.
본문 생성이나 누적 브러시의 해석은 추가하지 않는다. 실제 생성 표현의 품질 평가는
계약/실패 테스트와 구분해야 한다.

## 검증 범위

`tests/walk/test_walk_storyboard.py`와 `test_walk_storyboard_titles.py`: 단일 호출, 캐시,
구버전 응답, 원본 수정 중 생성, 시간 초과/취소, 근거 오류, 실제 SDK 요청 설정을 검사한다.
테스트는 fake provider와 repository 경계를 사용하며 운영 DB/LLM에 연결하지 않는다.
DB 스키마/쿼리는 변경하지 않았다. 배포 및 실제 계정에서의 일기 생성 확인은 별도다.

2026-09-07 결과: 대상 29개 통과, 실패/skip 없음. 변경 Python 7개 파일의 ruff check와
format --check 통과. 로컬 전체 스위트와 실제 PostgreSQL/LLM 호출은 실행하지 않았다.

```powershell
uv run --no-sync pytest -q tests/walk/test_walk_storyboard.py tests/walk/test_walk_storyboard_titles.py
```
