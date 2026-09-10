# 첫 시즌 회원별 보상 — DEV 연결

[DEV #396](https://github.com/SAJOYO/DAENGS_dev/pull/396)의 구현 범위다.
계산 원본은 [GEO #264](https://github.com/rkbuhtig/DAENGS_geo/pull/264)
`18b810ec1f4785245ae7c97e70f142ee8a3454ae`의 `first_season_rewards.py`이며 import 경로만
DEV의 `services/activity_core`로 바꿨다. 제품 합의는
[GEO #263](https://github.com/rkbuhtig/DAENGS_geo/pull/263)이다.

## 적용되는 행동

| 행동 | 기본 보상 | 탈취 보너스 |
| --- | --- | --- |
| 빈 곳 미인증 점령 | 회원·장소·시즌 누적 20점까지 차액 | 0 |
| 빈 곳 인증 점령 / 자기 미인증 강화 | 같은 원장 누적 100점까지 차액 | 0 |
| 다른 회원의 영역 인증 탈취 | 같은 원장 누적 100점까지 차액 | 매번 20 |
| 같은 회원의 다른 강아지로 인증 점령 | 같은 원장의 남은 차액 | 0 |

이전 주인의 인증 여부는 탈취 보너스에 영향을 주지 않는다. 기본 기지급 0/20/100일 때
인증 탈취 지급은 120/100/20점이다. 기본 자격은 회원 기준이지만 지급은 행동한 강아지에게
귀속한다. 다른 강아지의 과거 점수를 옮기거나 차감하지 않는다.
10분 인증 보호와 새 사진 검증은 유지하며 별도 반복·일일·회원 쌍 한도는 추가하지 않는다.

## 저장과 원자성

- `activity_base_rewards`: `(season_id, app_user_id, site_id)` 기본 원장, `paid`는 0/20/100.
  강아지 FK를 두지 않아 강아지 변경·삭제로 기본 자격이 초기화되지 않는다.
- `activity_game_receipts`: 기존 `(season_id, event_id)` 영수증과 총 지급액을 유지한다.
- `activity_reward_details`: 같은 영수증에 기본 지급 전/후·기본 차액·탈취 보너스를 분리한다.
  영수증 및 회원 원장에 복합 FK로 연결한다. 원본 요청/사진의 재시도는 기존 claim·photo
  불변 식별자 확인과 판정 상태를 통해 다시 적용되지 않는다.
- `activity_accounts.score`: 새 버전은 `base_bonus`, `takeover_bonus`를 더 가지며
  기존 `bonus`는 두 값의 합이다. 점수 API의 분리 필드는 기존 시즌에서 `null`이다.

`activity_game.transition`이 기존 advisory lock `(260,36)`과 활성 시즌 잠금 아래서
회원 원장을 `FOR UPDATE`로 읽고, 소유권·이전 보유 구간 정산·새 점수·원장·영수증·
보상 내역을 사진 판정과 **같은 트랜잭션**으로 저장한다. DAO와 계산기는 commit하지 않는다.
신규 원장도 이 공통 잠금으로 직렬화하고 기본키로 중복을 방어한다.
사진 방문 증거만 남기는 보호/시즌 종료 거절과, 저장 장애로 전체 롤백하는 경우를 구분한다.

회원 식별은 요청 문자열이나 강아지 이름이 아니라 현재/이전 claim의 서버 게임 세션에서
얻는다. 회원 탈퇴 때 새 원장과 연결 보상 내역은 트리거로 정리하며 게임 OFF에서도 작동한다.
강아지 삭제 때 그 강아지의 영수증/내역은 기존 FK로 삭제되지만 회원의 기본 원장은 유지한다.
현재 서비스의 탈퇴 순서는 강아지 삭제 뒤 회원 상태 변경이다.

## 정책 버전과 시간 점수

새 시즌의 `rules.version`은 `first-season-rewards-v1`이다. 기존
`draft-2026-09-06`, `certified-protection-v2` 저장 규칙과 `game_policy.py`는 변경하지 않는다.
`first_season_policy.py`가 기존 인증 보호·CAS·보유 수 전이를 재사용하되 기존 배점 계산을
0으로 끄고 GEO의 새 계산 결과를 연결한다.

새 점수의 `current_count`는 전체, `scoring_count`는 인증 보유 수다.
미인증 수는 둘의 차이이며 시간당 `미인증 수 × 2 + 인증 수 × 10`을 지급한다.
인증 강화/탈취 직전에 기존 구간을 먼저 정산하므로 미인증 1시간 뒤 인증 1시간은 12점이다.
동시 보유 계수·4시간 상한은 없다. `holding_units` 정수 정밀도와 주인 카드의 표시식을 유지한다.
기존 명시적 시즌 결산에서도 새 계산기로 종료 시각까지 정산하고 보상 분리 내역을 보존한다.

## DB 확인과 적용 순서

2026-09-10 사용자 제공 접속 정보로 PostgreSQL의 `information_schema.columns`와 시즌 상태
집계를 **read-only 트랜잭션**에서 조회했다. 기존 activity 테이블·인증 도전·`certified_at`은
있었고 시즌 행은 0개였다. 초기 구현 검증에서는 운영 DB에 쓰거나 게임 설정을 변경하지 않았다.

1. 기존 활동/인증 스키마 위에 `db/migrations/2026-09-10_activity_rewards.sql`을 적용한다.
   새 DB 원본은 `db/init/29_activity_rewards.sql`. 현재 데이터나 시즌 규칙을 백필하지 않는다.
2. `verify_2026-09-10_activity_rewards.sql`로 컬럼·복합키·FK·배점 제약·정리 트리거와
   저장 내역의 합계를 검사한다. 두 SQL 모두 재실행 가능하다.
3. DEV 코드를 배포한다. 기존 시즌은 기존 정책으로 실행되며, 마이그레이션만으로
   새 시즌이나 게임 ON이 생기지 않는다.
4. 아래 후속 구현까지 검증한 뒤 새 정책을 명시해서 시즌을 시작한다.

원격 적용은 기존 `db-migrate.yml`을 사용하며 `file=2026-09-10_activity_rewards.sql`,
`ref=이 PR의 검증된 커밋`, `verify=true`, `backup=true`로 실행할 수 있다.
**2026-09-10 10:50 KST, 사용자 요청으로 운영 마이그레이션 적용 완료.**
검증한 커밋 `18367e7530d42aa49b604d53cb3ef2503ef65604`의 SQL과 작업 파일의 일치를
확인하고, PostgreSQL 18의 `pg_dump --schema-only --format=custom` 백업과 목록 검증을
완료한 뒤 직접 접속으로 적용했다. 적용 SQL과 verify SQL을 같은 트랜잭션에서 실행했으며
커밋 후 새 read-only 연결에서도 verify가 통과했다. 새 원장/내역은 각각 0행, 시즌은 0행이고
기존 시즌 규칙·revision·점수 계정/영수증 수가 보존된 것을 확인했다.
백업과 실행 보고서는 작업 환경의 `outputs/reward-migration-20260910/`에 보관한다.
DB 변경만 완료했으며 PR 머지·코드 배포·시즌 생성·게임 ON은 실행하지 않았다.

새 정책 JSON은 모든 필드를 명시한다. 아래는 계약 예시이며 지금 운영 시즌을 만들라는 뜻이 아니다.

```json
{
  "version": "first-season-rewards-v1",
  "unverified_base_target": 20,
  "verified_base_target": 100,
  "takeover_bonus": 20,
  "unverified_hourly_points": 2,
  "verified_hourly_points": 10,
  "protection_ms": 600000
}
```

## 검증과 후속

로컬 `127.0.0.1:55439/claims_test`의 테스트별 임시 스키마에서 새 SQL 반복 적용,
120/100/20 배점, 동일 사진 중복 판정, 같은 회원의 두 강아지 동시 요청, 보상 저장 실패
전체 롤백, 강아지 삭제 후 회원 한도 유지, 회원 탈퇴 정리, 결산·새 시즌 자격을 검증했다.
새 계산 사례 51개와 규칙 파일/응답 계약 1개, 새 DB 통합 7개, 기존 정책·조회 회귀 40개가 통과했다.
새 migration 항목의 반복 적용·정상 구조·구조 훼손 검증 8개도 통과했다.

`uv run check`는 통과했다. Windows 검증은 현재 테스트 프로세스에만 스크립트 실행을
허용하고 UTF-8로 실행했다. 시스템 실행 정책이나 배포 스크립트는 바꾸지 않았다.
로컬 전체 스위트는 실행하지 않으며 기존 PR CI의 전체 검증은 유지한다.

**후속:** 72시간 만료/현장 연장, 월간 자동 결산·다음 시즌 시작, APP의 새 정책 버전·
보상 표시, 워커 배포와 실제 활성화. 현재 코드는 자기 인증 영역의 유지 재촬영을
기존처럼 `already_certified`로 막으며, 72시간 만료 시각을 아직 저장/정산하지 않는다.
