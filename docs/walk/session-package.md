# 원본·업로드·봉인 서비스 패키징 — 8단계 (#532)

기준은 DEV `a8284218`(#531 머지)이다. 원본 입력·불변 백업·업로드·봉인을 `services/walk_session`으로 배치한다. 측정 계산 엔진과 공간 조회는 9단계 대상이며, 현재 봉인 산출물 패키지와 일기 실행 패키지를 유지한다.

## 소유 경로

| 기존 services 모듈 | walk_session의 소유 모듈 | 책임 |
| --- | --- | --- |
| walk | lifecycle / errors | 산책 조회·업로드·봉인 트랜잭션 / 공통 소유권·상태 오류 |
| walk_chunk | chunk | 원본 청크 codec·기록 필드 보존 |
| walk_finalize | finalize | 봉인 입력 검증·지문·증거 DTO 준비 |
| walk_recording | recording | GPS 기록 구분 영수증·제한된 원본 보완 |
| walk_upload_receipt | upload_receipt | opt-in 수신 확인·청크 무결성·재시도 |
| walk_motion | motion | 불변 motion 백업·청크 수신·완료/조회 |
| walk_precision | precision | 원본 좌표 비트의 불변 정밀도 백업 |
| walk_motion_contract | motion_contract | motion manifest·관측 검증·정규 지문 |
| walk_precision_contract | precision_contract | 정밀도 지문·원본 좌표와의 교차 검증 |

`walk_session.__init__`은 실행기를 모아서 불러오지 않는다. 원본 codec/계약/백업을 사용하는 기록·측정·일기·도구는 실제 소유 모듈을 직접 import한다. 공통 `WalkNotFoundError`·`WalkStateConflictError`는 `errors`에 있어 백업/조회가 예외 타입 때문에 봉인·외부 어댑터·산출물 구현을 로딩하지 않는다.

옛 9개 경로는 [공개 이름별 호환 목록](session-package.json)에 있는 타입·함수·상수만 같은 객체로 지연 제공한다. 구현이나 예외 객체를 복제하지 않는다. 내부 의존성 변수·비공개 함수나 옛 모듈에 대한 monkeypatch 전달은 공개 호환 계약이 아니다. 해당 테스트 대역은 새 구현 소유 모듈에 주입한다.

## 보존하는 계약과 남기는 경계

- 업로드는 client session 중복/UNIQUE 충돌 후 rollback·재조회·기존 응답을 유지한다. receipt-v1은 청크 범위·내용 무결성과 재시도 응답을 독립적으로 검증한다.
- 봉인은 최초 읽기 트랜잭션을 종료한 뒤 계산·날씨 조회를 수행한다. 이후 게임 공통 잠금 → Walk 행 잠금 순서로 다시 읽고, 원본/manifest 일치를 확인한 뒤 분석·cellophane·capsule·활동 집계를 같은 트랜잭션에서 저장한다.
- 다른 봉인이 먼저 완료하면 같은 분석을 재사용한다. 원본 변경/삭제/외부 조회 실패와 저장 오류의 기존 처리 및 legacy capsule 복구도 유지한다.
- motion/precision은 같은 Walk 잠금 아래 원본·완료 지문을 확인한다. precision이 motion의 내부 잠금/검증 함수를 쓰는 관계는 같은 백업 패키지 내부에 남는다. 측정 엔진은 공개 `completed_input` 결과를 소비하고 CPU 계산 전에 세션 잠금을 해제한다.
- 청크 직렬화·정규 해시·기록 구분·핀 정책·계산 정책·시간 제한·HTTP/DB 저장 형식은 바꾸지 않는다. ORM·DAO·schema·라우터는 MVC 계층에 남긴다.
- `lifecycle`은 한 산책의 트랜잭션 조정을 계속 소유한다. 측정 계산은 기존 측정 영역, 봉인 산출물 조립은 `walk_artifacts`, 활동 잠금/집계는 활동 영역, 날씨 조회는 기존 어댑터를 소비한다. 공급/계산 로직을 이 패키지에 복제하지 않는다.

현재 [소유권 목록](ownership/README.md)은 732개 파일·1,725개 직접 로컬 import·350개 영역 간 관계다. 서비스 루트 walk 계열 46개도 전부 분류되어 있으며, 이번 9개 파일은 호환 역할로 남는다. 동적 호환은 정적 import 목록 밖이므로 명시 목록과 동일 객체 테스트로 별도 검사한다.

## 검증 기록

2026-09-14, Python 3.12의 기존 backend uv 환경에서 수행했다.

- 기준 커밋과 함수·클래스 67개(비공개 보조 함수 포함)의 AST 본문 비교: import를 제외한 차이 0개.
- 직접 회귀 16개 파일, 493개 통과: 원본 codec/기록 구분·핀, 업로드/봉인 HTTP, motion/precision 고정 표본, 측정 계산/투영, 산출물, 기록·과거 storyboard·공동 돌봄 접점 및 패키지 경계.
- 임시 localhost Postgres에서 76개 통과/skip 0: 업로드 경쟁, 봉인 중 입력 변경/삭제/중복 완료/롤백, 수신 확인, v2 기록, 과거 storyboard, 활동 집계와 공유 산책 삭제 접점. 활동 테스트는 직접 연결되는 3개 사례만 선택했다.
- 기존 `tools/check_walk_motion_backup.py --dsn .../walk_motion_test`: HTTP 104건, 등록된 motion/precision/measurement 마이그레이션 검증 52건 모두 통과, skip 없음. 실제 JSONB·완료 전/후 조회·중복 업로드·해시 손상·정밀도·저장 측정·삭제 경쟁을 확인했다. 전체 마이그레이션 스위트를 실행한 것은 아니다.
- 새 인터프리터에서 원본/백업의 봉인·일기 없는 import와 공통 예외의 설정/DB 없는 import를 검증한다. 공개 이름 동일성과 제품의 옛 경로 역참조도 검사한다.

`uv run --no-sync check`, 변경 Python 66개 Ruff/형식 검사, 소유권 검사/self-test와 `git diff --check`도 통과했다. 실행 명령은 PR #532 본문에 기록한다. 임시 DB는 검사 후 종료했으며, 공유 DB·실제 외부 공급자/모델·실기기·전체 저장소 스위트는 이번 범위 밖이다.

이 문서 작성 당시 측정/공간 조회와 과거 지원 구현이 남아 있었다. 후속 조사에서 붙인 9·10 번호는 최초 청사진의 고정 단계가 아니며, 해당 10개 구현과 호출자는 [잔여 패키징 #533](remaining-packages.md)에서 함께 정리한다. 8단계의 검증 기록은 당시 범위를 뜻한다.
