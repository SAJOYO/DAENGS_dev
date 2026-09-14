# 산책 일기 서비스 패키지

2026-09-14, #512는 dev `1c30430b`의 서비스 33개를 `daengs_backend.services.walk_diary`로
묶는다. 후속 #514의 2단계에서 장면 선정·공간 관계 정책도
[`daengs_walk.diary` 패키지](diary-domain-package.md)로 묶는다.
장면 목표 수·거리 간격·프롬프트·공개 JSON·저장 형식·생성 마감은 이 작업에서 변경하지 않는다.

## 진입점과 책임

| 위치 | 책임 |
| --- | --- |
| `api.py` | 기존 storyboard 서비스와 미리보기 라우터가 사용하는 생성·조회·협상·미리보기 진입점 |
| `runtime.py` | 기본 `write_board`·`write_cards`, 모델과 수집기를 기존 `build_diary_orchestrator`에 주입 |
| `contracts.py` | 저장 판독에서도 사용하는 작성 결과 데이터 계약. 설정·실행·소유권 검사 의존성 없음 |
| `model_input.py`·`model_materials.py` | #508의 허용 필드 투영·짧은 참조 복원 계약. 저장 판독에서도 사용하며 현재 작성 정책·모델 SDK를 import하지 않음 |
| `guard.py`·`deadline.py` | 준비/공개가 공유하는 소유권·생성 검사와 요청 마감 |
| `preparation/` | 저장 입력·관측 검증·기본 보드 준비. 예약·발행·모델 호출 없음 |
| `lifecycle/` | 생성 예약 commit → 외부 작성 → 원본 재확인·완료 commit, GET 복구와 형식 협상 |
| `collection/` | 표준 배경 조회, 요청별 진행·고정·자료 적용. `comparison.py`는 명시적 비교 도구 전용 |
| `writing/` | 공간·행동·제목 작업과 프롬프트·전송·결과 조립 |
| `storage/` | 기존 저장 형식·영수증의 생성·판독·무결성 검사 |
| `legacy/` | 계속 지원하는 과거 bundle/slot 작성과 과거 보드 표본 조립 |

각 `__init__.py`는 책임 설명만 둔다. 패키지 import만으로 설정·모델·생성 서비스를
불러오지 않는다. `api.py`는 필요한 서비스만 호출 시 연결하며 내부 함수 전체를 재노출하지 않는다.

```text
storyboard 서비스 / 미리보기 라우터
  → walk_diary.api
  → lifecycle: 준비·예약·기존 공개본 확인
  → runtime: 기본 카드 작성 의존성 연결
  → 기존 orchestration.runtime.build_diary_orchestrator
  → 기존 orchestration.diary + 공유 execution.JobExecutor
      collection: 확보·고정·적용
      writing: 공간 / 조건부 행동 → 본문 고정 → 제목 → 조립
  → lifecycle: 원본 재확인·완료
  → storage: 기존 JSONB·영수증
```

그래프는 기존 위치에 남으며 작업 계약·수집·작성 모듈을 직접 사용한다. `runtime.py`나
모델 provider를 역참조하지 않는다. 공통 `orchestration`의 기존 공개 이름은 지연 import로
같은 객체를 제공하므로 동결된 어시스턴트 벤치마크 경로는 그대로다. 공통 실행기 import는
어시스턴트 그래프·런타임을 로딩하지 않는다.

## 저장 판독과 과거 실행

`storage.board.load_board/read_board → storage.card_receipt → contracts`는 현재 작성 정책·
모델·그래프·설정 없이 저장 v1/v2와 과거 슬롯/카드 영수증을 읽는다. 새 영수증 생성은
`storage.provenance`가 당시 정책과 채택 결과를 대조한다. 과거 bundle의 정책 비교와 bare
bundle 영수증 보완은 `storage.bundle`의 기존 동작으로 유지하며, 정책 비교 시점에는 과거
writer 정책을 사용한다. 이를 현재 보드의 순수 판독 보장과 혼동하지 않는다.

#508을 합칠 때 통합 이동의 계약·작업·정책·조립도 위 책임별 모듈에 배치했다.
평면 작성기나 import shim은 복원하지 않았다. 전체 맥락 제목의 현재 모델/프롬프트 검사와
#511 수집 진행·부분 적용 처리를 함께 유지한다. 기획 정책은 [활동 서술](diary-activity.md)을 따른다.

기본 `runtime.write_board`는 과거 작성기를 import하지 않는다. `legacy.board_slots`는
`write_legacy_slot_board`와 슬롯 결과 완료만 맡는다. 생성 서비스는 결과 종류에 따라 현재
카드 조립과 과거 슬롯 조립을 명시적으로 선택한다. `legacy`라는 이름이 과거 형식 생성
지원 중단을 뜻하지 않는다. 선택 조건은 기존 형식 협상을 유지한다.

## 이전 파일과 새 위치

아래 새 위치는 `backend/src/daengs_backend/services/walk_diary/` 기준이다. 저장소의
Python 호출자·평가 도구·테스트는 새 경로로 전환하며 평평한 옛 import shim은 남기지 않는다.
이후 병합할 브랜치나 외부 로컬 스크립트의 옛 import도 이 표로 전환한다. 공개 HTTP 계약은 같다.

| 이전 services 파일 | 새 위치 |
| --- | --- |
| `walk_diary_base_board.py` | `preparation/board.py` |
| `walk_diary_board_provenance.py` | `storage/provenance.py` |
| `walk_diary_board_slot_writing.py` | `legacy/board_slots.py` |
| `walk_diary_board_storage.py` | `storage/board.py` |
| `walk_diary_board_writing.py` | `legacy/board_bundle.py` |
| `walk_diary_card_assembly.py` | `writing/assembly.py` |
| `walk_diary_card_contracts.py` | `contracts.py` |
| `walk_diary_card_jobs.py` | `writing/jobs.py` |
| `walk_diary_card_policy.py` | `writing/policy.py` |
| `walk_diary_card_prompts.py` | `writing/prompts.py` |
| `walk_diary_card_provider.py` | `writing/provider.py` |
| `walk_diary_card_receipt.py` | `storage/card_receipt.py` |
| `walk_diary_card_writing.py` | `runtime.py` |
| `walk_diary_collection_application.py` | `collection/application.py` |
| `walk_diary_collection_progress.py` | `collection/progress.py` |
| `walk_diary_contract.py` | `guard.py` |
| `walk_diary_deadline.py` | `deadline.py` |
| `walk_diary_generation.py` | `lifecycle/generation.py` |
| `walk_diary_input.py` | `preparation/input.py` |
| `walk_diary_lifecycle.py` | `lifecycle/reservation.py` |
| `walk_diary_negotiation.py` | `lifecycle/negotiation.py` |
| `walk_diary_observations.py` | `preparation/observations.py` |
| `walk_diary_prepare.py` | `preparation/diary.py` |
| `walk_diary_publication.py` | `lifecycle/publication.py` |
| `walk_diary_route_policy.py` | `preparation/route_policy.py` |
| `walk_diary_scene_collection.py` | `collection/comparison.py` |
| `walk_diary_slot_writing.py` | `legacy/slots.py` |
| `walk_diary_slots.py` | `preview.py` |
| `walk_diary_snapshot.py` | `lifecycle/snapshot.py` |
| `walk_diary_space_collection.py` | `collection/service.py` |
| `walk_diary_space_snapshot.py` | `collection/snapshot.py` |
| `walk_diary_storage.py` | `storage/bundle.py` |
| `walk_diary_writing.py` | `legacy/bundle.py` |

`board_slot_writing.write_board`는 표의 과거 슬롯 모듈이 아니라 `runtime.write_board`로
이동했다. `card_writing`에서 재노출하던 계약·작업·정책은 각각 `contracts`, `writing.jobs`,
`writing.policy`, `writing.assembly`를 직접 참조한다.

## 검증과 다음 단계

`test_diary_service_package.py`는 준비·수집·작성·과거 실행의 의존 방향, 외부 서비스의
API 진입, 가벼운 import와 공통 오케스트레이션 이름의 호환을 확인한다. 기존
`test_diary_writing_boundaries.py`의 공개/저장 해시·판독 격리·그래프 역참조 금지도 유지한다.
테스트 경로명은 기능별 기존 위치를 사용하며 실제 실행 명령과 결과는 PR에 기록한다.

2단계의 기획 규칙 패키징은 [도메인 이동표와 의존 경계](diary-domain-package.md)를 따른다.
거리 기반 자동 장면 선정은 이후 정책 변경으로 다루며 이 이동 작업에서 목표 장수를 바꾸지 않는다.
