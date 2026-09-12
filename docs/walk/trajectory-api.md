# 산책 동선 검증 후보 조회 API

상태: **소유자 전용 후보 조회 구현**, 2026-09-12, PR #475.
[동선 계약](trajectory-contracts.md)과 [그림자 계산](trajectory-shadow.md)을 앱에서
대조할 수 있는 HTTP 경계다. 봉인된 입력의 계산 결과를 반환하지만, 기기 대조를 완료했거나
활성 산책 화면으로 채택했다는 뜻은 아니다. 기존 거리·일기·장면·동선을 갱신하지 않는다.

## 요청과 가용 범위

기존 앱 회원 Bearer 인증과 `Walk` 소유권 검사를 사용한다.

```http
GET /app/walks/trajectory-capabilities
GET /app/walks/{walk_id}/trajectory-calculation?version=walk-trajectory-calculation-v1
GET /app/walks/{walk_id}/trajectory-calculation?version=walk-trajectory-calculation-v1&expected_measurement_id=shadow-{64자리 hex}
```

`version`은 필수다. 생략하거나 지원하지 않는 값을 보내면 422다. capability의
`calculation_versions`를 확인한 뒤 명시적으로 요청한다. 저장 스키마가 준비되지 않으면
버전 목록은 비어 있고 실제 계산 요청은 503 `motion_storage_unavailable`이다.
capability는 개별 산책의 백업 완전성을 보장하지 않는다.

이번 전송은 `delivery=whole_result`, **최대 관측 10,000개**다. 기존 motion 백업의
100,000개 제한과 별개이며, 초과하면 409 `trajectory_point_limit`을 반환한다.
점을 잘라내거나 선을 단순화해서 성공 응답으로 바꾸지 않는다. 산책 목록에서 자동 호출하는
용도가 아니라 선택한 완료 기록의 명시적 대조용이다. 긴 기록의 제품 연결에는 불변 저장과
chunk 전달이 필요하다.

capability와 결과는 `candidate`, `device_result_verified=false`, 관측 연결 정책은
`experimental`이다. `persisted_measurements_supported`와 `active_read_view_supported`는
둘 다 false다. 이 버전 협상은 기존 motion/precision 업로드 capability와 독립이다.

## 한 응답에 묶이는 사실

| 필드 | 의미 |
| --- | --- |
| `walk_id`, `measurement.scope` | 서버 산책 ID와 소유자·기기 산책 ID. 측정 ID만으로 권한을 판단하지 않는다 |
| `measurement` | ID·입력/정책/정밀도 key·봉인된 최종 원장. `superseded`는 빈 배열로 제공한다 |
| `result_digest` | 최종 원장의 계약 지문. 좌표·투영·전송 응답 전체의 지문은 아니다 |
| `locations` | 원본 관측의 좌표와 SourceRef. 제외/불확실 관측도 있으므로 이 배열을 곧바로 polyline으로 잇지 않는다 |
| `wall_times` | 저널과 같은 순서의 SourceRef별 원본 벽시계 밀리초. 관측은 raw의 `at`, 제어는 epoch 시작/종료 시각 |
| `observed_runs`, `walking_sections` | 동일한 최종 원장에서 파생한 연결 가능한 관측 경로와 인정 보행 구간 |
| `boundaries` | 기록 시작/종료, 첫/마지막 확인 위치, 첫/마지막 보행점. 위치가 없으면 null |
| `metrics` | 인정 거리, 확보된 위치·시간, 공백/불명 구간 요약. 최종 원장을 한 번만 합산한다 |
| `average_walking_speed_mps` | 인정 보행거리 ÷ 인정 이동 시간. 그 시간에 불명 구간이 있거나 분모가 0이면 null |
| `located_fraction_of_known_time` | 위치를 확인할 수 있는 시간 ÷ 길이를 아는 시간. 분모가 0이면 null |
| `motion_recording_duration_ns` | 기존 motion-v1 기록 시간. 위치 확보 시간·이동 시간과 별도다 |
| `observation_policy` | 그림자 관측 연결 정책의 실제 파라미터. 기본 20초/200m는 실험 기준이며 보행 속도 필터가 아니다 |

API의 `measurement.completion=sealed`는 **그 응답을 만든 입력/정책의 계산이 완료됨**을 뜻한다.
서버 결과 우선 채택, 검증 완료, 영속 저장 완료를 뜻하지 않는다. 재평가로 대체된 판단은
진단 서비스에 남지만 이 HTTP 응답의 집계·경로에는 포함하지 않는다.

원본 시각은 정렬하거나 보정하지 않는다. 위치가 없는 첫 GPS 전/마지막 GPS 후에도
제어 사건의 시간 주소가 남는다. `wall_times`로 서로 다른 clock epoch를 이어 붙이거나
공백의 길이·경로를 추정하지 않는다. 판단 가능한 경과 시간은 저널의 `elapsed_ns`,
부적합한 원본 시각과 근거는 `original_elapsed_ns`/`time_reasons`를 확인한다.
나노초는 64비트 정수로 읽는다. JavaScript Number를 거치면 정확성을 잃을 수 있다.

## 조회 일관성·재요청

1. 기존 `completed_input()`이 같은 Walk 잠금 아래 소유권, 완료 상태, raw/motion/precision
   범위·지문을 검증하고 입력을 분리한다. DB rollback으로 잠금을 먼저 해제한다.
2. CPU 계산·투영·JSON 직렬화는 별도 스레드에서 수행한다. 좌표·시각을 추가로 다시 조회하지 않는다.
3. `expected_measurement_id`가 있으면 그 입력으로 계산한 ID와 일치해야 한다.
   다르면 409 `trajectory_measurement_changed`이고 성공 결과·새 ID를 함께 반환하지 않는다.
4. 응답 전체를 받은 뒤 버전·소유자·세션·측정 ID·무결성을 확인하고 대조에 사용한다.

이 API는 **현재 조회 시점에 확보한 입력의 계산**이다. 이전 ID를 지정한다고 과거 입력이나
계산 결과를 저장소에서 찾아주는 API가 아니다. 정밀 좌표 백업이 나중에 완료되면 key와 ID가
달라지고, 예전 ID를 지정한 재요청은 409가 된다. 기대 ID 없이 새로 조회한 결과는 새로운
후보로 취급한다. 계산 도중 새 입력이 가용해져도 이미 분리한 응답 내부의 버전을 섞지 않는다.

동일한 소유자·산책·입력·계산/전송 버전의 재요청은 같은 응답 바이트를 낸다.
HTTP `ETag`는 **UTF-8 JSON 응답 바이트 전체의 SHA-256을 큰따옴표로 감싼 값**이다.
측정 ID·최종 원장 지문과 별개로 좌표·시각·투영까지 묶어 확인할 수 있다. JSON을 파싱한 뒤
재직렬화한 문자열을 해시하지 않는다. 계산/전송 형식을 바꾸는 후속 구현은 해당 버전을
올려야 한다. 이 endpoint는 `If-Match`/`If-None-Match`/304 조건부 조회를 구현하지 않는다.

성공 응답·capability·도메인 오류에는 `Cache-Control: private, no-store`와
`Vary: Authorization`을 붙인다. 서버에는 후보 캐시나 활성 참조를 저장하지 않는다.
기기의 영속 캐시와 비교·채택은 후속 계약에서 별도로 결정한다.

| 응답 | 의미 |
| --- | --- |
| 401 | 인증 실패. 백업 저장소 접근 전에 거부 |
| 404 | 해당 회원의 산책 또는 motion 백업 없음. 다른 회원의 존재 여부를 알리지 않음 |
| 409 | 미완료·손상·미지원 저장 입력, 정밀 백업 충돌, 기대 ID 변경, 관측 상한 초과 |
| 422 | 누락/미지원 API 버전 또는 잘못된 요청 ID 형식 |
| 503 | motion 백업 저장 스키마 미준비 |

409의 기존 `motion_*`/`precision_*` 코드는 그대로 유지하고, 그 밖의 잘못된 입력 모양은
`trajectory_calculation_invalid_input`으로 처리한다. 실패를 legacy 계산 성공으로 바꾸지 않는다.

## 검증과 후속 작업

HTTP 테스트는 기존 raw/motion/precision 저장소 반환값을 주입한 실제 FastAPI 라우터를
통과한다. 기존 Kotlin 기준 입력 32건의 거리·구간·원본 시각·공백·끝점을 직렬화 후 검증한다.
소유권, 인증, 지원 버전, 완료/손상 거부, 늦은 precision에 따른 ID 변경, 같은 응답 바이트,
DB 잠금 해제 후 별도 스레드 계산을 확인한다. DB 쓰기·SQL·기존 입력 검증은 변경하지 않았다.

```powershell
uv run --no-sync pytest -q -rs tests/walk/api/test_trajectory_calculation.py tests/walk/api/test_motion_calculation.py tests/walk/api/test_walk_motion_contract.py tests/walk/measurement/test_trajectory.py tests/walk/measurement/test_trajectory_view.py tests/walk/measurement/test_trajectory_shadow.py tests/walk/measurement/test_motion_replay.py tests/walk/measurement/test_motion_precision.py tests/walk/test_package_boundary.py tests/test_main_stays_light.py
uv run --no-sync check
```

위 타겟 **377 passed, skip 없음**. 변경 Python 파일의 ruff check/format과 저장소 check도
통과했다. Windows 검사 실행 시 이 프로세스에만 `PSExecutionPolicyPreference=Bypass`,
`PYTHONUTF8=1`을 지정해 검증 스크립트 실행과 한글 출력 디코딩을 맞췄다.

개발 PC의 단일 합성 10,000점 실행에서 전체 JSON은 **23,109,110바이트**, 투영·직렬화 약
**2.52초**, Python 역직렬화·검증·최종 지문 확인 약 **1.36초**였다. 입력 누락 없이 한 보행
구간으로 반환됐다. 이는 DB/네트워크/실기기를 제외한 한 번의 로컬 실행이며 운영 지연이나
Android 성능 수치가 아니다. 큰 응답이므로 제품 상세 화면의 자동 조회 전에는 저장된
manifest/chunk 전달 경계를 연결해야 한다.

불변 측정의 영속 저장, 검증 기록, 준비 완료 후 활성 화면 전환, 장면 revision/binding CAS,
Android 측정 비교·파서·화면 적용은 후속이다. APP #333의 구간/장면 선택 UI는 현재 기존
앱 경로를 사용한다. 이 API 추가만으로 실제 509m/951m 차이의 원인이나 해결을 주장하지 않는다.
