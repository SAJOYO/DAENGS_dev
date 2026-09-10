# 기존 산책의 누락 공공자료 보강

[#404](https://github.com/SAJOYO/DAENGS_dev/pull/404)는 [지역 캐시 자동 준비](regional-catalogs.md) 다음 단위다.
현재 기록 revision의 동 주소·상권·공원·하천 자료를 찾아 기존 outbox에 명시적으로 예약한다.
주기적으로 과거 기록 전체를 다시 처리하는 작업은 아니다.

## 선택 정책

| 상태 | 처리 |
| --- | --- |
| 현재 revision·현재 v1/v2 정책의 작업이 없음 | 해당 공공자료 작업만 생성 |
| completed/failed + not_requested/unavailable | 새 수집 회차 예약 |
| failed이며 마지막 시도가 crash로 봉투 없이 끝남 | 새 회차 예약 |
| known / partial / empty | 유지. 부분 자료와 정상적인 0건은 누락이 아님 |
| pending / running / cancelled | 유지 |
| 같은 보강 정책으로 이미 예약 | 유지. 실패해도 자동으로 다시 3회를 주지 않음 |
| 삭제·임시 핀·위치 없음·지원 지역 밖 | 제외 |
| 과거 revision 또는 현재 sidecar와 다른 정책 | 제외 |
| 저장된 storyboard 행이 있는 산책 | 제외. running/ready/failed 모두 보호 |

v2의 확정 핀이 있으면 그 위치를 쓰며 원본 위치로 되돌리지 않는다.
sidecar가 없는 v1 기록은 원본 위치를 쓴다. v2 글 기록의 pin payload가 null이면 원본 위치를 쓴다.
새 좌표·행동·글·사진을 추정하거나 생성하지 않는다. facility/weather는 이번 대상이 아니다.

**기존 보드 제외는 실제 조회 계약 때문이다.** 현재 `get_diary`는 배경이 바뀌면 입력 revision이
달라져 기존 결과를 stale로 반환하고 bundle을 숨긴다. 보강만으로 사람이 보던 일기가 바뀌지 않도록
저장된 보드가 있는 산책은 이번 실행에서 보호한다. 보드 재생성/편집 보존 정책을 연결한 뒤 이 범위를
열어야 한다. 이 도구는 보강 직전에 Walk 잠금 아래서 다시 확인하며 일기 생성기를 호출하지 않는다.
보강 예약 후 사용자가 새 보드를 생성하는 경우까지 수집 완료를 기다리게 하는 정책은 추가하지 않았다.

공공자료는 현재 조회한 등록 자료다. 과거 산책 당시에도 같은 상가·공원이 있었다는 증거로 바꾸지 않고,
기존 수집기의 `lookup_snapshot`·retrieved_at·partial 표기를 유지한다.

## 회차와 이력

`collection_round`를 job과 envelope에 추가했다. 기존 데이터는 0회차이며 한 회차의 attempts는
기존처럼 0~3이다. 재수집할 때 job의 회차를 올리고 attempts를 0으로 시작한다.
봉투의 유일성은 `(job_id, collection_round, attempt)`이고 이전 봉투는 삭제·수정하지 않는다.
최신 조회와 지역 캐시 대기 재개는 현재 회차만 읽는다. worker 완료는 기존 lease token과 회차를
함께 확인하므로 이전 실행 결과가 새 회차로 들어가지 않는다.

`backfill_policy=walk-public-missing-v1`는 현재 revision·자료 작업별 예약 표식이다.
누락 작업을 새로 만들 때도 표식을 남긴다. 같은 정책으로 반복 실행하거나 재실행해도 또 예약하지 않는다.
다른 재수집 정책은 새 검토/구현 단위이며 운영자가 임의의 정책 문자열을 넘기는 옵션은 없다.

## 소량 미리보기와 적용

기본은 DB 읽기·잠금 후 rollback하는 미리보기다. 외부 API와 Gemini는 호출하지 않는다.
산책 ID 1~10개 또는 명시적 시간대가 있는 시작일 구간 `since <= started_at < until`을 지정한다.
날짜 조회는 UUID 오름차순 cursor로 페이지를 나누며 기본 1개·최대 10개 산책이다.

한 산책은 최대 600개 기록을 검사하고 초과하면 건너뛴다. **한 번의 적용은 최대 40개 자료 작업**이다.
`eligible_sources`는 전체 대상, `batch_sources`는 이번 묶음, `scheduled_sources`는 실제 예약 수다.
40개를 넘으면 같은 페이지를 다시 미리보고 남은 자료를 예약한 다음 cursor를 넘긴다.

`plan_digest`는 현재 기록·핀 revision, 수집 회차·상태·봉투 ID, 대상 및 제외 사유의 요약값이다.
적용은 그 값을 요구한다. 미리보기 이후 수정/삭제/생성/수집 상태가 달라지면 전체 예약을 rollback하고
`StaleBackfillPlan`을 반환한다. 이미 적용한 digest를 다시 보내도 중복 예약하지 않는다.
적용 후 새 미리보기의 `already_requested`로 확인한다.

현재 설정과 SGIS 키, 자동 지역 캐시 준비가 갖춰져야 적용할 수 있다.
실제 다운로드는 기존 수집 worker와 지역 갱신 worker가 자기 트랜잭션 밖에서 처리한다.
40개 예약이 즉시 40개 완료를 뜻하지는 않으며 기존 공급자 실패·일일 한도·3회 재시도가 적용된다.
최근 30일 밖 산책도 pending 자료가 생기면 지역 캐시 준비 대상에 들어간다.

backend에서:

```powershell
uv run --no-sync python -m daengs_backend.cli.walk_context_backfill --walk-id <산책 UUID>
uv run --no-sync python -m daengs_backend.cli.walk_context_backfill --walk-id <같은 UUID> --apply --expected-plan <미리보기 digest>
```

기간 조회에는 `--since 2026-09-01T00:00:00+09:00 --until 2026-09-10T00:00:00+09:00 --limit 1`을 쓴다.
다음 페이지는 응답의 `next_cursor`를 `--after`에 준다. 날짜나 산책 ID 없는 전역 실행은 거부한다.

## 서버 적용 순서

1. `Walk diary runtime` workflow를 작업 브랜치에서 `operation=Backfill / backfill_mode=Migrate`로 실행한다.
   격리 checkout에서 이 migration·verifier만 복사한다. DB dump를 checkout 밖
   `C:/deploy/daengs/db-backups/`에 보존한 뒤 단일 트랜잭션으로 SQL과 verifier를 적용한다.
2. `2026-09-10_walk_context_recollection.sql` 적용 후 변경 코드를 dev에 머지·배포한다.
   이전 worker는 추가 필드의 기본값 0으로 계속 동작한다. **새 회차 예약은 새 worker 배포 뒤에만 한다.**
3. 동일 workflow의 `Backfill / Smoke`로 임시 계정의 이전 수집 실패를 재현해 기존 Beat·worker·Gemini까지 확인한다.
4. `Backfill / Preview`에서 `backfill_scope`를 JSON으로 지정한다.
   `{"walk_id":"산책 UUID"}` 또는 `{"since":"2026-09-01T00:00:00+09:00","until":"2026-09-10T00:00:00+09:00","limit":1}`.
5. 같은 범위와 `backfill_plan` digest로 `Backfill / Apply`를 실행한다.

Preview/Apply/Smoke는 검사한 checkout과 운영 HEAD가 같아야 하며 운영 소스를 바꾸지 않는다.
로그는 건수·정책·digest·cursor·예외 종류만 담고 메모·좌표·계정·인증키는 출력하지 않는다.
새 DB 필드는 유지한 채 보강 명령 실행을 중단하면 된다. **회차 1 이상이 생긴 뒤에는 회차를 모르는
구 worker로 되돌리지 않는다.** 일시 중단은 기존 Stop으로 큐 소비를 멈추고 자료를 유지한다.

## 검증 범위

- 초기 구현: 명시한 보강 DB·기존 수집 DB/단위·지역 대기 재개·기동 검사 62 passed.
- SQL 반복 적용·기존 봉투 보존, 회차별 3회 제한, 동시 실행·변경된 계획 거부, 삭제/위치/현재 정책,
  기존 보드 보호, 40개 제한과 날짜 cursor를 임시 PostgreSQL에서 확인했다.
- PowerShell 서버 명령과 운영 재수집 Smoke의 추가 검증 결과는 PR에 실행 결과와 함께 기록한다.
- 이 단계는 기존 보드 재생성, 앱 UI 변경, 모든 과거 기록의 일괄 완료를 의미하지 않는다.
