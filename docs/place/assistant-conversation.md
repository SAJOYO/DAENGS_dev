# 시설 대화와 공통 오케스트레이션

공통 채팅과 시설 화면이 같은 `facility-conversation-v2` 세션을 사용한다. 공통 라우터는
Place 실행 여부만 고른다. 시설 의도 해석·조건 적용·후보 교체·확인은 기존 시설 실행기가,
짧은 답변은 커밋된 receipt를 읽는 서버 renderer가 맡는다. 별도 응답 LLM 호출은 없다.

구현은 `schemas/assistant_facility.py`, `orchestration/adapters/facility.py`,
`routers/assistant.py`다. 대응 앱 변경은 [APP #334](https://github.com/SAJOYO/DAENGS_APP/pull/334).

## 요청과 결과

`POST /assistant/query`에 선택 필드 `facility`를 추가했다. 앱 회원 토큰만 사용할 수 있고
owner·전체 필터·검색 결과는 요청에서 받지 않는다. 기존 클라이언트가 이 필드를 생략하면
기존 단발 `place-capability-v1` 경로를 사용한다.

```json
{
  "query": "첫 번째 골라줘",
  "facility": {
    "client_request_id": "22222222-2222-4222-8222-222222222222",
    "session_id": "11111111-1111-4111-8111-111111111111",
    "expected_revision": 3,
    "visible_order": [{"source": "tourapi", "ref": "123"}],
    "visible_selected": {"source": "tourapi", "ref": "123"},
    "bookmark_commands": "v1"
  }
}
```

`visible_order`는 발화 시점의 실제 카드 순서다. `visible_selected`는 해당 목록의 선택
대상이다. 서버는 기존 시설 API와 동일하게 저장 상태와 대조한다. 세션이 있으면 그 검색
중심을 Place에 사용하며, Walk가 함께 선택되면 별도로 기기 위치가 있어야 한다.
`requested_capability: "place"`를 명시할 수도 있고, 생략하면 공통 의미 라우터가 고른다.

처음 요청할 때는 `client_request_id`와 기기 `location`만 보낸다. 시설 화면과 같은
반경 3km·카페 기본 검색을 만든 뒤 사용자 발화를 적용한다. 최초 검색용 ID는 요청 ID에서
결정적으로 파생한다. 응답 유실 후 같은 요청을 보내도 새 세션·LLM 실행을 중복 생성하지
않는다. 같은 ID로 발화가 바뀌면 기존 시설 서비스가 충돌로 거절한다.

공통 응답의 Place `data`는 다음처럼 작게 유지한다. 전체 시설 상태를 저장 채팅에 복제하지
않는다. `message` 집계는 기존 규칙대로 `data.answer` 또는 해당 오류·확인 문구를 사용한다.

```json
{
  "contract_version": "place-facility-v2",
  "answer": "마루카페 골라뒀어요!",
  "facility": {
    "session_id": "11111111-1111-4111-8111-111111111111",
    "revision": 4,
    "client_request_id": "22222222-2222-4222-8222-222222222222"
  }
}
```

앱은 기존 `/app/places/conversation/recover`에 `session_id`, `client_request_id`를 보내고,
응답의 세 ID·revision이 위 참조와 일치할 때 현재 시설 상태를 갱신한다. recovery 요청에는
revision 필드가 없다. 저장된 옛 대화를 읽는 것만으로 지도를 복원하지 않는다.

## 상태와 실제 행동

- 소유자와 session/revision/CAS 검증은 기존 `FacilityConversationService`를 통한다.
  다른 회원의 세션은 만료·없는 세션과 같은 오류로 응답하며 상태를 노출하지 않는다.
- 라우터 프롬프트에는 현재 시설 검색이 있다는 신호만 넣는다. owner·session ID·카드·필터는
  모델에 전달하지 않는다. 무관한 새 질문은 해당 기능으로 라우팅한다. 프롬프트 버전에는
  시설 문맥을 넣은 경우만 `-facility1`이 붙는다.
- `facility_conflict`와 `facility_expired`는 짧은 오류로 반환한다. 앱은 현재 조건을 복구하고
  발화를 다시 받는다. 예전 “첫 번째”나 찜 명령을 새 목록에 자동으로 적용하지 않는다.
- `bookmark_commands=v1`은 앱의 기존 찜 컨트롤러가 실행할 준비된 명령이다. 서버 단계에서는
  “요청을 준비했어요.”까지만 말한다. 앱은 실제 저장·해제 결과를 기다려 현재 말풍선을 바꾼다.
  서버에 저장된 채팅은 준비 시점 응답을 보존하며 앱의 찜 완료 문구로 사후 수정하지 않는다.
- 공통 연결의 참조 대상은 일반 시설 검색이다. 찜 목록 자체의 조건 검색·탭 전환은 기존
  시설 화면의 [찜 범위 대화](saved-conversation.md)를 사용한다. 이 연결에서는
  `saved_search`를 협상하지 않는다.

## 응답 길이

기본은 실행한 행동을 알리는 한 문장이다. “조건에 맞는 카페 3곳 찾아뒀어요!”,
“이미 아는 곳을 반영해 새 후보 2곳 찾아뒀어요!”처럼 결과 수·실제 행동만 말한다.
실패나 조건만 변경한 경우에는 목록이 그대로임을 함께 알린다. 명시적인 사실·선택 이유
질문에는 기록된 근거로 짧게 답한다. 지원하지 않는 조건을 제외할지 확인할 때는 적용할
후보 조건을 보존해서 보여준다. 길이를 줄이려고 확인 대상이나 실패 사실을 숨기지 않는다.

`edit_only` 이후 “아는 곳” 교체에서 예전 조건의 카드가 남던 문제도 수정했다. 부분 교체는
기존 검색 스냅샷이 현재 필터와 찜 상태에 일치할 때만 허용한다.

## 적용 순서와 검증

1. DEV의 backend와 place 변경을 먼저 배포한다. 기존 클라이언트의 계약은 유지된다.
2. 같은 서버의 시설 API와 공통 요청 계약을 확인한 뒤 APP 출시 빌드를 배포한다.
   앱은 debug/release에서 시설 v2를 켜며 debug만 `-PfacilityConversation=false`로 끌 수 있다.
3. 실제 회원·지도·찜 저장과 긴 요청의 게이트웨이 시간 제한은 배포 환경에서 확인한다.
   이 PR 자체는 배포나 스토어 업로드를 수행하지 않는다.

로컬 회귀: `tests/place/api/test_assistant_conversation.py`는 실제 공통 API→시설 gateway→
Place ASGI 경계를 연결하고 공급자·검색·세션 저장소만 대체한다. 동일 세션·재시도·타 회원
차단·관리자 차단·입력 검증·프롬프트 경계를 검사한다. K 교체 회귀는
`tests/place/conversation/test_candidates.py`에 있다. 운영 Gemini·Redis·DB 실행 검증과는 구분한다.
