# 온라인 점유 저장·API

> 2026-09-08 후속: [인증 우선 정책 v2](../certified-territory-v2.md). 아래의 획득 시각 보호·새 산책 필수 규칙은 `draft-2026-09-06` 시즌에 한정한다.

[DAENGS_dev#260](https://github.com/SAJOYO/DAENGS_dev/pull/260)는
[Geo 제작 계획](https://github.com/rkbuhtig/DAENGS_geo/pull/230)의 4단계 중 서버 부분이다.
APP의 지도·접근 피드백은 [DAENGS_APP#155](https://github.com/SAJOYO/DAENGS_APP/pull/155)를
참조한다. APP 서버 공급자 교체와 실제 기기 검증은 후속 작업이다.

## 저장과 판정

- `territory_claim_sessions`: 계정에 귀속된 진행 중 게임 세션. 로컬 산책 UUID, 시작 시각,
  참여견 목록을 최초 등록 때 고정하고 RECORDING / PAUSED / ENDED와 버전을 저장한다.
  기존 `walks`는 종료된 산책 업로드용이므로 열린 Walk 행을 만들지 않는다.
- `territory_claim_sites`: 장소별 점유 버전과 잠금 기준. 중립 게임판/좌표 원본은 계속 Place다.
- `territory_claims`: 세션·장소당 한 시도. 선택한 강아지와 최초 접촉 근거, 판정 결과를 보존한다.
- `territory_occupancies`: 현재 소유권. 원본 시도를 통해 강아지·계정·산책을 추적한다.
- `territory_claim_photos`: 촬영 시도와 게임 시도의 연결. 촬영 하나를 두 시도에 재사용할 수 없다.

미점령이면 사진 없이 UNVERIFIED로 점유한다. 인증된 타인의 영역에는 PHOTO_REQUIRED를
반환하고 사진 결과를 기다린다. 타인의 미인증 영역에 무사진 액션을 하면 기존
POLICY_UNDECIDED를 유지한다. 어느 경우든 사진 인증으로 점유를 시도할 수 있다.
대표견은 세션 전체의 대표가 아니라 **장소별 시도에 고정**된다.

세션·장소 UNIQUE와 장소 행 잠금으로 재전송/동시 요청을 처리한다. 같은 시도의 사진 강화는
같은 점유이며 최초 점유 시각을 보존한다. `GRANTED`는 과거 시도의 결과이므로 현재 주인은
항상 응답의 `site.occupancy`를 읽는다.

사진 판정 중 장소 버전이 바뀌면 방문 인증은 VERIFIED로 남기고 게임 응답에는
`resolution_code: "site_changed"`를 기록한다. 새 점유를 덮어쓰거나 시도 버전을 몰래 갱신하지
않는다. 그 시도는 종료되며 다시 탈취하려면 새 산책에서 새 접촉·촬영이 필요하다.
동시 인증 중 먼저 점유를 확정한 트랜잭션이 이긴다. 이탈/재진입·쿨다운·점수 정책은 추가하지 않는다.

## APP 호출 순서

선택한 주인의 시즌 점수·점령 수 공개 조회는 [주인 요약 API](owner-summary-api.md)를 참조한다.

모든 아래 API는 앱 회원 인증을 요구한다. owner ID는 요청으로 받지 않는다.

| 메서드·경로 (`/app/territory` 아래) | 역할 |
| --- | --- |
| `GET /occupancies?site_ids=…&site_ids=…` | 최대 100개 장소의 현재 상태. 산책 시작 없이 조회 |
| `PUT /claim-sessions/{client_session_id}` | 시작 시각·참여견 등록. 동일 본문으로 재시도 |
| `GET /claim-sessions/{client_session_id}` | 내 게임 세션 복구 |
| `PATCH /claim-sessions/{client_session_id}` | phase와 expected_version으로 일시정지·재개·종료 |
| `POST /claims` | 접촉 근거를 확인하고 장소별 시도 생성 또는 동일 시도 반환 |
| `GET /claims/{claim_id}` | 내 시도 결과와 현재 점유 조회. 사진 대기 중 polling |
| `PUT /claims/{claim_id}/photos/{photo_id}` | 기존 촬영 API가 발급한 photo attempt를 게임 시도에 연결 |

1. 둘러보기는 Place에서 받은 장소 ID로 occupancies만 조회한다. 반환한 `occupancy: null`은
   저장된 점유가 없다는 뜻이며 장소의 존재를 보장하지 않는다. 실제 액션 때 Place에서 다시 확인한다.
   다른 회원에게는 강아지 ID·이름, 인증 여부·점유 시각·`is_mine`만 보여준다. 계정 ID, 산책/시도 ID,
   사진 경로, 접촉 좌표는 공유하지 않는다.
2. 산책을 시작하면 아래처럼 등록한다. 참여견은 본인 소유이며 배웅되지 않은 강아지여야 한다.
   UUID는 로컬 산책과 동일하게 유지한다. 종료 후 PUT을 재전송해도 RECORDING으로 돌아가지 않는다.

   ```json
   {"started_at":"2026-09-05T14:00:00Z","pet_ids":["00000000-0000-0000-0000-000000000001"]}
   ```

3. 점령지를 선택하고 접근했을 때 POST /claims에 `client_session_id`, `site_id`,
   `claiming_pet_id`, `observed_at`, `lat`, `lng`, `accuracy_m`, `is_mock`을 보낸다.
   서버는 RECORDING, 참여견, 현행 게임판 좌표를 검사하고 **거리 + GPS 오차 ≤ 20m**를 요구한다.
   접촉 시각은 세션 시작 이후이며 수신 시점에서 30초 이내(미래 오차 최대 5초)여야 한다.
   이 신선도 검사는 오래된 위치를 새 접촉으로 제출하지 않기 위한 입력 검증이다.
4. APP은 최초 요청 본문을 저장하고 응답 유실 시 **같은 본문**을 재전송한다. 관측 시각이나
   대표견을 바꿔 새 요청으로 만들면 `attempt_identity_conflict`다. 이미 저장된 동일 요청은
   위치가 오래됐거나 산책이 끝났어도 기존 시도를 돌려준다. 다른 장소의 시도는 허용한다.
5. 인증 촬영은 기존 `POST /app/territory/attempts`를 사용한다. **영역표시 시도가 생성된 뒤
   촬영**해야 하고, 기존 사진 API의 **거리 + 오차 ≤ 10m** 조건은 유지된다. 20m 접근 범위와
   사진 인증 범위가 다르므로 APP 서버 연동에서 촬영 가능 안내를 구분해야 한다.
6. 티켓 발급 직후, 업로드/confirm 전에 게임의 photos PUT을 보낸다. 촬영 시각·회원·세션·장소를
   다시 검사하고 30초 이내에 연결한다. 연결 응답이 유실되면 같은 photo ID로 재시도한다.
   촬영은 서버 시도 생성 시각 이후여야 하므로 앱 시계가 서버보다 늦으면 시간 오류를 처리해야 한다.
7. 기존 URL에 사진을 업로드하고 `/app/territory/attempts/{photo_id}/confirm`을 호출한다.
   워커의 방문 인증·점유 변경은 **같은 DB 트랜잭션**으로 확정된다. 앱이 종료되거나 산책이 끝나도
   이미 연결된 사진은 판정을 마친다. 사진 원본 정리는 기존처럼 commit 이후 실행한다.
8. GET /claims로 결과를 읽는다. REJECTED/RETRY_PENDING이면 RECORDING 중 새 촬영을 만들어
   **같은 claim**에 다시 연결한다. 기존 사진의 FAILED는 종결이므로 그 사진을 다시 confirm하지 않는다.
   새 사진이 진행 중일 때 과거 사진/판정을 재전송해도 현재 사진이나 점유를 바꾸지 않는다.
9. 일시정지·종료는 PATCH에 `{"phase":"PAUSED","expected_version":0}`처럼 보낸다.
   응답 버전을 보존하고 충돌이면 GET으로 복구한다. END는 되돌릴 수 없다.

사진 업로드·판정 흐름에 연결하지 않은 기존 방문 인증은 소유권을 만들지 않는다.
일반 산책 사진/일기 Pin은 이 계약에 넣지 않는다.

## 오류와 신뢰 범위

- 404: 없는 세션/시도 또는 다른 회원의 객체. 두 경우를 구분하지 않는다.
- 409 `detail.code`: `session_identity_conflict`, `session_changed`, `session_ended`,
  `attempt_identity_conflict`, `ineligible_pet`, `NOT_RECORDING`, `UNTRUSTED_LOCATION`,
  `OUT_OF_RANGE`, `stale_location`, `site_not_nearby`, `photo_target_mismatch`,
  `photo_time_mismatch`, `photo_already_bound`, `photo_already_in_progress_or_verified` 등.
- 422: ID·좌표·시각·목록·추가 필드 검증 실패. 503: Place 게임판 확인 불가.

서버가 검사하는 GPS·촬영 시각·세션 phase는 **앱 attestation**이다. 임의 클라이언트의 실제
이동/강아지 신원까지 증명하지 않는다. 기존 VLM도 사진에 강아지가 있는지 판정하며 대표견과의
동일 개체 인증은 하지 않는다. 이번 작업에서 공급자나 판정 프롬프트는 변경하지 않았다.

강아지 삭제는 해당 시도·점유를 CASCADE로 제거한다. 계정 삭제는 세션·시도·사진 연결도 제거한다.
기존 탈퇴 흐름의 강아지 삭제에도 점유가 따라 지워진다. 남은 장소 행에는 좌표·회원 정보 없이
site_id와 버전만 남는다. 시즌 점수/장기 이력은 아직 이 저장 구조를 소비하지 않는다.

## 배포와 검증

**머지로 새 웹/워커가 배포되기 전에** 기존 DB에
[`2026-09-05_territory_claims.sql`](../../db/migrations/2026-09-05_territory_claims.sql)을 적용한다.
기존 방문 인증 워커도 연결 조회를 하므로 이 순서가 필요하다. 신규 DB는
[`20_territory_claims.sql`](../../db/init/20_territory_claims.sql)이 같은 테이블을 만든다.
두 파일은 동일하고 재실행 가능하다. 운영 DB 변경은 PR 구현/로컬 검증에 포함하지 않는다.

적용 뒤 [`verify_2026-09-05_territory_claims.sql`](../../db/migrations/verify_2026-09-05_territory_claims.sql)을
`psql -X -v ON_ERROR_STOP=1`로 실행한다. 다섯 테이블의 컬럼 형식/NULL 허용,
PK·UNIQUE·CHECK·FK(삭제 동작 포함), 유효한 인덱스를 검사하고 불일치하면 예외를 낸다.
스키마 검증 성공을 확인한 뒤 웹/워커 코드를 배포한다. 데이터·VLM·앱 종단 검증은 별도다.
공통 워크플로의 누락/빈 파일 차단과 실패 전파는 [#264](https://github.com/SAJOYO/DAENGS_dev/pull/264)에서
수정한다. 해당 PR을 먼저 반영한 뒤 최신 dev를 통합해 `verify=true`로 실행한다.

```powershell
cd backend
uv run pytest -q tests/test_territory_ownership_api.py tests/test_territory_claim.py tests/test_territory_attempts.py tests/test_territory_vision.py
# 팀 DB가 아닌 별도 로컬 테스트 DB에서만 실행. 앱 .env로 fallback하지 않는다.
$env:TERRITORY_TEST_DATABASE_URL='postgresql+asyncpg://postgres@127.0.0.1:55439/claims_test'
uv run pytest -q tests/test_territory_ownership_db.py tests/test_main_stays_light.py
```

DB 테스트는 실제 init SQL 및 마이그레이션을 실행하고 매 테스트마다 임시 schema를 제거한다.
두 계정 탈취·중복 요청·동시 점유/인증·지연 결과·트랜잭션 롤백·다견 선택·사진 재사용·종료 후 판정·
삭제·기존 데이터 보존을 확인한다. 환경 변수 없이는 DB 테스트가 명시적으로 skip된다.
전용 GitHub-hosted PostgreSQL CI는 이를 실제 실행하고 기존 모든 PR 대상 전체 테스트는 유지한다.

후속 [DEV #281](https://github.com/SAJOYO/DAENGS_dev/pull/281)은 이 확정 경계에
보호 시간·점수·시즌과 산책/점령 통계 원본을 연결한다. 별도 migration과 기본 비활성 flag를
사용하며, 계약·조회 API·활성화 순서는 [activity-game-integration.md](../activity-game-integration.md)에 있다.

