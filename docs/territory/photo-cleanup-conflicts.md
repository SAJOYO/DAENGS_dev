# 사진 정리 충돌과 명시적 재개

confirm한 사진이 사라지거나 다른 generation으로 교체되면 `redact()`가
`StorageObjectChangedError`를 낸다. 종결 판정만 저장하고 정리 시각을 비워 두던 구현은
이 행을 매번 자동 재발행했다. 이제 기존 `vision_retry_reason`에
`photo_cleanup_conflict`를 저장해 자동 발행과 이미 큐에 남은 중복 요청을 중단한다.

## 상태와 복구 경계

| 상태 | 다음 처리 |
| --- | --- |
| `VISION_PENDING` | 기존 DB lease·최대 2회 처리 예산 유지 |
| 종결·정리 미완료·중단 코드 없음 | 기존 주기적 정리 재발행; 일시적인 저장소 장애 포함 |
| 종결·정리 미완료·`photo_cleanup_conflict` | 자동 발행·중복 전달 제외, 운영자 확인 필요 |
| 종결·`photo_redacted_at` 있음 | 완료. 재판정·재정리 없음 |

종결은 `VERIFIED`, `REJECTED`, `FAILED`다. 판정·방문 인증·게임 소유권을 먼저 commit하고
잠금 없이 사진을 정리한다. 실패가 영구 충돌이면 새로 행을 잠그고 같은 원본과 정리 상태인지
확인한 뒤 중단 코드를 저장한다. 이 저장 자체가 실패하면 일시 오류로 전파되고 기존 자동 복구가
계속 찾을 수 있다. `photo_redacted_at`은 저장소 정리가 성공한 뒤에만 채운다.

정리 I/O 전의 `vision_available_at`을 기억하고 실패 반영 시 대조한다. 명시적 재개는 이 값을
단조 증가시켜 먼저 시작한 작업의 늦은 실패가 재개를 되돌리지 못하게 한다. 다른 정리의 성공,
행 삭제, 원본 generation·키 변경도 다시 조회해 보존한다. 겹친 작업 중 정리가 성공하면
중단 코드를 지우고 완료한다. 자동 복구는 `vision_dispatch_after`만 예약하므로 오래 걸리는
정리 중 새 메시지가 발행돼도 영구 오류 기록을 막지 않는다. 정리 시도는 모델 호출 예산을 소비하지 않는다.

## 확인과 재개

1. [상태 조회](vision-observability.md)의 `cleanup_blocked_count`를 확인한다.
   `cleanup_pending_count`에는 이 건수도 포함되지만 `cleanup_dispatch_due_count`에서는 제외된다.
2. 권한 있는 운영 셸에서 대상 시도의 ID·원래 `photo_object_generation`·저장 키와 실제 저장소를
   대조한다. 서비스가 잘못된 볼륨/버킷을 보고 있는지, 원본이 누락되거나 교체됐는지 확인한다.
3. 같은 원본을 안전하게 정리할 수 있는 상태로 복구한 뒤 아래 명령으로 **한 시도만** 재개한다.
   명령은 저장소를 수정하거나 복구 여부를 자동 판정하지 않는다.

LocalBridgeStorage의 generation은 바이트의 SHA256이므로 정확한 원본을 복원한 경우에만
동일 generation이 된다. GCS는 같은 바이트를 다시 올려도 새 generation을 발급한다.
GCS 원본 복구·tombstone 복원은 저장소별로 별도 확인해야 하며, 재업로드만으로 해결됐다고
간주하지 않는다. 다른 객체를 덮어쓰거나 DB의 원래 generation을 현재 객체 값으로 바꿔 우회하지 않는다.
원본을 확인할 수 없다면 중단 상태를 유지한다.

다음은 **수정된 worker를 배포한 뒤** 운영 저장소 루트의 PowerShell에서 실행하는 명령이다.
두 변수에는 위에서 확인한 실제 대상 값을 넣는다. 스크립트는 새 서비스 함수가 필요하므로
읽기 전용 점검 스크립트와 달리 구형 worker에 단독 복사해서 사용할 수 없다.

```powershell
$attemptId = '확인한 시도 UUID'
$expectedGeneration = 'confirm에 저장된 원래 generation'
docker cp tools/resume_territory_photo_cleanup.py daengs-territory-vision-worker:/tmp/resume_territory_photo_cleanup.py
if ($LASTEXITCODE -ne 0) { throw '정리 재개 도구 복사 실패' }
docker exec -w /app daengs-territory-vision-worker uv run --no-sync python /tmp/resume_territory_photo_cleanup.py `
  --attempt-id $attemptId --expected-generation $expectedGeneration
if ($LASTEXITCODE -ne 0) { throw '정리 재개 조건 불일치 또는 실행 실패: 상태 재확인 필요' }
```

`{"cleanup_resumed": true}`는 해당 행의 중단 상태를 해제해 재발행 대상으로 돌렸다는 뜻이다.
즉시 모델·저장소·큐를 호출하지 않는다. 기존 30초 발행 예약이 남았다면 그 기한 뒤 Beat가
찾는다. 실제 완료 시각은 worker·broker 가용성과 다음 처리에 달려 있다.
`false`와 종료 코드 1이면 시도 없음·generation 불일치·pending·이미 완료/재개 중 하나다.
실행 오류도 종료 코드 1이며 오류 종류만 출력한다.

재개 뒤 `cleanup_blocked_count` 감소만으로 정리 완료를 판단하지 않는다. 대상의
`photo_redacted_at`과 전체 `cleanup_pending_count`를 다시 확인한다. 원본 문제가 남아 있으면
다음 정리 실패가 다시 중단 상태를 만든다. 판정·방문 인증·소유권·모델 예산·원래 generation은
재개 명령으로 변경되지 않는다.

## 적용과 검증

기존 lease 컬럼을 사용하므로 새 SQL migration·환경 변수·패키지·APP 배포는 필요 없다.
DEV 코드를 반영하고 backend·사진 worker를 갱신해야 적용된다. 기존 `dev` 자동 배포는 이 둘을
재생성하고 Beat를 재시작한다. 구형 worker로 되돌리면 중단 코드가 남아 있어도 예전 자동 발행이
재개될 수 있으므로, 상태 조회의 `ready`와 코드 배포 여부를 구분한다.

회귀 검증은 실제 PostgreSQL에서 파일 누락/교체, 반복 복구·이미 큐에 있던 중복 요청,
일시 오류, 저장 실패, 완료·재개·원본 변경·삭제와의 경합을 확인한다. 실제 임시 파일 복원 뒤
tombstone과 create-only 업로드 차단도 확인한다. 별도 Celery solo 프로세스와 Redis에서는
worker 재시작 뒤 중단 유지와 명시적 재개 뒤 정리를 확인하며 모델은 대역이다.
Docker 엔진 없는 실행 준비는 [Windows 검증](vision-worker.md#docker-엔진ci-없는-windows-검증)을 따른다.

```powershell
# backend/에서, 위 문서의 폐기용 DB·Redis 환경 변수를 설정한 뒤 실행
uv run pytest -q -rs tests/territory/visits tests/territory/ownership tests/territory/certification `
  tests/activity/test_vision_lease_db.py --tb=short -k 'not test_guarded_commands'
```

2026-09-12, `ae7a2e2` 기반 최종 구현에서 위 18개 파일의 **서로 다른 227개 테스트를 확인**했다.
Windows·Python 3.12·PostgreSQL 17.11·Redis 8.2.9를 사용했다. 묶음 실행은 226 passed,
1 failed, 0 errors, 0 skipped였다. 변경하지 않은 동시 파일 업로드 테스트의 5초 Barrier에서
`BrokenBarrierError`가 한 번 발생했다. 해당 저장소 테스트 파일을 수정 없이 따로 재실행해
12 passed를 확인했다. 한 번의 전체 실행이 무실패였다는 뜻은 아니며, 대기 초과의 환경 원인은 확정하지 않았다.
변경 없는 PowerShell 컨테이너 조작 대역 48건은 명시적으로 제외했다. 새 회귀 15건에는
정리 충돌 DB 13건, 집계 1건, 실제 worker 재시작 1건이 포함된다. 실제 worker 회귀 전체는 5건이다.
`uv run check`, 변경 Python 9개 lint·format, 재개 CLI의 인자 도움말도 확인했다.
전체 pytest·Linux prefork·실제 GCS/VLM·운영 부하·실제 서버 배포는 이 로컬 검증에 포함하지 않는다.
