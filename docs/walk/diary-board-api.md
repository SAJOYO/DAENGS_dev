# 기본 보드 API·저장 연결

`walk-diary-board-v1`은 [내부 기본 보드](diary-base-board.md)를 기존
`/app/walks/{walk_id}/storyboard` 생성·조회 경로로 공개한다.
생성 예약·발행·저장은 기존 스토리보드 경로를 사용한다. 현재 작성 그래프는
[독립 카드 작성](card-orchestration.md)을 따른다.
앱 연결 카드는 [DAENGS_APP#273](https://github.com/SAJOYO/DAENGS_APP/pull/273)이다.

## 생성과 표시

1. 기존 소유자 확인·원본 동기화 검증과 확정 동선 재생으로 기본 보드를 준비한다.
2. 사용자 기록을 유지하고, 부족한 중간 장면을 관측과 실제 동선 지점으로 보충한다.
   시작·종료는 중간 목표 수 밖의 두 장면이다. GPS가 없으면 위치 없는 카드로 남는다.
3. 새 작성이 필요하면 기존 스토리보드 행에 예약을 저장하고 트랜잭션을 해제한다.
   #505부터 기본 경로는 기존 배경 수집 job의 `pending/running`을 진입 조건으로 삼지 않는다.
4. 카드 그래프에서 공간 수집·작성과 준비된 행동 작성을 독립 실행한다. 채택한 본문을
   고정한 뒤 카드 제목을 작성한다. 관측 설명과 사용자 원문은 코드로 보존한다.
5. 같은 원본·세대인지 확인한 뒤 완성 보드와 작성 영수증을 저장한다. 부분 실패는 해당
   부분의 기본 결과로 마무리하며 기존 공개 마감이 최종 발행 권한을 갖는다.

요청의 `preparation_budget_ms`가 제공되면 최초 예약에 마감을 저장하고 수집·본문·제목이
그 예산을 공유한다. 예산이 소진되면 기본 보드를 발행하며, 취소된 요청도 저장된 마감 뒤
GET에서 정산할 수 있다. 예산을 보내지 않는 기존 호출은 기존 lease 정책을 유지한다.

## 첫 생성과 기존 배경 수집 — #505

이전에는 첫 보드를 만들기 전에 entry-context 수집 완료를 기다렸다. 새 카드 그래프는
자체 공간 수집과 행동 작성이 독립적이지만, 이 선행 조건 때문에 그래프 밖에서 전체가
대기할 수 있었다. **기본 POST는 미완료 배경 job이 있어도 예약 후 바로 그래프에 진입한다.**
GET은 여전히 작성하지 않으므로 미생성 상태에서는 `pending`을 반환할 수 있다.

이미 저장된 유효 배경은 입력 준비에서 읽고, 새 공간 조회는 그래프의 기존 마감 안에서
수행한다. 준비되지 않은 자료를 유효한 것으로 바꾸거나 기존 수집 job을 완료·취소하지 않는다.
행동이 먼저 준비되면 공간 수집 완료 전에 작성하며, 공간 실패·시간 초과가 성공한 행동을
버리지 않는다. 이후 entry-context 작업이 완료돼도 기존 발행본은 자동 교체하지 않는다.

과거 슬롯 작성의 수집 유예가 필요한 내부 호출만
`generate_diary(..., writer=write_legacy_slot_board, legacy_context_wait=True)`로 선택한다.
이 인자는 HTTP 요청 필드나 운영 플래그가 아니다. writer를 직접 전달하거나 `partial`·래퍼로
감싸는 것, 모델·수집기를 주입하는 것만으로 유예가 켜지지 않는다. 예약 전 조회를 선택하는
`legacy_collector`와도 독립적이다. 과거 `walk-diary-bundle-v1` 형식에는 새 유예를 적용하지 않는다.

명시적 유예는 예전 조건을 유지한다. 첫 보드이며 수집 기능이 켜져 있고 현재 유효 기록의
job이 `pending/running`인 경우에만, 서버 업로드 시각 `walk.created_at`부터 최대 10분간
예약 없이 `pending`을 반환하고 트랜잭션을 해제한다. 완료·실패·취소, 삭제된 기록, 기능 꺼짐,
업로드 시각 미상에는 기다리지 않는다. 재시도가 기한을 연장하지 않는다.

기존 공개본 보존과 `background_update_available` 정책은 그대로다.
`test_diary_context_entry.py`는 기본 HTTP 요청에서 미완료 job과 행동핀이 공존할 때 수집 성공·실패·
시간 초과, 예산 소진, 원문 변경, 늦은 배경 도착을 검증한다. DB·외부 제공자·모델은 대역이므로
실제 Gemini나 운영 네트워크의 완료 시간을 입증하는 검사는 아니다.

## 공개 계약

capabilities는 기존 `walk_diary_enabled`가 켜졌을 때 기존 v1과 새 형식을 광고한다.
새 앱은 `bundle_format=walk-diary-board-v1&target_scene_count=5`로 조회한다.
응답 `format`은 `walk-diary-board-response-v1`이다.

각 장면은 `id`, `order`, `core`, `kind`, `anchor`, `title`, **완성된 `body`**를 가진다.
`kind`에 맞는 페이로드 하나만 존재한다.

| kind | 내용 | 위치 |
| --- | --- | --- |
| user_record | 메모·행동·사진의 원본 내용 | 확인된 원본 앵커 |
| movement_observation | 기기의 체류·상대 속도 관측 | 업로드된 GPS 순서·체인·시각 |
| route_checkpoint | 분석 ID·연속 구간·경로상 거리 | 선택된 실제 GPS 한 점 |
| session_boundary | start 또는 end | 정확한 시각의 관측이 없으면 위치 없음 |

`body`는 화면과 편집기가 함께 사용하는 전체 장면이다. 원본 메모·행동 문자열을
앱에서 다시 덧붙이지 않는다. `model_status`, `failure_code`, `preparation_counts`는
진단용으로 보존하며 새 앱은 이를 별도 원본/실패 안내 블록으로 표시하지 않는다.
private pin payload·owner ID·전체 원천 입력은 공개하지 않는다.

## 저장과 기존 기록

기존 `walk_storyboards.bundle` JSONB에 `walk-diary-board-storage-v2` receipt를 저장한다.
완성 보드 해시·원본 revision·생성 revision·선정 결과에 더해, 실제 인용 근거와 당시
슬롯 정책·writer 버전을 내부 `writing_receipt`에 보존한다. 원문 해시와 배경 문장으로
발행 본문과의 결합을 확인한다. 공개 응답은 계속 `walk-diary-board-v1`이며 이 영수증은
반환하지 않는다. 자세한 저장 범위는 [파트 슬롯 서비스 연결 3단계](diary-part-slots.md)를 따른다.
공개된 보드는
이후 배경 수집, 목표 수, writer 정책 변경만으로 교체하지 않는다. 사용자 원본 기록,
사진, 핀, 경로가 바뀌면 stale로 판정한다. 별도의 앱 편집본은 원본 입력이 아니다.

기존 storage-v1은 그대로 읽으며 당시 없었던 서술 영수증을 새로 만들지 않는다.
새 저장값은 v2 읽기를 지원하는 서버가 필요하므로 이전 서버로 롤백할 때 읽기 호환을 유지한다.
새 형식을 요청해도 이미 저장된 구형 v1/legacy 보드는 **저장된 형식과 목표 수**로
읽는다. 읽기·기능 협상만으로 새 보드를 만들지 않는다. 새 형식의 POST `refresh`는
재생성 명령으로 취급하지 않는다. 구형 앱의 생성 요청은 새 보드/생성 예약을
덮어쓸 수 없으며 409를 받는다.

예산 필드가 없는 예약은 기존 JSONB에 형식 마커만 넣는다. 원본 변경 후에도 아직
유효한 다른 요청의 lease를 탈취하지 않는다. late writer의 완료는 기존 세대·원본
비교로 배제한다. 후속 [단일 공개 정책](diary-publication.md)은 예산을 보낸 새 요청의
예약에 기본 보드와 마감을 함께 보존한다.

## 검증 자료

- API: `backend/tests/walk/diary/test_diary_board_api.py`
- 실제 PostgreSQL 저장·lease·삭제: `test_diary_board_db.py`
- 인용 근거·버전 보존·구형 저장 호환: `test_diary_board_receipt.py`
- 공유 계약: `backend/evals/walk-diary/board-v1.json`
- fixture 재생: `test_diary_board_contract.py`와 `support/board_contract.py`

공유 fixture는 합성 동선을 기존 저장 입력 어댑터·기본 보드·writer 응답 검증기에
통과시켜 만든다. 모델 응답은 고정 fixture이며 실제 Gemini 호출 결과가 아니다.
APP의 `app/src/test/resources/storyboard/diary-board-v1.json`과 바이트 단위로 같다.
