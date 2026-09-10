# 월간 점령 시즌 자동 결산

[DEV #403](https://github.com/SAJOYO/DAENGS_dev/pull/403)은
[첫 시즌 보상](first-season-rewards.md)과 [72시간 유지·만료](ownership-expiry.md)를
월간 시즌 전환에 연결한다. 최초 활성화는 명시적으로 실행하며, 이후만 자동 전환한다.

## 달력과 시작

- 한국 시간 매월 1일 00:00부터 다음 달 1일 00:00 직전까지 한 시즌이다.
  서버의 로컬 시간대와 관계없이 KST(UTC+09:00)로 월을 계산하고 UTC 밀리초로 저장한다.
- 첫 시즌은 실행 시점부터 해당 월 종료까지다. 예를 들어 2026년 9월 11일에 시작하면
  10월 1일 00:00 KST에 종료한다. 30일 길이로 계산하지 않는다.
- 월간 ID는 `territory-YYYY-MM`이다. 다음 시즌의 시작은 지난 시즌의 종료와 정확히 같다.
- `start-monthly`로 생성한 시즌만 자동 전환한다. 기존 `start-season` 수동 시즌이나
  이미 종료된 과거 시즌을 자동 전환 대상으로 바꾸지 않는다. 시즌이 없는 상태에서 worker를
  실행해도 첫 시즌이 생기지 않는다. 시작 명령을 중복 실행하면 `active_season_exists`다.

## 결산 순서와 복구

기존 activity advisory lock `(260,36)`을 잡은 상태에서 아래를 한 트랜잭션으로 실행한다.

1. 종료된 영역은 실제 만료 시각 순으로 정산한다. 보유 점수는 만료 또는 시즌 종료까지만 받는다.
2. 지난 시즌의 점수와 강아지별 최종 순위를 저장하고 남은 영역을 중립으로 전환한다.
3. 지난 시즌을 FINALIZED로 확정한 뒤 다음 월의 ACTIVE 시즌과 이전 시즌 연결을 만든다.
4. 중단 기간에 여러 월이 지났다면 현재 달에 도달할 때까지 같은 순서로 처리한다.
   중간 달은 소유권과 계정 없는 빈 시즌으로 남고 점수/영역을 넘겨받지 않는다.

새 시즌 생성이나 저장이 실패하면 지난 시즌 결산·영역 중립화까지 함께 롤백한다.
재시도는 그 상태에서 다시 시작한다. 공통 잠금, 시즌 ID 기본키, ACTIVE 시즌 유일성,
이전 시즌당 후속 하나라는 UNIQUE 제약이 중복 전환을 방어한다.
후속 ID가 이미 다른 데이터로 존재하면 `monthly_season_conflict`로 중단하며 덮어쓰지 않는다.

기존 30초 Beat의 `activity.process`가 자동 전환을 수행한다. 점령·도전·사진 판정의
진입 경로에도 연결되어 worker가 늦어도 월 경계 이후 쓰기는 새 시즌을 먼저 준비한다.
조회 안에서 전환을 계산한 경우에는 조회 트랜잭션과 함께 롤백될 수 있으며,
다음 writer/worker가 동일하게 확정한다. 실제 worker 배포/기동은 별도 운영 작업이다.

## 점수와 인증의 경계

- 최종 점수는 기존 `activity_accounts.final_score`, 최종 순위는 새 nullable `final_rank`에 저장한다.
  해당 시즌에 점수 계정이 있는 강아지를 전체 비교하며, 정수 단위 총점이 같으면 공동 순위다.
  기존 계산기의 경쟁 순위(예: 1, 1, 3)를 따른다. 동네별 실시간 순위표 구현은 별도다.
- 새 시즌에는 이전 계정·점수·보유 구간을 복사하지 않는다. 이전 기본 원장도 보존하므로
  회원·장소별 기본 100점 자격은 새 season_id 아래에서 새로 열린다.
- 지난 시즌의 challenge로 늦게 도착한 사진은 방문 인증 기록만 남고 `season_ended`로 끝난다.
  새 시즌의 소유권이나 보상에 적용하지 않는다. 새 challenge와 새 사진은 정상 참여할 수 있다.
- 기존 데이터 삭제 정책(FK의 회원/강아지 삭제 처리)은 유지한다. 순위를 과거 모든 시즌에
  소급 계산하지 않으므로 이미 종료된 기존 계정의 `final_rank`는 null일 수 있다.

기존 본인 강아지 통계 API
`GET /app/activity/territory/{season_id}/pets/{pet_id}`에 `final_rank`를 추가한다.
진행 중이거나 저장된 결산 순위가 없으면 null이며, 점수와 함께 반환한다.
기존 `score_as_of_ms`와 통계 캐시 상태의 의미는 그대로다. 앱 화면 연결은 후속 작업이다.

## 스키마와 운영 명령

- `activity_monthly_seasons`: season_id PK/FK와 nullable previous_season_id UNIQUE/FK.
  행이 존재한다는 것이 명시적인 월간 자동 전환 설정이다.
- `activity_accounts.final_rank`: nullable 양수 bigint. 과거 순위 자동 백필 없음.

새 DB는 `db/init/31_activity_monthly.sql`, 기존 DB는
`db/migrations/2026-09-10_activity_monthly.sql`을 사용한다. 두 SQL은 동일하고 재실행 가능하다.
`verify_2026-09-10_activity_monthly.sql`은 형식·키·FK·CHECK와 저장된 시즌 연결의 정합성을 검사한다.
배포 전에 선행 보상/만료 SQL 및 이번 SQL을 적용하고 검증해야 한다.

아래 명령은 **향후 활성화 시점의 절차**다. 이번 PR 작업에서 운영 DB 적용, 배포,
환경 변수 변경, 첫 시즌 생성이나 게임 활성화는 실행하지 않는다.

```powershell
# backend/에서. 게임 기능 설정과 모든 서버/worker 준비를 마친 뒤 최초 한 번 실행.
# rules.json은 first-season-rewards.md의 모든 필드를 명시한 첫 시즌 정책 JSON.
uv run python -m daengs_backend.cli.activity start-monthly --rules <rules.json>

# 기존 activity worker/Beat 또는 명시적 처리 명령이 이후 월 전환을 수행한다.
uv run python -m daengs_backend.cli.activity process --limit 100
```

새 시즌의 규칙은 직전 월의 저장된 규칙을 그대로 이어받는다. 임의로 배점을 바꾸거나
운영 중 자동 전환을 끄는 관리 기능은 이번 범위가 아니다. 장애 복구 시 시즌 행·원장을 삭제하거나
기존 ID로 덮어쓰지 말고 실패 원인을 수정한 뒤 처리 명령을 재시도한다.
