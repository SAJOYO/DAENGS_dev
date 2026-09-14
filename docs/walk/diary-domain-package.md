# 산책 일기 기획 규칙 패키지

2026-09-14, #514는 #512에 이은 2단계로 평면 `daengs_walk/diary_*.py` 36개를
`daengs_walk/diary/`로 묶는다. [서비스 패키지](diary-service-package.md)가 실행·수집·저장을
맡고, 이 패키지는 주어진 자료의 검증·장면 선정·공간 해석·슬롯 채택·출력 계약을 맡는다.
독립 서버나 새로운 오케스트레이터를 추가하지 않는다.

## 책임과 호출 방향

| 위치 | 책임 |
| --- | --- |
| `contracts/` | 입력·공개 bundle·카드 서술·슬롯 자료형·과거 슬롯 영수증·정규화된 주장 값 |
| `selection/` | 사용자 기록 보존, 관측 후보 채택, 실제 경로의 빈 구간 보충과 경계 장면 선정 |
| `route/` | 원본/버전과 경로 증거 연결 검증, 이동 관측과 경로 형태 해석 |
| `space/` | 공급된 주소·상권·공원·피복 등의 투영·정규화·공간 관계 해석. 외부 자료 조회는 하지 않음 |
| `slots/` | 해석한 자료를 이미 선정된 장면에 연결, 충돌·중복 제거와 파트별/전체 용량 적용 |
| `board/` | 보드 자료형·조립·공개 출력·장면별 작성 재료·미리보기 조립 |
| `legacy/` | 계속 지원하는 과거 bundle 작성의 순수 준비·검증 |
| `cli/` | 명시적 로컬 파일 입력을 받는 공간/경로 정규화와 공간 재생 도구 |

```text
backend 준비 → selection.board.prepare_base_board → board.assembly.assemble_base_board
backend 수집 → 고정된 배경 자료
기본 카드 작성 → slots.service.prepare_board_slots
                 ├─ route / space: 주어진 증거 해석
                 └─ slots.admission: 주장 해소·용량 적용
              → board.scene_input / board.writing_context → backend 작성 작업
저장 판독 → contracts / board.output
명시적 미리보기 → board.preview.prepare_slot_preview → 선정·조립·슬롯 적용
```

모든 `__init__.py`는 책임 설명만 둔다. 도메인에서 backend·DB·HTTP·모델 SDK·그래프를
import하지 않는다. `cli`만 명시적인 파일 입출력을 가지며 나머지 도메인은 이를 참조하지 않는다.
`spatial_diary.py`와 산책 측정 코어는 별도 책임을 유지한다.

## 단순 이동 외에 끊은 의존성

- `contracts.slots`의 슬롯 정책·자료·스냅샷은 슬롯 실행기와 별개다. 과거 슬롯 영수증도
  이 자료형만 읽는다. 보드 선정·공간 해석·슬롯 실행 없이 검증과 직렬화가 가능하다.
  경로 옵션 자료형은 `route.patterns.RoutePatternBindingPolicy`를 그대로 사용한다.
- `contracts.canonical.normalize`는 공간 정책과 슬롯 어댑터가 공유한다. 공간을 해석하기
  위해 슬롯 충돌 해결기를 역참조하던 의존성이 사라진다.
- `route.binding.verified_route`는 선정과 경로 슬롯 적용이 공유한다. 슬롯이 장면 선정의
  비공개 `_route`를 부르지 않는다.
- `slots.admission.admit`는 이미 적용 가능한 자료에 채택 규칙만 적용한다. 보드 선정이나
  미리보기 조립을 import하지 않는다.
- 미리보기의 선정→조립→슬롯 적용은 `board.preview`가 맡는다. 기본 작성에서 사용하는
  `slots.service.prepare_board_slots`는 주어진 보드에만 적용한다.

`board.models`의 준비된 보드는 기존 `selection.stamps.PreparedDiary`와 `StampPolicy`를
포함한다. 따라서 모든 보드 자료형이 선정 모듈과 독립이라고 주장하지 않는다. 독립 판독을
보장하는 계약과, 준비·조립을 표현하는 내부 보드 계약을 구분한다.

## 이전 파일과 새 위치

다음 표의 새 위치는 `backend/src/daengs_walk/diary/` 기준이다. 저장소의 Python 호출자와
도구는 모두 새 경로를 사용하며 옛 평면 shim은 남기지 않는다. HTTP 경로와 JSON 형식은 같다.

| 이전 daengs_walk 파일 | 새 위치 |
| --- | --- |
| `diary_area_background.py` | `space/area.py` |
| `diary_background.py` | `space/projection.py` |
| `diary_board.py` | `board/models.py` |
| `diary_board_assembly.py` | `board/assembly.py` |
| `diary_board_output.py` | `board/output.py` |
| `diary_board_receipt.py` | `contracts/slot_receipt.py` |
| `diary_board_selection.py` | `selection/board.py` |
| `diary_card_narrative.py` | `contracts/narrative.py` |
| `diary_input.py` | `contracts/input.py` |
| `diary_kakao_background.py` | `space/kakao.py` |
| `diary_observations.py` | `route/observations.py` |
| `diary_output.py` | `contracts/output.py` |
| `diary_public_background.py` | `space/public.py` |
| `diary_route_geometry.py` | `route/geometry.py` |
| `diary_route_normalize.py` | `cli/route_normalize.py` |
| `diary_route_patterns.py` | `route/patterns.py` |
| `diary_route_slots.py` | `slots/route.py` |
| `diary_scene_backgrounds.py` | `board/backgrounds.py` |
| `diary_scene_input.py` | `board/scene_input.py` |
| `diary_slot_claims.py` | `slots/claims.py` |
| `diary_slot_sources.py` | `slots/sources.py` |
| `diary_slot_spatial.py` | `slots/spatial.py` |
| `diary_slots.py` | `slots/service.py` |
| `diary_space_cases.py` | `space/cases.py` |
| `diary_space_coverage.py` | `space/coverage.py` |
| `diary_space_geometry.py` | `space/geometry.py` |
| `diary_space_materials.py` | `space/materials.py` |
| `diary_space_memory.py` | `slots/memory.py` |
| `diary_space_normalize.py` | `cli/space_normalize.py` |
| `diary_space_policy.py` | `space/policy.py` |
| `diary_space_replay.py` | `cli/space_replay.py` |
| `diary_space_slots.py` | `slots/space.py` |
| `diary_stamps.py` | `selection/stamps.py` |
| `diary_temperature.py` | `slots/temperature.py` |
| `diary_writing.py` | `legacy/writing.py` |
| `diary_writing_context.py` | `board/writing_context.py` |

표의 `slots/service.py`에서 자료형은 `contracts/slots.py`, `SlotPreview`와
`prepare_slot_preview`는 `board/preview.py`, `admit`는 `slots/admission.py`로 분리했다.
`slot_claims.normalize`는 `contracts/canonical.py`, `board_selection._route`는
`route/binding.py`의 `verified_route`로 이동했다. 전체 함수를 원래 모듈에서 재노출하지 않는다.

CLI 호출은 다음과 같다. 입력/출력 파일 형식과 옵션은 유지한다.

```powershell
uv run python -m daengs_walk.diary.cli.space_normalize --help
uv run python -m daengs_walk.diary.cli.space_replay --help
uv run python -m daengs_walk.diary.cli.route_normalize --help
```

## 정책 변경과 검증

이번 작업은 현재 정책을 보존한다. 사용자 기록 보존, 중간 장면 목표 5개, 경로 간격 100m,
시작/종료 장면 추가, 슬롯 예산, 프롬프트·정책 버전·공개/저장 해시를 바꾸지 않는다.
거리와 서로 다른 공간을 기준으로 자동 장면을 선정하는 정책은 이후 `selection/`에서
변경할 작업이다. 한곳의 체류 시간만으로 여러 장면을 만들지 않겠다는 제품 방향과 이번
패키징 완료를 혼동하지 않는다.

`test_diary_domain_package.py`가 책임별 의존 방향과 새 인터프리터에서의 계약 판독을
확인한다. 기존 선정·공간·경로·슬롯·API·DB 테스트 및 고정된 공개/저장 golden을 함께
사용한다. 실행 범위·명령·결과와 실환경 미검증 범위는 PR 본문에 기록한다.
