# 자동 장면의 지도 위치

서버는 장면을 고를 때 사용한 **실제 GPS 관측 지점**을 v4 `scene.observation`으로 전달한다.
앱 구현: [DAENGS_APP#192](https://github.com/SAJOYO/DAENGS_APP/pull/192).

```json
{"client_seq":30,"chain_index":0,"at":"2026-09-07T00:09:00Z","lat":37.5,"lng":127.00339}
```

위 값은 계약 예시다. 순번은 업로드 원본의 client_seq이고, chain_index는 원본의 pause/resume
구간이다. selector가 만든 block 번호나 앱 표시 선분 번호와 같다고 가정하지 않는다.

## 생성과 표시

- canonical segment의 실제 끝점에 원본 식별자를 붙이고, 속도/거리 보충 장면을 고를 때 보존한다.
- 출발/도착 장면은 첫/마지막 **유효 관측 구간의 끝점**을 대표 위치로 쓴다.
  세션 시작/종료 시각과 GPS 관측 시각은 다를 수 있다. 실제 출발/도착 위치를 별도로 추측하지 않는다.
- 관측 공백 장면에는 observation을 넣지 않는다. 유효 구간이 없으면 출발/도착도 null이다.
- 사용자 행동 위치/시각으로 환경 조회 anchor를 만든 경우 근처 GPS의 식별자를 빌려 쓰지 않는다.
  이 자동 환경 장면은 observation=null이며, 사용자 행동 장면은 앱의 원본 entry 위치를 사용한다.
- 앱은 선택한 세션 원본에서 순번·chain·시각을 대조하고, 좌표는 직렬화 오차 1m 이내만 허용한다.
  불일치·원본 누락·중복 순번은 위치 없는 카드로 남긴다. 같은 장소의 다른 시점으로 대체하지 않는다.
- 표시용 동선이 생략한 원본 점이라도 확인된 관측 위치 자체를 사용한다. 경로상 거리로 역산하거나
  표시 선에 스냅해서 관측 좌표를 바꾸지 않는다. 서버와 앱의 표시 필터가 다르면 선과 약간 떨어질 수 있다.

## 호환성과 저장

`walk-storyboard-candidates-v4`는 v3의 제목과 근거를 유지하고 각 scene에 필수 키
`observation: object | null`을 추가한다. 같은 bundle JSONB에 저장하므로 스키마 변경은 없다.
제목 생성은 이 필드를 보존하며 LLM 입력에는 추가하지 않는다. 위치를 LLM이 선택하지 않는다.

v1/v2/v3 요청에는 observation을 제거해 엄격한 파서를 유지한다. 기존 캐시를 읽는 것만으로
새 분석을 돌리지 않는다. 이전 형식으로 저장된 일기에 자동 위치를 붙이려면 사용자가
명시적으로 장면을 재분석해야 한다. 앱은 형식 enum 거절에만 v4 → v3 → v2로 협상한다.

## 검증 자료

`tests/walk/fixtures/v4-observations.json`은 **합성 왕복/일시정지/GPS 공백 자료**다.
실제 사용자 기록이 아니며, 생산 측정/선택/builder 함수로 만든 v4 결과와 원본 81개를 담는다.
동일 파일을 앱 테스트에 사용한다. 장면 7개 중 관측 위치 6개이며 공백 장면은 위치가 없다.
서버 테스트가 원본에서 재계산한 bundle과 파일을 비교하고, 앱은 실제 Room에 넣은 원본을 읽어
번호 핀/카드에 연결한다. UI 테스트의 지도 표면은 대역이며 실기기 NAVER 지도 검증과 구분한다.

2026-09-07 대상 검증: 아래 3개 파일의 39개 테스트 통과. 변경 Python 파일 8개의
Ruff 검사와 포맷 검사도 통과했다. 운영 DB와 실제 LLM 호출은 수행하지 않았다.

```powershell
uv run --no-sync pytest tests/walk/test_storyboard_observations.py tests/walk/test_walk_storyboard.py tests/walk/test_walk_storyboard_titles.py -q
```
