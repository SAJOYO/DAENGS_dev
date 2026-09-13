# 산책 측정 저장·상세 소비 — 1~2단계

완료된 motion 백업과 원본 좌표 정밀 백업에서 만든 측정을 불변 결과로 저장한다. 일반 APP 상세가 요약과 경로 청크를 검증한 뒤 함께 채택한다. 연결 PR은 [DEV #495](https://github.com/SAJOYO/DAENGS_dev/pull/495), [APP #368](https://github.com/SAJOYO/DAENGS_APP/pull/368)다. 운영/main 반영은 별도 합의 대상이다.

## HTTP 계약

모두 `/app/walks` 아래이며 현재 산책 소유자의 인증이 필요하다. POST/GET 모두 `?version=walk-measurement-v1`이 필수다.

| 요청 | 동작 |
| --- | --- |
| `POST /{walk_id}/measurements` | 현재 봉인 입력의 기존 결과를 반환하거나 계산·저장한다. 같은 입력은 CPU 재계산 없이 저장된 바이트를 반환한다. |
| `GET /{walk_id}/measurements/{measurement_id}` | 불변 요약을 읽는다. |
| `GET /{walk_id}/measurements/{measurement_id}/chunks/{index}` | 요약에서 지정한 경로 청크를 읽는다. |

`trajectory-capabilities`의 `persisted_measurements_supported`와 `measurement_versions`가 저장 지원을 알린다. 기존 `trajectory-calculation` 진단 계약은 유지하며 그 `max_points=10,000`, `delivery=whole_result`는 진단 응답에 대한 값이다. 새 저장 계약은 원본 최대 100,000점, 경로 청크당 최대 256점을 받는다. 100,000점 성능을 실측했다는 뜻은 아니다.

요약에는 측정의 scope/key/result_digest, base·precision 입력 지문, 거리와 시간 지표, 기록/관측/산책 경계, 청크 순서·점 수·UTF-8 바이트 수·SHA-256이 들어간다. 경로 점은 section 종류·ID·순번, 원본 SourceRef, 좌표·원본 wall/elapsed 시각, **들어오는 최종 구간의 거리 기여량**을 가진다. 청크 경계는 구간 경계가 아니다. observed_run에 담긴 기여량을 walking_section과 중복 합산하면 안 된다.

응답 ETag는 실제 UTF-8 응답 바이트의 SHA-256이다. 모든 성공/소유권·저장 오류 응답은 private/no-store, Vary Authorization을 사용한다. 소유자 불일치·삭제·없는 결과는 404, 불완전한 정밀 백업/입력 변경/결과 손상은 409, 저장 미지원은 503이다. 버전·ID·청크 범위 오류는 422다.

## 저장과 채택 경계

- `37_walk_measurements.sql`과 `2026-09-13_walk_measurements.sql`은 같은 추가 스키마다. 기존 방식대로 migration과 verify를 함께 적용한다. 이번 작업에서 공유 DB에 적용하지 않았다.
- 계산은 검증된 입력을 분리한 뒤 DB 잠금 밖에서 실행한다. 저장할 때 부모 Walk 소유권과 봉인 지문을 다시 확인하고 요약·모든 청크를 한 트랜잭션에 넣는다. 동시 호출은 동일 결과로 수렴하며, 계산 중 삭제된 산책은 되살리지 않는다.
- 같은 ID에 다른 결과를 덮어쓰는 갱신 API는 없다. transport/계산·projection 변경은 버전을 올리고 새 측정 정체성 계약을 정해야 한다. 서비스의 `input_key` 버전만 바꾸고 기존 ID를 재사용하면 안 된다.
- 서버의 `device_result_verified=false`, 관측 정책의 `experimental`은 계속 사실이다. APP이 로컬 원본·정밀 지문·기기 산책 구간·거리·기록 시간·원본 경계를 따로 검증한다. 이 앱 검증을 서버의 기기 검증 영수증으로 표현하지 않는다.
- 서버의 활성 read-view 포인터, 장면 binding의 measurement/event/scene revision, 관측 보조선 UX와 시간 slice는 다음 단계다. APP은 기존 상세 화면의 준비/교체 경계를 사용한다.

## 검증

- `uv run pytest tests/walk/api/test_trajectory_calculation.py tests/walk/measurement/test_stored_measurement.py -q -rs`: 76 통과. 기존 32개 정밀 재생 사례와 600점 장거리의 공유 wire fixture를 실제 Kotlin 소비자도 읽는다.
- `uv run python tools/check_walk_motion_backup.py --dsn postgresql+asyncpg://.../walk_motion_test`: 격리된 로컬 PostgreSQL에서 HTTP 104개, migration 변조 52개 통과, skip 없음. 동시 준비, 소유권, 반복 조회 무재계산, 저장 바이트 손상, 계산 중 삭제, FK 삭제 전파 포함.
- 등록된 `walk_measurements` migration 검사 22개 통과. `uv run check` 통과. 전체 SQL 확장 기능 검사는 이 로컬 PostgreSQL에 vector 확장이 없어 완료하지 않았다.

공유 fixture는 `backend/tests/walk/fixtures/walk-measurement*.json`과 APP `app/src/test/resources/walk/`에 동일하게 둔다. 출력 문자열을 재직렬화하면 청크 해시가 달라진다. 계산이 바뀌면 서버 fixture 비교와 Kotlin 대조를 함께 검토한다.
