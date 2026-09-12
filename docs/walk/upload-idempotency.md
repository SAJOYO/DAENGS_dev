# 최초 산책 업로드의 동시 재시도

`POST /app/walks`의 멱등 키는 `(app_user_id, client_session_id)`다. 처음 저장하면
HTTP 201, 이미 저장된 산책을 반환하면 HTTP 200이며 둘 다 같은 `WalkDetailResponse`를 쓴다.
같은 키로 다른 날씨·좌표·참여견을 보내도 먼저 저장된 내용을 유지한다. 다른 회원이 같은
`client_session_id`를 쓰는 것은 독립적인 산책이다.

## 저장과 복구

기존 산책이 없을 때 Walk·참여견·좌표 청크를 한 트랜잭션에 넣는다. 게임 ON에서는 기존처럼
공통 게임 잠금을 먼저 획득하고 활동 세션 연결도 함께 저장한다. 게임 OFF에서는 두 요청이
모두 기존 행이 없음을 확인한 뒤 INSERT할 수 있다.

INSERT/flush/commit의 `IntegrityError`는 우선 rollback한다. SQLAlchemy가 보존한
원래 asyncpg 오류에서 **SQLSTATE `23505`와 제약 이름 `walks_client_session_unique`가
둘 다 일치할 때만** 기존 산책 재조회로 복구한다. 오류 메시지 문자열은 판정에 쓰지 않는다.
다른 UNIQUE·CHECK·외래키 오류는 성공으로 바꾸거나 반복 실행하지 않고 그대로 전달한다.

rollback은 기존 게임 잠금과 시즌·만료 반영도 되돌리므로 복구는 `activity_game.acquire()`부터
다시 시작한다. 기존 행은 회원과 client session으로 한 번 재조회하고 좌표·참여견을 미리 읽어
상세 응답을 만든다. 게임 ON의 활동 연결은 같은 기존 업로드 경로로 기록하고 commit한다.
충돌 직후 다른 요청이 산책을 삭제해 재조회 결과가 없으면 최초 오류를 유지한다. 새 산책을
자동 생성하거나 없는 행을 성공으로 응답하지 않는다.

스키마·요청/응답·게임 순서 정책 변경은 없다. 최초 정상 저장의 201과 재시도의 200을 유지하며,
분할 좌표 append와 finalize의 처리 방식은 별도 계약이다.

## 검증

`backend/tests/walk/api/test_walk_upload_db.py`는 기존 Territory/Activity의 폐기용 DB
fixture로 실제 SQL을 적용한다. `TERRITORY_TEST_DATABASE_URL`의 host는 loopback,
DB 이름은 `claims_test`여야 하며 앱의 DB 설정으로 대체하지 않는다. 환경 변수가 없으면
skip하므로 반드시 `-rs` 결과를 확인한다.

```powershell
# backend/ — 사전에 별도 로컬 PostgreSQL에 claims_test DB를 준비한다.
$env:TERRITORY_TEST_DATABASE_URL = 'postgresql+asyncpg://postgres@127.0.0.1:55439/claims_test'
uv run pytest -q -rs tests/walk/api/test_walk_upload_db.py
```

동시 HTTP 업로드는 실제 별도 세션과 PostgreSQL 유니크 제약을 사용한다. 게임 OFF는 두 요청의
최초 조회를 맞춰 경쟁을 강제로 재현하고, ON은 실제 공통 잠금을 그대로 사용한다. 같은 요청·
다른 내용 재전송, 회원 경계, 단일 좌표/참여견/활동 연결, 다른 제약 오류의 전파와 rollback을
검사한다. 추가 경계 테스트는 첫 조회의 시점만 대역으로 만들어 실제 충돌을 일으킨 뒤,
별도 DB 연결의 `pg_try_advisory_xact_lock`으로 복구 중 잠금을 확인하고 충돌 뒤 삭제도 검증한다.

2026-09-12 Windows의 별도 PostgreSQL 17.11에서 실행했다. 운영 DB·Redis·외부 모델은
사용하지 않았다. 실행 결과와 전체 머지 게이트의 수행 범위는 해당 PR에 기록한다.
