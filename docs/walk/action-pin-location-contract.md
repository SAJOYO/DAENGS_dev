# 행동 즉시 기록과 핀 위치 추정 계약 v1

상태: 1단위 명세 초안. API·DB·APP 런타임은 아직 구현하지 않았다.
작성/소스 확인: 2026-09-09, DEV `b041ce451b4e61b2b050e78991601ab0de9a65c1`, APP `7cec8a6`.
카드: [DEV #354](https://github.com/SAJOYO/DAENGS_dev/pull/354), [APP #227](https://github.com/SAJOYO/DAENGS_APP/pull/227).
이 문서가 공통 계약 원본이다. APP은 원문을 복제하지 않고 연결 지침과 소비 경계를 관리한다.

## 1. 사용자 동작과 범위

산책 중 킁킁·배설·짖기를 누르면 액션 원본을 즉시 로컬에 보존한다. GPS 불안정으로 버튼을
비활성화하거나 액션을 버리지 않는다. 좌표 근거가 있으면 최선의 좌표에 핀을 표시한다.
정상 GPS는 기존 측정 위치를 쓰고, 불안정한 경우에만 액션 시점 주변의 관측을 추가 분석한다.
fallback은 정지 때만 허용하는 구조가 아니며 이동 중에도 작동한다.

불안정한 액션은 누르기 전 관측으로 잠정 위치를 만들고, 짧은 후속 관측으로 **누른 시각의 위치**를
재추정한다. 후속 수신이 없거나 계산이 실패하면 기존 최선의 좌표로 확정한다. 좋은 좌표가 될
때까지 무한 대기하거나 사용자가 다시 누르도록 요구하지 않는다.

해당 산책에 유효한 좌표 근거가 처음부터 전혀 없을 때만 좌표 없는 액션을 남긴다. 시청·지도
중심·다른 산책의 좌표·(0, 0) 같은 대체 좌표를 발명하지 않는다. 이는 입력 실패가 아니다.
DB 쓰기 실패는 별도 오류이며 저장 성공으로 안내하지 않는다.

이 단위에서 정하는 것은 계약과 수명이다. 추정 알고리즘 선택, 시간/거리 문턱, 실기기 정확도,
UI 구현, API/SQL 구현은 후속 2~6단위다. '산책당 약 열 번'은 사용 빈도 가정이며 저장 상한이 아니다.

## 2. 현재 코드에서 바뀌어야 하는 경계

- [현재 EntryContent](../../backend/src/daengs_backend/schemas/walk_entry.py)는 behavior의 location을 필수로 검증한다.
- [현재 서비스](../../backend/src/daengs_backend/services/walk_entry.py)는 kind/recorded_at/location 변경을 금지한다.
- [현재 계약](entries-and-record-profile.md)의 v1은 그대로 유지하고 새 표현은 명시적으로 버전 구분한다.
- APP `WalkEntry.validate`도 위치 없는 행동을 거부한다. `latestMomentFix`는 경로 필터 결과가 아니라
  정확도 기준을 통과한 공급자 표본이다. 이 값을 추정 좌표로 덮어쓰지 않는다.
- DEV 주 DB는 `db/init` + `db/migrations` 소유다. JSON payload가 있다고 물리 스키마 변경이
  없다고 단정하지 않는다. 위치 revision/멱등성/삭제 수명의 실제 SQL은 3단위에서 정의한다.

## 3. 원본과 위치 결과

한 액션 ID 아래에 `content`와 `pin`을 둔다. 별도 액션을 추가해 위치를 정정하지 않는다.

| 값 | 의미 / 변경 규칙 |
|---|---|
| `id`, `walk_id` | UUID. 기존 기록 ID와 산책 소유권 재사용 |
| `content.vocabulary_version` | `walk-behavior-v1`; 행동 어휘를 이번 작업에서 바꾸지 않음 |
| `content.kind` | behavior 또는 note, 생성 뒤 불변 |
| `content.recorded_at` | 사용자가 누른 UTC 시각, 생성 뒤 불변. 서버 산책 시작~끝 범위 안 |
| `content.behavior_code`, `pet_id`, `note` | 기존 허용 정정 규칙 유지. 새 문서가 위치 수동 편집을 추가하지 않음 |
| `content.location` | 누른 시점에 선택·보존한 **실제 공급자 표본 한 건** 또는 null. captured_at/accuracy_m의 원래 의미 유지. 추정 좌표나 후속 좌표로 덮어쓰지 않음 |
| `pin` | 지도 표시용 위치 결과. 자체 상태/버전/근거를 가짐. content.location과 좌표가 다를 수 있음 |
| `revision` | content/pin/삭제 등 사용자에게 보이는 어느 변경이든 증가하는 전체 서버 revision |
| `pin_revision` | pin 변경에만 증가. 생성 시 pin이 있으면 1, 없는 note는 0 |
| `mutation_id` | 마지막 성공 변경의 UUID. 모든 쓰기 요청의 멱등성 식별자 |

`observed`는 위치 공급자로부터 관측했다는 뜻이지 실제 정답 좌표/현장 인증이라는 뜻이 아니다.
첫 원본 위치가 부정확해도 실제 관측이면 보존할 수 있다. 핀에는 다른 추정 좌표를 쓸 수 있다.
note는 기존 위치 선택형 동작을 유지한다. 이번 자동 추정기는 behavior만 대상으로 한다.
behavior는 세 코드 중 하나이며 note=null, note는 비어 있지 않은 2000자 이하 본문과
behavior_code=null/pet_id=null을 요구한다. 위치 유무를 제외한 기존 내용 검증을 완화하지 않는다.

### pin 필드

| 필드 | 허용 값 / 불변식 |
|---|---|
| `resolution_id` | UUID. 해당 액션의 한 번의 위치 확정 작업 ID. 재시작/재전송에도 유지 |
| `state` | `provisional`, `resolved`, `unlocated` |
| `method` | `observed`, `estimated`, `last_known`, `none` |
| `target_at` | 항상 content.recorded_at과 같은 순간. 후속 관측 시각으로 변경 금지 |
| `point` | `{lat, lng}` 또는 null. 유한수, -90≤lat≤90, -180≤lng≤180 |
| `computed_at` | 이 결과를 계산한 UTC 시각. target_at·관측 시각과 구분 |
| `policy_version` | 정상 위치 선택/추정 파라미터를 식별하는 불변 버전 문자열 |
| `algorithm_version` | 계산 구현을 식별하는 불변 버전 문자열 |
| `resolve_by` | 해당 작업이 후속 관측을 기다릴 마지막 UTC 시각. 생성 뒤 연장 금지 |
| `source_refs` | 같은 산책의 원본 GPS 참조 목록. 각 항목은 client_seq/chain_index/at. 중복 금지 |
| `uncertainty_m` | 추정 불확실성 반경의 유한 양수 또는 null. 관측 accuracy_m를 복사해 추정의 정확도로 주장하지 않음 |
| `uncertainty_basis` | `provider_accuracy`, `model_bound`, `unknown`. unknown이면 uncertainty_m=null; 나머지는 양수 필수 |
| `reason` | `direct_fix`, `awaiting_observations`, `refined`, `deadline`, `session_ended`, `recovered`, `estimator_failed`, `no_evidence` |

모든 시각은 timezone이 있는 ISO 8601이며 wire 예시는 UTC Z다. computed_at은 target_at 이후다.
resolve_by는 target_at 이상인 유한 시각이다. 즉시 확정은 둘이 같을 수 있다. 업로드 지연으로 computed_at/서버 수신 시각이
resolve_by보다 늦어져도 거부하지 않는다. 대신 **수집 근거의 상한**은 resolve_by다.

pin의 모든 필드는 필수다(null 허용은 표에 명시). 알려지지 않은 필드는 거부한다.
source_refs는 at/client_seq 오름차순의 유일한 원본 참조이며 client_seq/chain_index는 0 이상의 정수다.
pin에 적용한 정책/알고리즘은 resolution 생애 동안 변경하지 않는다. 활성화/호환 정책은
서버가 허용한 버전 목록으로 관리하며, 과거에 허용해 생성한 작업의 종료·재전송은 버전 변경으로 막지 않는다.

uncertainty_m는 정확도 보증이 아니다. model_bound의 계산 의미/보수성은 policy_version 문서에
정의해야 하며, 검증 전에는 unknown을 사용할 수 있다. 소비자는 unknown을 0m 오차로 해석하지 않는다.

### 상태별 제약

| 상태 | point / method | 의미 |
|---|---|---|
| provisional | 좌표가 있으면 estimated 또는 last_known; 없으면 none | 원본은 저장됨. 위치 작업만 진행 중 |
| resolved | 좌표 필수; observed/estimated/last_known | 이번 추정 작업 종료. '정확한 실제 위치'나 '인증됨'을 뜻하지 않음 |
| unlocated | null / none | 해당 산책의 사용 가능한 관측 근거가 전혀 없어 좌표를 만들지 못한 종료 상태 |

point가 있으면 source_refs가 하나 이상 있어야 한다. observed는 content.location과 좌표가 같고
그 captured_at에 해당하는 원본 참조가 정확히 하나다. last_known은 참조한 실제 표본의 좌표를
그대로 사용하며 관측 나이와 불확실성을 숨기지 않는다. last_known 표본은 target_at 이전 또는 같은 시각이다.
후속 표본만 있으면 target_at에 대한 estimated로 다루며 last_known/observed로 위장하지 않는다.
estimated는 보간/필터/예측 결과다.
여러 방법의 구체적인 이름과 조합은 algorithm_version으로 식별하며 wire enum을 계속 늘리지 않는다.

## 4. 생성·확정 상태 전이

| 입력 상황 / 사건 | 처리 |
|---|---|
| 정상 GPS에서 누름 | content + resolved/observed를 원자적으로 로컬 저장. 추가 추정 작업 없음 |
| 불안정하지만 최근 좌표 근거 있음 | content + provisional 위치 + 복구 가능한 추정 작업을 한 로컬 트랜잭션으로 보존 |
| 누를 때 근거 없음 | content + provisional/null 저장. 후속 관측으로 과거 target_at을 추정할 여지를 둠 |
| 후속 관측으로 개선 가능 | 같은 resolution_id의 resolved 결과로 한 번 확정 |
| 기한/산책 종료/추정기 실패 | 가진 최선의 좌표로 resolved. 유효 좌표가 하나도 없으면 unlocated |
| 프로세스 재시작 | 저장한 작업/원본을 재사용. 기한이 지나면 추가 수집 없이 저장된 근거만으로 종료 |
| 액션 삭제 또는 계정 전환 | 추정 작업 중단. 늦게 온 결과로 재생성·다른 계정에 귀속 금지 |

최초 저장과 확정만 영속화하는 것이 v1의 기본이다. provisional을 반복 갱신해 핀이 계속 움직이는
방식은 넣지 않는다. resolved/unlocated는 이 resolution_id에서 다시 열지 않는다. 미래 재분석이나
사용자 위치 정정은 별도 작업이며 현재 계약에서 묵시적으로 허용하지 않는다.

후속 수신이 늦어도 액션 시각을 바꾸지 않는다. 새 GPS 한 건을 그 시각의 실제 관측인 것처럼
복사하지 않고, target_at을 대상으로 추정한다. 일시정지·재개·새 산책의 경계를 가로질러
이동을 보간하지 않는다. 산책 종료 후 새 수집을 강제하지 않고 그때까지 가진 근거로 마감한다.
센서 자체의 긴 공백은 추정기가 오차를 평가하는 대상이며 액션을 거부하는 조건이 아니다.

짧은 산책도 액션이 있으면 보존한다. 원본 좌표가 0개인 산책의 생성/finalize/기록 동기화 경로를
후속 구현에서 함께 검증한다. 좌표 0개를 이유로 저장한 액션을 삭제하거나 동기화를 영구 차단하지 않는다.

## 5. 제안 API — 아직 존재하지 않는 v2 경로

v1에 nullable behavior나 새 필드를 무조건 보내면 구버전 APP의 validate/parse가 실패한다.
따라서 **별도 v2 표현 경로**를 사용한다. 어휘 버전과 표현 버전은 별개다.

| 경로 | 역할 |
|---|---|
| `GET /app/walks/entry-capabilities` | 인증된 세션에 read_versions/write_versions/active_policy_versions 반환. 새 경로가 없는 구서버는 404로 판별 |
| `GET /app/v2/walks/{walk_id}/entries` | 전체 현재 원본+pin+최소 삭제 표식. 같은 snapshot의 revision 반환 |
| `PUT /app/v2/walks/{walk_id}/entries/{entry_id}` | 생성 또는 기존 허용 content 정정 |
| `PUT /app/v2/walks/{walk_id}/entries/{entry_id}/pin` | 기존 provisional pin 확정 전용. 액션 생성/내용 변경 불가 |
| `DELETE /app/v2/walks/{walk_id}/entries/{entry_id}` | 기존 expected_revision/mutation_id의 삭제 의미 유지 |
| `POST /app/v2/walks/record-profile/query` | 위치 없는 행동도 횟수와 근거에 포함. v1 어휘/기간/반려견 귀속 규칙 유지 |

capabilities의 형식은 `{ "read_versions": ["walk-entry-v1", "walk-entry-v2"],
"write_versions": ["walk-entry-v1"], "active_policy_versions": [] }`다. v2 쓰기 허용은 실제 구현과
읽기 호환 배포 뒤의 별도 활성화다. 인증/네트워크 실패를 'v1만 지원'으로 간주하지 않는다.
위 버전 표시는 보안 권한이 아니며 서버의 기존 owner/pet 확인은 모든 경로에서 수행한다.

### 요청·응답 예시

[기계 판독 예시](action-pin-location-contract.examples.json)에 정상 생성, 잠정 생성, 확정,
재전송, content 정정, 근거 없는 종료 및 삭제 응답을 둔다. 숫자/좌표는 합성 예시이고 정책 튜닝값이 아니다.

생성은 `expected_revision=0`, `mutation_id`, `content`, `pin`을 보낸다. 성공하면 revision=1,
pin_revision=1이다. behavior의 pin은 필수이며 좌표를 모르면 provisional/null 또는 종료된 unlocated다.
정상 입력을 observed로 저장한 경우도 같은 wire를 쓴다. note의 pin은 null이다.

content 정정 PUT은 expected_revision/mutation_id/content만 보낸다. pin을 넣어 위치를 덮는 요청은
거부한다. kind/recorded_at/content.location은 여전히 불변이다. 코드/반려견/메모 정정은 기존 규칙이다.
pin_revision은 그대로이며 전체 revision만 증가한다.

확정 PUT은 expected_revision/expected_pin_revision/mutation_id/pin을 보낸다. 서버는 전체 revision과
위치 revision을 모두 비교하고 resolution_id/target_at/resolve_by 불변 및 허용 상태 전이를 확인한다.
성공하면 전체 revision과 pin_revision이 각각 1 증가한다. 위치 갱신은 행동 횟수를 늘리지 않는다.

이미 로컬에서 위치가 확정된 뒤 처음 업로드하는 경우 최종 pin을 최초 생성에 포함할 수 있다.
서버에 provisional이 먼저 생성됐을 때만 확정 PUT이 필요하다. wire에 로컬 중간 revision을
서버 revision으로 전송하지 않으며 서버 ACK 전후의 변경을 outbox가 구분한다.

### 충돌·재전송·삭제

- 인증 없음 401; 남의/없는 산책 또는 조회/확정 대상 액션은 404. 검증 오류 422. 기존 삭제 표식은 부활시키지 않는다.
  예외로 소유한 산책에서 **아직 업로드되지 않은 액션의 DELETE(expected_revision=0)** 는
  revision=1의 최소 삭제 표식을 생성한다. 삭제가 최초 생성보다 먼저 도착해도 뒤늦은 생성은 410이다.
- CAS 불일치 409 `walk_entry_conflict`. 같은 snapshot의 현재 revision/pin_revision을 조회하고 판단한다.
- 수정 가능한 behavior_code/pet_id만 바뀌고 원본·resolution이 같으면 최신 revision에 맞춰 위치 확정을
  다시 제안할 수 있다. 해당 CAS 재시도는 새 mutation_id다. 삭제되었거나 이미 확정됐으면 결과를 버린다.
- 같은 mutation_id와 같은 정규화 요청의 재전송은 한 번만 적용하고 **원래 ACK**를 반환한다. 다른
  요청 내용으로 ID를 재사용하면 409다. 응답 유실 뒤 재시도에 다른 ID를 만들지 않는다.
  중간에 다른 정정이 있었으면 원래 ACK의 revision은 현재보다 낮을 수 있다. APP은 이 ACK로
  최신 원본/pin을 덮지 않고 해당 outbox 전송만 완료 처리한 뒤 현재 snapshot을 조회한다.
- 마지막 mutation_id 하나만 저장해서는 중간에 다른 수정이 들어온 재전송을 구별할 수 없다.
  3단위에서 수명 내 변경 영수증을 보존하거나 동등한 멱등성 저장을 구현한다. JSON 해시는 키 순서를
  정규화하고 배열의 계약상 순서를 고정한다. 멱등 키 범위는 walk_id/entry_id/mutation_id이며
  연산 종류·전체 본문·expected_revision도 요청 식별에 포함한다. 다른 연산에 같은 ID를 재사용할 수 없다.
- 삭제는 최우선이다. 삭제 뒤 과거 mutation 재전송에도 옛 원본 ACK를 노출하지 않고 410
  `walk_entry_deleted`를 반환한다. 최소 표식은 id/revision/mutation_id/deleted=true뿐이다.
- 삭제와 같은 트랜잭션에서 pin/근거/좌표를 포함한 영수증을 파기한다. identity 표식만 남겨 지연 요청을
  차단한다. 반복 DELETE는 현 삭제 ACK를 반환한다. 계정/산책 삭제는 기존 FK 수명에 맞춘다.
- 서버는 APP의 'resolved'를 현장 검증 성공으로 승격하지 않는다. 참조한 원본의 같은 산책/시각/chain과
  보존 원본 일치, source_refs의 순서·중복·resolve_by 이내 여부를 검증한다. 알고리즘 정확도를 재보증하지 않는다.
- reference fix는 원본 GPS 업로드와 연결한다. 업로드 전에 먼저 로컬 기록이 존재하는 것은 정상이다.
  서버 기록 쓰기에는 참조한 원본이 도착해야 하며, 순서 문제는 outbox에서 해결한다. 참조를 조용히 삭제하지 않는다.

## 6. 구버전 호환과 활성화 순서

1. APP에 v2 **읽기**와 위치 없는 행동·pin 결과의 파싱/표시를 먼저 배포한다. v2 쓰기는 꺼 둔다.
2. DEV에 v2 경로와 storage/멱등성을 구현한다. 기존 v1 기록은 원본을 바꾸지 않고 읽기 어댑터로 노출한다.
   v1 behavior는 resolved/observed, `policy_version=legacy-v1`, source_refs는 원본 참조가 없어 빈 배열을
   허용하는 **서버 생성 legacy 예외**다. 새 v2 쓰기는 이 예외를 사용할 수 없다. pin_revision=0이다.
   legacy의 resolution_id는 entry ID, target_at/computed_at/resolve_by는 recorded_at,
   algorithm_version은 legacy-v1, 불확실성은 unknown이다. 원본 captured_at은 content.location에 남긴다.
   legacy에는 자동 위치 갱신을 받지 않는다. note의 pin은 null이다.
3. APP은 capabilities가 read+write v2와 사용 policy를 모두 허용할 때만 v2 업로드를 시작한다.
   지원 전 만들어진 v2 로컬 원본은 보존/대기하며 v1으로 몰래 변환하거나 기록을 버리지 않는다.
4. v1은 원래 v1 기록만 안전하게 읽고 쓸 수 있다. 범위에 새 v2 활성 기록이 있는 v1 목록/프로필 요청은
   426 `walk_entry_upgrade_required`다. nullable behavior/추정값을 관측으로 위장/빈 목록/필터 누락으로
   성공시키지 않는다. v2 기록을 대상으로 한 v1 수정·삭제도 426이며 권한 확인을 먼저 수행한다.
5. 426은 구버전에서 자동 해결할 수 없다. 동일 계정의 모든 설치를 업데이트했다고 가정하지 않는다.
   읽기 배포는 위험을 줄이지만 제거하지 못한다. 지원 최소 버전 안내/업데이트 경로를 검증하기 전
   서버 v2 쓰기를 켜지 않는다. 새 APP은 426을 재시도 루프가 아닌 업데이트 필요 상태로 표시한다.
6. 쓰기를 끄는 롤백은 새 v2 입력을 로컬 보존/대기시키며 이미 생성한 v2 **읽기**는 유지한다.
   신규 스키마나 읽기 어댑터를 제거하는 롤백은 하지 않는다. 이미 저장한 추정값을 v1로 덮지 않는다.

resolved와 별개로 `legacy-v1`은 source_refs/policy 정보를 사후 발명하지 않는 표시다. 과거 기록에
새 추정기를 자동 실행하지 않는다. 별도 재분석은 이번 단위의 범위가 아니다.

## 7. APP 생명주기·시각·관측 보존 의무

- 누른 순간 액션 ID/resolution_id/원본 시각/pin/기한/정책 버전/추정 작업을 원자적으로 로컬 보존한다.
  성공 안내는 실제 로컬 commit 뒤다. 잠정 지도 표시가 DB 실패를 숨기면 안 된다.
- 활성화된 session/owner를 캡처한다. 연속 탭은 서로 다른 액션이며 계산을 공유해도 ID를 합치지 않는다.
- 대기 시간은 실행 중 monotonic clock으로 잰다. UTC는 wire 식별에 사용한다. 재시작/재부팅 때 기한이나
  세션 종료가 확인되면 더 기다리지 않고 보존 근거로 마감한다. 시계가 역행해도 대기를 무한 연장하지 않는다.
- source_refs의 client_seq/chain_index/at와 계산에 쓴 원본 스냅샷을 재현 가능하게 보존한다.
  새 세션/다른 계정의 표본은 소비하지 않는다. 앱이 그리는 경로를 원본 GPS인 것처럼 역변환하지 않는다.
- 후속 관측을 확보하려고 산책 종료 후 백그라운드 위치 수집을 계속하지 않는다. 종료 때 가진 입력으로
  확정 후 동기화하며, 추정기 실패/재시작이 산책 종료와 원본 저장의 성공을 막지 않는다.
- '기한 만료'는 기록 삭제나 좌표 숨김이 아니다. 현재 최선 좌표는 유지되고 이유만 명시된다.
  장기간 최신 관측이 없으면 last_known/unknown으로 끝낼 수 있다. 그것을 최신 GPS로 표시하지 않는다.

## 8. 소비자 경계

| 소비자 | 의무 |
|---|---|
| 지도/상세 | 좌표가 있으면 같은 액션 ID의 핀을 유지. provisional→resolved 시 선택·편집 상태 유지. 좌표 없음은 시간순 목록에 유지 |
| 행동 프로필 | 위치와 무관하게 기존 기록 수/반려견 귀속 집계. pin만 바뀌어도 근거 source_revision은 갱신하되 횟수 증가 없음 |
| 스토리보드 | 원본 액션을 유지하고 최신 pin을 별도 anchor로 사용. 원본 GPS observation identity로 바꾸지 않음. 위치 revision을 결과 fingerprint에 반영 |
| 주변 봉투 수집 | provisional은 기본 수집 대기. resolved 중 정책상 공간 근거가 충분할 때만 조회; 나머지는 not_requested와 이유 보존 |
| 점령·방문·사진 인증 | 추정 pin/content를 인증 fix로 사용하지 않음. 기존 실측/소유권 검증 입력 경로 유지 |

이 문서의 위치 갱신은 기존 source fingerprint에 포함되지 않는 새 차원이다.
[DEV #339](https://github.com/SAJOYO/DAENGS_dev/pull/339)의 예약 키/조회 대상/늦은 응답 검사에는
연결 시 pin_revision을 함께 반영해야 한다. 현재 #339가 이미 이 계약을 지원한다고 간주하지 않는다.
위치가 없는 기록, provisional, last_known/unknown은 시설 방문·내부 여부의 근거가 아니다.
pin이 resolved가 돼도 단순 주변 정보 조회일 뿐 이용/행동 원인 판정을 추가하지 않는다.

## 9. 1단위 완료 조건과 후속 검증

| 사례 | 계약상 기대 결과 |
|---|---|
| 정상 위치에서 누름 | 즉시 원본+resolved/observed, 추가 작업 없음 |
| 이동 중 잠깐 GPS 점프 | 즉시 원본+잠정 좌표, 이동 중 추정 허용, 같은 ID로 확정 |
| 후속 위치 수신 없음 | 기한에 최선 위치로 종료, 행동 유지 |
| 시작부터 좌표 없음 | 원본 유지, 끝까지 없으면 unlocated, 지도 가짜 좌표 없음 |
| 후속 시각에만 유효 표본 | 당시 시각을 target으로 추정하거나 last_known 의미를 구분; recorded_at 변경 금지 |
| 원본 정정과 위치 확정 경합 | CAS 충돌 후 원본/작업 확인, 사용자 정정 보존 |
| 확정 응답 유실 후 재전송 | 같은 ACK, revision/횟수 추가 증가 없음 |
| 삭제 후 옛 응답 | 원본·좌표 부활 없음, 410 |
| 종료/재시작/계정 전환 | 종료는 마감, 재시작은 재현, 전환은 이전 계정 작업 차단 |
| 구버전 기기에서 새 기록 조회 | 426, 추정값 위장·침묵 누락 없음 |

1단위 검증은 문서 상태 전이/JSON 예시/상대 링크/현재 코드 근거 대조다. 실제 API·DB·Kotlin
테스트 통과나 추정 품질을 주장하지 않는다. 알고리즘 수치·최소 지원 APP 버전·물리 SQL·worker
구현은 각각 후속 2/3/4단위에서 확정한다. 이 문서만으로 v2 활성화나 배포가 완료되는 것은 아니다.
