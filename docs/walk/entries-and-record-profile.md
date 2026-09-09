# 산책 기록과 산책 기록 프로필 v0

위치 없는 행동과 별도 핀 확정은 [v2 서버 저장 구현](action-pin-v2-storage.md)을 참고한다.
기본 비활성 기능이며, 아래는 기존 v1 계약이다.

2026-09-05 구현. 앱 연결 PR: [DAENGS_APP#144](https://github.com/SAJOYO/DAENGS_APP/pull/144). 서버 PR: [#251](https://github.com/SAJOYO/DAENGS_dev/pull/251).

후속 명세: [행동 즉시 기록·핀 위치 추정 공통 계약](action-pin-location-contract.md),
[DEV #354](https://github.com/SAJOYO/DAENGS_dev/pull/354). 아래 v1 구현을 대체한 상태가 아니라
별도 v2 표현과 위치 확정 경계를 정의하는 문서다.

## 기록 계약

`/app/walks/{walk_id}/entries` GET은 현재 기록과 최소 삭제 표식을 반환한다. `/{entry_id}` PUT은 생성·수정, DELETE는 제거다. 기존 GPS finalize의 의미와 보관 수명은 변경하지 않는다.

PUT 본문:

```json
{
  "expected_revision": 0,
  "mutation_id": "00000000-0000-4000-8000-000000000001",
  "content": {
    "vocabulary_version": "walk-behavior-v1",
    "kind": "behavior",
    "behavior_code": "sniffing",
    "recorded_at": "2026-09-05T09:00:00Z",
    "location": {"lat": 37.5, "lng": 127.0, "captured_at": "2026-09-05T09:00:00Z", "accuracy_m": 5},
    "pet_id": null,
    "note": null
  }
}
```

`sniffing=킁킁`, `excretion=배설`, `barking=짖기`. 대소변 세분화, 행동 원인·감정·실제 횟수는 이 기록의 의미가 아니다. 메모는 `kind=note`, `behavior_code=null`, `pet_id=null`, 비어 있지 않은 `note`(최대 2000자)로 보낸다. 메모 위치는 없어도 된다. 행동에는 위치가 필요하다.

관측 시각·위치·kind는 수정할 수 없다. 행동 종류와 대상 반려견, 메모 본문은 정정할 수 있다. 기록 시각은 산책 시각 범위 안이어야 하고, 선택한 반려견은 내 반려견이며 그 산책의 동행이어야 한다.

응답은 `id/revision/mutation_id/content`. 최초 expected_revision은 0이고 성공 시 revision이 증가한다. 같은 mutation의 같은 내용 재전송은 중복 생성하지 않는다. 다른 수정 버전은 409이며 클라이언트가 내용을 확인해야 한다.

DELETE는 query에 `expected_revision`과 `mutation_id`를 넣는다. payload를 비운 삭제 표식이 남아 지연된 생성/수정으로 부활하지 않는다. 표식에는 본문/좌표가 없다. 산책/계정 삭제는 FK로 함께 제거한다. 남의 산책은 404, 미인증은 401이다.

## 산책 기록 프로필

`POST /app/walks/record-profile/query`에 `pet_id`, 선택적인 timezone 포함 `since/until`을 전달한다. 산책 시작 시각으로 `[since, until)`을 선택한다. 현재 구현은 같은 snapshot 안의 집계와 근거를 **한 응답에** 반환한다. 근거 페이지 API는 아직 제공하지 않는다.

- `profile_version=walk-record-profile-v0`, `vocabulary_version=walk-behavior-v1`.
- `walk_count`: 선택한 반려견이 동행한 보관된 완료 산책 수. 행동이 없는 산책 포함.
- `behaviors`: 세 코드별 `entry_count`, `walks_with_entries`.
- `unassigned_entry_count`: 선택 산책에서 어느 반려견인지 미지정된 행동 수. 개별 행동 집계에서 제외.
- `source_revision`: 선택 조건·산책 집합·기록 revision의 fingerprint.
- `evidence`: 대상이 확인된 행동의 ID/revision/산책/위치/시각. 메모 본문 제외.

두 산책에 킁킁 기록이 2개와 1개라면 entry_count=3, walks_with_entries=2다. 실제 킁킁 횟수가 아니다. 삭제/정정 후 최신 조회는 현재 기록에서 다시 계산한다. 성격·선호·개인화 문장·추천 정책은 구현하지 않았다.

## 현재의 맥락 경계

외부 주변 정보 수집은 아직 실행하지 않는다. 모든 근거에 `context_status=not_requested`, `context_refs=[]`를 반환한다. 현재 날씨나 가까운 시설을 산책 당시의 행동 원인으로 만들어 넣지 않는다. 행동별 조회 원천·범위와 산책 후 일기 출력은 다음 별도 작업이다.

## DB 반영과 검증

빈 DB: `db/init/19_walk_entries.sql`. 기존 DB: `db/migrations/2026-09-05_walk_entries.sql`을 운영자가 해당 DB에 적용해야 한다. 새 테이블을 만든 뒤 서버 코드를 배포한다. 이 PR은 운영 DB 적용이나 배포를 수행하지 않았다.

`test_walk_entries.py`는 재전송·삭제·귀속·프로필 집계를, `test_walk_entry_http.py`는 실제 라우터/서비스/응답 직렬화를 fake 저장소로 검증한다. 실제 PostgreSQL migration/동시 요청 검증을 대신하지 않는다.

