# 사진 판정 상태와 복구 실행 관측

`tools/inspect_territory_vision.py`의 DB 상태 조회와 사진 worker의 복구 로그를 함께 확인한다.
사진 상태·처리 예약·재발행 조건은 [기존 worker 계약](vision-worker.md)을 따른다.
집계는 점검 명령을 실행할 때만 수행하며 사진 판정이나 재발행을 일으키지 않는다.

## 상태 조회

기존 `lease_columns_present`, `attempt_counts`, `worker_tasks`, `worker_queue_ready`, `ready`는
유지한다. 새 `backlog`에는 아래 값이 추가된다. `ready`는 컬럼·등록 task·큐의 준비 확인이다.
대기 건수가 많아도 `ready:true`일 수 있으며, 이것만으로 최근 복구 실행이나 정상 처리 속도를
증명하지 않는다. 실제 처리 상황은 `backlog`와 실행 로그를 함께 본다.

| 필드 | 의미 |
| --- | --- |
| `checked_at` | 집계 기준 DB 트랜잭션 시각, 시간대가 있는 ISO-8601 |
| `pending_count` | `VISION_PENDING`인 시도 수 |
| `active_lease_count` | pending 중 처리 예약 기한이 기준 시각보다 뒤인 수 |
| `expired_lease_count` | pending 중 처리 예약 기한이 기준 시각 이하인 수; 예약이 없던 행은 제외 |
| `exhausted_awaiting_completion_count` | pending 중 유효한 예약이 없고 DB 처리 횟수 2회를 사용한 수 |
| `pending_dispatch_due_count` | pending 중 현재 재발행 조건을 충족한 수 |
| `cleanup_pending_count` | 판정이 종결됐고 `photo_redacted_at`이 비어 있는 전체 수; 조치 필요 건도 포함 |
| `cleanup_blocked_count` | 정리 대기 중 `vision_retry_reason=photo_cleanup_conflict`로 중단된 수 |
| `cleanup_dispatch_due_count` | 정리가 남은 종결 시도 중 조치 필요 건을 제외하고 현재 재발행 조건을 충족한 수 |
| `dispatch_due_count` | 위 두 재발행 대상 수의 합 |
| `oldest_pending_created_age_seconds` | pending 중 가장 오래된 `created_at`부터 기준 시각까지 초; pending이 없으면 null |

재발행 조건은 `vision_available_at`과 `vision_dispatch_after`가 모두 기준 시각 이하이고,
처리 예약이 없거나 만료된 경우다. 종결 행의 `photo_cleanup_conflict`는 제외한다.
이 숫자는 배치 상한·다른 트랜잭션의 행 잠금 적용 전의
후보 수이며 실제 `SKIP LOCKED` 조회가 이번에 선택할 개수와 다를 수 있다.

세부 지표는 서로 겹친다. 예를 들어 예약이 만료되고 횟수도 소진된 시도는 만료·소진·재발행
대상에 함께 포함될 수 있다. 이 값들을 모두 더해서 총 pending을 계산하지 않는다.
두 번째 모델 호출의 예약이 유효하면 `active_lease_count`에 포함되고 소진 후 마무리 대기에서는
제외한다. 유효한 예약은 처리권을 확보했다는 의미이며 모델 실행 중임을 단정하지 않는다.

경과 시간은 **시도 생성 기준**이다. confirm 이후 큐 대기 시간·모델 실행 시간을 뜻하지 않는다.
재발행으로 `updated_at`이 바뀌어도 이 나이는 초기화되지 않는다. 미래 생성 시각은 0초로 제한한다.

DB 조회는 `REPEATABLE READ, READ ONLY`, SQL statement timeout 5초를 사용한다. 상태별 총수와
backlog가 같은 스냅샷을 보고, 처리 행을 잠그거나 ORM 객체·사진·좌표를 가져오지 않는다.
lease 컬럼이 부족하면 `backlog:null`, `lease_columns_present:false`로 보고한다. 확인하지 못한
집계를 0으로 표시하지 않는다.

점검 스크립트는 기존 유지보수 명령이 단독으로 실행 중 worker에 복사한다. 새 서비스/저장소
helper에 의존하지 않아 이전 worker 소스에서도 기존 컬럼을 검사할 수 있다. 처리 예산 상수는
DB의 2회 제약 및 worker와 짝 테스트로 대조한다.
`cleanup_blocked_count`의 제외 정책은 수정된 worker를 배포해야 실제 발행에도 적용된다.
구형 worker에 점검 스크립트만 복사한 경우에도 집계는 새 정책으로 계산하지만, 구형 worker는
중단 상태를 무시할 수 있다. `ready:true`는 이 정책의 코드 버전까지 증명하지 않는다.

## 복구 로그

기존 사진 worker의 INFO 로그에서 `"event": "territory_vision_recovery"`를 찾는다. Celery의
로그 접두사 뒤에 JSON 객체 하나가 출력되며 `version:1`, 실행별 무작위 `run_id`, `phase`,
UTC `at`, 단조 시계로 측정한 `elapsed_ms`, `stage`와 집계가 들어간다.

| 값 | 의미 |
| --- | --- |
| `phase:started` | 복구 함수가 실행을 시작함; 0건 처리에도 출력 |
| `phase:finished` | 배치를 마침. broker 실패 후 조기 중단도 포함하므로 `failed/deferred`를 함께 확인 |
| `phase:failed` | 예약/발행 중 예기치 않은 예외가 발생함. 기존 예외는 호출자에게 전파 |
| `phase:cancelled` | 복구 코루틴이 취소됨. 취소는 호출자에게 전파 |
| `stage:reserve/publish` | DB 예약 단계 / 큐 발행 단계 |
| `selected` | 예약 commit의 성공 응답을 확인한 대상 수 |
| `attempted` | 발행 함수를 호출하려고 시작한 수 |
| `published` | 발행 함수의 정상 반환을 확인한 수; 모델 처리 완료 수와 구분 |
| `failed` | 발행 함수에서 예외를 받은 수; 메시지 미수신까지 증명하는 값은 아님 |
| `deferred` | 이번 배치에서 아직 발행을 시도하지 않은 수 |
| `unconfirmed` | 시도했지만 성공/실패 반환을 확인하지 못한 수. 스레드 발행 중 취소될 수 있음 |

정상 반환 값은 `selected/attempted/published/failed/deferred`다. 이전 `failed`는 미발행 전체를
세었지만 이제 실제 예외를 받은 시도만 센다. 예를 들어 100건 중 3건 발행 뒤 한 번 실패하면
`selected=100, attempted=4, published=3, failed=1, deferred=96`이다. 기존 소비자가 미발행
전체를 원하면 정상 종료에서 `failed + deferred`로 읽는다.

같은 `run_id`의 시작·종료를 묶어서 최근 실행 시각과 결과를 본다. `unconfirmed`가 있으면
동기 publisher 스레드가 뒤늦게 성공할 수도 있으므로 실패나 성공으로 추측하지 않는다.
프로세스 강제 종료·전원 차단으로 종료 로그 자체가 없으면 결과는 미확인이다. 로그 부재 하나만으로
Beat 중단·큐 장애·worker 중단·로그 유실 중 어느 원인인지 확정할 수는 없다.

새 이벤트에는 예외 종류만 넣고 오류 본문·접속값·사진 키·좌표·시도 ID·lease token·공급자 원문을
넣지 않는다. 별도 heartbeat 저장소·자동 알림·SLA 판정을 추가하지 않는다. 처리 예산·30초 복구
예약·배치 상한·첫 broker 실패 후 중단·DB 세션 종료 후 발행 규칙은 유지한다.

## 확인 순서와 실행

1. 기존 [Inspect/Verify 명령](vision-worker.md#적용-순서)으로 준비 상태와 backlog를 조회한다.
2. pending의 나이가 늘고 재발행 후보가 남아 있다면 같은 시간대의 worker 복구 로그를 찾는다.
3. 최근 실행의 `failed/deferred`가 있으면 발행 장애 여부를, 시작만 있으면 실행 중단 또는 로그
   누락 여부를 확인한다. 완료 로그는 있지만 pending이 유지되면 유효한 예약·만료·소진 상태를 함께 본다.
4. `cleanup_blocked_count`가 있으면 [원본 확인과 명시적 정리 재개](photo-cleanup-conflicts.md)를
   따른다. 사진 데이터나 게임 상태를 수정하는 조치는 이 읽기 전용 점검의 일부가 아니다.

서버 운영 셸에서는 기존 `docker compose logs --tail=100 territory-vision-worker`로 로그를 본다.
현재 Windows 개발 환경의 검증은 [휴대용 PostgreSQL·Redis 준비](vision-worker.md#docker-엔진ci-없는-windows-검증)
후 `backend/`에서 수행할 수 있다. Docker 엔진·GitHub Actions가 필요하지 않다.

```powershell
$env:TERRITORY_TEST_DATABASE_URL = 'postgresql+asyncpg://postgres@127.0.0.1:55439/claims_test'
$env:TERRITORY_RUNTIME_TEST_REDIS = 'redis://127.0.0.1:56379/0'
uv run pytest -q -rs --tb=short `
  tests/territory/visits/test_territory_vision_inventory_db.py `
  tests/territory/visits/test_territory_recovery_observability.py `
  tests/territory/visits/test_territory_vision_jobs_db.py `
  tests/territory/visits/test_territory_vision.py `
  tests/territory/visits/test_territory_runtime_commands.py `
  tests/territory/visits/test_territory_vision_runtime.py -k 'not test_guarded_commands'
```

2026-09-12, 구현 `d782802`에 최신 dev `01f0ae5`를 통합한 `4116a5d`에서 위 범위를 재검증했다.
선택한 6개 파일의 62개 테스트가 Windows·Python 3.12·PostgreSQL 17.11·Redis 8.2.9에서 통과했다.
실패·오류·skip은 0개이며, 변경 없는 PowerShell 컨테이너 제어 대역 48건은 명시적으로 선택에서 제외했다.
DB 스냅샷·동시 변경·행 잠금 중 조회·migration 전 컬럼 부족·구형 코드 의존성 없는 점검,
실제 Celery solo worker의 0건/Beat 복구 JSON 로그와 worker 재시작 회귀를 확인했다.
모델·사진 저장소는 대역이며 전체 pytest·Linux prefork·운영 부하·실제 서버 배포 검증은 포함하지 않는다.
