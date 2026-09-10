# 회원별 점령지 북마크 API

[DEV #431](https://github.com/SAJOYO/DAENGS_dev/pull/431)의 6-1 서버 계약.
로그인한 회원이 관심 있는 **중립 점령지 site ID**를 저장한다.
북마크는 개인 목록이며 현재 점령지 목록과 별개다.

## 정책

- 회원당 최대 **20개**. `services/territory_bookmark.py::BOOKMARK_LIMIT`가 정책의 한 곳이다.
  앱은 응답의 `limit`을 표시한다. 한도 변경에 DB 컬럼 변경은 필요 없다.
- `(app_user_id, site_id)` 복합 PK로 중복을 막는다. 회원은 토큰에서만 결정한다.
- 강아지 등록·선택, 현재 주인, 점령 이력, 시즌, 산책 시작, 게임 활성화 여부와 독립적이다.
  장소에 처음 북마크를 해도 점령·점수·소유권 행을 생성하지 않는다.
- 회원의 북마크는 시즌 전환·점유 만료·탈취 뒤에도 유지된다. 탈퇴 시 삭제되며 재가입해도 복원하지 않는다.
  일시 정지된 회원은 조회/수정할 수 없지만 저장 데이터는 유지한다.
- 신규 저장은 Place의 현행 `/territory/sites/by-ids`에서 실제 장소를 확인한다.
  좌표나 장소 이름을 클라이언트가 보내거나 북마크 DB에 복사하지 않는다.
- 저장·해제는 멱등이다. 이미 저장한 곳을 다시 저장해도 저장 시각은 그대로다.
  20개가 찼거나 Place에 장애가 있어도 기존 북마크 재저장은 성공한다.
- 이번 단계는 북마크 저장만 제공한다. 알림 구독 필드·발송 이벤트·푸시는 7번 작업에서 추가한다.
  북마크를 저장했다고 알림이 켜졌다고 표시하면 안 된다.

## HTTP

모두 앱 회원 access token을 사용한다. 회원/강아지/시즌 ID를 요청에 받지 않는다.

| 메서드 | 경로 | 결과 |
| --- | --- | --- |
| GET | `/app/territory/bookmarks` | 내 북마크 전체, 최신 저장순(같은 시각은 site ID순) |
| PUT | `/app/territory/bookmarks/{site_id}` | 저장 또는 기존 저장 유지, 요청 본문 없음 |
| DELETE | `/app/territory/bookmarks/{site_id}` | 내 북마크 해제, 없어도 성공 |

20개 한도라 첫 계약에는 페이징이 없다. 이후 한도를 크게 늘릴 때 응답 크기/페이징도 함께 검토한다.

GET 응답 예:

```json
{
  "total_count": 1,
  "limit": 20,
  "items": [{
    "site_id": "territory-site:hex-v1:140:1:2",
    "created_at": "2026-09-10T03:00:00Z",
    "location": {"lat": 37.5, "lng": 127.0},
    "location_status": "AVAILABLE"
  }]
}
```

- `AVAILABLE`: 현행 Place 좌표 사용 가능.
- `NOT_FOUND`: 저장은 남아 있지만 현행 게임판에서 장소를 찾지 못함. 위치는 null.
- `UNAVAILABLE`: Place 장애로 좌표 조회 실패. 위치는 null.
- 뒤의 두 경우에도 항목/개수는 보존하고 해제할 수 있다. 좌표를 가짜로 복원하거나 저장을 자동 삭제하지 않는다.
- 주인/점수 정보는 저장하지 않는다. 선택 후 기존 [공개 주인 요약](owner-summary-api.md)을 조회한다.

PUT/DELETE 응답(200):

```json
{
  "site_id": "territory-site:hex-v1:140:1:2",
  "is_bookmarked": true,
  "created_at": "2026-09-10T03:00:00Z",
  "total_count": 1,
  "limit": 20
}
```

DELETE는 `is_bookmarked=false`, `created_at=null`과 해제 후 개수를 돌려준다.

| 상태 | 의미 |
| --- | --- |
| 401 | 토큰 불가·관리자 토큰 또는 활성 회원이 아님 (기존 앱 인증 계약) |
| 422 | site ID 형식 오류 |
| 404 | 신규 저장할 site ID가 현행 Place에 없음: `detail.code=territory_site_not_found` |
| 409 | 신규 저장 한도 초과: `detail={code:bookmark_limit_reached,limit:20,total_count:20}` |
| 503 | 신규 저장의 Place 확인 실패: `detail.code=territory_sites_unavailable` |

## 정합성·APP 연결

쓰기 요청은 회원 행 잠금으로 **개수 확인과 추가/삭제를 한 트랜잭션에서** 수행한다.
먼저 중복/한도를 확인한 뒤 잠금을 풀고 Place를 조회하며, 저장 직전에 회원 상태와 한도를 다시 확인한다.
따라서 동시에 마지막 빈칸에 저장하면 서로 다른 장소 중 하나만 성공하고, 같은 장소 재시도는 둘 다 성공한다.
회원 탈퇴도 같은 회원 행에서 직렬화된다.

목록은 활성 회원 확인과 저장 목록을 읽기 전용 REPEATABLE READ snapshot에서 읽고,
트랜잭션을 종료한 다음 Place를 부른다. 외부 HTTP 대기 동안 DB 연결/행 잠금을 잡지 않는다.
응답은 그 조회/변경 시점 기준이므로 이후 다른 기기의 변경은 다시 조회해야 반영된다.

6-2 APP은 진입 시 이 목록으로 ☆ 상태와 `저장 수/limit`을 구성하고, PUT/DELETE 뒤 반환 상태를 반영한다.
회원 전환·로그아웃 시 캐시를 비우고 이전 계정의 늦은 응답을 적용하지 않는다.
목록 항목은 별도 점령 지도에서 선택해 열며, `location_status != AVAILABLE`이면 이동 대신 재조회/해제를 제공한다.
점령·GPS 권한이나 OS 알림 권한을 북마크 저장의 선행 조건으로 두지 않는다.

## 적용 순서

1. 기존 DB에 `db/migrations/2026-09-10_territory_bookmarks.sql` 적용.
2. 같은 ref의 `verify_2026-09-10_territory_bookmarks.sql` 실행.
3. backend 코드 배포 후 인증된 테스트 계정으로 저장/목록/해제 확인.
4. APP 6-2를 연결.

새 DB는 `db/init/33_territory_bookmarks.sql`을 사용한다. SQL은 회원 테이블만 필요하며,
기존 게임 데이터 변경/초기화는 없다. 별도 환경변수·의존성 변경도 없다.
실제 운영 DB 적용은 구현/로컬 검증과 별도다. 기존 볼륨에는 init 파일이 자동 재실행되지 않는다.

## 검증 범위

`tests/territory/bookmarks`: 실제 HTTP 인증, 회원 격리, 중립 장소, 멱등 저장/해제,
20개 한도와 마지막 칸 동시 저장, 탈퇴 중 저장 재검사/정리/재활성화,
쓰기 실패 rollback, 읽기 snapshot, Place 장애/없어진 장소, init/업그레이드 재실행.
기존 게임·공동 돌봄 탈퇴 트리거와의 공존도 실제 DDL로 확인한다.

재사용하는 `test_territory_site_batch_lookup.py`와 마이그레이션 변조 하네스를 함께 검증한다.
전체 저장소 pytest와 실제 폰/푸시 검증은 이 단계의 타겟 범위에 포함하지 않는다.
