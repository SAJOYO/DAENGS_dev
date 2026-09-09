# 인증 우선 점령 정책 v2

2026-09-08, DEV #335 / APP #205. 서버의 시즌 `rules.version`이
`certified-protection-v2`일 때 적용한다. 이미 저장된 `draft-2026-09-06` 시즌은 변경하지 않는다.

| 현재 점령 | 다른 강아지의 무사진 점령 | 사진 도전 |
| --- | --- | --- |
| 미점유 | 가능 | 가능 |
| 미인증 | 불가 (`PHOTO_REQUIRED`) | 즉시 가능 |
| 인증·보호 중 | 불가 | 촬영 전 차단 |
| 인증·보호 종료 | 불가 | 새 사진으로 가능 |

같은 강아지가 자신의 미인증 영역을 인증하면 최초 획득 `occupied_at`은 그대로 두고
`certified_at`을 판정 확정 시각으로 기록한다. 보호는 그 시각 + 600,000ms이며
`server_now >= protected_until`부터 탈취 가능하다. 인증 강화에 획득 보너스는 없다.
이미 인증한 자기 영역을 재촬영하여 보호를 연장할 수 없다.

## 서버와 앱의 순서

1. `/app/territory/occupancies` 및 claim 응답에 `server_now`, `season_id`, `policy_version`,
   occupancy의 `certified_at`, `protected_until`을 포함한다. 미인증의 보호 종료는 null이다.
2. 기존 MARK는 산책/site당 하나인 claim의 식별자와 참여견을 유지한다. 촬영 진입 전
   `GET /app/territory/claims/{claim_id}/photo-access`로 서버의 `allowed_action`을 확인한다.
   `PHOTO_TAKEOVER`/`PHOTO_UPGRADE`만 카메라 진입을 허용한다. `WAIT`에는 보호 종료가 있다.
3. 셔터 직전 새 UUID로 `PUT /app/territory/claims/{claim_id}/challenges/{capture_uuid}`,
   body `{"expected_site_version": N}`을 보낸다. 응답은 `challenge_id`, `expires_at`이며
   30초 유효하다. 동일 UUID/입력은 재전송이고 다른 입력은 identity conflict다.
4. 기존 `/attempts`의 `client_capture_id`에 같은 UUID를 사용하고 기존 claim/photos 경로에
   연결한 뒤 업로드한다. 서버는 claim·시즌·유효기간·촬영 시각·현재 site version·보호·소유자·
   세션/참여견을 다시 확인한다. 기존 촬영 위치/10m/신선도 검사는 유지한다.
5. 판정은 방문 인증과 점령 여부를 따로 보존한다. 경합 시 `site_changed` 또는 `protected`,
   시즌이 바뀌면 `season_ended`를 기록하고 소유권과 점수는 지급하지 않는다.

촬영 허가는 소유권 예약이 아니다. 허가와 판정 사이에 다른 사람이 먼저 확정하면 거절될 수 있다.
지연된 방문 판정은 유지하되 현재 claim의 사진과 일치하는 callback만 점령에 영향을 준다.
판정 이후에는 동일 요청을 재전송해도 소유권·보너스를 다시 지급하지 않는다.

## 같은 산책에서 재도전

`UNIQUE(session_id, site_id)` claim은 유지하고 자식 `territory_challenges`를 추가한다.
각 자식은 예상 version/시즌/사진 연결/완료 시각/거절 코드를 보존한다.
보호 차단은 claim을 소모하지 않는다. 경합 패배·사진 부적합·처리 실패 뒤에도 현재 산책이
RECORDING이고 현장 위치가 유효하면 새 challenge UUID와 새 사진으로 다시 도전한다.
현재 사진이 PENDING이면 중복 도전을 막는다. 종료된 산책은 재사용하지 않는다.
구버전 시즌의 종결 제한은 그대로다. 구버전 앱은 v2 사진 연결 시 `challenge_required`로 거절된다.

## 메인 화면과 현재 시즌

인증된 `GET /app/activity/seasons/current`는 `server_now_ms`와
`season: {id, starts_ms, ends_ms, policy_version}` 또는 명시적인 null을 반환한다.
flag 비활성은 기존대로 503 `activity_disabled`다. 조회가 시즌을 만들거나 종료하지 않는다.
앱은 선택 강아지의 시즌 점수/현재 점령 수를 홈 산책 기록 버튼 아래에 표시한다.
현황을 누르면 시즌 종료까지 남은 시간·탈취 횟수와 산책 지도 진입을 제공한다.
화면이 STARTED일 때만 30초 간격으로 조회하며 계정/대표견 변경 시 화면 상태를 새로 만든다.
실패·집계 대기를 0점으로 위장하지 않는다. 시간 점수는 서버 정산 값이고 활동 처리기의 실행이 필요하다.

## 반영 순서

1. 기존 #260/#281 SQL을 전제로 `db/migrations/2026-09-08_certified_territory.sql`을 먼저 적용하고
   `verify_2026-09-08_certified_territory.sql`을 실행한다. 신규 DB는 init/24가 대응한다.
   기존 인증 시각은 저장된 적이 없어 `occupied_at`으로 보수적으로 backfill한다.
2. 웹과 사진 판정 worker를 같은 버전으로 반영한다. 기존 활동 집계/시즌 종료 worker 및
   스케줄러도 운영 설정에서 실행되어야 한다. 배포/시즌 관리 명령은 기존 activity 문서를 따른다.
3. 새 앱을 준비하고 기존 시즌을 종료한 뒤 모든 Rules 필드를 명시한 JSON의 version을
   `certified-protection-v2`로 지정하여 새 시즌을 만든다. 활성 시즌 rules를 SQL로 덮어쓰지 않는다.
4. 서버 activity flag와 앱 server-actions 설정은 기존의 명시적 활성화 절차를 따른다.
   이 변경은 운영 flag, 배포, 시즌 생성, 실기기 설치를 자동 수행하지 않는다.

GEO는 같은 정책의 검증 환경이다. GEO 0035는 독립 policy DB의 certified_ms를 추가하며
DEV의 운영 DB를 대체하거나 중계하지 않는다. GEO와 DEV policy의 기존 import 시간 허용 차이는 유지한다.
