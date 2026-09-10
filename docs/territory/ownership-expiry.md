# 첫 시즌 영역 72시간 유지·만료·현장 연장

[DEV #400](https://github.com/SAJOYO/DAENGS_dev/pull/400)은
[회원별 보상 #396](https://github.com/SAJOYO/DAENGS_dev/pull/396) 위에 연결하는 서버 구현이다.
합의한 `first-season-rewards-v1`에만 적용한다. 기존 두 정책의 보호·보상·유지 방식은 유지한다.

## 기간과 점수

- 미인증 획득, 인증 획득/강화, 유효한 현장 연장의 만료 시각은
  `min(서버 확정 시각 + 72시간, 시즌 종료 시각)`이다. 72시간은 첫 시즌의 고정 정책이다.
- `occupied_at`은 소유 획득 시각, `certified_at`은 인증 보호의 시작 시각,
  새 `expires_at`은 영역 유지 종료 시각이다. 자기 영역 연장으로 앞의 두 시각은 바뀌지 않는다.
- **연장 보상은 0점**이다. 기본 100점 한도나 반복 탈취 20점에 추가 지급하지 않는다.
  인증 연장도 기존 인증 보호 10분의 종료 시각을 늦추지 않는다.
- 정확히 `expires_at`에 중립이 된다. 소유권·인증·보호 정보를 제거하고 장소 버전을 증가시킨다.
  강아지의 누적 점수, 보유 구간 이력, 회원·장소·시즌 기본 지급 이력은 보존한다.
- 만료 후 빈 곳을 다시 획득하면 기본 지급 이력의 남은 차액만 받을 수 있다.
  예: 기본 20점을 받은 회원의 재인증 획득은 80점, 이미 100점을 받은 회원은 0점이다.
  만료되어 주인이 없는 곳에서는 탈취 20점을 지급하지 않는다.

## 앱 계약

점유 조회와 claim 응답의 `site.occupancy`에 nullable `expires_at`을 추가한다.
첫 시즌의 소유권에는 시각이 있으며 구 정책에서는 null이다. 앱은 `server_now`와 함께 표시한다.
만료된 영역은 `occupancy=null`이며, 주인 요약 조회도 만료된 강아지를 반환하지 않는다.

### 미인증 연장

다음 산책에서 기존 `POST /app/territory/claims`로 자기 강아지의 미인증 영역을
새롭게 현장 확인하면 자동 연장한다. 같은 산책·장소의 기존 mark 재전송은 최초 요청 조회일 뿐이다.
그 요청 본문을 변경하면 기존대로 `attempt_identity_conflict`다.

같은 산책 중 새 현장 확인으로 연장할 때는 다음 엔드포인트를 사용한다.

```text
PUT /app/territory/claims/{claim_id}/renewals/{renewal_id}
```

`renewal_id`는 새 행동의 UUID이며, 응답 유실 시 **같은 UUID와 같은 본문**으로 재시도한다.
본문은 기존 MarkRequest의 `client_session_id`, `site_id`, `claiming_pet_id`, `observed_at`,
`lat`, `lng`, `accuracy_m`, `is_mock`에 현재 `expected_site_version`을 추가한 형태다.
서버가 claim 소유 회원과 산책/강아지/장소 일치, RECORDING, 30초 이내 관측,
위치 정확도를 포함한 20m 현장 범위, 현재 미인증 주인, 장소 버전을 검사한다.
앱 위치 증거의 신뢰 수준은 기존 mark와 같다.

응답은 `{renewal_id, site_version, expires_at}`이다. 성공한 요청의 재전송에는 최초 결과를
반환하며 현재 시간이나 장소 공급자 상태에 따라 다시 연장하지 않는다. 결과를 다시 받더라도
그 사이 탈취/만료됐을 수 있으므로 **현재 소유권은 점유 조회로 확인**한다.

주요 오류는 404(다른 회원/없는 claim), 409 `renewal_identity_conflict`, `site_changed`,
`not_current_owner`, `photo_required`, `NOT_RECORDING`, `stale_location`,
`UNTRUSTED_LOCATION`, `OUT_OF_RANGE`, `season_ended`, `policy_unavailable`이다.
버전 충돌 뒤에는 현재 상태를 다시 조회하고 새 현장 행동으로 판단한다.

### 인증 연장

자기 강아지의 인증 영역은 `photo-access.allowed_action=PHOTO_RENEW`를 반환한다.
기존처럼 새로운 challenge UUID 발급 → 새 사진 생성/연결/업로드 → 방문 인증 판정을 거친다.
기존 VERIFIED 사진의 재전송은 기간을 연장하지 않는다. 위치 확인만으로 인증 영역을 연장하면
`photo_required`다. 새 산책에서 자기 인증 영역에 mark만 하는 경우에도 기간은 늘어나지 않는다.

사진 대기 중 연장·탈취·만료로 장소 버전이 바뀌면 방문 인증 기록은 남기되
소유권과 보상에는 적용하지 않고 `site_changed`로 끝낸다. 시즌을 넘어온 판정도 다음 시즌의
점수나 소유권을 만들 수 없다. 새 challenge를 받아야 한다.

## 정산·트랜잭션

기존 advisory lock `(260,36)` 아래에서 새 점령·연장·사진 확정 전에 만료를 처리한다.
worker의 기존 `activity process` 경로에도 연결한다. 늦게 실행된 worker는 만료된 영역들을
실제 만료 시각 순서대로 처리하며 각 시점에 그 강아지의 보유 점수를 정산한 뒤 개수를 줄인다.
그 후 남은 영역만 현재 시각/시즌 종료까지 정산한다. 여러 영역이 서로 다른 시각에 만료되어도
만료 후 점수를 추가 지급하지 않는다.

점유 조회는 같은 만료 처리를 통해 중립 상태를 반환한다. 조회 트랜잭션이 롤백되더라도 다음
writer/worker가 같은 시각에서 확정하므로 추가 점수나 중복 만료가 생기지 않는다.
read-only 주인 요약은 만료 시각을 비교해 중립 상태와 그에 해당하는 장소 버전을 투영한다.

연장·점유·보상 영수증과 GPS 연장 결과는 같은 트랜잭션에서 저장한다.
기간 연장과 탈취가 같은 장소 버전을 대상으로 경쟁하면 하나만 적용한다.
저장 실패는 전체 롤백하므로 연장 응답 이력만 빠지거나 기간만 늘어나는 상태가 없다.

## 스키마와 배포

- `territory_occupancies.expires_at`: nullable timestamptz, 만료 검색용 부분 인덱스.
- `territory_renewals`: 요청 UUID 기본키, claim FK(CASCADE), 시즌 식별자,
  원본 요청 비교용 contact, 최초 확정 장소 버전/시각/만료 시각.
  회원 기본 보상 원장에는 변경이 없다.

새 DB는 `db/init/30_territory_expiry.sql`, 기존 DB는
`db/migrations/2026-09-10_territory_expiry.sql`을 사용한다. 두 파일은 동일하며 재실행 가능하다.
**새 코드를 배포하기 전에** 마이그레이션과 `verify_2026-09-10_territory_expiry.sql`을 적용한다.
웹과 사진/활동 worker를 같은 버전으로 배포해야 한다. 마이그레이션만으로 게임은 켜지지 않는다.

이번 SQL은 기존 소유권/점수/시즌을 백필하지 않는다. 새 첫 시즌을 명시적으로 시작하면
가져온 기존 소유권에는 활성화 시각부터 72시간(시즌 종료로 제한)을 부여하고,
기존 획득/인증 보호 시각은 유지한다. 이미 실행 중인 구 첫 시즌에 null 만료가 남아 있는 상태를
자동 보정하지 않으므로, 그런 데이터가 있으면 별도 전환 검토가 필요하다.
2026-09-10 #396 마이그레이션 확인 당시 운영 시즌은 0행이었다. 이 PR의 운영 적용 여부와
검증 결과는 PR 본문에서 확인한다.

월별 자동 결산·다음 시즌 생성은 [DEV #403](https://github.com/SAJOYO/DAENGS_dev/pull/403)의
[월간 시즌 계약](monthly-seasons.md)을 따른다. 후속은 APP의 새 응답/연장 버튼 연결,
worker 운영 및 게임 활성화다.
