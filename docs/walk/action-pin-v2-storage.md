# 행동 핀 v2 저장 — 3단위

구현: [DEV #357](https://github.com/SAJOYO/DAENGS_dev/pull/357).
계약: [1단위 고정 문서](https://github.com/SAJOYO/DAENGS_dev/blob/cae3a4403948ee774ff4d78b6a4fb6e0cd71b850/docs/walk/action-pin-location-contract.md).
추정기: [APP #228](https://github.com/SAJOYO/DAENGS_APP/pull/228).
APP 버튼/Room/worker/지도/동기화는 후속 4단위다. 이번 서버 구현만으로 앱 동작이 바뀌지 않는다.

## 실제 경로

| 경로 | 동작 |
|---|---|
| GET `/app/walks/entry-capabilities` | 인증된 사용자에게 읽기/쓰기 버전과 신규 핀 정책 반환 |
| GET `/app/v2/walks/{walk_id}/entries` | 원본·핀·최소 삭제 표식을 같은 snapshot으로 조회. revision은 목록 해시 |
| PUT `/app/v2/walks/{walk_id}/entries/{entry_id}` | 신규 원본/핀 생성 또는 허용 content 정정 |
| PUT `/app/v2/walks/{walk_id}/entries/{entry_id}/pin` | 기존 provisional의 일회성 종료. 액션 생성 불가 |
| DELETE `/app/v2/walks/{walk_id}/entries/{entry_id}` | 원본·핀·영수증 파기. 업로드 전이면 삭제 표식 먼저 생성 |
| POST `/app/v2/walks/record-profile/query` | 위치 유무와 무관한 행동 기록 수·산책 수·반려견 귀속과 pin 근거 |

모든 경로는 기존 CurrentAppUser 인증을 사용한다. capabilities도 비인증이면 401이다.
없는/남의 산책·반려견은 404, 잘못된 값은 422, CAS·mutation 재사용 충돌은 409다.
삭제된 기록의 생성·확정 재전송은 410 `walk_entry_deleted`이며 옛 ACK도 반환하지 않는다.

생성 PUT은 pin 필드를 요구한다. behavior는 객체, note는 명시적 null이다. 내용 정정에서는 pin을
**생략**해야 하며 null도 거부한다. 종류·recorded_at·content.location은 불변이다. behavior location은
null이어도 되고, 행동 코드/메모/동행 반려견 검증은 유지한다. note는 산책 전체 기록이다.

pin의 모든 필드는 필수다. extra 필드, timezone 없는 시각, 비유한/범위 밖 좌표, 0 이하 불확실성,
중복/역순 source_refs, 상태와 맞지 않는 method/point/reason은 422다. source_refs는 최대 256개다.
신규 허용 버전은 `action-pin-policy-v1` + `action-pin-local-v1`이다. 서버는 추정 정확도를 보증하지 않는다.

## 저장과 일관성

기존 walk_entries에는 원본 content와 전체 revision을 유지한다. 새 표는 다음 둘이다.

| 표 | 내용 |
|---|---|
| walk_entry_pins | 같은 walk/entry FK, pin_revision, pin JSON. note/삭제는 pin=null |
| walk_entry_mutations | walk/entry/mutation별 정규화 요청 SHA-256과 원래 성공 ACK |

별도 표여서 v2 비활성 상태의 기존 ORM 조회가 새 컬럼을 요구하지 않는다. 기존 v1은 읽을 때만
legacy-v1의 resolved/observed로 표현하고, 원본 변경이나 사후 raw 참조 생성은 하지 않는다.
legacy pin_revision은 0이며 자동 위치 확정 대상이 아니다. v2에서 legacy content를 정정하면
이 표현을 sidecar에 보존하고 이후 v2 기록으로 취급한다.

쓰기 순서는 **소유 산책 행 잠금 → 삭제 확인 → 성공 영수증 재전송 → CAS → 검증 → 한 TX commit**이다.
같은 산책을 잠그므로 아직 행이 없는 최초 생성 경합도 직렬화된다. pin 확정은 전체 revision과
pin_revision을 모두 비교/증가시킨다. content 정정은 pin_revision을 바꾸지 않는다.
위치 확정으로 액션 ID나 행동 횟수가 늘지 않는다.

해시는 연산 종류, expected revision, pin 생략 여부와 정규화한 전체 본문을 포함한다.
시각은 UTC로, 사전 키는 정렬해 정규화한다. 후속 정정 이후라도 같은 mutation 재시도는 최초 성공 ACK를
그대로 반환한다. APP은 옛 ACK로 최신 로컬 기록을 덮으면 안 된다. 새 CAS 제안에는 새 mutation ID가 필요하다.

반복 DELETE는 현재 최소 표식만 반환한다. SQL NULL/JSON null 삭제 모두 DB 트리거가 pin JSON/위치 revision과
모든 영수증을 같은 TX에서 파기한다. sidecar의 FK는 v2 식별용으로 남는다. 삭제 뒤 SQL 쓰기로 pin/ACK를
다시 넣는 것도 트리거가 거부한다. walk/account 삭제는 FK CASCADE다. 활성 영수증은 기록 수명 동안
보관하므로 수정 횟수에 비례해 저장량이 증가한다.

목록과 프로필은 read-only REPEATABLE READ 세션을 사용한다. content/pin/revision 또는 분모/근거가
다른 시점에서 섞이지 않는다. 핀 확정은 profile source_revision에도 반영한다.

## 원본 GPS 검증

같은 산책의 walk_point_chunks를 기존 decoder로 읽어 client_seq·chain_index·at와 non-mock 여부를 확인한다.
estimated는 chain을 가로지를 수 없다. 참조 시각은 computed_at/resolve_by 이내여야 하고 provisional은
탭 이후 관측을 쓰지 않는다. last_known/observed는 탭 이전 또는 같은 시각의 실제 한 점을 복사한다.

content.location도 업로드된 원본에 같은 관측 시각·좌표·accuracy가 있어야 한다. **raw 업로드가 먼저**다.
기존 저장 정밀도는 좌표 소수점 6자리/시각 밀리초여서 비교도 같은 Decimal 양자화를 사용한다.
pin/content의 정밀한 값을 저장 때 덮어쓰지는 않는다. observed의 pin과 content.location은 정확히 같아야
하고 provider_accuracy는 원본 accuracy와 같아야 한다.

후속 GPS만 있는 estimated도 target_at=recorded_at을 유지한다. unlocated 종료에는 해당 관측 창의
사용 가능한 원본 좌표가 없어야 한다. 이미 가진 provisional 좌표도 unlocated로 버릴 수 없다.
서버는 대기를 실행하지 않으며 시계·수집 종료·재시작 복구는 APP 책임이다.

## 기존 소비자 보호

- v1 목록/프로필 범위에 활성 v2가 있으면 426 `walk_entry_upgrade_required`다. v2 대상의 v1 PUT/DELETE와
  기존 contexts 읽기도 차단한다. 다른 v1 기록은 계속 쓸 수 있다. 삭제된 v2만 있으면 목록은 최소 표식으로 읽는다.
- v2 쓰기는 별도 v2 정책으로 주변 정보를 예약하며 원본 인증 GPS와 핀 좌표를 분리한다.
  profile context_status의 요약 연결은 별도이고, v2 contexts API로 결과를 조회한다.
- 활성 v2가 있는 산책의 구버전 storyboard는 소유권 확인 후 명시적 409로 차단한다.
  v5는 pin_revision과 위치 출처를 보존한다. [장면·주변 정보 연결](action-pin-context.md)을 따른다.

## 적용·활성화·롤백

1. [DB 적용 절차](../../db/migrations/README.md)에 따라 `2026-09-09_walk_entry_pins.sql`과
   `verify_2026-09-09_walk_entry_pins.sql`을 적용한다. 선행 조건은 walks와 19_walk_entries.sql이다.
   기존 DB용 마이그레이션은 재실행 가능하며 초기 25_walk_entry_pins.sql과 같다.
2. 서버 배포 후 `DAENGS_WALK_ENTRY_V2_ENABLED=true`로 v2 읽기/호환 보호를 켠다. WRITE_ENABLED는 false다.
3. APP 읽기/저장/동기화/426 안내, storyboard/주변 정보 연결과 최소 지원 버전 정책 검증 뒤에만
   `DAENGS_WALK_ENTRY_V2_WRITE_ENABLED=true`를 별도 활성화한다.
4. 롤백은 WRITE_ENABLED만 false로 돌린다. 새 생성은 409로 거부하고 기존 정정/삭제/핀 종료/성공 재전송은
   허용한다. capabilities의 신규 정책 목록은 비워진다.
5. **v2 데이터가 생긴 뒤에는 ENABLED나 읽기/sidecar를 제거하지 않는다.** v1이 nullable 행동을 파싱하거나
   v2 기록을 수정하게 되므로 안전한 롤백이 아니다. v1으로 몰래 변환하지 않는다.

2026-09-09 12:15 KST에 사용자가 지정한 DB에 SQL을 트랜잭션으로 적용했고, 같은 TX와
새 읽기 전용 연결에서 verifier를 통과했다. 기존 데이터 backfill은 없었다. 머지/배포와
플래그 활성화는 실행하지 않았다.

APP #232 연동에서 terminal pin의 선택 필드 `observation_cutoff_at`을 추가했다.
일시정지/종료 이후에 받은 좌표를 과거 행동의 계산에 섞지 않도록 실제 관측 종료 시각을
전달한다. target_at 이상이며 computed_at/resolve_by 이하여야 하고, source_refs 및
unlocated의 원본 존재 검사는 이 cutoff까지 수행한다. capabilities의
`pin_observation_cutoff_supported`로 협상한다. 필드 생략/null은 기존 관측 창과 같고,
기존 mutation receipt의 요청 해시는 유지한다. JSON 메타데이터여서 추가 SQL은 없다.

## 검증

로컬의 기록 v1/v2 HTTP·서비스, 주변 정보, storyboard, 좌표 0개 포함 finalize, main 경량 기동을 선택해
101개를 통과했다. v2 사례 추가 후 해당 파일 28개를 다시 통과했다. 변경 backend Python의 ruff 검사도 통과했다.

```powershell
uv run pytest -q tests/walk/entries/test_walk_entry_v2.py tests/walk/entries/test_walk_entries.py tests/walk/entries/test_walk_entry_http.py tests/walk/context/test_walk_entry_context.py tests/walk/storyboard/test_walk_storyboard.py tests/walk/measurement/test_finalize_contract.py tests/test_main_stays_light.py
```

`walk entry v2 PostgreSQL` CI는 격리된 PostgreSQL 17에서 실제 SQL 반복 적용, raw 참조 정밀도, 원래 ACK,
CAS, commit rollback, 동시 생성/삭제·확정, snapshot 및 계정 CASCADE를 검증한다. 명령은
`uv run pytest -q tests/walk/entries/test_walk_entry_v2_db.py tests/walk/entries/test_walk_entry_v2.py`다. 로컬 실행에는 명시적인
WALK_PIN_TEST_DATABASE_URL이 필요하며 localhost의 walk_pin_test DB만 허용한다. 미설정이면 DB 검증은 skip이다.

기존 migration verifier에도 등록해 정상 스키마의 성공과 트리거/PK/FK/필수 컬럼 제거 시 실패를 확인한다.
기존 전체 backend CI도 그대로 유지했다. 최신 커밋의 결과는 PR Checks를 기준으로 한다.
