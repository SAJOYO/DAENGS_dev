# 점령지 방문 인증 워킹 스켈레톤

이 문서는 산책 중 인앱 카메라로 남긴 한 번의 방문 증거가 비동기 사진 판정을 거쳐
`VerifiedVisit`이 되는 PR1 계약을 설명합니다. 아직 “누가 점령했는가”를 결정하지 않습니다.

## 경계

- `place-search`는 현행 140u 중립 게임판과 점령지 좌표를 소유합니다.
- `backend`는 회원별 촬영 시도, 10m 위치 판정, 사진 판정 상태, 검증 완료 사실을 소유합니다.
- 앱은 카메라 화면에서 얻은 사진만 직접 저장소에 올립니다. 앨범 첨부 계약은 없습니다.
- VLM 워커와 점령/갱신/해제 규칙은 후속 PR에서 이 계약을 소비합니다.

촬영 시점에는 서버 `walks` 행이 아직 없습니다. 현재 산책 API는 산책 종료 뒤 완성된 기록을
만들기 때문입니다. 따라서 PR1은 서버 `walk_id` 대신 앱 로컬 산책의
`client_session_id`를 기록합니다. 산책 종료 요청이 이 식별자로 방문들을 서버 Walk에 연결하는
것은 후속 작업입니다. 존재하지 않는 `walk_id` 아래에 촬영 API를 두지는 않습니다.

## 요청 흐름

1. 앱이 촬영 순간의 `client_capture_id`, `client_session_id`, 선택한 `site_id`, 시간, GPS,
   mock-location 표시, 사진 MIME을 `POST /app/territory/attempts`로 보냅니다.
2. backend가 `place-search`의 50m 주변 조회로 해당 현행 140u 점령지 좌표를 가져오고,
   서버에서 다시 직선거리를 계산합니다. `계산 거리 + GPS accuracy ≤ 10m`이고 mock 위치가
   아닐 때만
   `PENDING_UPLOAD` 시도를 만들고 직접 업로드 티켓을 반환합니다.
3. 앱이 티켓 주소로 JPEG 또는 WebP 사진을 올린 뒤
   `POST /app/territory/attempts/{attempt_id}/confirm`을 호출합니다.
4. backend는 1~12 MiB 객체의 Content-Type, 크기와 storage generation을 확인·고정하고
   `VISION_PENDING`으로 바꿉니다. 요청 안에서 사진을 열거나 VLM 응답을 기다리지 않습니다.
   앱은 티켓의 `upload_headers`를 그대로 보내야 하며, 그 안의 generation-match 조건 때문에
   이미 올라간 객체를 덮어쓸 수 없습니다.
5. 후속 VLM 워커는 고정된 generation만 읽고 서비스 경계 `record_vision_decision`에 결과를
   기록합니다. 앱은
   `GET /app/territory/attempts/{attempt_id}`로 상태를 조회합니다.
6. 서비스는 판정 사실을 먼저 DB에 commit한 뒤 원본을 0바이트 tombstone으로 조건부
   치환합니다. 저장소 정리가 실패해도 판정은 남고, 같은 결과 재시도로 정리만 이어집니다.

## 상태와 사실

| 상태 | 의미 | 사진 |
|---|---|---|
| `PENDING_UPLOAD` | 위치 10m 통과, 업로드 대기 | 아직 없거나 임시 보관 |
| `VISION_PENDING` | 파일 확인 완료, 비동기 판정 대기 | 임시 보관 |
| `VERIFIED` | 강아지 사진 판정 통과 | 원본 정리 진행 또는 완료 |
| `REJECTED` | 강아지 사진 판정 불통과 | 원본 정리 진행 또는 완료 |
| `FAILED` | 기술적 판정 실패, 새 촬영 필요 | 원본 정리 진행 또는 완료 |

`TerritoryAttempt`는 재시도와 판정 과정을 담는 상태 원장입니다. `VerifiedVisit`은 보수적인
10m 위치 조건과
사진 판정을 모두 통과했을 때만 별도 행으로 생기는 사실입니다. 반경 진입이나 업로드 완료를
점령 완료로 간주하지 않습니다. 세션 비정상 종료가 있어도 이미 만들어진 시도는 단건 조회로
복구할 수 있고, 같은 회원의 같은 `client_capture_id` 재전송은 같은 증거일 때만 멱등입니다.

## 의도적으로 아직 정하지 않은 것

- `VerifiedVisit`을 실제 점령·방어·갱신 상태로 바꾸는 게임 정책
- VLM 공급자, 모델, 큐, 지연 목표와 재시도 횟수
- 사진 촬영 시각의 허용 지연과 서버가 발급한 산책 세션에 촬영을 결합하는 방식
- 종료된 서버 Walk와 `client_session_id`를 연결하는 방식
- 오래된 `PENDING_UPLOAD`/`VISION_PENDING` 시도 정리 주기

이 값들을 PR1에서 임의로 확정하지 않습니다. 현재 위치·시각·mock 표시는 앱이 제출한
attestation이며 서버 관측값은 아닙니다. 따라서 후속 점령 정책은 서버 산책 세션 결합 전의
`VerifiedVisit`을 단독 소유권 근거로 사용하면 안 됩니다. 결과를 나중에 설명할 수 있도록 판정
당시 점령지 좌표, 계산 거리, GPS 정확도, 사진 generation·크기, VLM 모델과 버전을 원장에
보존합니다. 사진 원본은 판정 commit 뒤 tombstone으로 치환합니다.

## 오류 계약

위치나 증거 충돌은 HTTP 409의 `detail.code`로 구분합니다.

- `mock_location`: OS가 mock으로 표시한 위치
- `site_not_nearby`: 촬영 위치 주변의 현행 게임판에서 점령지를 찾지 못함
- `outside_capture_radius`: 서버 계산 거리가 10m 초과
- `insufficient_location_accuracy`: 계산 거리와 GPS 오차의 합이 10m 초과
- `capture_id_conflict`: 같은 재시도 키로 다른 증거를 보냄
- `photo_not_uploaded`: confirm 시 사진이 없음
- `invalid_photo_size`: 사진이 비어 있거나 12 MiB 초과
- `photo_content_type_mismatch`: 객체 Content-Type과 발급 형식이 다름

게임판 또는 저장소가 일시적으로 불가능하면 503, 없는 시도와 다른 회원의 시도는 같은 404를
반환합니다.
