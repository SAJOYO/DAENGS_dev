# GPS 측정 자료의 분할 백업 — gps-motion-backup-v1

APP #319가 고정한 측정 정책과 수신 메타데이터를 기존 산책 원본에 연결해 보관한다.
완료 응답은 **자료의 저장·범위·지문 일치**를 뜻한다. 서버에서 Android와 같은 거리·시간을
계산했다는 뜻은 아니며, 모든 상태 응답의 `calculation_verified`는 `false`다.
기존 raw 업로드·finalize·recording eligibility 보완·v1 행동 핀 경로는 그대로 사용한다.

이 PR은 서버 저장 API와 SQL까지다. Android 전송·재시도·복원 연결과 서버 측정 엔진의
동일 계산 검증은 후속 작업이다. 머지만으로 앱이 이 API를 호출하지 않는다.

## 호출 순서

모든 경로는 앱 회원 인증과 산책 소유권을 확인한다. 같은 산책의 쓰기는 기존 Walk 행의
잠금으로 직렬화한다. 아래 경로의 공통 접두사는 `/app/walks`다.

| 순서 | 메서드·경로 | 역할 |
| --- | --- | --- |
| 1 | GET `/motion-capabilities` | `backup_supported`, 고정 청크 크기 256, 최대 원본 100,000개·epoch 1,024개 확인 |
| 2 | 기존 raw 업로드·finalize·eligibility 보완 | `derived` 산책, 연속 `client_seq`, 모든 원본의 확정된 `recording_eligible` 준비 |
| 3 | PUT `/{walk_id}/motion-backup` | 종료된 epoch 목록·고정 정책·원본 지문을 담은 manifest 확정 |
| 4 | PUT `/{walk_id}/motion-backup/chunks/{index}` | `{manifest_fingerprint, points}`로 측정 메타데이터 전송 |
| 5 | POST `/{walk_id}/motion-backup/complete` | `{manifest_fingerprint, evidence_fingerprint}`로 전체 일치 검증·완료 |
| 6 | GET `/{walk_id}/motion-backup` | manifest, 수신 청크 번호, collecting/complete 상태와 완료 지문 조회 |
| 7 | GET `/{walk_id}/motion-backup/chunks/{index}` | 완료된 자료를 청크별로 읽기 |

청크 `i`는 `client_seq=i*256`부터 최대 256개다. 마지막 청크만 짧을 수 있다.
도착 순서는 자유이고 동일 manifest/청크/완료 요청의 재전송은 같은 결과를 반환한다.
내용이 다른 덮어쓰기는 409다. 빈 산책은 원본 0개와 마지막 STOP epoch를 가지며,
청크 없이 완료할 수 있다. 실제 수집되지 않은 옛 기록의 정책·메타데이터를 생성해서 채우지 않는다.

`collecting` 중에는 상태로 누락 청크를 확인하고 전송을 재개한다. 모든 청크가 저장돼도
complete 요청의 전체 지문 검사가 끝나기 전에는 완료로 취급하지 않는다.
복원 클라이언트는 기존 raw 원본도 읽어 `raw_input_fingerprint`를 대조하고, 각 청크와
완료 지문을 다시 계산한 뒤 로컬 측정 자료로 사용한다. 페이지 응답의 지문만 믿고 누락 페이지를
완료로 처리하지 않는다. 원본 좌표·시각·정확도는 기존 raw에 있고 새 테이블에 중복 저장하지 않는다.

## 손실 없는 전송과 지문

필드 형식은 `schemas/walk_motion.py`, 실행 가능한 기준값은
`backend/tests/walk/fixtures/gps-motion-backup-v1.json`이다.

- timestamp/nanos/sequence는 JSON 정수다. Android `Long`을 유지하고 중간에 Double이나
  JavaScript Number로 바꾸지 않는다. 기준값에 2^53보다 큰 nanos가 있다.
- speed·bearing과 각각의 accuracy는 Android `Float.toRawBits()`를 8자리 소문자 16진수로
  보낸다. `80000000`(-0), `7fc00001`(NaN payload), `null`(미수집)을 구분한다.
- nullable 관측 필드도 생략하지 않고 명시적 `null`로 보낸다. 예상하지 못한 필드와 숫자/불리언
  자동 변환을 거부한다. source/clock/provider 문자열은 스키마의 ASCII 범위로 제한한다.
- `policy.config_json`은 APP에서 고정한 원문 그대로 보관한다. `config_hash`는 원문 UTF-8의
  SHA-256 소문자 64자리이며 접두사가 없다. 전송 어댑터가 JSON을 재정렬하거나 새 기본값을 넣으면 안 된다.
- 지원 정책은 `version=motion-v1`, `observation_schema_version=15`,
  `measurement_version=motion-measurement-v1`이다. 알려진 설정 키와 유효 범위를 검사한다.

아래 지문은 배열을 **공백 없는 UTF-8 JSON**으로 직렬화하고 SHA-256을 계산한
`sha256:` + 소문자 64자리다. 정수는 십진수, 불리언/null은 JSON 리터럴을 사용한다.
객체 키 정렬에 의존하지 않으며 배열 필드 순서는 다음과 같이 고정한다.

```text
manifest = [version, client_session_id, raw_input_fingerprint, point_count,
            policy.version, policy.observation_schema_version,
            policy.measurement_version, policy.config_hash, [epoch, ...]]

epoch = [source_epoch, clock_epoch_id, chain_index, started_at_millis,
         started_elapsed_nanos, first_ingress_seq, ended_at_millis,
         ended_elapsed_nanos, target_ingress_seq, persisted_count,
         end_kind, drained, failure_reason, first_failed_seq]

point = [client_seq, source_epoch, clock_epoch_id, chain_index,
         elapsed_realtime_nanos, received_elapsed_nanos, received_at_millis,
         speed_mps_bits, speed_accuracy_mps_bits, bearing_degrees_bits,
         bearing_accuracy_degrees_bits, provider, recording_eligible]

chunk = [point, ...]  // client_seq 오름차순
evidence = ["gps-motion-backup-v1", manifest_fingerprint,
            [chunk_fingerprint, ...]]  // chunk_index 오름차순; 빈 기록은 []
```

epoch의 연속 원본 범위·개수, source 중복, clock 일치, 증가하는 chain, 겹치지 않는 단조 시각,
마지막 STOP과 앞선 PAUSE, `drained=true` 및 실패 정보 null을 검증한다.
마지막 종료 wall time은 기존 Walk의 종료 밀리초와 같아야 한다. 원본별 chain·eligibility도 대조한다.
기록 대상인 관측의 수신 단조 시각이 STOP 이후면 거부한다. 미수집된 측정 시각은 null로 보관하며,
이후 측정 엔진이 제외 판단을 하도록 한다. wall time 차이로 활동 시간을 추정하지 않는다.

## 오류와 저장 수명

| 응답 | 처리 |
| --- | --- |
| 401 | 앱 인증 갱신 후 재시도 |
| 404 | 해당 회원의 산책/백업/완료 청크를 찾을 수 없음 |
| 422 | 형식·지원 버전·상한 위반. 요청 수정 필요 |
| 409 | `detail.code`로 원본/manifest/epoch/청크/완료 지문 불일치 구분 |
| 503 | `motion_storage_unavailable`: 마이그레이션 전 상태. 기존 raw/핀 동기화는 계속 가능 |

불완전한 수신 상태는 `motion_chunks_incomplete`, 완료 전 읽기는 `motion_backup_incomplete`다.
그 외 충돌은 동일한 잘못된 내용을 무한 재전송하지 말고 로컬 원본과 서버 상태를 대조한다.
잘못된 청크는 저장 전에 거부한다. 오류 시 트랜잭션을 되돌리고, 완료된 자료를 덮어쓰지 않는다.
`walk_motion_backups` → `walk_motion_chunks`는 산책에 종속되며 산책의 물리 삭제 때 함께 삭제된다.

## 적용과 검증

최초 DB에는 `db/init/35_walk_motion_backup.sql`, 기존 DB에는
`db/migrations/2026-09-11_walk_motion_backup.sql`을 적용하고
`db/migrations/verify_2026-09-11_walk_motion_backup.sql`의 성공을 확인한다.
적용은 멱등이고 검증 SQL은 읽기 전용이다. API 코드와 SQL 적용이 모두 끝나야 capability가 열린다.
이 PR의 로컬 검증은 임시 DB에서 했으며 공유 개발/운영 DB에 적용하지 않았다.

일반 계약·인증 검증은 별도 DB 없이 backend에서 실행한다.

```powershell
uv run pytest -q -rs tests/walk/api/test_walk_motion_contract.py
uv run check
```

저장 경로를 바꿀 때만 아래 명시적 검증을 추가한다. 기본 pytest에 DB 설정 요구나 skip을 추가하지 않는다.
팀원이 상시 DB를 유지할 필요는 없다. 도구는 loopback의 `walk_motion_test` DB만 허용하고
자신이 만든 UUID 스키마를 검사 후 제거한다. 실행할 DB가 없으면 실패하며 건너뛰지 않는다.

```powershell
docker run -d --rm --name codex-gps-motion-check -p 127.0.0.1:55449:5432 -e POSTGRES_PASSWORD=motion-test-only -e POSTGRES_DB=walk_motion_test pgvector/pgvector:pg17
docker exec codex-gps-motion-check pg_isready -U postgres -d walk_motion_test
uv run python tools/check_walk_motion_backup.py --dsn postgresql+asyncpg://postgres:motion-test-only@127.0.0.1:55449/walk_motion_test
docker stop codex-gps-motion-check
```

실제 PostgreSQL에 마이그레이션 반복 적용, 검증 SQL, API 재전송·동시 중복·역순 수신·중단 재개,
다른 회원 차단·잘못된 raw/청크 거부·빈 기록 완료·새 연결 복원·원본 불변·삭제 연쇄를 확인한다.
루트 SQL 하네스에 등록된 이 마이그레이션의 정상/변조 사례 15개도 실행한다.
신규 계약/인증 24개, 기존 raw·recording·repository·finalize 관련 45개,
실제 DB HTTP 검사 35개와 SQL 사례 15개를 통과했다. 전체 pytest나 운영 배포 검증은 포함하지 않는다.
