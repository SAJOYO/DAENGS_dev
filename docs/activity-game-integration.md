# 산책·점령 통계와 게임 정책의 DEV 연결

> 2026-09-08 후속: [인증 우선 정책 v2](certified-territory-v2.md). 아래의 획득 시각 보호·새 산책 필수 규칙은 `draft-2026-09-06` 시즌에 한정한다.

## 목적과 선행 조건

Geo에서 만든 계산·저장 경계의 다음 단계인 **DEV 실제 원본 연결(S4)** 이다.
Geo는 가짜 보호자·강아지와 독립 PostgreSQL로 계약을 검증하는 장소였다.
실서비스의 회원 인증, 산책 분석, 사진 방문 판정, 실제 점령 소유권은 DEV가 소유한다.
따라서 Geo 서버를 운영 데이터 중계 서버로 두지 않고 필요한 코어를 DEV 서비스에 이식한다.

- 선행 PR: [DEV #260](https://github.com/SAJOYO/DAENGS_dev/pull/260), 실제 점령 시도·현재 소유권.
- 코어/저장 참조: [Geo #249](https://github.com/rkbuhtig/DAENGS_geo/pull/249).
- DEV 연결: [#281](https://github.com/SAJOYO/DAENGS_dev/pull/281).
- 선행 배포 절차: [territory/ownership-api.md](territory/ownership-api.md).

#260은 dev에 병합되었으며, 이 PR도 최신 dev를 반영하고 base를 dev로 변경했다.
운영 적용에는 #260 SQL과 이 문서의 후속 SQL이 모두 필요하다.
이 PR을 열거나 테스트하는 것 자체로 운영 DB·웹·워커·앱을 활성화하지 않는다.

## 원본과 파생 데이터

| 영역 | 권위 있는 원본 | 이 구현이 저장/제공하는 것 |
| --- | --- | --- |
| 산책 | DEV `walks`, `walk_pets`, 봉인된 `walk_analyses`와 receipt/capsule | 명시적으로 선택된 분석 head, 처리 revision, 기여량·제외 이유 |
| 게임 세션 | #260 `territory_claim_sessions` | 보호자+client UUID로 산책 UUID와 게임 UUID 연결 |
| 점령 | #260 실제 `territory_occupancies` 변경 | 점수 계정, 보유 구간, 중복 지급 키, 처리 영수증 |
| 점령 통계 | 실제 변경에서 작성한 보유 구간 | 획득/탈취 수, 누적·인증 보유 시간, 현재/최대 동시 보유 수 |
| 시즌 | 명시적 관리 명령과 확정 규칙 | 범위, 활성화 이후 관측 범위, 종료 점수와 보유 구간 보존 |

GPS로 게임 세션을 추측하지 않는다. 서버 UUID와 client UUID도 같은 것으로 취급하지 않는다.
같은 client UUID라도 보호자가 다르면 별개의 연결이다. 한쪽만 먼저 도착하면
`WAITING_FOR_WALK` 또는 `WALK_ONLY`, 양쪽의 시작 시각·참여견이 같으면 `LINKED`,
다르면 `CONFLICT`와 구체적 이유를 반환한다. 충돌은 원본을 덮어쓰지 않는다.

산책은 업로드 완료만으로 통계에 더하지 않는다. finalize가 검증한 입력 fingerprint와
Analysis/Receipt/Capsule을 같은 트랜잭션의 통계 head로 연결한다. finalize 재시도는
같은 분석을 선택하므로 revision이나 거리 합계를 증가시키지 않는다.
여러 강아지와 걸은 산책은 보호자 합계에서 한 번, 각 참여견의 조회에서 한 번씩 기여한다.
각 강아지가 별도 GPS 센서를 가진 것처럼 해석하지 않는다.

현 DEV 버전 조합은 Facts 1 / calculation 4 / receipt 1 / capsule 1이다.
Receipt의 `canonical_segment_time_s > 0`을 관측 가능한 구간의 조건으로 사용한다.
관측 불가면 거리·시간은 `null`이고 device 원본은 `no_observed_intervals`로 분리한다.
`mock/mixed/unknown` 원본(빈 좌표열 포함)은 코어의 `non_device_evidence` 제외 규칙을
먼저 적용한다. 행동·성격 판단을 추가하지 않는다.
조회 기간은 산책 종료 시각 `[from_ms,to_ms)` 기준이며 한 요청에 최대 366일이다.

## 트랜잭션과 처리기

산책 finalize → 분석·capsule 봉인 + head 선택 → commit → 처리기가 head를 읽어 기여량 확정.
사진 판정 → VerifiedVisit + 정책 계산 + 소유권 + 보유 구간 + 점수/영수증 → 하나의 commit.
보호 시간/시즌 종료/소유권 경합으로 점령만 거절되면 검증된 방문은 유지하고
claim의 `resolution_code`를 기록한다. 보호 중인 요청을 시간이 지난 후 자동 재지급하지 않는다.

첫 구현은 PostgreSQL transaction advisory lock `(260,36)`으로 활성화된 쓰기와 통계
조회·처리의 순서를 직렬화한다. 사진 행 잠금보다 먼저 획득한다. 여러 프로세스에서도
같은 순서로 사진 → 세션 → site 잠금을 잡는다. 외부 장소 조회는 잠금 전에 수행한다.
산책 finalize의 기존 날씨 조회는 기존 트랜잭션 안에 남아 있으므로 긴 처리 시 전체 activity
대기 시간이 늘 수 있다. 대규모 운영 처리량을 보장하는 설계는 아니며, 배포 전 부하 검증과
향후 범위별 잠금 분리를 검토해야 한다.

head/account의 `revision > processed_revision` 자체가 내구성 있는 처리 대기 상태다.
따라서 DB commit과 별도 메시지 발행 사이의 유실이 없다. 처리 중 예외는 전체 배치를 rollback하며
다음 실행에서 같은 head를 다시 읽는다. 워커는 매번 NullPool 세션을 생성하므로 Celery의
서로 다른 asyncio loop 사이에 연결을 재사용하지 않는다. 계정은 오래된 processed revision
순으로 처리하여 작은 배치에서 뒤쪽 강아지가 영구히 밀리지 않게 한다.

`rebuild`는 처리 진행값만 초기화한다. 점수 지급·소유권 변경을 재생하지 않는다.
보유 구간과 선택된 분석으로 통계를 다시 계산한다. 과거 head 선택 전체의 이벤트 소싱 저장소는
아니며, 이전 분석 자체는 기존 DEV 분석 저장소에 남는다. 통계 캐시에는 pet/owner UUID를 넣지 않는다.
산책 조회는 현재 참가견 FK와 처리된 불변 분석을 다시 검증하고 합산한다. GPS 재계산은 없다.

## 점수와 시즌

10분 보호는 실제 `occupied_at` 기준이다. 동일 강아지의 사진 인증은 보호 시작 시각을
갱신하지 않고 획득 보너스도 다시 주지 않는다. 실제 다른 소유자로 바뀌는 경우에만
점령/탈취 보너스를 적용한다. 이벤트 키는 서버가 만든 `mark:<claim UUID>` / `photo:<photo UUID>`다.
`daily_pet_site` 선택 시 시즌·강아지·site·UTC day로 보너스를 한 번만 지급한다.

시간 점수는 정수 `holding_units`로 저장한다. 전체 점수 단위는
`bonus * 36_000_000_000 + holding_units`이고 화면용 점수는 이 값을 36_000_000_000으로 나눈 값이다.
보유 수가 변하는 시점마다 기존 수로 먼저 정산한 뒤 새 수를 적용한다.
처리기는 변동이 없어도 현재 시각까지 정산하며 시즌 종료 시각을 넘겨 정산하지 않는다.
수치 기본값은 Geo 실험안이다. CLI는 모든 Rules 필드를 명시한 JSON을 요구한다.
이 PR이 보너스/배율 등 제품 밸런스를 확정하는 것은 아니다.

시즌은 현재 시각을 포함하는 범위로 한 개만 활성화한다. 기존 #260 소유권은
활성화 시점부터 `IMPORTED` 구간으로 가져온다. 기존 획득 시각은 보호 계산에 유지하지만
과거 점수/획득 횟수를 만들어내지 않는다. `coverage_start_ms`가 그 한계를 드러낸다.
시즌 종료 처리기는 종료 점수를 보존하고 구간을 닫으며 현재 소유권을 비운다.
site version도 증가시켜 늦은 사진 판정이 지난 시즌 소유권을 되살리지 못하게 한다.
다음 시즌은 별도 명령으로 시작한다. 기존 세션의 한 site 재시도 제한은 유지되므로 새 산책 게임
세션이 필요하다. 비교 범위가 없는 상태에서 시즌 전체 점수 정렬을 동네 순위로 노출하지 않는다.

## 저장·삭제 계약

`db/init/21_activity_game.sql`, `db/migrations/2026-09-06_activity_game.sql`은 같은 스키마다.
`models/activity.py`가 이를 따른다. 회원·강아지·산책·분석은 기존 FK에 연결된다.

- 강아지 삭제: 계정, 보유 구간, 보너스 키, 영수증은 CASCADE 삭제.
  게임 세션의 pet_ids 배열에서도 식별자를 제거하고 기존 점령 site version을 갱신한다.
  다른 강아지의 보유 구간에는 상대 강아지 ID를 복제하지 않아 상대 기록을 보존할 수 있다.
- 산책 삭제: 분석 및 head는 CASCADE, 세션 연결의 walk_id는 SET NULL.
  공동 산책은 기존 정책에 따라 남으며 현재 참가견 관계로 집계한다.
- 회원 탈퇴: app_users 행이 남는 기존 구조를 고려하여 세션 연결과 게임 세션도 명시 삭제한다.
  SQL status 트리거로 flag가 꺼진 상태에서도 새 연결 기록을 정리한다.
  기존 사진 저장소의 삭제 정책을 이 PR이 대체하지 않는다.
- 활성 시즌에서는 지연 제약 트리거가 실제 소유권과 열린 보유 구간의 소유자·claim·인증을
  commit 시점에 비교한다. 정책을 모르는 구버전 프로세스의 변경이 조용히 통과하는 것을 막는다.

## 조회 API

세 엔드포인트 모두 기존 앱 인증을 사용한다. 다른 보호자의 리소스는 404다.
기능 flag가 꺼져 있으면 새 조회는 503 `activity_disabled`, 기존 #260 동작은 유지한다.

| GET | 내용 |
| --- | --- |
| `/app/activity/sessions/{client_session_id}` | 소유한 산책·게임 UUID와 연결 상태 |
| `/app/activity/walks/summary?from_ms=...&to_ms=...&pet_id=...` | 종료 시각 기준 산책 합계, 제외 이유, 분석 출처 |
| `/app/activity/territory/{season_id}/pets/{pet_id}` | 보유 통계, 점수, 보유 구간 출처 |

산책의 미처리 항목은 `pending_walk_count`이며 완료된 기여량과 구별한다.
점령은 캐시 없음 `PENDING`, 원본 이후 변경 있음 `STALE`, 확정 시각까지 처리 완료 `READY`다.
`READY`는 실시간을 의미하지 않는다. `confirmed_through_ms`, `score_as_of_ms` 및 revision을
함께 확인해야 한다. 점수는 권위 있는 계정, statistics는 비동기 캐시이므로 기준 시각이 다를 수 있다.
출처에는 본인 원본 UUID/site만 있고 원시 좌표·사진 URL·다른 보호자 식별자는 없다.

## 적용과 복구 순서

운영 접속 정보는 이 문서에 저장하지 않는다. 명령은 검토된 대상 DB에서 담당자가 실행한다.

1. 기존 산책 Analysis/Capsule SQL과 #260 migration/검증을 먼저 완료한다.
2. `psql`의 `ON_ERROR_STOP=1`을 켜고 후속 migration을 한 트랜잭션으로 실행한다.
   `db/migrations/verify_2026-09-06_activity_game.sql`로 확인한다. 빈 DB는 init 순서를 따른다.
3. 새 웹·사진 판정 워커·activity 워커 코드를 배포한다. 기본 flag는 false다.
4. 점령 쓰기를 잠시 중단한 상태에서 웹/모든 워커에 `DAENGS_ACTIVITY_GAME_ENABLED=true`를
   동일하게 적용하고, 명시적 시즌 설정을 실행한다. 구버전 writer가 남지 않았음을 확인한다.
5. 아래 process 명령과 API를 확인한 뒤 쓰기와 앱 기능을 활성화한다.

```powershell
# backend/에서. Rules JSON은 검토한 값을 모두 명시한 별도 파일이다.
uv run python -m daengs_backend.cli.activity start-season <season-id> --starts-ms <utc-ms> --ends-ms <utc-ms> --rules <rules.json>
uv run python -m daengs_backend.cli.activity process --limit 100
uv run python -m daengs_backend.cli.activity rebuild
# rebuild 뒤 process를 반복하여 pending을 소진한다.
```

필수 Rules 키: `version`, `protection_ms`, `claim_points`, `takeover_points`, `hourly_points`,
`extra_site_bps`, `maximum_bps`, `repeat_bonus`, `unverified_scores`.
`protection_ms`는 600000 고정이며 JSON은 저장된 시즌의 규칙으로 보존된다.

주기적 처리는 별도 `activity` 큐를 사용한다. 아래 워커와 Beat를 명시적으로 운영하거나
기존 스케줄러에서 process 명령을 호출한다. 이 PR은 compose/운영 스케줄을 자동 변경하지 않는다.

```powershell
uv run celery -A daengs_backend.tasks.activity:app worker -Q activity --loglevel=INFO
uv run celery -A daengs_backend.tasks.activity:app beat --loglevel=INFO
```

Beat 간격은 30초이고 한 배치의 산책/계정 각각 최대 100개를 처리한다. 적체가 있으면
처리량·호출 주기를 조정한다. 오래된 기존 산책은 자동 일괄 선택하지 않는다. 원본 manifest로
기존 finalize를 재호출하면 봉인을 검증한 뒤 head를 생성한다. 기존 게임 세션도 같은 start 요청의
재시도로 링크를 복구할 수 있다. 이를 하지 않은 과거 원본은 아직 수집되지 않은 상태로 남는다.

활성 시즌이 만들어진 뒤 flag만 끄고 구버전 쓰기를 재개하면 integrity guard가 변경을 막는다.
롤백은 쓰기를 중지하고 처리기 오류를 복구하거나 검토된 시즌 종료 절차를 수행해야 한다.
캐시 오류에는 rebuild를 사용하고 원본·시즌·점수 테이블을 삭제하는 방식으로 복구하지 않는다.

## 검증과 후속 범위

`tests/test_activity_db.py`는 localhost의 `claims_test` DB 안에서 테스트마다 임의 schema를
만들고 제거한다. 팀 DB 설정으로 fallback하지 않는다. #260과 후속 SQL을 재실행하고 실제
산책 finalize, 사진 판정, 처리기, API, 삭제 및 시즌 종료를 검증한다.
`.github/workflows/territory-ownership-tests.yml`은 PostgreSQL 17에서 이를 실행한다.
전체 기본 backend 테스트 워크플로우도 그대로 유지한다.

APP에는 아직 이 조회 계약 연결, 동일 client UUID 전달 확인, pending/stale 표시,
점수·시즌 화면이 필요하다. 동네 순위의 비교 범위/집단 스냅샷, 칭호의 경향·판정 정책,
칭호 부여·회수 이력은 이 변경에 포함되지 않는다. 이 통계·출처·세션 층이 그 입력 기반이다.

## 코어 이식 출처와 차이

Geo commit `454831a`의 `activity_core/{common,sessions,walk,territory}.py`와
`game/policy.py`를 DEV `services/activity_core/`에 이식했다. 외부 HTTP/DB 의존성은 없다.
`game_policy.validate_context`의 기존 소유 시각 검증은 시즌 이전의 실제 #260 소유권도
허용하도록 수정했다. 점수는 활성화 시각부터만 계산한다는 별도 DEV 어댑터 제약과 함께 사용한다.
이에 대한 10분 경계·소급 점수 방지 테스트는 `tests/test_activity.py`에 있다.
Geo의 JSON snapshot 저장 어댑터는 복사하지 않았다. DEV는 실제 FK와 삭제 계약, 순서 잠금,
revision 기반 처리 대기로 구현한다. 향후 코어 변경 시 출처 commit과 이 차이를 함께 검토한다.
