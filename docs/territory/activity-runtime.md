# 점령 점수·결산 런타임 운영

DEV #452는 기존 activity.process를 실행하는 worker와 30초 Beat를 추가한다.
게임 배점·DB 스키마를 바꾸지 않고 첫 시즌도 생성하지 않는다.

## 구성과 기본 상태

- `activity-worker`, `activity-beat`는 `activity-game` profile이다. 기본 `docker compose up -d`로 시작하지 않는다.
- backend·사진 워커·activity 프로세스는 같은 env_file 순서와 DB/Redis 접속 설정을 사용한다. 게임 flag는 기존 `DAENGS_ACTIVITY_GAME_ENABLED` 하나이며 기본 false다. `backend/.env`에서 관리하고 다른 override 파일에 중복 지정하지 않는다.
- activity worker / Beat는 각각 별도 venv를 사용하고 ML 그룹을 설치하지 않는다. 컨테이너 재시작 정책은 `unless-stopped`다.
- OFF에서는 Beat가 작업을 보내지 않는다. 남은 activity 메시지를 worker가 받아도 DB 세션을 열지 않고 disabled로 종료한다.
- ON에서는 30초마다 기존 process를 호출한다. 오래된 주기 메시지는 60초에 만료되며 다음 작업이 DB revision과 실제 시간으로 따라잡는다. 미처리 원본은 큐에만 의존하지 않는다.
- Beat는 Redis DB당 `daengs:activity:beat:leader:v1` 잠금 하나를 60초 임대로 사용하고 최대 5초마다 갱신한다. 나머지는 standby이며 발행하지 않는다. Redis 연결을 잃으면 발행을 중단한다. 인메모리 스케줄이라 재시작 후 첫 요청까지 약 30초 걸린다.
- 분산 잠금은 exactly-once 전송 보장이 아니다. 프로세스 정지·브로커 장애 경계나 수동 process 호출에 따른 중복 처리는 기존 DB advisory lock과 원장/영수증의 중복 방어를 따른다. 옛 `--scheduler` 지정으로 잠금을 우회하는 별도 Beat는 함께 운영하지 않는다.

## 개발 서버에서 OFF 상태로 준비

아래는 **서버 PC의 실제 배포 checkout**에서만 실행한다. 로컬 개발 PC에서 앱 compose를 띄우지 않는다. `.env`/암호화 키·DB·Redis가 기존 서버와 같고 게임 flag가 false인 것을 먼저 확인한다. 이미 있는 서비스를 새 DB로 교체하지 않는다.

```powershell
docker compose --profile activity-game up -d --no-deps --force-recreate --wait --wait-timeout 240 activity-worker
docker compose --profile activity-game up -d --no-deps --force-recreate --wait --wait-timeout 240 activity-beat
docker compose --profile activity-game ps activity-worker activity-beat territory-vision-worker backend
docker compose exec -T activity-worker uv run --no-sync python -m daengs_backend.cli.activity_runtime
docker compose exec -T activity-beat cat /tmp/activity-beat.json
```

각 명령의 종료 코드가 0일 때만 다음으로 진행한다. worker와 Beat는 시작 전에 `/schema-checks`에 마운트된 점령 관련 8개 verify SQL을 읽기 전용 트랜잭션에서 확인한다. 성공 출력은 `schema_checks:8`, `game_enabled:false`, 최초 준비 시 `active_seasons:0`이다. 스키마 누락/접속 실패는 기동 실패로 표시되며 마이그레이션을 자동 적용하지 않는다. 초기 의존성 설치가 240초를 넘으면 로그를 확인하고 완료 후 다시 점검한다.

## 상태·처리 확인

```powershell
docker compose logs --tail 60 activity-worker activity-beat territory-vision-worker
docker compose exec -T activity-worker uv run --no-sync celery -A daengs_backend.tasks.activity:app inspect registered
docker compose exec -T activity-worker uv run --no-sync python -m daengs_backend.cli.activity_runtime
```

worker health는 지정된 `activity@hostname`의 ping, Beat health는 30초 이내 tick heartbeat를 본다. `disabled`와 `leader`가 healthy이고 `standby`는 unhealthy로 드러나므로 중복 Beat를 정리한다. health 성공만으로 DB 배치/사진 판정 성공을 보장하지 않는다. ON 후에는 worker 로그의 `activity.process ... succeeded`, 현재 시즌과 `pending_accounts`, API의 confirmed/score 기준 시각을 함께 확인한다. 반복 실패·pending 증가 시 원인을 해결한다. 사진 워커 ping은 실제 사진 저장소·Gemini 호출 검증을 대체하지 않는다.

## 재배포와 중지

개발 자동 배포는 시작 전 실행 중인 activity 두 컨테이너를 확인한다. 둘 다 없거나 중지됐으면 그대로 두고, 하나만 실행 중이면 자동으로 나머지를 켜지 않고 배포를 실패시킨다. 둘 다 실행 중이면 Beat/worker를 중지하고 웹·사진 워커를 재생성한 뒤 worker→Beat 순서로 재생성·health 확인한다. 앱 게임 flag와 시즌은 변경하지 않는다. 새 배포가 갱신 중인 배포를 취소하지 않도록 deploy concurrency의 cancel-in-progress는 false다.

사진 워커도 매번 재생성하므로 기존 장기 실행 프로세스에 이전 import/환경 변수가 남지 않는다. 처리 중인 작업의 종료 유예는 60초다. 실제 배포에서는 잠깐 사진 처리 대기와 API 재시작이 발생한다. 배포 중 실패/수동 취소로 activity가 멈추면 로그를 해결한 뒤 위 준비 명령으로 명시적으로 재시작한다. 이후 배포가 멈춰 둔 서비스를 임의로 다시 켜지는 않는다.

```powershell
# 점수/만료/결산 처리만 중지. 시즌 종료나 게임 OFF 명령이 아니다.
docker compose --profile activity-game stop activity-beat activity-worker
```

시즌이 ACTIVE인 상태에서 flag만 끄면 기존 무점수 점령 writer와 DB integrity guard가 충돌할 수 있다. 게임 OFF를 롤백 방법으로 삼지 않는다. 중지 시 신규 점령 쓰기를 통제하고, 장애를 해결해 같은 정책 처리기를 복구한다. `down -v`, 큐 전체 삭제, 시즌/원장 삭제는 복구 절차에 없다.

## 실제 오픈과 GCP

실제 ON은 후속 작업이다. 앱 점령 전송을 아직 열지 않은 상태에서 웹·사진·activity의 flag를 함께 true로 반영하고 모두 재생성한 후, [월간 시즌](monthly-seasons.md)의 `start-monthly`를 명시적으로 실행한다. worker는 시즌이 없다고 첫 시즌을 만들지 않는다. 그 뒤 실제 점령·사진 판정·점수·연장을 검증한다.

GCP에는 이 파일이 자동 적용되지 않는다. 내부 checkout/DB를 확인한 뒤 GCP compose overlay를 포함한 별도 배포가 필요하다. 이 PR에서 GCP 접속·배포·DB 변경은 수행하지 않는다.

참고: [Celery periodic tasks — 단일 스케줄러와 custom scheduler](https://docs.celeryq.dev/en/latest/userguide/periodic-tasks.html).

## 검증 기록 (2026-09-11)

- `tests/activity/test_runtime.py`, `test_activity.py`, `test_runtime_deploy.py`: 22개 통과. OFF 시 DB/브로커 미접근, 처리기 위임, 읽기 전용 preflight, heartbeat, Compose 렌더, PowerShell 배포의 미시작/활성/부분 기동/웹 갱신 실패를 확인했다.
- `test_runtime_redis.py`: 별도 loopback Redis 컨테이너에서 3개 통과. 실제 due Beat 2개가 메시지 하나를 발행, 잠금 소유권 교체, 실제 Celery worker의 OFF 메시지 수신·DB 미접속을 확인했다. 이 테스트는 `ACTIVITY_RUNTIME_TEST_REDIS=redis://127.0.0.1:<임시포트>/0`을 명시해야 실행되며 공유 Redis 기본 포트 6379를 거부한다.
- 변경 Python ruff, actionlint(deploy.yml), `git diff --check`, `uv run check`의 세 항목 통과. Windows 검사 프로세스에만 `PSExecutionPolicyPreference=Bypass`, `PYTHONUTF8=1`을 적용했다.
- 제품 Compose 스택 기동·실제 운영 배포·ON 상태의 실게임·사진 판정·DB 변경은 하지 않았다. 전체 pytest 스위트는 실행하지 않았다.
