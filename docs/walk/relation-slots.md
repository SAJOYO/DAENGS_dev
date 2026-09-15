# 관계 모듈과 결과 칸 — v6

## 전체 장면 비교 계약 — 1단계 추가

`backend/src/daengs_walk/diary/relational/scene_comparison_contracts.py`에
`scene-comparison-v1` 계약을 추가했다. 기존 v6 생성·저장 형식과 registry는 그대로다.
2단계에서 스냅샷 조립, 4단계에서 실제 비교 작성, 5단계에서 v7 발행 카드와 파일 저장·조회 경로를 연결했다. 운영 DB·APP의 기본 경로 전환은 포함하지 않는다.

| 계약 | 보존하는 것 |
| --- | --- |
| SceneSnapshot / SceneFact | 기록 시각·위치, 도로·피복·주변 대상·지역 자료, 출처·대상 키·자료 시점·범위·수집 상태 |
| SpatialCorrespondence | 비교 축·결과·양쪽 근거, 동일 대상 거리 차이 또는 영역 구성 비교, 공통 범위 설명 |
| SpatialComparisonSlots | background / proximity / area_context의 고정 결과 칸. 기존 이동·행동 슬롯을 대체하지 않음 |
| SceneConnection | 기록 사이 시간, GPS 연결 상태와 근거 참조. 시간 간격만으로 연결을 만들지 않음 |
| SpaceComparisonInput | 이전·현재 전체 스냅샷과 관계 칸·시간 연결. 초기 장면에는 이전 장면과 관계가 없음 |
| SpaceComparisonAnswer | focus / relation_ids / evidence_ids / text. 초점은 짧은 진단용 의미이며 품질 보증이 아님 |
| SceneCardHeader | 표시용 동·날씨. 공간 작성 입력 밖에서 관리 |

규칙 계산 결과에는 문장 예문·재료 우선순위·필수 나열 순서가 없다.
동일 공원은 제공기관을 포함한 subject_key로 대응하며 이름 일치로 확정하지 않는다.
서로 다른 위치의 비교를 동일 장소의 시간 변화로 취급하는 축은 이 버전에서 지원하지 않는다.
한쪽 자료만 있을 때는 거리 차이를 붙일 수 없다. 값 비교와 거리 계산 자체는 3단계 모듈이 담당한다.

source_refs는 내부 추적용이고 작성기 인용 대상이 아니다. citation_ids는 스냅샷의 fact ID와
경로 근거 ID에서 도출한다. relation_ids는 별도로 반환하고 validate_against로 두 허용 집합을 검사한다.
4단계 작성 입력은 내부 추적 필드를 분리하고 요청별 짧은 인용 ID를 전달한다. 응답은 원본 ID로 복원한다.
value는 기존 정규화 자료를 담는 JSON이며 내용의 의미 검증이나 자유 텍스트 검열을 수행하지 않는다.

원문 메모·사진과 현재 행동은 새 공간 계약에 추가하지 않는다.

계약 검사: `uv run --no-sync pytest -q tests/walk/diary/test_scene_comparison_contracts.py`.
실제 LLM 품질·생성 경로 통합·DB 저장 검증을 대신하지 않는다.

## 전체 장면 스냅샷 조립 — 2단계 연결

준비 서비스 `preparation/relational.py`가 기존 적합성 검사 후, 용량 제한 전의
`eligible_evidence`를 `preparation/scene_snapshot.py`에 전달한다. 결과는 준비 프레임의
`scene_snapshot`과 별도의 `card_header`에 저장된다. 4단계 새 작성 계획은 이 스냅샷을 사용하며
동·날씨를 생성 입력에서 제외한다. APP의 새 표시 계약 연결은 별도 단계다.

`preparation/scene_facts.py`는 기존 `model_materials`와 `LAND_WORDS`를 재사용한다.
피복은 한국어 표현과 원분류·레이어·영상 기준일, 공원은 이름·종류·출처별 대상 ID·등록점·거리·
면적·기준일·카탈로그 완전성을 보존한다. 상권은 조회 원·한국어 구성·업종별 수·등록점 분포·
기준월·계산 정책을 보존한다. 동일한 구성 문구라도 조회 원이 다르면 coverage_key가 다르다.
원본 폴리곤과 전체 업소 ID 목록은 eligible_evidence에 남기고 새 스냅샷에는 참조한다.

도로는 해당 좌표의 SGIS addr_type=10 성공 응답에서 road_nm만 받는다. 건물번호나
전체 주소로 대체하지 않으며, 양재천로3길처럼 도로명 자체의 숫자는 유지한다.
서로 충돌하는 도로명은 하나를 임의 선택하지 않는다. 도로명 일치로 같은 도로 객체를 확정하지 않는다.

조회 상태를 모르는 보관 자료에는 `unknown`을 추가했다. 자료 누락을 empty나 not_requested로
바꾸지 않는다. 완전한 정상 상권 조회에서 no_registered_shops_in_footprint만 반환되고 자료가 없으면
empty로 남긴다. 오류·불완전 조회 audit가 함께 있으면 partial로 유지한다. 그 밖의
성공 응답도 오류 audit나 적합 자료 누락이 있으면 partial로 남기며
collection_reasons에 이유를 보존한다. SavedBackground의 unavailable은 실패·미지원 등을
포괄하므로 세부 원인 없이 failed로 단정하지 않는다. 피복·공원 등의 기준일을 기록 시각의 관측으로
바꾸지 않고, 출처가 둘 이상이면 각 원본 ID와 버전을 보존한다.

검사 명령: `uv run --no-sync pytest -q tests/walk/diary/test_scene_snapshot_assembly.py tests/walk/diary/test_scene_comparison_contracts.py tests/walk/diary/test_relational_planning.py::test_working_skeleton_failure_originals_and_saved_read`

모의 공공데이터 응답을 실제 정규화→적합성→준비 경로에 넣어 용량 0에서도 네 공간 분야가
보존되는지 확인한다. 2단계 코드 검사 18개 통과. 새 공공 API·LLM 호출은 없었다.

## 전체 장면 관계 계산 — 3단계 연결

동일 registry의 `COMPARISON_MODULES`가 새 스냅샷용 세 슬롯을 관리한다.
`collect_spatial_comparisons`는 같은 산책의 시간순 장면을 검사한 뒤 다음 모듈을 실행한다.

| 새 슬롯 | 모듈 | 계산 |
| --- | --- | --- |
| background | point_context | 도로명·원피복 분류의 동일/차이. 조회 시각·참조 ID 교체는 값 차이로 세지 않음 |
| proximity | proximity.evaluate_snapshot | 제공기관을 포함한 대상 ID 대응. 등록 좌표가 같고 거리값이 유효할 때 현재−이전 거리 계산 |
| area_context | area_context | 조회 원 동일 여부와 구성·통계 차이. 통계는 자료 계열·기준월·반경·분류 정책이 맞을 때 비교 |

관계는 문장이나 중요도 순위를 담지 않는다. comparison_basis에는 비교 속성·비교 가능 여부·
달라진 통계 필드와 이유를 보존한다. 동·날씨·행동은 이 목록에 들어가지 않는다.
상권의 한국어 구성 문구가 같아도 등록 수 등 비교 가능한 세부 값이 다르면 changed_fields에 남는다.

같은 이름의 다른 공원은 대응하지 않는다. 동일 대상 ID라도 등록점 좌표가 바뀌거나 거리 근거가
충돌하면 incomparable로 두고 거리 차이를 계산하지 않는다. 한쪽 근거만 있으면
only_one_snapshot_has_evidence로 두며 출현·소멸·이탈로 표현할 관계는 만들지 않는다.
피복 자료의 레이어가 다르거나 값이 충돌하면 incomparable로 보존한다. 같은 레이어의 두 기록 위치는
영상 기준일이 달라도 원피복 분류를 비교하며 source_dates와 source_dates_differ를 함께 남긴다.
이는 위치별 지도 분류 비교이며, 같은 장소의 시간 변화나 산책 당시 현장 상태를 확정하지 않는다.
서로 다른 상권 조회 영역은 범위 차이를 남기되 기준이 맞지 않으면 composition_values_equal은 null이다.
한 장면에 여러 상권 후보가 있으면 현재 버전에서는 임의로 짝짓거나 모든 조합을 만들지 않고
ambiguous_area_pairing으로 보류한다. 이는 지원 범위이며 범용 영역 매칭이 구현됐다는 뜻이 아니다.

준비 프레임에 `spatial_comparison_slots`를 고정한다. 준비 묶음의 scene_backgrounds에 실제
배경 원본을 중복 ID 없이 보존하고, 프레임의 scene_background_ids로 연결한다.
작성 전 validate_prepared는 eligible_evidence·배경 원본·도로 응답에서 스냅샷과 카드 표시를
다시 조립해 대조한 다음 관계를 재계산한다. 따라서 원자료는 그대로 두고 스냅샷·관계·전체 해시를
함께 바꾼 경우도 거절한다. 이는 묶음 내부의 일관성 검사이며 원자료 묶음 전체의 외부 진위 인증은 아니다.
새 기능 마커는 scene_comparison_version이며 필수 파생 필드나 원본 목록이 빠지면 거절한다.
마커 없이 새 스냅샷만 가진 2~3단계 초기 실험 준비본은 다시 준비해야 한다. 새 스냅샷을 사용하지 않는
기존 v6의 relation_slots와
작성 계획은 그대로 읽는다. legacy proximity.evaluate의 미구현 표시는 기존 프레임용이며,
새 스냅샷의 proximity.evaluate_snapshot과 구별한다. 실제 LLM에 새 관계를 전달하는 경로는 아래 4단계에 연결했다.

검사: `uv run --no-sync pytest -q tests/walk/diary/test_spatial_correspondences.py tests/walk/diary/test_scene_snapshot_assembly.py tests/walk/diary/test_scene_comparison_contracts.py tests/walk/diary/test_relation_slots.py tests/walk/diary/test_relational_planning.py::test_working_skeleton_failure_originals_and_saved_read`

3단계 최초 검사 36개 통과 후 리뷰에서 원자료 연결 검증 누락·정상 빈 결과 오분류·영상 기준일의
과도한 비교 제한을 재현하고 수정했다. 회귀를 추가한 같은 범위의 코드 검사 40개 통과.
3단계에서는 실제 LLM 호출과 문장 품질 확인을 수행하지 않았다.

Python 3.12와 저장소 uv.lock을 사용한다. v5 외부 전달본은 Python 3.13.5에서 검증됐으며, 이번 통합은 저장소 Python 3.12 환경에서 진행했다.

## 전체 장면 비교 작성 — 4단계 연결

`comparison_writing.py`가 전체 이전·현재 스냅샷, 세 관계 슬롯, 시간 간격과 GPS 연결 근거를
`SpaceComparisonInput`으로 조립한다. planning은 이 입력을 실제 공간 작업으로 만들고,
`writing/relational.py`가 전용 입력·프롬프트·응답 스키마를 모델에 전달한다.
한 호출에서 focus / relation_ids / evidence_ids / text를 반환한다. 관계마다 문장 예시나
재료 우선순위를 지정하지 않으며 모든 변화의 나열도 요구하지 않는다.

모델에는 요청별 e1… / r1…을 전달한다. 스냅샷·관계 끝점·허용 목록·응답 스키마를 동일하게
변환하며 응답은 원본 ID로 복원한다. 내부 source_refs와 조회 시각은 작성 입력에서 제외하고
원래 준비본에 보존한다. 인용 대응표도 실행 기록에 남긴다. 의미 검수에는 모델이 받은 짧은 ID와
동일한 후보를 전달한다. ID 검사는 문장의 의미 정확성을 보증하지 않는다.

행동 작성은 현재 핀·현재 공간·핀 시각의 이동 맥락만 유지한다. 이전 스냅샷·관계는 넣지 않는다.
도로 참조도 새 스냅샷에서 정규화된 도로명만 사용한다.

새 입력은 `comparison_sequence.py`를 통해 실제 short_memory 진입점에 연결된다.
앞 소개가 실패하고 다음 동일 배경의 작업이 생략됐다면 소개를 복구한다. 성공 여부와 현재
근거의 의미를 기준으로 처리하며 이전 생성문을 사실로 주입하지 않는다. 4단계에서 writer trace에만
보존했던 초점과 선택 ID는 아래 5단계에서 발행 카드에도 연결했다.

코드 검사: 작성 연결·스냅샷·계약·기존 숏메모리·저장 회귀의 지정 범위 28개 통과 후,
공원 거리 변화와 짧은 ID 스키마 일치를 포함한 최종 작성 검사 5개 통과(앞 검사와 중복).
전체 저장소 검사는 실행하지 않았다.

실제 실험 14는 보관된 공공 공간 자료와 SGIS 도로명 응답을 사용했다. 새 공공 API 호출이나
TMAP, GPS 연결 근거 추가는 없었다. gemini-3.1-flash-lite 공간 3회·행동 1회 이후,
마지막 공간의 ID 처리 수정 확인 2회로 총 6회 호출했다. 응답 후 최소 10초 간격,
자동 재시도 없이 실행했다. 제목·의미 검수는 이 제한된 실험에서 끄고 unverified로 남겼다.

최초 마지막 공간은 긴 ID 출력 중 JSON이 잘렸고, 짧은 ID 도입 직후에는 스키마의 긴 ID 목록이
남아 불일치했다. 입력·스키마·복원 목록을 통일한 최종 호출은 정상 파싱·복원됐다.
실패 원문과 최종 원문을 모두 보존했다.

서술 품질은 미해결이다. GPS 연결이 없는 입력에서 “강남대로를 지나 양재천로3길로 들어서니”,
피복 분류만으로 “숲이 우거진”, 제공되지 않은 “차분한 분위기”가 출력됐다.
다른 장면에서도 넓은 조회 영역의 흩어진 상가 등록점을 현재 위치의 모인 상가처럼 표현했다.
관계 입력과 초점 선택의 실제 실행은 확인했지만 공간 범위·이동·경험의 의미 정확성 통과로
판정하지 않는다. 이 결과를 근거로 금지 문구를 추가하거나 다른 모델로 교체하지 않았다.

## 발행 카드·저장·전달 기억 — 5단계 연결

`publication.py`의 `ComparisonPublication`이 새 카드의 `comparison` 한곳에 다음을 고정한다.
전체 이전·현재 스냅샷, 세 관계 슬롯, 경과 시간, GPS 연결 근거, 표시용 동·날씨,
실제로 채택된 초점·관계 ID·근거 ID·본문, 의미 검수 상태, 전달 기억의 갱신 전·후 값이다.
작성하지 않은 카드나 실패 카드도 공간 맥락과 헤더를 보존한다. selection은 null이고
semantic_status는 not_published다. 카드 하나만 읽어도 비교 대상과 범위를 찾을 수 있다.

새 발행본은 relational-diary-skeleton-v7이며 준비 입력의 기존 v6 버전과 구분한다.
`relation_selection.space`에는 실제 선택된 새 관계 ID가 들어가고, 해당 관계의 정의는
`comparison.context.relation_slots`에 있다. 카드의 기존 relation_slots는 기존 이동·행동 등
v6 관계 자료를 보존하는 칸이다. 두 관계 목록을 같은 계약으로 해석하지 않는다.

`delivery.py`가 실제 전달 기억을 한곳에서 계산한다. 활성 배경 소개와 최근 채택된 공간 결과
최대 두 건을 보존하며, 각 결과에는 장면·작업·초점·선택 ID·의미 검수 상태가 남는다.
본문을 새 사실로 사용하지 않고, 선택하지 않은 모든 재료를 이미 전달했다고 표시하지 않는다.
새 배경의 작성 실패 또는 현재 공간 자료 누락이면 이전 배경의 활성 소개를 해제한다.
과거 성공 이력은 최대 두 건으로 남긴다. 같은 배경 소개 실패 후 다음 장면에서 복구하는 동작은 유지한다.
지점 식별값 변경만으로 소개를 반복하지 않되, 조회 영역의 범위 차이는 구별한다.

조립기는 returned 결과만 본문·selection·전달 기억에 채택한다. 실패 후보·검수 실패 원문은
writing 로그에만 남긴다. 검수를 끈 명시적 실험은 unverified이며 검수 통과로 승격하지 않는다.
메모·사진 원문은 기존 originals 경로 그대로다.

`save_skeleton`은 고정 준비본과 채택 결과에서 발행본을 대조한 뒤 새 파일에 저장한다.
`read_skeleton`은 저장된 발행본만 읽고 입력·선택·본문·전달 기억의 내부 일관성을 확인한다.
현재 API·준비 서비스·모델 호출 없이 조회하며 기존 파일을 덮어쓰지 않는다.
v2~v6 읽기는 새 필드를 요구하거나 생성하지 않는다. 해시와 내부 대조는 외부 진위 인증이 아니다.
의존 입력의 전체 스냅샷 revision과 카드 비교 context_revision을 보존한다. 운영 캐시나 DB 테이블은
추가하지 않았다. 현재 연결 대상은 기존 파일 기반 스켈레톤 저장 어댑터다.

검사 범위: test_comparison_publication.py, test_comparison_writer.py, test_short_memory.py,
test_relational_planning.py::test_working_skeleton_failure_originals_and_saved_read.
지정 범위 코드 검사 16개 통과. 모델 응답을 주입한 코드 검사이며 새 LLM 호출은 없다.
4단계 실제 응답의 실패 포함본과 마지막 장면 수정 확인본을 각각 v7로 조립·저장·재조회하는
재생 확인도 완료했다. 두 묶음 각각 카드 3개의 저장 전후 발행본 전체가 같았다.
기존 실패 카드의 공간 본문은 비어 있고 실패 로그는 유지됐다. 앞 단계의 의미 오류를 수정하거나
품질 통과로 바꾸는 작업은 아니다. 덮어쓰기 방지 검사를 실제 저장 함수 호출로 보강한 뒤
해당 저장·조회 검사 1개도 다시 통과했다.

### 5단계 리뷰 수정

리뷰에서 세 경로를 재현한 뒤 수정했다.

- 이전 장소만 인용한 채택 결과가 현재 배경을 소개한 것으로 처리되던 오류:
  최근 채택 이력에는 남기되 현재 배경의 활성 소개로 올리지 않는다. 검수 없는 실험에서는
  현재 사실 인용 또는 선택 관계의 현재 끝점이 있어야 소개로 센다. 의미 검수 경로에서는
  검수기의 used_evidence_ids를 원본 ID로 복원해 현재 근거를 실제 사용했는지 판단한다.
  이는 구조적 전달 근거이며 인용으로 자연어의 의미를 증명한다는 뜻은 아니다.
  첫 소개 실패 → 이전 장소만 쓴 복구 응답 → 다음 장면의 현재 소개 복구를 검사했다.
- 역순 작성 계획이 저장까지 통과하던 오류: 새 비교 계획의 시각은 호출 전에 엄격히
  증가해야 한다. 저장 발행 카드도 같은 산책의 시간순이어야 한다. 임의 정렬로 입력을 바꾸지 않는다.
- 저장 도중 실패 시 깨진 최종 파일이 남던 오류: 같은 디렉터리의 임시 파일에 JSON을 쓰고
  flush/fsync 후 hard link로 최종 이름을 원자적으로 확정한다. 기존 최종 파일은 덮어쓰지 않는다.
  직렬화·확정 실패 때 임시 파일을 정리하고, 같은 경로 재저장이 가능하다.
  하드 링크를 지원하지 않는 파일시스템에서는 저장 오류를 반환하며 불완전한 최종 파일로 대체하지 않는다.

위 지정 범위에 회귀를 추가한 코드 검사 22개 통과. 새 LLM·운영 DB 호출은 없다.

## 기존 v6 관계 목록

`backend/src/daengs_walk/diary/relational/relations/registry.py`가 유일한 관계 모듈 목록이다. `contracts.py`의 `RelationSlots`는 모든 장면에 다음 여섯 칸을 요구한다. 결과가 없는 칸도 생략하지 않는다.

| 칸 | 현재 구현 | 입력과 범위 |
| --- | --- | --- |
| background | 연결됨 | 기존 피복·행정 위치·도로 주소 비교와 비교 축 |
| proximity | 미구현 표시 | 동일 대상 식별자·거리 관측을 준비 단계에서 연결해야 함 |
| continuity | 미구현 표시 | 중간 경로를 포함하는 공간 적용 범위가 필요함. 양 끝의 동일 값으로 지속을 만들지 않음 |
| route_revisit | 되짚기 연결됨 | 기존 MovementCatalog의 retrace 근거. 일반적인 경로 재연결 탐지는 추가하지 않음 |
| movement | 연결됨 | 기존 구간의 속도·방향 등 기기 관측. 되짚기는 별도 칸으로 분리 |
| event_context | 연결됨 | 현재 핀·현재 공간·핀에 연결된 이동 맥락. 이전 사건을 유지하지 않음 |

상태는 confirmed, no_change, insufficient_evidence, not_applicable, not_implemented, failed를 구분한다. no_change는 해당 분석에서 표현할 관계가 없다는 뜻이며 현장의 무변화나 정상 속도를 보증하지 않는다. 예기치 않은 계산 오류는 현재 실행을 실패시키며 빈 결과로 숨기지 않는다.

## 연결 경로

준비 서비스가 기존 이동 카탈로그에서 기록 사이의 근거를 추출하고 frame.relation_observations에 고정한다. 각 항목에 원래 source_claim과 source_ids 및 잘린 시간 범위를 보존한다. 공백·서로 다른 관측 블록은 근거 부족이다.

planning.make_plan → collect_relations → relation_slots → 기존 공간/행동 작업 및 기기 관측 조립 순으로 연결된다. 행동 작업은 event_context의 현재 핀을 사용한다. 공간 작성에는 배경 관계가 들어가며 되짚기·속도는 기존 기기 관측 출력에 연결된다. 이 단계에서 이동 관측을 공간 LLM의 새 필수 서술로 넣지 않는다.

relation_selection은 어떤 관계를 공간·행동·기기 관측에 연결했는지 별도로 남긴다. 관계 전체와 서술 선택의 저장 칸을 분리했으며 배경 관계의 기존 선택 정책은 유지한다. 이후 중심·보조 선택 정책은 planning에서만 바꾼다.

작성 전 관계 칸을 프레임으로 재계산해 확인하고, 발행 카드에도 relation_slots와 relation_selection을 보존한다. 저장 형식은 v6이며 v2~v5 발행본은 변경 없이 읽는다. 과거 준비 입력은 최신 작성 입력으로 자동 취급하지 않는다.

검수를 끈 명시적 실험도 성공한 소개는 반복 억제에 사용한다. 이것을 의미 검수 통과로 표기하지는 않는다. v5의 검수 기본 정책 자체는 이번 모듈화에서 변경하지 않았다.

## 확인 범위

관계별 분배·근거 보존·현재 핀 격리·관계 칸 변조 거절·저장 재조회 및 v5 회귀를 Python 3.12에서 검사한다. 실제 LLM 호출과 운영 DB·APP 배포는 별도이며 이 문서는 서술 품질 성공을 주장하지 않는다.
