# 산책 소유 패키지 재배치의 완료 범위

기준 DEV `0b53cb0e` (#532), 후속 PR #533. 최초 청사진은 0~4의 의존성 정리 이후 소유 영역별 패키징이었다. 5~10은 후속 조사에서 세분화한 작업 순서이며 최초부터 고정된 단계 수가 아니다. 이 문서는 남은 10개 구현과 호출자 정리로 그 범위를 닫는다.

## 구현의 최종 소유자

| 옛 서비스 루트 파일 | 실제 구현 |
| --- | --- |
| `walk_analysis` | `walk_metrics.analysis` — 측정 분석 JSONB/ORM 조립·복원 |
| `walk_measurement` | `walk_metrics.measurement` — 측정 저장·요약·페이지 |
| `walk_measurement_projection` | `walk_metrics.measurement_projection` — 공통 계산 결과 |
| `walk_motion_engine` | `walk_metrics.motion_engine` — 동결된 재생 엔진 |
| `walk_motion_calculation` | `walk_metrics.motion_calculation` — motion 조회 응답 |
| `walk_trajectory_shadow` | `walk_metrics.trajectory_shadow` — ledger 재생·투영 |
| `walk_trajectory` | `walk_metrics.trajectory` — 동선 조회 응답 바이트 |
| `walk_spatial_diary` | `walk_views.spatial_diary` — 봉인 산출물 조회·공간 집계 |
| `walk_storyboard_context` | `walk_legacy.context` — 과거 생성용 장소 조회 |
| `walk_storyboard_titles` | `walk_legacy.titles` — 과거 생성용 제목 작성 |

패키지 이름 앞에는 `daengs_backend.services`가 붙는다. 새 패키지 루트는 하위 실행 모듈을 자동 import하지 않는다. 라우터·기존 서비스·점검 도구의 직접 호출은 위 소유 모듈로 전환했다. ORM·DAO·schema는 기존 MVC 계층에 유지한다.

`walk_analysis`의 옛 셀로판/캡슐 조립 이름은 기존 루트 호환 파일에서 `walk_artifacts`로 직접 연결한다. 새 `walk_metrics.analysis`에는 그 호환 분기를 넣지 않는다. 측정 저장은 조회 응답이 아닌 공통 투영 결과를 소비하고, 공간 조회는 봉인 산출물만 읽는다. 과거 작성의 시간 제한·프롬프트·모델·실패 처리는 바꾸지 않는다.

## 바깥에 남은 파일과 허용 관계

서비스 루트 `walk.py`·`walk_*.py` 46개는 **구현 없는 호환 44개 + 독립 유지 2개**다. 호환 이름은 이번 [10개 목록](remaining-packages.json), 기존 [배경](background-package.json)·[기록/사진](records-package.json)·[원본/봉인](session-package.json) 목록과 `walk_capsule`·`walk_storyboard`·`walk_storyboard_state`의 기존 명시 재노출로 설명된다.

- `walk_activity_context`: 활동 시스템이 봉인 산책을 소비하는 통합 접점으로 유지한다.
- `walk_style`: 표시 정책을 독립 유지한다.
- 과거 도메인 `daengs_walk.storyboard_*`: 지원 중인 저장 형식·선정 계약의 구현이다. 현재 일기의 과거 정책 역참조는 이미 제거했으며 이 파일들을 지우거나 일기에 넣지 않는다.
- 공통 생성 상태는 `walk_generation.state` 하나로 유지한다. 기존 공유 저장 행과 예약·완료 경쟁 보호를 복제하지 않는다.
- 원본/봉인 → 측정·산출물, 일기 준비 → 측정 분석 복원, 공간 조회 → 봉인 산출물, 과거 생성 → 기록/공통 상태는 기능 소비 관계로 유지한다.

전체 파일별 이유와 직접 import 관계는 [소유권 목록](ownership/README.md)에 있다. 이번 재배치에서 새 교차 관계를 무조건 허용하지 않고, 이동 전 관계와 대응시켜 기존 판단을 보존했다. 지연 호환은 정적 import 목록 밖이므로 공개 객체 동일성과 새 인터프리터 검사를 별도로 둔다.

## 검증

기준 커밋과 이동한 함수·클래스 49개(비공개 보조 함수 포함)를 AST로 비교해 import를 제외한 본문 차이가 없음을 확인했다. 옛 분석 모듈의 호환용 `__getattr__`는 기존 루트 파일에 남기므로 이 비교에서 제외했다.

직접 회귀 20개 파일 **572개 통과**. 측정 투영의 기존 65개 고정 표본, 측정 ID·조회 응답 바이트·페이지 해시, 분석/산출물 복원, 공간 조회 API, 과거 관측·핀·제목 작성, 현재/과거 생성 계약과 공개 호환 객체를 검사했다. 기대 표본은 수정하지 않았다.

임시 localhost PostgreSQL 17에서 직접 DB 사례 **55개 통과, skip 0**. 첫 실행은 DB 준비 전 시작되어 17건이 접속 단계에서 실패했고 38건이 통과했다. DB 준비 확인 뒤 실패했던 17건만 재실행해 통과했다. 중복 실행을 합산하지 않는다.

| 경로 | 실제 확인 |
| --- | --- |
| 현재 카드 생성·지원 중인 슬롯/번들 생성 | `test_generation_contract_boundary`, `test_diary_generation`, `test_diary_writer_entrypoints` 및 `test_diary_board_db`: 선택 계약·현재 작성기·저장/실패 처리 |
| 과거 스토리보드 생성·조회 | `test_walk_storyboard`, 관측·핀·제목 테스트와 `test_walk_storyboard_db`: 기존 요청/응답·저장본 재사용·입력 revision 경합 |
| 기존 분석/공간 결과 읽기 | 분석 저장·산출물 표본·공간 조회 테스트와 `test_walk_summary_queries_db`: 저장본 복원·오염 검출·집계 조회 |
| 봉인 트랜잭션 | `test_walk_finalize_db`: 잠금 해제·중복 완료·입력 변경·취소·원자적 롤백·과거 캡슐 복구 |
| 저장 측정 API | `tools/check_walk_motion_backup.py`: HTTP 104건·등록된 마이그레이션 검증 52건 통과, skip 0. JSONB·응답 해시·저장 결과 재사용·입력 변경 경쟁 확인 |

DB 테스트 실행 파일은 `tests/walk/api/test_walk_finalize_db.py`, `tests/walk/diary/test_diary_generation_db.py`, `tests/walk/diary/test_diary_board_db.py`, `tests/walk/storyboard/test_walk_storyboard_db.py`, `tests/activity/test_walk_summary_queries_db.py`다. `uv run --no-sync pytest -q -rs`로 실행했다. 재실행에는 같은 파일 목록과 `--lf`를 사용했다. 측정 점검기는 별도 `walk_motion_test` DB를 사용하며 위 55개 pytest 수에 합산하지 않는다. 전체 마이그레이션 스위트를 실행한 것은 아니다.

변경 backend Python 49개 Ruff check/format, `uv run --no-sync check`, 소유권 검사/self-test, `git diff --check`를 통과했다. 소유권 목록은 745개 파일·1,735개 직접 import·348개 교차 관계와 일치한다. 임시 DB 컨테이너는 종료/제거했다. 공유 DB와 실제 공급자·모델·실기기는 사용하지 않았다.

## 완료의 의미

최초 0~4의 공용 계약·경로 정책·생성 호환·측정 투영, 조사에서 구체화한 산출물 경계, 소유 영역별 서비스 패키징을 이번 범위로 묶는다. 파일 삭제나 새로운 단계 추가를 완료 조건으로 삼지 않는다.

현재 생성·지원 중인 과거 생성·기존 저장본 읽기의 검증은 각각의 테스트 결과로 구분한다. 전체 저장소·실제 공급자/모델·실기기·APP/GEO 저장소 전체를 검증했다는 뜻은 아니다. 장면 선정·속도 임계값·프롬프트·모델 호출 전략·DB 스키마 변경과 호환 경로 제거는 이번 작업에 포함하지 않는다.
