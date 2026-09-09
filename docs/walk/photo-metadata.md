# 사진 메타데이터와 일기 입력 연결

Geo → Dev 이관 2단위. [Dev #372](https://github.com/SAJOYO/DAENGS_dev/pull/372) /
[App #235](https://github.com/SAJOYO/DAENGS_APP/pull/235).
[1단계 계약](diary-contract.md) 다음으로 사진 전송과 실제 저장 모델의 입력 변환을 구현한다.
이미지 파일·다운로드 URL·이미지 해석은 이 API에 없다.

## 전송 계약

모든 경로는 기존 App 인증과 산책 소유권 경계를 사용한다.

| 경로 | 동작 |
| --- | --- |
| GET `/app/walks/photo-metadata/capabilities` | `write_versions`, `max_records=200`. 비활성이면 선택적 DB 표를 읽지 않는다. |
| GET `/app/walks/{walk_id}/photo-metadata` | 승인된 목록, 개별 사진 revision, 삭제 tombstone을 읽는다. |
| PUT `/app/walks/{walk_id}/photo-metadata` | 촬영 기기의 현재 사진 목록을 CAS로 교체한다. |

요청은 `format=walk-photo-metadata-v1`, `publisher_id`, 단조 증가하는 `revision`,
마지막 승인된 `expected_revision`, `photos[]`다. 사진은 UUID, `captured_at`,
`location_captured_at`, `point{lat,lng}`, `accuracy_m`만 보낸다.
촬영은 산책 시간 범위 안이어야 하고 위치 샘플은 촬영보다 미래일 수 없다. 중복 ID는 거부한다.

응답은 App의 `client_session_id`, publisher/revision, `records[]`다.
한 번도 목록을 받지 않았으면 `not_available`; 0개인 명시적 목록도 `complete`다.
`complete`는 **서버가 마지막으로 승인한 버전**이며 기기의 오프라인 편집까지 모두 도착했다는
뜻이 아니다. 후속 일기 생성에서 App 승인 버전과 입력의
`photo_manifest{publisher_id,revision}`을 맞춰야 한다.

## 재전송·삭제·복구

- 같은 publisher/revision/요청 해시는 응답 유실 재전송으로 받아 같은 결과를 반환한다.
  새 버전은 현재 서버 revision과 expected revision이 같을 때만 반영한다.
  다른 publisher, 같은 버전의 다른 내용, 늦게 도착한 옛 요청은 409다.
- 목록에서 빠진 사진은 개별 revision이 전진하고 `content=null` tombstone이 된다.
  위치·촬영 시각을 tombstone에 남기지 않으며 삭제 ID의 재사용은 거부한다.
- 한 산책의 누적 사진 ID 상한은 tombstone 포함 200이다. 조용히 자르지 않는다.
  행동/메모 200개와 사진 200개를 보존하는 입력 상한에 맞췄다.
  상한 초과와 산책 범위 밖 촬영 시각은 **422 입력 거절**이며 저장 버전은 바뀌지 않는다.
  게시자·버전 충돌과 삭제 ID 재사용의 409와 구분한다([후속 보완 #374](https://github.com/SAJOYO/DAENGS_dev/pull/374)).
- 기존 Walk 행 잠금 아래에서 소유권 확인과 CAS를 수행한다. 계정·산책 삭제는 FK cascade로
  메타데이터까지 삭제한다.
- App Room 12→13은 `walk_photo_sync`만 추가하고 기존 기록을 유지한다.
  기기에서 새 산책을 시작하면 빈 목록도 게시할 수 있는 publisher를 만든다.
  마이그레이션에서는 사진이 실제 있는 세션만 등록한다.
  서버에서 복원한 빈 세션은 publisher를 만들지 않으므로 다른 기기의 사진을 지우지 않는다.
  출처를 확인할 수 없는 옛 빈 세션은 미수집으로 남을 수 있다.
- 사진 저장/삭제와 목록 revision 증가가 같은 Room 트랜잭션이다.
  네트워크 호출 전에 본문을 `pendingPayload`로 고정한다. 프로세스 종료나 응답 유실 뒤에도
  같은 요청을 먼저 확인하고, 전송 중의 새 편집은 다음 요청으로 보낸다.
  전송 예약 실패가 이미 저장된 사진을 되돌리지 않는다.
  App은 새 요청을 고정하기 전에 capability의 사진 수 상한을 확인한다. 422를 받으면
  소유자·고정 본문·마지막 승인 버전이 일치할 때만 고정을 해제한다. 승인 버전을 전진시키지
  않으므로 삭제 등 최신 편집을 같은 기준 버전에서 다시 보낼 수 있다. 응답을 기다리는 중
  이미 편집됐으면 같은 실행에서 이어 보내고, 편집이 없으면 추가 재시도 없이 거절로 끝낸다.
- 기존 `GPS 확정 → 기록 → 사진 메타데이터 → storyboard 동기화`를 사용한다.
  WorkManager가 네트워크 실패를 재시도하고 앱 시작 때 대기 목록을 복구한다.
  capability 404/비활성은 전송만 건너뛰고 미전송 원본을 유지한다.
  409는 영구 충돌로 남기며 publisher를 바꾸거나 강제로 덮어쓰지 않는다.

## 실제 저장 입력 어댑터

`services/walk_diary_input.py:read_input()`은 호출자의 트랜잭션에서 소유한 Walk를 잠그고
entry·pin sidecar·확정 분석·사진 manifest·저장 배경을 읽는다.
호출 전 미반영 변경은 flush해야 한다. identity map을 비워 이전 생성 때 읽은 메모/핀을
재사용하지 않는다. HTTP/LLM 호출이나 새 generation 예약은 하지 않는다.

v1은 원문·이벤트 시각·위치 샘플 시각·정확도를 보존한다. v2 읽기가 활성화되면 내용 revision과
pin revision을 분리하고 원본 pin의 정책·불확실성도 입력에 보존한다. 추정·미확정·위치 없음
상태를 실제 GPS 관측으로 바꾸지 않는다. 사진의 `app-private-photo:<id>`는 원본 연결 참조이며
파일이 다른 기기에 존재한다는 보장이 아니다.

v1 행동을 v2로 수정하면 서버가 `legacy-v1`, `pin_revision=0`, 빈 `source_refs`인
sidecar를 남긴다. 입력 어댑터는 이 형식을 별도로 검사하며 원본 기록 ID·좌표·시각과
맞는 경우만 읽는다. 원본 샘플 시각·정확도를 보존한 `legacy/last_known` 앵커로 표시하고
GPS 근거를 만들어 넣지 않는다. 새 v2 핀의 source 참조 검사는 유지한다.
해당 legacy 핀에 묶인 v2 배경도 정확한 원본 핀·샘플 시각·위치가 일치해야 재사용한다.

`walk-entry-context-v1/v2`는 대상 revision·좌표·시각·payload hash가 맞는 것만 보존한다.
잘못되거나 오래된 봉투는 원문을 출력하지 않는 제외 사유와 함께 분리한다.
v2 주변 정보 수집은 이미 머지된 Dev #371을 재사용한다. sidecar가 있으면 v2 정책으로
조회하고 pin revision·원본 pin·위치 산출 방법도 맞춘다. 위치 없는 핀에 옛 원본 좌표를
대입하지 않으며 v1 봉투를 v2용으로 재라벨하지 않는다. 핀이 없는 v2 메모도 구분한다.
[스탬프 준비 단위](diary-stamps.md)에서는 검증된 현재 `known/partial` 봉투를
`selected_background_ids`에 사용 가능한 입력으로 지정하고 조각 선택은 스탬프 코어가 한다.
후속 [확정 동선 관측 공급](diary-observations.md)에서 `read_input()`은 저장 chunk와
확정 분석을 검증해 체류·상대 속도 후보와 원본 출처를 함께 전달한다.
유효한 분석이 없으면 관측은 비우고 `evidence_origin=unknown`으로 유지한다.
출발 시 날씨를 모든 장면의 날씨로 확장하지 않는다.

## 적용과 검증

1. Dev #370을 먼저 반영한다. #372는 그 PR 위에 쌓은 후속 PR이다.
2. 운영에는 `2026-09-09_walk_photo_manifests.sql`과 짝 verifier를 적용해야 한다.
   init 파일만 커밋해도 기존 DB는 바뀌지 않는다.
3. 적용 확인 후 `DAENGS_WALK_PHOTO_METADATA_ENABLED=true`로 연다.
   기본값은 false다. 이번 작업에서 운영 migration/설정 변경/배포는 하지 않았다.
4. App #235가 capability 협상 후 기존 동기화 경로로 메타데이터를 보낸다.

로컬: 사진 전송·입력·계약·기존 storyboard 대상 76개 통과, PostgreSQL 3개는 격리 DB
미설정으로 skip. Dev #371 반영과 v2 문맥 어댑터 보강 뒤 직접 영향받는 5개 파일에서
95개 통과(앞선 검사와 겹치는 항목을 포함한다).
수정 Python Ruff, migration 이름/짝/검증 등록 검사 통과.
App 대상 5개 클래스는 Kotlin 컴파일·Room 스키마 생성과 함께 통과했다.
전체 로컬 스위트나 실사용 사진/LLM 호출은 수행하지 않았다.
실제 PostgreSQL의 멱등·CAS 경쟁·계정 삭제 검사는 기존 walk DB CI에
`tests/test_walk_photo_db.py`로 추가했다. CI 결과는 PR 상태를 따른다.

후속 **사용자 기록 중심 선택·스탬프 코어**는 [diary-stamps.md](diary-stamps.md),
실제 동선 후보 연결은 [diary-observations.md](diary-observations.md)에 정리한다.
기존 `walk_storyboard.generate()`의 새 일기 분기는 [diary-generation.md](diary-generation.md)에
연결한다. App 화면 전환은 별도이며 서버 기본 활성화 값은 false다.
