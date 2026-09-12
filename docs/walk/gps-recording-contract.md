# GPS 기록 구분의 전달·저장·복원

서버 #441 / [APP #305](https://github.com/SAJOYO/DAENGS_APP/pull/305).
공통 wire 계약의 원본은 이 문서다. `gps-recording-v1`은 앱의 기존
`recordingEligible`을 보존한다. 속도·거리 계산 엔진이나 GPS 인증 정책을 교체하지 않는다.

## 의미와 저장

`WalkPointUpload`와 상세 응답의 `recording_eligible`은 true/false/null이다.
true는 이번 기록 구독의 관측 후보, false는 구독 전 캐시 또는 미래 관측,
null/필드 없음은 과거 자료의 구분 정보 없음이다. true는 정확도 합격이나 방문 인증이 아니다.
앱은 저장한 값을 그대로 보내며 서버는 문자열/숫자를 Boolean으로 강제 변환하지 않는다.

기존 7개 원본 필드와 client_seq/chain_index는 유지한다. 구분이 없는 chunk는 v1,
구분이 있는 chunk는 v2로 저장한다. v2는 기존 cols 뒤에 `eligible`을 추가하고
`recording_policy: gps-recording-eligibility-v1`을 포함한다. 혼합 chunk의 과거 관측은 null이다.
읽기는 v1/v2를 지원하고 모르는 형식·정책은 거부한다. 상세 GET도 구분을 반환하여
새 앱의 Room 복원 시 캐시를 정상 관측으로 바꾸지 않는다.

서버의 기존 원본/분석 `input_fingerprint` v1은 변경하지 않는다. 저장 완료된 분석을
메타데이터 보완 때문에 재생성하지 않는다. 서버 거리·공간 계산 정책이 앱 motion 정책과
같아졌다는 뜻은 아니다. 이번 공통 후보 범위 적용의 소비자는 행동 핀 검증이다.

## 지원 협상과 저장 확인

`GET /app/walks/entry-capabilities`의 `gps_recording_versions`가 `gps-recording-v1`을
반환한다. 기록·핀 v2 활성화 플래그와 별개인 원본 형식 지원이다.
새 앱은 알려진 구분이 있는 원본을 올리기 전에 지원을 확인한다. 없는 서버에는 값을 빼고
보내지 않고 기기에 보관한다. 행동 v2의 기존 읽기/쓰기/관측 cutoff 협상도 유지한다.

산책 POST/append/상세 GET은 `recording_receipt`를 반환한다.

| 필드 | 의미 |
| --- | --- |
| contract_version | gps-recording-v1 |
| policy_version | gps-recording-eligibility-v1 |
| raw_input_fingerprint | 기존 정규화 원본 좌표열 v1 SHA-256 |
| evidence_fingerprint | 아래 구분 정보의 SHA-256 |
| point_count | 현재 저장된 전체 원본 수 |
| known_point_count | 구분이 true/false인 원본 수 |

구분 지문의 canonical UTF-8 JSON은 공백 없이
`{"v":1,"policy":"gps-recording-eligibility-v1","points":[[0,false],[1,true],[2,null]]}`이다.
points는 client_seq 오름차순이다. 두 지문은 `sha256:` 접두사를 쓴다.
원본 지문의 정규화는 `services/walk_finalize.py` v1을 따른다.

앱은 전체 원본의 지문·개수와 구분 지문을 확인한 뒤 finalize/핀 전송을 진행한다.
RAW_UPLOADED와 DERIVED 재시도도 확인을 건너뛰지 않는다. GPS 0개는 빈 좌표열의 지문을 쓴다.
v2 핀 생성·종료 요청에는 확인한 `recording_evidence_fingerprint`를 붙이고 outbox에 동결한다.
서버는 산책 행 잠금 안에서 지문과 원본을 검증한다. ACK 유실 재요청은 기존 mutation receipt가
우선하며, 과거 outbox에 없던 필드를 덧붙여 동일 mutation의 본문을 바꾸지 않는다.
필드가 없던 옛 요청의 request hash도 보존한다.

## 이미 올라간 자료의 보완

`PUT /app/walks/{walk_id}/recording-evidence`:

```json
{
  "contract_version": "gps-recording-v1",
  "policy_version": "gps-recording-eligibility-v1",
  "raw_input_fingerprint": "sha256:cdc7d9aee6984f6a207ac2928e79424c51bdfd3df7dae7b1c5f9ab83c00eb38f",
  "points": [{"client_seq":0,"chain_index":0,"at":"2026-09-11T02:59:30Z","lat":37.5665,"lng":126.9779,"accuracy_m":5.7,"is_mock":false,"recording_eligible":false}]
}
```

points는 신규 좌표 추가가 아니다. 기존 좌표와 지문 정규화 수준에서 같은지 확인한 뒤
누락된 구분값만 채운다. 다른 좌표/원본 지문은 `recording_raw_mismatch`, 알려진 값을 바꾸려는
요청은 `recording_metadata_conflict`로 409를 반환한다. 실제 null을 보완값으로 보내지 않는다.
같은 보완 재요청은 성공하며, chunk 분할과 무관하게 전체 receipt를 반환한다.
모든 요청 값의 검증을 마친 뒤 같은 트랜잭션에서 JSONB를 새 값으로 대입한다.

이미 활성 v2 기록이 있는 산책은 새로운 메타데이터 보완을 보수적으로 잠근다
(`recording_metadata_locked`, 409). 기존 핀이나 동결된 근거를 자동으로 바꾸지 않는다.
같은 값의 재확인은 이 경우에도 가능하다. 이 경계는 앱의 단순 반복 재시도로 해결되지 않으며
원본을 보존한 상태에서 별도 확인이 필요하다. 원본 구분이 폰에도 없으면 값을 추정해 채우지 않는다.

앱은 구분 검증 후, 캐시 제외 자료가 있는 산책에서 최초 생성에 실패한 unlocated 행동의
옛 일반 위치 근거 오류를 한 번 다시 시도한다. 새 검증 실패는 구분되는 오류로 남겨 반복하지 않는다.
다른 종류의 충돌·삭제·이미 수용된 핀은 이 복구 대상으로 삼지 않는다.

## 핀 검증과 배포

false 원본은 unlocated 판정, 원본 위치 매칭, source_refs 검증에서 제외한다.
null은 기존 후보 의미를 유지한다. mock, 관측 cutoff, 원본 좌표 일치 검사는 계속 적용한다.
추정값을 raw GPS나 현장 인증 입력으로 승격하지 않는다.

이 변경은 테이블/컬럼 및 Room schema 변경이 없고 일괄 SQL 이관도 없다.
서버 읽기·보완·검증 지원을 먼저 배포하고 새 앱을 전환한다. v2 핀 쓰기는 기존 서버 플래그로
통제한다. v2 chunk가 생긴 뒤에는 v1만 읽는 옛 서버 바이너리로 되돌리지 않는다.
문제가 있으면 신규 핀 쓰기를 중단하되 v1/v2 읽기와 기존 요청 재시도 지원은 유지한다.

## 검증 경계

공통 사례는 `backend/tests/walk/fixtures/gps-recording-v1.json`이다. 앱 테스트가 실제
WalkApi/PinPending 직렬화로 만든 `app/build/outputs/contracts/gps-recording-v1.json`을
`GPS_APP_CONTRACT_FILE`로 지정해 동일 서버 계약 테스트에 넣을 수 있다.
이 시험은 실제 스키마·JSON codec·HTTP/서비스·핀 검증을 사용하고 저장소는 대역이다.
실제 PostgreSQL의 잠금·commit 내구성, 배포 환경 플래그와 실기기 인증 통신을 검증했다고
해석하지 않는다. 서버 원본/metadata 동시 변경은 기존의 동일 Walk 행 잠금을 사용한다.
