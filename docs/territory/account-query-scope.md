# 일반 점령의 계정 조회 범위

`activity_game.transition()`은 신규 주인과 기존 주인의 강아지 ID 집합을 계산하고,
`accounts_for_pets(db, season_id, pet_ids)`로 해당 시즌의 관련 계정만 읽는다.
빈 집합은 계정을 읽지 않는다. 같은 강아지의 사진 인증은 한 계정만 읽으며, 아직 계정이 없는
강아지는 기존 점수 정책에 따라 새 계정을 만든다.

기존 `accounts(db, season_id)`는 전체 시즌 계정 조회를 유지한다. 시즌 전체의 보유 시간
정산·최종 순위 확정과 영역 만료 일괄 처리는 별도 경로다. 이번 변경은 일반 점령의 계정
로딩 범위를 제한하며 모든 게임 요청의 조회량을 두 행으로 제한한다는 뜻은 아니다.

게임 공통 잠금 → 시즌/점유 처리 순서와 점수 계산, 보상 원장, 판정 영수증, commit/rollback
경계는 유지한다. DB 스키마·API·게임 규칙·의존성 변경은 없다.

## 검증

`backend/tests/activity/test_account_queries_db.py`는 폐기용 PostgreSQL의 실제 점령·
사진 인증·탈취·재전송 경로에서 SQLAlchemy가 로딩한 계정 행을 센다. 무관한 계정이
0개일 때와 250개일 때를 구형 정책과 첫 시즌 보상 정책 각각에서 비교한다.

- 최초 점령에서 계정이 없으면 무관한 계정을 읽지 않고 생성
- 자신의 사진 인증에서는 한 계정, 기존 계정 사이 탈취에서는 두 계정만 로딩
- 완료된 사진의 중복 전달에서는 계정을 다시 로딩하지 않음
- 시즌 종료에서는 무관한 계정을 포함한 전체 계정의 최종 점수·순위 확정
- 빈 강아지 집합, 다른 시즌, 중복 ID·존재하지 않는 ID의 조회 경계 유지

```powershell
# backend/ — 별도 loopback PostgreSQL의 claims_test만 사용
$env:TERRITORY_TEST_DATABASE_URL = 'postgresql+asyncpg://postgres@127.0.0.1:55439/claims_test'
uv run pytest -q -rs tests/activity/test_account_queries_db.py
```

2026-09-12 PostgreSQL 17.11/Windows에서 검증했다. 수정 전에는 신규 점령 한 번에
무관한 250개 계정을 모두 로딩하는 것을 두 정책에서 재현했다. 수정 후 신규 테스트 5건이
통과했다. 관련 기능 회귀 결과와 전체 머지 게이트의 수행 범위는 PR에 기록한다.
