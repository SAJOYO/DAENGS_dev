# 내 점령지 전체 조회

[DEV #418](https://github.com/SAJOYO/DAENGS_dev/pull/418). 앱의 내 점령지 목록과 조회 전용
지도에 연결할 API다. 지도 화면에 불러온 전봇대에 한정하지 않고, 로그인 회원이 현재
보유한 점령지를 전체 또는 강아지별로 조회한다. APP의 기존 진입 화면은
[APP #260](https://github.com/SAJOYO/DAENGS_APP/pull/260)을 참고한다.

## 요청

`GET /app/territory/my-sites?limit=50`

앱 Bearer 인증이 필요하다. 회원 ID는 토큰에서 결정하며 요청으로 받지 않는다.

| 매개변수 | 계약 |
| --- | --- |
| `pet_id` | 선택. 생략하면 회원 전체, 지정하면 본인 강아지 한 마리. 타 회원 또는 없는 강아지는 동일하게 404 |
| `limit` | 기본 50, 최소 1, 최대 100 |
| `cursor` | 이전 응답의 `next_cursor`. 비어 있거나 1024자를 넘으면 422 |

거리·현재 위치·산책 세션은 입력하지 않는다. 회원 전체와 강아지별 조회는 같은
서버 점유 데이터를 사용하고, 북마크·과거 점령 이력은 포함하지 않는다.

## 응답 예시

아래 값은 가상 예시다.

```json
{
  "status": "READY",
  "season_id": "example-season",
  "server_now_ms": 1800000000000,
  "pet_id": null,
  "total_count": 1,
  "items": [{
    "site_id": "territory-site:hex-v1:140:324:777",
    "version": 2,
    "pet_id": "00000000-0000-0000-0000-000000000001",
    "pet_name": "두부",
    "pet_breed": "BICHON",
    "certification": "VERIFIED",
    "occupied_at": "2027-01-15T07:59:55Z",
    "expires_at": "2027-01-18T07:59:55Z",
    "location": {"lat": 37.5, "lng": 127.0},
    "location_status": "AVAILABLE"
  }],
  "next_cursor": null
}
```

- `total_count`는 선택 조건에 해당하는 전체 현재 점령 수다. 현재 페이지의 길이 또는
  점수 계정에 저장된 비동기 집계 수가 아니다.
- 현재 시즌의 열린 보유 구간과 실제 점유 행을 함께 확인한다. 현재 점령 강아지·claim이
  일치해야 하며, 과거 시도·인증 대기만으로 목록에 들어오지 않는다.
- `expires_at <= server_now_ms`인 영역은 목록과 개수에서 함께 제외한다. 만료 워커가
  늦어져도 조회에서 제외하며, 이 API가 소유권을 지우거나 점수를 정산하지 않는다.
- `expires_at`이 없는 기존 시즌 데이터는 null을 유지한다. 72시간을 임의로 만들어
  표시하지 않는다. 시즌 종료 이후에는 전체 목록을 비운다.
- 시즌 상태가 ACTIVE여도 `starts_ms <= now < ends_ms` 밖이면
  `status=NO_ACTIVE_SEASON`, `season_id=null`, `total_count=0`, `items=[]`를 반환한다.
- 좌표는 Place 게임판의 실제 위치다. 촬영 좌표·산책 위치·격자 중심을 대신 반환하지 않는다.
  지명·주소·거리·프로필 사진은 이 계약에 없다. 앱은 없는 지명을 임의로 붙이지 않는다.
- Place에서 해당 ID가 사라졌다면 `location=null`, `location_status=NOT_FOUND`다.
  보유 목록과 개수는 유지하고, 앱은 위치 정보 없음으로 표시해 지도 이동을 막는다.
- `version`은 기존 점유 응답의 전봇대 버전이다. 게임플레이 진입 시에는 현재 상태를
  다시 읽어야 하며 이 목록 응답만으로 점령 가능 여부를 판단하지 않는다.

## 페이지와 일관성

목록은 `site_id` 오름차순의 keyset 방식이다. 서버는 `limit+1`개까지만 읽고 다음 행이
있을 때 마지막 반환 ID로 커서를 만든다. 행 삭제로 앞 페이지의 길이가 줄어도 offset처럼
뒤 항목을 건너뛰지 않는다. `next_cursor=null`이 조회 끝이다.

한 응답의 시즌·인가·전체 개수·목록은 읽기 전용 REPEATABLE READ 스냅샷에서 읽는다.
행을 메모리에 확정한 뒤 DB 트랜잭션을 닫고 Place를 한 번 호출한다. 지도 좌표는 Place의
별도 조회 시점이므로 두 서비스 전체를 묶은 원자적 스냅샷은 아니다.

여러 페이지 전체를 고정하는 스냅샷은 아니다. 각 페이지는 현재 소유권을 다시 조회하므로
도중에 탈취·만료되면 개수가 바뀐다. 이미 지난 ID 구간에 새로 점령한 곳은 다음 새로고침에서
보인다. 앱은 ID로 합치고, 새로고침·강아지 변경 시 커서와 기존 목록을 함께 초기화한다.

커서는 회원·강아지 필터·시즌에 묶인다. 다른 회원/필터로 재사용하거나 형식이 잘못되면
400 `invalid_cursor`, 시즌이 바뀌거나 끝났으면 409 `season_changed`다. 409에서는 첫
페이지부터 다시 읽는다. 커서는 페이지 위치를 나타내는 값이며 권한 증명이 아니다.
변조 여부와 무관하게 SQL이 항상 로그인 회원의 소유권을 제한한다.

## 오류

| 상태 | 의미 |
| --- | --- |
| 401 | 앱 인증 필요 |
| 404 `pet_not_found` | 없는 강아지 또는 본인 소유 아님 |
| 400 `invalid_cursor` | 형식·버전·회원·강아지 필터가 맞지 않음 |
| 409 `season_changed` | 커서의 시즌이 현재 시즌과 다름 |
| 422 | UUID·limit·cursor 길이 검증 실패 |
| 503 `activity_disabled` | 기존 게임 기능 스위치가 꺼져 있음 |
| 503 `territory_sites_unavailable` | Place 연결 실패·시간 초과·응답 형식 오류 |

서버 실패는 보유 0곳으로 바꾸지 않는다. 빈 목록이나 활성 시즌이 없을 때는 Place를 호출하지 않는다.

## Place 연결과 배포

`GET /territory/sites/by-ids?site_ids=...&site_ids=...`를 추가한다.

- 현재 `hex-v1:140` ID를 1~100개 받고 같은 ID는 중복 제거한다.
- 응답은 `{"sites":[{"site_id":"...","lat":37.5,"lng":127.0}]}`이며 찾지 못한 ID는 생략한다.
- 실제 `territory_site`의 PK로 조회하고 현행 게임판만 반환한다. 주변 조회 반경과 무관하다.
- 기존 공개 중립 게임판과 같은 경계다. 회원·강아지·점유·촬영 정보는 받거나 반환하지 않는다.
- backend는 최대 100곳을 한 번 요청하고, HTTP 응답을 128KB로 제한한다. 예상 밖 ID,
  중복 ID, 잘못된 좌표는 정상적인 미발견과 구분해 503 처리한다.

스키마·의존성·설정·nginx 변경은 없다. 기존 `territory_site_base_url`을 사용한다.
backend와 place-search 코드가 모두 배포되어야 한다. Place가 이전 버전이면 이 API의
좌표 조회가 503으로 실패한다. 기존 주변 조회와 점령 API 계약은 유지한다.

## 검증

테스트는 `tests/territory/ownership/test_territory_owned_api.py`,
`test_territory_site_batch_lookup.py`, `tests/activity/test_owned_territories_db.py`,
`tests/place/api/test_territory_site_ids.py`에 있다. DB 테스트는
`TERRITORY_TEST_DATABASE_URL`의 **localhost 또는 127.0.0.1 / claims_test**만 허용한다.
PostGIS가 필요하며 테스트별 임시 스키마를 만들고 정리한다. 실제 검증 결과와 명령은 PR에 기록한다.
