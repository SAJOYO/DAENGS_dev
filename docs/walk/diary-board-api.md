# 기본 보드 API·저장 연결

`walk-diary-board-v1`은 [내부 기본 보드](diary-base-board.md)를 기존
`/app/walks/{walk_id}/storyboard` 생성·조회 경로로 공개한다.
새 워커·오케스트레이터·테이블·마이그레이션은 추가하지 않는다.
앱 연결 카드는 [DAENGS_APP#273](https://github.com/SAJOYO/DAENGS_APP/pull/273)이다.

## 생성과 표시

1. 기존 소유자 확인·원본 동기화 검증과 확정 동선 재생으로 기본 보드를 준비한다.
2. 사용자 기록을 유지하고, 부족한 중간 장면을 관측과 실제 동선 지점으로 보충한다.
   시작·종료는 중간 목표 수 밖의 두 장면이다. GPS가 없으면 위치 없는 카드로 남는다.
3. 첫 생성에서 현재 기록의 배경 수집이 진행 중이면 아래 유예 정책에 따라 `pending`을
   반환한다. 생성할 때는 기존 스토리보드 행에 예약을 저장하고 트랜잭션을 해제한다.
4. 기존 일기 writer를 한 번 호출한다. 기존 기록·관측의 AI 배경 서술과 제목을
   고정된 기본 보드에 옮긴다. 지점·시작/종료에는 기본 문장을 사용한다.
5. 같은 원본·세대인지 확인한 뒤 완성된 보드 한 개를 저장한다. writer 오류에도
   기본 보드가 남는다. 취소된 요청은 기존 60초 lease를 통해 복구할 수 있다.

현재 writer의 15초 제한과 기존 클라이언트 생성 시점은 그대로다. 아래 수집 유예는
종료 직후 전체 생성을 10초 안에 끝내는 예산 정책과 별개다. 새 동선 지점의 공공데이터
수집·LLM 배경 작성 확대도 이번 범위에 포함하지 않는다.

## 첫 생성의 수집 유예

새 기본 보드이고 아직 스토리보드 행이 없을 때만 현재 유효 기록의 context job을 본다.
삭제된 기록, 이전 revision·policy의 job은 대상이 아니다. 수집 기능이 켜져 있고
`pending` 또는 `running` job이 남아 있으면 **서버에 산책이 최초 업로드된 시각
(`walk.created_at`)부터 최대 10분까지** 생성을 미룬다. 산책 시작 시각이나 첫 생성
요청 시각 기준이 아니다. 현재 앱은 종료된 산책만 서버에 업로드한다.

유예 중 POST는 기존 응답 형식의 `status=pending`, `generation=0`, `bundle=null`을
반환한다. 생성 예약·writer 호출 없이 트랜잭션을 해제하며 서버 요청을 잠든 채로
두지 않는다. 앱의 기존 `DiaryStillPending` 처리와 WorkManager 재시도가 다음 요청을
보낸다. GET 자체는 생성이나 수집을 실행하지 않는다.

10분은 서버가 생성을 유예하는 조건의 상한이며 **화면이 10분 안에 ready가 되는
상한이 아니다.** 현재 WorkManager는 30초부터 지수 간격으로 재시도하므로, 기한이
지나도 다음 재시도에서 생성이 진행된다. 기기 네트워크·작업 스케줄링에 따라서도
표시까지 걸리는 시간은 달라질 수 있다.

대상 수집이 끝나면 다음 요청에서 즉시 생성한다. 업로드 후 10분이 지났으면 job이
미완료여도 그때 확보된 자료로 기본 보드를 생성한다. `completed`·`failed`·`cancelled`
job, 수집 기능 꺼짐, 서버 업로드 시각을 확인할 수 없는 경우에는 유예하지 않는다.
재시도·추가 수집은 이 기한을 연장하지 않으며 기존 ready·running·stale 처리도 유지한다.

이 정책이 모든 배경 자료의 포함을 보장하지는 않는다. 새 지역의 카탈로그 준비가
필요하면 `catalog_preparing` 후 context 재시도 간격만 300초이므로, 최장 유예 안에도
자료가 준비되지 않을 수 있다. 최초 업로드 뒤 좌표·분석·행동 동기화가 10분 넘게
지연되거나 오래된 산책에 기록이 늦게 추가되면 별도 유예가 생기지 않는다. 이미
`ready`인 보드는 늦은 수집 결과로 덮어쓰지 않으며, 기존
`background_update_available`로 배경 변경 가능성만 알린다.

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

기존 `walk_storyboards.bundle` JSONB에 `walk-diary-board-storage-v1` receipt를 저장한다.
완성 보드 해시·원본 revision·생성 revision·선정 결과를 함께 보존한다. 공개된 보드는
이후 배경 수집, 목표 수, writer 정책 변경만으로 교체하지 않는다. 사용자 원본 기록,
사진, 핀, 경로가 바뀌면 stale로 판정한다. 별도의 앱 편집본은 원본 입력이 아니다.

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
- 공유 계약: `backend/evals/walk-diary/board-v1.json`
- fixture 재생: `test_diary_board_contract.py`와 `support/board_contract.py`

공유 fixture는 합성 동선을 기존 저장 입력 어댑터·기본 보드·writer 응답 검증기에
통과시켜 만든다. 모델 응답은 고정 fixture이며 실제 Gemini 호출 결과가 아니다.
APP의 `app/src/test/resources/storyboard/diary-board-v1.json`과 바이트 단위로 같다.
