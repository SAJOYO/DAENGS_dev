# 고정 GPS 정책의 서버 재생 계산 (#456)

DEV #449의 완료된 측정 백업을 APP #319의 `motion-v1` / `motion-measurement-v1`
규칙으로 재생한다. 전송·복원 연결은 [APP #325](https://github.com/SAJOYO/DAENGS_APP/pull/325)를 참조한다.
이번 단위는 **서버 계산과 인증된 조회**다. 기존 finalize의 legacy 분석·지도 원판을 교체하거나
앱 화면이 이 응답을 자동으로 채택하는 변경은 포함하지 않는다. v1 행동 핀은 그대로 사용할 수 있다.

> #457의 [정밀 확장](gps-motion-precision.md)이 있으면 원본 비트로 계산한다. 아래 6자리 한계는 확장 자료가 없는 기록에 해당한다.

## 호출과 응답

`GET /app/walks/{walk_id}/motion-calculation` — 앱 회원 인증과 산책 소유권이 필요하다.

기존 `/motion-capabilities`에 `calculation_versions: ["gps-motion-calculation-v1"]`을 추가한다.
백업 테이블이 없으면 빈 배열이다. 기존 backup version·필드·`calculation_verified=false`는
유지한다. APP #325는 이 기존 계약을 검사하므로 계산 지원을 이유로 그 값을 true로 바꾸면 안 된다.

계산 결과에는 다음이 들어간다.

| 필드 | 뜻 |
| --- | --- |
| `version` | `gps-motion-calculation-v1` 응답·구현 세대 |
| `policy_version`, `measurement_version`, `config_hash` | 실제로 적용한 세션의 고정 정책 |
| `manifest_fingerprint`, `evidence_fingerprint` | 검증한 백업 근거 |
| `distance_m` | 정책이 인정한 구간들의 거리 합 |
| `recording_duration_nanos`, `active_duration_millis` | 일시정지를 제외한 **기록 구간의 시간**. 이동 상태인 시간만의 합은 아님 |
| `point_count`, `segment_count` | 읽은 전체 관측 수와 연결 가능한 구간 수 |
| `segments` | 구간별 원본 `client_seq` 배열. 새로운 좌표나 인덱스를 만들지 않음 |
| `reason_counts` | 관측별 판정 이유의 건수. 하나의 관측에 이유가 여러 개일 수 있음 |
| `coordinate_basis` | `stored-raw-v1-six-decimals` |
| `device_result_verified` | false — 해당 폰의 원래 입력·계산 결과와 대조했다는 뜻이 아님 |

정책 원문의 설정으로 관측 창·기기 속도/좌표 속도·노이즈 기준점을 각각 계산한다.
고속 제외 후에는 재진입 근거가 있어야 새 구간을 시작하며, 제외 구간을 다음 저속 점에 잇지 않는다.
GPS 시간 오류는 해당 관측을 제외하고 연결을 끊는다. cached/out-of-order/동일 시각 중복은
앱 엔진과 같은 방식으로 제외한다. STOP과 같은 시각에 접수된 적격 관측은 포함할 수 있다.
전체 시간은 epoch의 단조 시각 차를 정수로 더하며 wall time 차이로 대체하지 않는다.

## 입력과 트랜잭션

기존 백업 쓰기와 같은 Walk 행 잠금 안에서 소유권·완료 상태를 확인하고, raw 지문·전체
청크 범위·각 청크 지문·정책/epoch·전체 완료 지문을 다시 검증한다. 저장됐다는 표시만 믿지 않는다.
검증된 DTO를 분리한 뒤 읽기 트랜잭션을 rollback하고 CPU 계산은 별도 스레드에서 수행한다.
계산 때문에 Walk 잠금이나 DB 연결 트랜잭션을 오래 유지하지 않는다.

결과를 새 테이블이나 기존 분석 행에 쓰지 않는다. 같은 요청은 같은 근거로 다시 계산한다.
조회 시작 시 일관된 근거를 사용하며, 조회 후 삭제된 기록을 재생성할 수 있는 쓰기 경로는 없다.
최대 100,000개 관측·1,024개 epoch는 백업의 기존 상한이다. 추정 창은 고정된 `windowSize` 이내다.
큰 결과의 전송량은 실제 인정한 원본 참조 수에 비례한다.

| 상태 | 응답 |
| --- | --- |
| 인증 없음 | 401 |
| 다른 회원·없는 산책·백업 없음 | 404 |
| collecting, 누락·변조·미지원/손상 정책, raw 불일치 | 409와 오류 code |
| 백업 테이블 미적용 | 503 `motion_storage_unavailable` |

legacy 기록에 새 정책을 추측해서 붙이거나, 실패를 legacy 계산 성공으로 숨기지 않는다.
추가 SQL·환경 변수·의존성 설치는 없다. #449 스키마와 API 코드가 전제다.

## 동등성의 범위

`gps-motion-replay-v1.json`의 expected/steps는 Python으로 만든 예상치가 아니라 실제 APP
Kotlin 엔진으로 생성했다. APP 커밋과 원본 경로의 SHA-256은 fixture에 남긴다.
32개 사례에서 구간 연결·참조·이유·정수 시간은 정확히 비교하고 거리/속도는
절대 1e-7 또는 상대 1e-10 허용오차로 JVM/Python 수학 함수의 수치 차이를 비교한다.

**이는 같은 저장 입력에서의 검증이다.** 기존 raw 저장은 좌표를 소수점 6자리로 반올림한다.
폰의 원래 Double 좌표가 그보다 정밀하면 거리뿐 아니라 문턱 근처의 구간 판정도 달라질 수 있다.
기존 raw 지문도 그 반올림을 기준으로 하므로 지문이 같다는 사실만으로 원래 좌표가 같다고
말할 수 없다. 이 한계를 보여주는 회귀 사례를 별도로 두었다. 정확도 값은 Android reader와
같이 Float32로 해석하고, 측정 속도·각도 자료는 백업의 원래 Float bits에서 읽는다.

따라서 폰 원본과의 완전한 동등성을 약속하려면 무손실 좌표 계약을 추가하고 해당 계약으로
실계정 왕복을 검증해야 한다. 기존 일기·지도 분석을 바꾸려면 그 계산 세대와 소비자를 별도로
연결해야 한다. 이 두 작업을 새 조회 API의 존재만으로 완료 처리하지 않는다.

## 검증과 재생성

backend에서 새 엔진·API와 공유 완료 검증의 소비자인 기존 백업 계약만 선택한다.

```powershell
uv run pytest -q -rs tests/walk/measurement/test_motion_replay.py tests/walk/api/test_motion_calculation.py tests/walk/api/test_walk_motion_contract.py
uv run check
```

2026-09-11 최종 표적 80개가 실패·skip 없이 통과했다. 변경 Python 파일의 ruff 검사·format과
저장소 check도 통과했다. Windows check에는 해당 프로세스의 실행 정책과 UTF-8 입출력만
설정했으며 PC의 영구 설정을 바꾸지 않았다. 전체 pytest·유료 CI·운영 배포·실기기 검증은 포함하지 않는다.

실제 DB 검사는 [백업 문서](gps-motion-backup.md)의 `check_walk_motion_backup.py`를 따른다.
새 계산 조회를 포함한 HTTP 44건과 기존 마이그레이션 정상·변조 15건을 로컬 임시 PostgreSQL에서
확인했다. 공유 개발/운영 DB에는 쓰지 않았고 임시 DB는 검사 후 제거했다.

기준값을 다시 만들 때는 fixture 옆 `MotionReplayFixtureExportTest.kt`를 APP #325 코드의
`app/src/test/java/com/daengs/app/walk/sync/`에 임시 복사한다. 원본 앱 코드가 바뀌면 fixture의
`source_commit`과 `engine_sources`를 먼저 갱신하고 변경을 리뷰한다.
`MOTION_REPLAY_CASES`는 기존 fixture, `MOTION_REPLAY_OUTPUT`은 별도의 절대 출력 경로로 두고
다음 **한 클래스만** 실행한다. expected/steps를 서버 코드로 다시 만들어 통과시키면 안 된다.

```powershell
.\gradlew.bat :app:testDebugUnitTest --tests 'com.daengs.app.walk.sync.MotionReplayFixtureExportTest'
```

실행 뒤 앱의 임시 내보내기 클래스를 제거한다. 원본 fixture와 생성 JSON의 구조를 비교하고
검토된 결과만 서버 fixture로 반영한다. 앱 소스 변경이나 전체 앱 테스트 실행은 필요하지 않다.
