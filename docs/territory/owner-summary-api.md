# 전봇대 주인의 공개 시즌 성적

[DEV #360](https://github.com/SAJOYO/DAENGS_dev/pull/360). 로그인한 사용자가 선택한 전봇대의
현재 주인과 해당 강아지의 시즌 점수·점령 수를 함께 읽는다. 기존 개인 활동 API의 소유자
제한은 유지한다. 순위·사진 공개·북마크·알림은 이 계약에 포함하지 않는다.

## 요청과 응답

`GET /app/territory/owner-summary?site_id=territory-site:hex-v1:140:324:777`

기존 앱 Bearer 인증을 요구한다. site ID 하나만 받으며 형식·길이 검사는 기존 점유 조회와
같다. 사용자 ID나 조회할 강아지 ID를 받지 않고, 서버가 현재 소유권에서 강아지를 결정한다.

```json
{
  "site_id": "territory-site:hex-v1:140:324:777",
  "version": 7,
  "server_now_ms": 1800000000000,
  "season_id": "example-season",
  "status": "READY",
  "owner": {
    "pet_id": "00000000-0000-0000-0000-000000000001",
    "name": "두부",
    "is_mine": false,
    "certification": "VERIFIED",
    "occupied_at": "2027-01-15T07:59:55Z",
    "season_record": {
      "points": "13.5",
      "owned_site_count": 7,
      "score_as_of_ms": 1799999999000
    }
  }
}
```

숫자와 이름은 예시다. `pet_id`는 내부 연결에만 쓰고 UI에 노출하지 않는다. 이름과
점수는 중복될 수 있다. `is_mine`은 로그인 계정의 강아지인지 뜻한다.
점령 시각과 인증은 전봇대 소유권 정보이며, `season_record`는 주인 강아지의 시즌 성적이다.

| status | 의미 |
|---|---|
| READY | 활성 시즌의 현재 주인 점수 계정을 읽었다. 실시간 집계 완료를 뜻하지 않는다. |
| PENDING | 활성 시즌과 주인은 있지만 점수 계정이 없다. `season_record=null`, 0점으로 바꾸지 않는다. |
| UNOCCUPIED | 활성 시즌은 있으나 저장된 주인이 없다. `owner=null`. |
| NO_ACTIVE_SEASON | 현재 시각을 포함하는 활성 시즌이 없다. `season_id=null`, 성적은 null. 남아 있는 소유권은 표시할 수 있다. |

시즌 없음이 미점령보다 우선한다. 종료 처리기가 지연돼 ACTIVE 행이 남아 있어도
`starts_ms <= server_now_ms < ends_ms` 밖이면 지난 시즌 성적을 현재 성적으로 반환하지 않는다.
기존 점유 조회와 같이, 모양이 유효하지만 저장된 점유 행이 없는 ID는 version 0이며
장소의 실재 여부까지 보증하지 않는다. 지도는 Place가 제공한 site를 선택해야 한다.

## 점수·일관성·공개 경계

- 점수는 `bonus + holding_units / 36_000_000_000`을 소수 첫째 자리까지 버림한 **문자열**이다.
  APP 홈 카드와 같은 표시 기준이다. 정수 연산으로 계산해 큰 값도 float 정밀도를 잃지 않는다.
- `owned_site_count`와 `score_as_of_ms`는 같은 권위 있는 점수 계정의 `current_count`·`last_ms`다.
  비동기 statistics 캐시가 PENDING/STALE이어도 점수 계정은 별도로 읽을 수 있다. 조회 시각까지
  점수를 외삽하거나 처리기를 실행하지 않는다. UI는 필요하면 기준 시각과 함께 표시한다.
- 기존 `get_snapshot_session`의 read-only REPEATABLE READ 세션으로 site, 주인, 시즌, 계정을
  읽는다. 시즌 행 잠금·점수 정산·commit이 없고, 실제 점령 쓰기를 막지 않는다.
- 공개 응답에는 계정 ID, 산책·게임 세션·claim·보유 구간 ID, 다른 점령지 목록, 사진 URL,
  GPS 기록을 넣지 않는다. 개인 API를 호출하거나 그 응답을 통째로 직렬화하지 않는다.
- APP은 선택한 site ID, version, pet ID, season ID를 함께 확인해야 한다. 새 점유 응답을 이미
  받았으면 더 낮은 version의 성적 응답으로 덮어쓰지 않는다. 선택·계정 변경 시 이전 요청은 폐기한다.
- 응답 오류는 오류로 유지한다. 집계 실패를 빈 주인·0점으로 대체하지 않는다.

401은 인증 필요, 422는 잘못된 site ID, 503 `detail.code=activity_disabled`는 기능 비활성이다.
네트워크·DB 오류는 성공 형태로 감추지 않는다. 기존 `/app/activity/territory/{season}/pets/{pet}`와
게임 세션 조회에서 타인 리소스가 404인 동작은 그대로다.

## 준비 상태와 배포

새 테이블·마이그레이션·의존성·환경 변수는 없다. 기존 activity 스키마와
`DAENGS_ACTIVITY_GAME_ENABLED=true`, 활성 시즌 및 집계 처리 운영이 전제다.
기존 적용 순서는 [활동 게임 연결](../activity-game-integration.md)을 따른다.

2026-09-09 공개 운영 주소를 읽기 전용 HTTP로 확인했다:

- `https://daengapi.weareithero.cloud/health`: `{"status":"ok","db":"ok"}`.
- `/app/activity/seasons/current`: HTTP 404.
- `/openapi.json`: 개인 activity 조회와 기존 territory 조회는 있으나 현재 시즌 경로는 없다.

따라서 저장소 최신 코드의 배포 여부를 별도로 확인해야 한다. 인증된 운영 계정으로
시즌/점수를 읽거나 flag·시즌·워커 설정을 변경하지 않았으므로 그 상태는 미확인이다.
이 관찰은 DB migration 적용 여부나 실제 점수 집계 성공을 뜻하지 않는다.

## 검증

`test_territory_owner_summary.py`는 인증, 공개 필드 제한, 동명 강아지 교체, 조회자 소유 여부,
시즌 경계, 계정 없음과 0점 구분, 기능 비활성, 큰 정수의 소수 버림을 검증한다.
기존 `test_activity_db.py`에 공개 타인 조회/개인 조회 404, 실제 탈취 commit 전후의
read-only snapshot 일관성 검증을 추가했다. 전용 PostgreSQL CI가 이 파일을 이미 실행한다.

```powershell
# backend/에서. DB 테스트는 전용 localhost claims_test만 허용하며 팀 DB로 fallback하지 않는다.
uv run pytest -q tests/test_territory_owner_summary.py tests/test_territory_ownership_api.py tests/test_activity.py
uv run pytest -q tests/test_activity_db.py -k 'public_owner or api_owner_scope_validation_pending_and_ready'
```

APP 카드 연결과 실제 운영 두 계정 검증은 후속이다.
