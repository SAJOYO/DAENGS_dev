# GPS 원본 좌표 보존과 기기 계산 대조 (#457)

기존 raw는 소수점 6자리 좌표다. #456 계산에 원래 기기 Double을 넣기 위해
완료된 `gps-motion-backup-v1`에 별도 `gps-motion-precision-v1` 자료를 연결한다.
APP 연결은 [APP #327](https://github.com/SAJOYO/DAENGS_APP/pull/327)이며 #325 위의 후속이다.
기존 백업 응답과 v1 핀 활성화는 유지한다.

## 저장·HTTP 계약

회원 인증과 산책 소유권 아래 `/app/walks/{walk_id}/motion-precision`을 사용한다.
PUT/GET은 manifest·상태, `/chunks/{index}` PUT/GET은 분할 자료,
`/complete` POST는 완료 지문이다. 모든 쓰기는 기존 Walk 행 잠금을 공유한다.
상한은 기존과 같이 관측 100,000개, chunk 256개, index 0..390이다.

- manifest 순서: `version`, `client_session_id`, `base_evidence_fingerprint`, `point_count`.
- point 순서: `client_seq`, `lat_bits`, `lng_bits`, `accuracy_bits`.
- 위경도는 binary64 원본의 소문자 hex 16자리, 정확도는 Float32 hex 8자리 또는 명시적 null.
- 시각·수신 정보·고정 정책·epoch는 기존 완료 백업을 참조한다. 정밀 확장에 복제하지 않는다.
- manifest 지문은 위 순서의 값 배열, chunk 지문은 point 값 배열들의 배열에 기존 canonical digest를 적용한다.
- 완료 지문은 `[version, manifest_fingerprint, [chunk_fingerprint, ...]]`의 digest다.
- 원래 좌표의 6자리 반올림이 기존 raw와 같아야 한다. 정확도는 Float32로 대조한다.
  유한하지 않은 값·좌표 범위 밖·음수 정확도·누락·순번 불일치는 거부한다. -0 비트는 보존한다.
- 부모 백업 완료가 전제이며, 정밀 완료/계산 시 부모의 raw·정책·epoch·지문도 재검증한다.
- manifest/chunk는 고정되며 같은 요청은 멱등이다. 완료된 자료의 변경은 409다.
- 소유자 불일치/없음은 404, collecting·손상·불일치는 409, 미적용 표는 503이다.

## 계산 및 호환

`motion-capabilities.precision_versions`는 추가 표가 준비됐을 때만 버전을 반환한다.
기존 `backup_supported`와 `calculation_verified=false`는 바꾸지 않는다.
`GET .../motion-calculation`은 완료 정밀 자료가 있으면 `coordinate_basis=device-fix-bits-v1`,
`precision_fingerprint`와 함께 계산한다. 없으면 기존 6자리 계산과 null 지문이다.
정밀 자료가 collecting이면 409로 반환한다. 중간 상태를 정밀 계산 성공으로 표시하지 않는다.
DB 잠금을 풀고 원본 스냅샷을 분리한 뒤 별도 스레드에서 엔진을 실행한다.

서버의 `device_result_verified`는 여전히 false다. 서버가 휴대폰과의 대조를 확인한 사실은 없다.
APP이 세션/정책/기존·정밀 지문을 확인하고 실제 Kotlin 엔진으로 거리·기록 시간·구간 참조·사유를
대조한 뒤에만 로컬 검증 영수증을 저장한다. 거리 허용오차는 abs 1e-7 또는 rel 1e-10이며,
정수·연결 결정·원본 참조·사유는 완전히 같아야 한다. 업로드 완료와 대조 완료는 별개다.

## 적용 순서

1. #449의 기반 표가 적용된 DB에 `db/migrations/2026-09-11_walk_precision_backup.sql`을 적용한다.
2. `db/migrations/verify_2026-09-11_walk_precision_backup.sql`로 확인한다.
3. 이 서버 코드와 APP #325 → #327을 반영하고 실계정의 새 산책으로 왕복 검증한다.

SQL은 `db/init/36_walk_precision_backup.sql`에도 반영했다. 기존 볼륨에 init만 배포하면 적용되지 않는다.
이번 PR에서는 공유 개발 DB에 이 새 SQL을 적용하지 않았다. 임시 로컬 PostgreSQL에서만 검사했다.
표가 없으면 기존 GPS 백업은 작동하며 새 APP의 정밀 자료는 대기한다.

## 검증

- `tests/walk/measurement/test_motion_precision.py`, `test_motion_replay.py`,
  `tests/walk/api/test_motion_calculation.py`, `test_walk_motion_contract.py`: 표적 128개 통과, skip 0.
- `gps-motion-precision-v1.json`의 32개 입력은 기존 APP 재생 사례의 좌표에 6자리 아래 정밀도를 더했다.
  expected는 Python에서 생성했으며 같은 파일을 APP의 실제 Kotlin 엔진이 독립적으로 대조한다.
  기존 `gps-motion-replay-v1.json`은 원래 Kotlin 생성 기준값 그대로 보존한다.
- `tools/check_walk_motion_backup.py --dsn <loopback walk_motion_test>`: HTTP 76건 + SQL 변조 30건 통과.
  실제 JSONB, 중복 동시 요청, collecting, owner, 빈 산책, 정밀 복원 자료와 삭제 CASCADE를 확인한다.
  도구는 UUID 임시 schema를 생성·삭제하고 실행 환경이 없으면 실패한다. 기본 pytest에 skip을 추가하지 않는다.
- `uv run --no-sync check`, 변경 Python의 ruff, diff 공백 검사.

실기기/실계정 왕복과 기존 finalize·지도 원판의 계산 세대 전환은 아직 별도 후속이다.
