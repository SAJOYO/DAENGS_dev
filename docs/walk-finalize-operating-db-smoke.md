# Walk finalize 운영 DB smoke

- 실행일: 2026-09-02 (KST)
- 대상 코드: `dev` `5b40f22` (#140 finalize API 포함)
- 대상 DB: 공유 운영 `vectordb`
- 최종 결과: **PASS**

접속 host, 비밀번호, 합성 테스트 UUID는 기록하지 않는다.

## 로컬 회귀

`backend/` 기준으로 실행했다.

```powershell
uv run pytest -q tests/walk
uv run ruff check src/daengs_backend/repositories/walk.py `
  src/daengs_backend/routers/walk.py `
  src/daengs_backend/schemas/walk.py `
  src/daengs_backend/services/walk.py tests/walk
```

| 검증 | 결과 |
| --- | --- |
| Walk 테스트 | PASS, 91개 |
| 변경 경계 Ruff | PASS |

## 첫 실행에서 발견한 migration 누락

첫 운영 DB smoke는 합성 산책의 좌표를 저장하는 단계에서 다음으로
실패했다.

```text
UndefinedTableError: relation "walk_point_chunks" does not exist
```

`2026-09-02_walk_analyses.sql`은 적용됐지만 선행
`2026-09-02_walk_point_chunks.sql`은 적용되지 않은 상태였다. 당시 운영
DB에는 `walks` 9행과 예전 `walk_points` 932행이 있었고,
`walk_point_chunks`는 없었다. 합성 데이터는 외부 transaction rollback과
exact cleanup으로 모두 제거했다.

## 운영 데이터 백업·이관

이관 전에 `public.walk_points_backup_20260902_pre_chunks`를 만들어 예전
좌표를 보존했다. 백업 테이블은 현재 DB user가 소유하고 `PUBLIC`
권한은 제거했다.

| 백업 검증 | 결과 |
| --- | --- |
| 원본 `walk_points` | 932행 |
| 백업 | 932행 |
| 키·시각·좌표·정확도·mock 행 불일치 | 0건 |
| 산책별 sequence | 7개 모두 0부터 연속 |
| orphan point | 0건 |

그다음 저장소의 `db/migrations/2026-09-02_walk_point_chunks.sql`을
그대로 적용했다.

| migration 후 검증 | 결과 |
| --- | --- |
| `walk_points` | 제거됨 |
| `walk_point_chunks` | 7행 |
| chunk의 `point_count` 합 | 932 |
| 백업에서 재생성한 JSONB와 payload 불일치 | 0건 |
| v·cols·payload 길이·첫/마지막 sequence 불일치 | 0건 |
| 필수 PK·FK·CHECK 누락 | 0건 |
| 백업 테이블 | 932행 유지 |

## 운영 DB rollback smoke

실제 SQLAlchemy repository/service를 운영 DB에 연결했다. 합성 AppUser·Walk만
외부 transaction 안에서 만들고, service의 `commit()`은 savepoint로 가두었다.
실행 후에는 외부 transaction을 rollback하고 합성 AppUser ID를 정확히 지우는
cleanup을 한 번 더 실행했다.

| 단계 | 결과 |
| --- | --- |
| 합성 산책 + 7개 좌표 chunk 저장 | PASS |
| 첫 finalize | PASS, analysis 1행 + sheet 1행 + `derived` |
| promotion fixture fingerprint·이동거리·이동시간 | PASS |
| 같은 finalize 재시도 | PASS, 같은 `analysis_id`, 신규 행 0 |
| finalize 후 append | PASS, `walk_already_finalized` |
| 불완전 manifest | PASS, `point_count_mismatch` |
| 불완전 산책의 상태·analysis | PASS, `collecting`·0행 |

## cleanup 결과

| 항목 | 결과 |
| --- | --- |
| 잔존 합성 AppUser | 0행 |
| 잔존 합성 Walk | 0행 |
| 운영 Walk | 기존 9행 유지 |
| 음수 Kakao user | 0행 |

이로써 #140의 repository/service 경로와 운영 DB 스키마는 실제 PostgreSQL에서
완주했다.

## Android 앱 왕복

DAENGS_APP #71 (`71acb6f`)의 실제 debug APK와 계측 APK를 Pixel 8
에뮬레이터에 `adb install -r`로 설치했다. 두 설치 모두 `Success`를 확인했고,
설치된 base APK와 빌드 산출물의 SHA-256도 일치했다.

공유 운영 DB에는 합성 AppUser 한 명만 잠깐 만들었다. 저장소의 빈 로컬 JWE 키를
운영 키로 추측하거나 교체하지 않기 위해, 일회성 JWE 키를 가진 같은 backend
코드를 개발 PC의 별도 8017 포트에 띄웠다. 앱은 `adb reverse`를 거쳐 이 HTTP
서버에 접속했고, 서버는 위와 같은 공유 운영 DB를 사용했다. access token은 계측
프로세스 인자로만 전달하고 출력하거나 파일에 저장하지 않았다.

| 앱 호출 | HTTP | 결과 |
| --- | ---: | --- |
| 첫 `POST /app/walks` | 201 | PASS |
| 첫 finalize | 201 | PASS, `derived`·7점 |
| 같은 finalize 재시도 | 200 | PASS |
| finalize 뒤 append | 409 | PASS, 앱에서 실패로 처리 |
| 목록 pull | 200 | PASS |
| 상세·7점 pull | 200 | PASS |

계측 결과는 `OK (1 test)`였다. 실행 직후 합성 Walk 1행과 AppUser 1행을
정확한 ID로 삭제했고, 잔존 합성 Walk·AppUser는 각각 0행, 운영 Walk는 기존
9행으로 돌아왔다. 테스트용 서버·ADB reverse·계측 APK도 제거했으며 에뮬레이터에는
공개 개발 주소를 보는 정상 #71 APK를 다시 설치해 `Success`와 해시 일치를 확인했다.

### 왕복에서 발견한 별도 버그

같은 `client_session_id`로 upload를 즉시 재시도하면 첫 요청은 201이지만 두 번째
요청이 500이었다. `upload_walk()`이 기존 Walk를 반환한 뒤 라우터의
`_to_detail()`이 eager-load되지 않은 `walk.points`를 읽어 async lazy load를
시도하면서 `sqlalchemy.exc.MissingGreenlet`가 발생한다. finalize 멱등성은 위와
같이 201 → 200으로 통과했지만, 앱 주석이 약속하는 **upload 멱등 재시도는 아직
운영 가능한 상태가 아니다.**

### 공개 배포 상태

`http://daengback.weareithero.cloud`는 `/health` 200이고 공개 OpenAPI에 finalize
경로가 있다. 반면 출시 주소 `https://daengapi.weareithero.cloud`는 `/health`는
200이지만 같은 OpenAPI에 finalize 경로가 없다. 따라서 공개 nginx + 운영 JWE를
포함한 최종 출시 왕복은 출시 backend를 #140 이상으로 배포하고 위 upload 재시도
500을 고친 뒤 다시 확인해야 한다.

## 백업 보관 주의

`walk_points_backup_20260902_pre_chunks`는 자동 삭제하지 않았다. migration 후
신규 chunk가 쌓이면 이 백업만으로는 최신 입력을 복원할 수 없으므로,
롤백할 때는 신규 `walk_point_chunks`도 함께 보존·역변환해야 한다. 백업
테이블 삭제는 실기기 왕복 검증과 보관 기간 결정 후에 별도로 한다.
