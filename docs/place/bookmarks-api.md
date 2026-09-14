# 회원 시설 찜

시설 검색 목록·상세에서 저장한 시설을 아래 `찜한 시설` 탭에서 다시 조회한다.
회원 관계는 APP backend의 basicPostgres `place_bookmarks`가 소유하고,
시설의 현재 사실·좌표·반려견별 판정은 Place PostGIS가 소유한다.
Geo 실험 저장소와 공개 Place API에는 회원 저장 기능을 추가하지 않는다.

## 회원 API

모든 요청은 APP 회원 Bearer 인증이며 active 회원만 허용한다.
관리자 토큰·미등록·정지·탈퇴 회원은 거절한다.

| 요청 | 동작 |
| --- | --- |
| `GET /app/places/bookmarks` | 회원의 전체 저장 목록, 최신 저장순 |
| `PUT /app/places/bookmarks` | JSON `{source, ref}`의 시설 저장, 반복 요청은 멱등 |
| `DELETE /app/places/bookmarks?source=…&ref=…` | 인코딩한 키의 찜 해제, 없는 키도 성공 |
| `POST /app/places/bookmarks/search` | JSON `{filters: {...}}`, 회원 전체 저장 키로 현재 시설 조회 |

목록·쓰기 응답은 `contract_version: "place-bookmarks-v1"`, `total_count`,
`limit: 200`, `items: [{key: {source, ref}, name, created_at}]`이다.
목록에는 숨은 페이지나 지도 반경 상한이 없다. 200개 초과 저장은
409 `place_bookmark_limit`; 없는 시설 저장은 404 `place_not_found`다.
이미 저장한 시설의 PUT과 DELETE는 Place 장애 중에도 가능하다.

키는 원천 `kcisa`, `kto`, `public:mois:animal_hospital`,
`public:mois:animal_pharmacy`와 1~256자 ref다. 이름·좌표는 키로 쓰지 않는다.
저장 당시 서버에서 읽은 이름은 원본이 사라졌을 때 찜 해제에 쓰는 보조 정보다.

## 전체 저장 시설 조회와 필터

backend는 회원 키 목록을 읽은 DB 트랜잭션을 끝내고,
기존 `place_search_base_url`의 내부 `POST /internal/place/bookmarks/lookup`으로
`{keys: [{source, ref}], filters}`를 전달한다. 회원 ID·토큰은 전달하지 않는다.
이 내부 경로를 nginx의 공개 Place 경로에 추가하지 않는다.

필터는 `lat`/`lng`(함께 생략 가능), `radius_m`(100~20000, 생략 시 지역 제한 없음),
`kinds`(빈 목록은 전체 업종), `name_query`(120자 이하 부분 일치),
`parking`(주차 우선 정렬), `hard: {all, any}`(기존 필수 조건 계약),
`dogs`(기존 반려견 snapshot 최대 20개)다. 반경은 기준 좌표가 있어야 한다.
필수 조건의 미확인은 충족으로 보지 않으며, 주차 불가도 원본 근거가 있어야 인정한다.
반려견 snapshot은 기존 시설 규칙 평가에만 사용하며 저장 소유자를 나타내지 않는다.

조회 응답은 전체 저장 목록에 `filters`(정규화한 적용 조건), `distance_available`,
`hits`(기존 PlaceSearchHit), `missing_keys`를 더한다.
필터 탈락과 원본 소실은 구분한다. 숨겨진 병합 원본·비활성 의료 시설은 missing이며
다른 원천 키로 자동 변경하지 않는다. 앱은 missing의 저장 이름과 해제 버튼을 보여 준다.
기준 좌표가 없으면 거리는 사용하지 않고 저장순으로 표시한다.
앱은 `distance_available: false`의 내부 거리값 0을 표시하지 않는다.

내부 조회는 최대 200키·응답 4MB·15초로 제한한다. 잘못된 필터는
422 `invalid_bookmark_filters`, Place 통신·응답 실패는 503 `place_lookup_unavailable`이다.
실패를 빈 찜 목록으로 바꾸지 않는다.

## 경합·계정 전환

저장 전후의 회원 row lock으로 한도와 active 상태를 다시 확인한다.
외부 HTTP 중에는 DB 트랜잭션·회원 lock을 유지하지 않는다.
동시 저장도 최대 200개이며, 탈퇴 처리 중인 회원을 재확인해 저장을 막는다.
실제 회원 삭제는 FK CASCADE, soft withdrawal은 status 트리거로 찜을 정리한다.

앱은 로그인 생애별 controller와 응답 generation을 사용한다.
계정 전환·동일 회원 재로그인 후 옛 응답을 버리고, 저장 응답이 불확실하면 목록을
재조회할 때까지 하트 상태를 확정하지 않는다. 검색 탭과 찜 탭의 조건·입력·카메라는
독립 보관한다. 해제 후 실행 취소는 동일 키를 다시 저장한다.

## 적용과 검증

1. 기존 basicPostgres에는 `db/migrations/2026-09-11_place_bookmarks.sql`과
   `verify_2026-09-11_place_bookmarks.sql`을 적용한다. 새 볼륨은 `db/init/34_place_bookmarks.sql`.
2. Place 내부 조회와 APP backend를 함께 배포하고 회원 API를 확인한다.
3. 앱 연동 PR [DAENGS_APP#304](https://github.com/SAJOYO/DAENGS_APP/pull/304)를 반영한다.

새 환경 변수·Place DB 마이그레이션·외부 공개 경로 변경은 없다.
마이그레이션을 되돌릴 때 테이블을 삭제하면 찜이 사라지므로 앱/서버 코드를 먼저 되돌리고
추가된 테이블은 보존한다.

로컬 검증은 `backend/`에서 아래 범위로 한다. 회원 DB fixture는
`TERRITORY_TEST_DATABASE_URL`에 **일회용 127.0.0.1의 claims_test**를 요구한다.
Place 테스트는 `DAENGS_PLACE_DATABASE_URL`에 별도 일회용 PostGIS를 지정하고
`uv run alembic -c infra/place/alembic.ini upgrade head`로 기존 스키마를 준비한다.

```sh
uv run pytest -q -rs tests/place_bookmarks tests/place/search/test_bookmark_lookup.py tests/place/test_boundary.py tests/place/search/test_search_v2.py tests/place/search/test_name_search.py
uv run check
```

DB 변경에 필요한 SQL 변조 검증은 [CI 안내](../ci/README.md)의
`tools/check_migration_verification.py sql` 하네스에 등록되어 있다.
실제 DB를 지정하지 않아 skip된 검사는 통과로 세지 않는다.
