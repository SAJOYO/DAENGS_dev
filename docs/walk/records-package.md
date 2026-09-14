# 기록·사진 서비스 패키징 — 7단계 (#531)

기준은 DEV `159067b7`(#530 머지)이다. 기록 8개 서비스와 사진 동기화 1개 서비스의 구현을 책임별 패키지로 배치한다. 수정·삭제·핀 확정·사진 동기화·배경 outbox의 기존 동작은 유지한다.

## 소유 경로

| 기존 services 모듈 | 구현 소유 경로 | 책임 |
| --- | --- | --- |
| walk_entry | walk_records.v1 | v1 기록 CRUD와 프로필 진입 |
| walk_entry_v2 | walk_records.v2 | v2 기록/핀 CAS, mutation 재시도 응답, 삭제 |
| walk_entry_errors | walk_records.errors | 기록의 공유 예외 객체 |
| walk_entry_pin | walk_records.pins | 원본 GPS 근거 및 핀 전이 검증 |
| walk_entry_policy | walk_records.policy | 기능 가용성·v1/v2 접근 정책 |
| walk_entry_profile | walk_records.profile | 공통 기록 프로필 계산 |
| walk_entry_context | walk_records.context | 기록 outbox 예약·claim·외부 수집·완료 |
| walk_context_backfill | walk_records.backfill | 누락 배경 재수집 dry-run·CAS 예약 |
| walk_photo | walk_photos.api | 사진 manifest CAS·재시도·삭제 tombstone |

호출자는 버전이나 책임이 드러나는 소유 모듈로 들어온다. `walk_records.__init__`과 `walk_photos.__init__`은 전체 구현을 모아서 로딩하지 않는다. 하나의 API가 v1/v2·사진·배경을 암묵 선택하지 않는다.

옛 9개 파일은 [공개 이름별 호환 목록](records-package.json)에 있는 함수·상수·예외를 같은 객체로 지연 제공한다. 기존 v2의 `capabilities`, `guard_v1`, `EntryUpgradeRequired` 재노출도 유지한다. 내부 의존성 변수나 옛 모듈에 대한 monkeypatch 전달은 공개 계약이 아니며 테스트는 구현 소유 모듈을 대상으로 한다.

## 트랜잭션과 의존성

- v1은 산책 잠금 안에서 기록 변경과 outbox 예약을 수행한 뒤 commit한다. v2는 부모 flush → pin/outbox/receipt 저장 → commit의 순서를 유지한다.
- `context.take`는 claim 결과를 commit하고 세션을 닫은 뒤 공급자를 호출한다. `context.finish`는 산책 → 작업 행 잠금 순서와 lease/collection round/원본 revision 검증을 유지한다. 과거 결과가 새 기록이나 삭제된 기록에 반영되지 않게 한다.
- 재수집은 기존 plan digest, 위치/pin revision, 최대 개수, 저장된 일기 제외 정책을 유지한다. 기록이나 일기를 새로 작성하지 않는다.
- 사진은 기록 서비스·배경·일기 패키지를 참조하지 않는다. 소유 산책 DAO와 사진 DAO를 사용하고, 요청 해시·publisher·manifest revision·삭제 표식은 기존 계약을 유지한다.
- 기록은 배경의 `contracts`/`collection`과 카탈로그 조회를 소비한다. 배경 공급자는 기록 패키지를 역참조하지 않는다.
- 원본 GPS 확인은 별도 원본 기능을 소비한다. 후속 8단계 #532에서 호출 경로가 `walk_session.chunk`·`walk_session.recording`으로 옮겨지며 핀 검증 정책은 유지한다.
- 라우터·ORM·DAO·schema·CLI·Celery는 MVC/실행 계층을 유지한다. HTTP URL·직렬화·task/queue 이름·설정·SQL 파일은 변경하지 않는다.

## 소유권 목록의 조정

`walk_records.context/backfill`과 기록별 outbox를 저장/조회하는 ORM·DAO·schema·CLI는 records 소유로 분류한다. 이전 background 분류에 포함되던 기록별 배경 상태의 트랜잭션 주체를 구체화한 것이다. ORM·DAO·schema·CLI의 실제 파일은 이동하지 않는다. 기록 context 처리와 배경 카탈로그 갱신을 함께 연결하는 Celery 모듈은 integration으로 분류한다.

현재 [소유권 목록](ownership/README.md)은 720개 파일·1,714개 직접 로컬 import·354개 영역 간 관계다. 서비스 루트의 walk 계열 46개 파일은 여전히 범위에 포함된다. 이번 9개는 구현이 제거된 호환 파일이므로 파일 개수만 보고 미완료/완료를 판단하지 않는다. 동적 호환 관계는 정적 AST 목록과 별도로 명시 목록/객체 동일성 테스트로 확인한다.

## 검증 기록

2026-09-14, Python 3.12의 기존 backend uv 환경에서 수행했다.

- 기준 커밋과 옮긴 함수/클래스 55개의 AST 본문 비교: import를 제외한 차이 0개. 잠금/flush/commit·예외·정규 해시·응답 생성 본문을 그대로 유지한다.
- 직접 회귀 13개 테스트 파일, 201개 통과: 기록 v1/v2·HTTP·핀·사진 manifest/삭제/재시도, context·날씨·backfill 명령·원본 근거·runtime smoke 및 패키지 경계.
- 임시 localhost Postgres에서 9개 테스트 파일, 51개 통과/skip 0: v2 mutation receipt/핀 CAS, v1/v2 기록과 outbox의 원자성, 사진 저장, context lease/늦은 완료, 재수집, catalog demand, 현재 일기 입력 변경과 과거 storyboard 저장 경로.
- 새 인터프리터에서 기록 오류·정책·프로필·핀의 독립 로딩과 사진의 기록/배경/일기 없는 import를 검증한다. 배경 공급자가 새 기록 패키지를 참조하는 것도 차단한다.

소유권 갱신 후 경계 36개 재검증, `uv run --no-sync check`, 변경 Python Ruff 검사/형식 검사, 소유권 검사/self-test, `git diff --check`가 통과했다. 실행 명령은 PR #531 본문에 기록한다. 전체 저장소 스위트·실제 공급자/모델·실기기는 이번에 검증하지 않았다.

## 남은 순서

8단계 원본/업로드/봉인은 후속 #532에서 다룬다. 이후 9단계 측정/공간 조회, 10단계 남은 과거 호환과 통합 검증이 남아 있다. 0~5단계에서 해결한 결합이나 이번 6~7단계의 패키징 성과를 산책 전체 완료로 확대하지 않는다.
