# 실제 산책 → 장면 분석 → app 검토

아래는 현재 서버의 규칙 기반 분석 구현을 설명한다.
전체 이해 → 장면별 갱신 → 재검토 → 검토본 → 선택적 일기는 geo에서 실험 중이며,
설계와 구현 범위의 기준은 DAENGS_geo `docs/explorations/walk/diary-storyboard-plan.md`다.

GPS 업로드·finalize와 행동/메모 동기화가 끝나면 app의 기존 WorkManager 작업이
`POST /app/walks/{walk_id}/storyboard`를 호출한다. 서버는 저장된 GPS chunk를 검증하고
`daengs_walk.analyze_walk`의 관측 구간으로 장면을 만든다. 합성 경로·시뮬레이터 정답·LLM은
실행 경로에 들어가지 않는다. scene builder/selector는 DAENGS_geo의
`app/features/storyboard/scenes.py`, `selection.py`와 같은 코드다.

## 교환 계약

요청은 `expected_entries: {entry_id: revision}`와 선택적인 `refresh: false`, `bundle_format`이다.
삭제 표식까지 포함한 **현재 기록 전체**를 대조한다. 누락·다른 버전은 409이므로 먼저
기록을 동기화해야 한다. 다른 회원의 산책은 404, 미봉인 GPS도 409다.

응답은 `session_id`(app의 client_session_id), `generation`, `input_revision`,
`status`, `entry_revisions`, `bundle`, `error_code`다. GET은 같은 경로에서 현재 상태를
읽는다. bundle_format을 생략하면 기존 v1, `walk-storyboard-candidates-v2`이면 v2이며
실데이터 경로는 synthetic=false다. GET도 같은 이름의 query로 형식을 선택한다.
준비된 최신 결과에만 bundle을 돌려준다. status는 pending/running/ready/failed/stale이다.

v2는 장면별 entry_id/revision/pet_id와 bundle.selection의 조회 목표·달성 여부·미달 사유·
긴 빈 구간·예산으로 보류한 행동 조회 수를 보존한다. 귀속/버전은 장면 revision에도 포함한다.
조회 진단은 종료 장면의 coverage facts에도 넣어 v1과 검토 완료 사본에서 읽을 수 있다.
규칙 버전 live-storyboard-v2로 기존 캐시를 재생성하고 canonical v2 하나를 JSONB에 보관한다.
v1 변환은 추가 필드만 제거하며 장면 revision/진단 facts는 보존한다. 형식 선택만으로 재분석하지 않는다.
새 SQL·의존성 없이 적용하며 서버를 먼저 반영한 뒤 v2 요청 앱을 배포한다.
계약 원본: DAENGS_geo `docs/contracts/walk-storyboard-candidates-v2.md`.

같은 원본의 ready 결과는 재사용하며 refresh=true만 환경을 다시 조회한다. 분석 중
요청은 같은 running 세대를 돌려준다. 실패 또는 60초가 지난 running은 다음 POST가
새 세대로 재시도한다. HTTP 요청 안에서 계산하되 running 상태를 먼저 커밋하므로
프로세스 종료·응답 유실 뒤에도 WorkManager 재시도로 복구할 수 있다.

## 현재 코드의 선택 규칙과 근거

아래는 운영 코드의 동작 설명이다. geo에서 실험하는 LLM의 장면 선정 정책과 구별한다.

- 최소 목표 4, 분리 거리 100m, 긴 빈 구간 300m, 조회 지점 최대 8의 초안을 재사용한다.
- 현재 코드는 액션 → 이전 산책 대비 이동 변화 → 세션 속도 변화 → 빈 거리 보충 순서다.
- 이전 비교는 같은 회원·같은 **한 마리만** 동행한 최근 3개 과거 봉인 산책을 사용한다.
  각 산책의 관측 이동속도 중앙값을 다시 계산하며 3개 모두 유효해야 비교한다.
  공간별 성향이나 개인화 프로필을 추정하지 않는다. 비교 산책 ID는 사실 출처에 남긴다.
  이 동작은 과거 기록의 공간적 경향 활용을 구현한 것이 아니다.
- GPS 공백은 이동·정지로 채우지 않는다. 구간에 붙일 수 없는 메모는 route=null이다.
- Place 서비스의 `/v2/places/search`를 지점별 250m로 조회한다. 현재 범위는 여가 시설·
  카페·음식점, 종류당 최대 3곳이다. 지점 8개, 동시 4개, 개별 3초·전체 10초 상한이다.
  등록 위치와의 거리만 사실로 쓰며 방문, 공원 내부, 혼잡도, 행동 원인으로 바꾸지 않는다.
- 조회 실패/일치 없음은 환경 자료 미확인으로 남긴다. 자료를 못 읽어도 시작·종료·관측
  기록이 있는 장면 구성은 만들어진다. 데이터 원천은 기존 Place 인덱스 범위다.
- 전체 환경 조회가 10초를 넘겨도 같은 원칙을 적용한다. 조회 작업을 취소하고 선택 지점의
  Place 출처를 unavailable로 남겨 관측 기반 장면을 ready로 저장한다. 다음 일반 요청은
  결과를 재사용하며 refresh=true로 환경을 다시 조회한다. HTTP 요청 자체의 취소와 원본
  분석 오류는 이 fallback으로 숨기지 않는다. 재분석 중 원본 변경/새 세대 게시 방어도 유지한다.

## 변경과 경쟁 처리

원본 revision은 계산 입력·기록 revision/payload·정책 버전·비교 산책 ID를 포함한다.
Walk 행 잠금으로 분석 세대를 할당하고 외부 조회 전에 커밋한다. 조회 후 다시 잠가
원본 revision과 generation이 모두 같을 때만 결과를 게시한다. 같은 잠금을 쓰는
기록 정정/삭제가 먼저 커밋하면 이전 계산은 게시되지 않는다. GET 역시 현재 원본과
대조하므로 기록 삭제 직후 이전 결과를 최신으로 반환하지 않는다.

app은 별도 Room `walk_scene_analysis`에 결과를 저장한다. 사용자 문구·숨김·마지막
검토본 테이블을 덮어쓰지 않는다. 로컬 입력 stamp와 계정 및 generation을 트랜잭션에서
대조해 지연 응답을 거절한다. 최신 원본이 준비되면 동일 ID에 사용자 편집을 결합한다.
실제 장면 ID의 start/end/entry:<id>는 기존 로컬 장면과 대응한다.

## 적용 및 검증

기존 DB에는 **`db/migrations/2026-09-05_walk_storyboards.sql`을 먼저 적용**해야 한다.
새 볼륨은 `db/init/20_walk_storyboards.sql`을 사용한다. 별도 geo 프로세스나 새 비밀키는
필요 없고 기존 `DAENGS_PLACE_SEARCH_BASE_URL` 설정을 사용한다. app은 Room 9→10이다.
이번 작업에서 운영 DB migration이나 배포는 실행하지 않았다.

```powershell
uv run pytest -q tests/walk/test_walk_storyboard.py
$env:LIVE_STORYBOARD_TEST_DSN = 'postgresql://postgres@127.0.0.1:55439/postgres'
uv run pytest -q tests/walk/test_walk_storyboard_db.py
```

DB 검증은 Docker 없이 별도 로컬 PostgreSQL에서 수행했다. 테스트는 loopback만 허용하고
임의 이름의 격리 스키마에서 실제 SQL 적용(중복 적용 포함), 업로드/finalize/기록 API,
분석 중 정정·더 최신 계산 게시·늦은 응답, 삭제 후 재분석, 회원 삭제 CASCADE를 확인한다.
Place 응답은 이 테스트에서 통제한 응답을 사용한다. 운영 인덱스 내용과 실기기 동작은
배포 후 확인이 필요하다. AI 일기 생성은 이 단계에 포함하지 않는다.

