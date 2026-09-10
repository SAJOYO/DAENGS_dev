# 시즌 순위와 공개 게임 프로필

DEV #428은 APP의 **순위 → 강아지 게임 프로필 → 현재 점령지** 탐색을 위한 읽기 API다.
[내 점령지 API](my-sites-api.md)는 계속 회원 전체/본인 강아지 조회로 사용한다.
공개 프로필은 다른 회원도 볼 수 있는 게임 정보이며 모든 새 경로에 앱 로그인이 필요하다.
토큰 검증 뒤 활성 회원인지를 같은 읽기 전용 스냅샷에서 확인한다. 요청 수명 동안 회원 행을
잠그는 별도 인증 세션을 열지 않아, 좌표·사진 외부 호출 전에는 인증을 포함한 읽기 연결이
종료된다. 탈퇴·없는 회원의 유효한 서명 토큰도 401로 거절한다.

## 경로

| GET 경로 | 응답 | 용도 |
| --- | --- | --- |
| `/app/territory/leaderboard?limit=50&cursor=...` | `GameLeaderboard` | 이번 시즌 참여 강아지 전체 순위 |
| `/app/territory/pets/{pet_id}/profile` | `GameProfile` | 강아지 이름·견종·얼굴 정보와 현재 시즌 성적 |
| `/app/territory/pets/{pet_id}/sites?limit=50&cursor=...` | `OwnedTerritoryPage` | 그 강아지의 현재 점령지만 지도/목록으로 탐색 |
| `/app/territory/pets/{pet_id}/photo` | 기존 `PetPhotoResponse` | 확정된 프로필 사진 읽기 주소 |

`limit`는 1~100, 기본 50이다. `cursor`는 서버가 반환한 값을 그대로 보내며 최대 1024자다.
실행 스키마는 `backend/src/daengs_backend/schemas/territory_game.py` 및 OpenAPI가 정본이다.
이번 경로는 현재 시즌 조회이며 지난 시즌 순위 선택 UI/API는 별도 범위다.

## 순위와 프로필의 공통 카드

순위 `items[]`의 각 원소는 `{pet, season_record}`다. 공개 프로필도 같은 두 필드를 쓴다.

| 필드 | 의미 |
| --- | --- |
| `pet.pet_id` | 강아지의 내부 연결 ID. 이름 중복과 무관한 식별자이며 화면에 표시하지 않는다 |
| `pet.name`, `pet.breed` | 해당 강아지의 현재 이름·견종 |
| `pet.is_mine` | 현재 회원이 대표 보호자인지. 내 점령지 API와 같은 기준 |
| `pet.has_photo`, `pet.photo_updated_at` | 확정 프로필 사진 유무·갱신 시점. 없으면 앱의 견종 그림 사용 |
| `season_record.rank` | 공동 순위. 정확한 총점 동점이면 1, 1, 3 방식 |
| `season_record.points` | 표시용 총점 문자열. 소수 첫째 자리까지 버림, 정수면 소수부 생략 |
| `season_record.base_points`, `takeover_points` | 시즌 누적 기본 보상·탈취 보너스. 구 정책은 구분 자료가 없어 null |
| `season_record.holding_points` | 표시용 누적 보유 점수 문자열 |
| `season_record.owned_site_count`, `verified_site_count` | 현재 전체·인증 점령 수. 만료를 반영한 실제 점유 조회 |
| `season_record.score_as_of_ms` | 점수 비교의 공통 정산 기준 시각 |

`season`은 `{id, starts_ms, ends_ms, policy_version, revision, score_as_of_ms}`이며,
응답의 `server_now_ms`는 현재 점령 수/만료 판정 시각이다. 사진은 순위 요청마다 발급하지 않고
앱이 보이는 강아지에 대해 별도 조회한다. 앱은 `pet_id`와 `photo_updated_at`으로 사진 캐시를
구분하고, 사진 삭제·404에는 견종 그림으로 돌아간다. 로컬 저장소 주소/세대 검사는 기존
프로필 사진 bridge를 재사용한다. 로컬 주소는 기한이 없는 기존 계약이고 GCS 주소는 만료된다.

### 같은 시점의 점수로 비교하기

게임 액션은 영향을 받은 강아지 계정을 먼저 정산하므로 계정별 `score.last_ms`가 서로 다를 수
있다. 읽기는 모든 계정의 보유 점수를 **시즌 `confirmed_ms`까지 같은 정책으로 계산**한다.
원장에 쓰거나 GET에서 워커를 돌리지 않는다. 임의로 요청 시각까지 늘리면 아직 워커가 처리하지
못한 만료 구간까지 보상을 더할 수 있으므로 확정 시각을 넘지 않는다.

순서는 `(bonus × POINT_DENOMINATOR + 보유 단위)` 내림차순이며 DB `NUMERIC`으로 정밀도를
유지한다. 소수 한 자리 표시값이 같아도 실제 점수가 다르면 순위는 다를 수 있다. 동점의 행
순서만 강아지 ID 오름차순으로 고정한다. 이는 [시즌 결산](monthly-seasons.md)의 순위 규칙과 같다.
첫 시즌 2/10점 보유 배점과 과거 정책의 계수 계산을 각각 적용하며 새 계수를 도입하지 않는다.

집계용 `statistics`가 아직 없어도 동기 기록된 점수로 순위를 읽는다. 모든 참여자는 실제
`ActivityAccount`가 있는 강아지다. 전체 등록 강아지를 0점으로 넣지 않으며, 점수나 참여 기록이
없는데 임의 순위를 만들지 않는다.

## 페이지와 변경 처리

- 순위는 정확한 점수·강아지 ID를 기준으로 다음 행을 읽는다. 커서는 시즌 및 시즌 revision에
  묶인다. 페이지 사이 점령/정산으로 revision이 바뀌면 `409 leaderboard_changed`이므로 앱은
  기존 페이지를 비우고 첫 페이지부터 새로 받는다. 시즌이 바뀌면 `409 season_changed`다.
- 공개 점령지는 강아지 ID·시즌·마지막 site ID를 기준으로 읽는다. 회원 ID를 커서에 담지 않으며,
  다른 강아지 또는 내 점령지/순위용 커서를 섞으면 `400 invalid_cursor`다.
- 각각의 응답은 별도 읽기 전용 REPEATABLE READ 트랜잭션이다. 순위·프로필을 서로 다른
  HTTP 요청으로 열면 그 사이의 실제 변화가 반영될 수 있다. 여러 요청에 걸친 영구 스냅샷은
  아니다. 탈퇴로 순위 대상이 줄면 같은 시즌 revision에서도 순위/전체 수가 줄 수 있다.
- 점령지는 페이지 사이 탈취·만료가 허용되는 실시간 목록이다. `total_count`는 각 응답 시점의
  개수이므로 이전 페이지 수와 합산하지 않는다. 새로 고침할 때 커서를 버린다.
- 점령지 목록과 카드의 점령 수는 같은 실제 점유·시즌의 열린 보유 기간 조건을 쓴다.
  계정의 정산 시점 점령 수를 현재 목록으로 간주하지 않는다.
- 공개 강아지 목록에는 돌보미가 그 강아지로 점령한 곳도 포함한다. 강아지별 시즌 계정과
  맞추기 위한 조회 범위이며, 액션 회원에게 귀속된 소유/보상 원장은 바꾸지 않는다.
  기존 내 점령지의 회원·대표 강아지 필터와는 목적이 다르다.
- 좌표는 이번 페이지 ID만 Place `/territory/sites/by-ids`로 조회한다. DB 트랜잭션은 외부
  호출 전에 닫는다. Place에 없는 항목은 개수/목록에 남겨 `location=null`,
  `location_status=NOT_FOUND`로 표시한다. Place 장애는 503이며 빈 목록 성공으로 숨기지 않는다.

## 상태와 오류

| 응답 | 앱 처리 |
| --- | --- |
| 순위 `READY`, `total_count=0` | 현재 시즌은 있지만 아직 참여자가 없음 |
| `NO_ACTIVE_SEASON` | 활성 시즌 없음/아직 시작 전/끝났지만 워커 처리 전. 점수·순위 없음 |
| 프로필 `NOT_PARTICIPATING`, `season_record=null` | 과거에는 참여했지만 이번 시즌에는 아직 참여하지 않음 |
| `401` | 앱 로그인 필요 |
| `404 pet_not_found` | 없는/삭제된/게임에 참여한 적 없는 강아지. 원래 사적 프로필을 공개하지 않음 |
| `404 no_photo` | 확정 프로필 사진 없음 |
| `400 invalid_cursor` / `422` | 잘못된 커서 / 쿼리·UUID 범위 오류 |
| `409 leaderboard_changed` / `season_changed` | 페이지 초기화 후 다시 조회 |
| `503 activity_disabled` | 기존 게임 스위치 OFF |
| `503 territory_sites_unavailable` / `photo_unavailable` | 좌표 제공 서비스 / 사진 저장소 준비 문제 |

공개 대상 여부는 현재 또는 과거 `ActivityAccount` 존재로 확인한다. 공개 API는 회원 ID,
건강·돌봄 정보, 산책 경로, 사진 인증 증거, 세션/시도 ID와 보상 원장 출처를 반환하지 않는다.
다른 회원의 기존 `/app/activity/territory/{season}/pets/{pet}` 상세나 `/app/pets/{pet}`에 대한
접근 권한은 추가하지 않는다. 프로필 사진 경로도 게임 참여 강아지의 **확정된 프로필 사진**만
선택하며 업로드 대기·방문 인증·산책 사진은 선택하지 않는다.

## 적용과 검증

기존 Activity/점유/프로필 사진 테이블과 설정을 사용한다. 추가 마이그레이션·환경 변수·의존성은
없다. 좌표 조회는 #418의 Place ID 일괄 조회가 배포되어 있어야 한다. 이번 PR은 게임을 켜거나
시즌을 시작하지 않는다. APP 화면 연결은 다음 단계다.

주요 검증:

- `tests/activity/test_public_game_db.py`: 격리 PostgreSQL에서 큰 정수의 1단위 차이,
  공동 순위·페이지·최종 순위 일치, 서로 다른 계정 정산 시각, 구/신 정책 산식,
  탈취·지연 만료·시즌 교체·삭제·repeatable-read 경계.
- `tests/territory/ownership/test_public_game_api.py`: 실제 등록 라우트의 인증·게임 스위치,
  응답 공개 범위·커서·상태, Place 실패와 사진 조회의 트랜잭션 종료 순서.
- 기존 내 점령지, 공개 주인 요약, 월간 결산 및 앱 조립 회귀 테스트.
