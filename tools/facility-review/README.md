# 시설 검색 웹 검토판

현재 Android debug 시설 화면의 검색창·로봇 전환·2행 카테고리·반경·주차·반려견 선택·지도·
접고 펼치는 카드를 웹으로 대응시켰다. 실제 Android/네이버 SDK 실행은 아니다.
지도는 Leaflet/OpenStreetMap이며 외부 지도·전화 링크를 제외한 Journey 동작은 이 범위에 없다.

## 실행

저장소 루트에서:

```powershell
npm --prefix tools/facility-review ci
```

`backend/`에서:

```powershell
uv sync --extra place --frozen
uv run python ../tools/facility-review/serve.py
```

접속: http://127.0.0.1:8766/ . loopback에만 바인딩한다.

## 실제 서버

`connection.example.json`을 같은 폴더의 `connection.local.json`으로 복사하고 개발 API 주소와
유효한 앱 계정 access token을 로컬에서 입력한다. 이 파일은 git에서 제외된다.
토큰은 서버에만 있고 브라우저 자산·설정 API·요청 trace에 노출하지 않는다.
로그인/토큰 발급 화면은 이 검토판 범위에 없다. 같은 연결의 기존 `GET /app/pets`에서
프로필 목록을 자동으로 받는다. Android의 `MainActivity → PlacesRoute` 프로필 전달을
웹 호스트에서 대응한 것이다. 시설 화면에서는 함께 갈 아이만 선택하며 프로필 입력·수정은 없다.
후속 검색 상태가 계정에 귀속되므로 시설 AI와 프로필 API 모두 앱 계정 토큰을 사용한다.

실제 서버에는 이번 backend와 Place 코드가 둘 다 반영되어 있어야 한다. 설정 파일이 있다는
것만으로 서버 연결/배포 성공을 표시하지 않는다. 401·404·timeout 등은 그대로 오류 처리한다.
실제 모드가 기본이며 자동 검색하지 않는다. 새 검색에 따라 실제 모델 호출 비용이 발생한다.

## 저장 표본 모드

왼쪽 데이터 연결에서 명시적으로 선택한다. 화면 상단에 **AI/DB 호출 없음**을 계속 표시한다.

- `places.fixture.json`: Geo `c5d2b7f`의 `app/static/place_ui_lab/fixtures.json` 중
  `gangnam-baseline` 공개 응답. 강남 카페·식당 표본이며 전체 지역·전체 업종의 데이터가 아니다.
- `fixtures.py`: 고정 문장의 의미 제안을 사용하고, 기존 production intent/planning과 새 시설
  consumer 및 다견 평가를 실행한다. DB는 저장 표본 조회로 대체한다. 실제 모델 품질이나
  PostGIS 쿼리 검증을 대신하지 않는다. 일반 검색도 같은 표본만 사용한다.
- 고정 예시 이외의 AI 문장은 오류로 안내한다. 자연어를 이해한 것처럼 임의 결과를 만들지 않는다.
- `싼 카페` → `가까운 곳` 선택 → `이 방향으로 검색`으로 보완·확정 흐름을 검토할 수 있다.
  기존 dev의 `resolve_search_facet`·`confirm_search_lens`를 실제 실행한다. 모델 재호출은 없다.
- 실제 경로와 같은 Backend 후속 상태 서비스를 사용하며, 저장 표본에서만 Redis를
  TTL·최대 256항목의 메모리 저장소로 대체한다. 브라우저별 HttpOnly 쿠키로 표본 검색을 분리한다.
- 넓은 반경으로 바꿔도 보유 표본이 늘지 않는다. 다른 위치에서 빈 결과가 나와도 실제 시설이
  없다는 의미가 아니다.
- `profiles.fixture.json`: 기존 PetListResponse 형식의 가상 계정 프로필을 함께 불러온다.
  선택하지 않은 상태가 기본이며 대표 반려견을 자동 선택하지 않는다. 선택한 프로필의
  체중·생일·updated_at을 자동으로 snapshot에 옮긴다. 가족이 된 날은 나이로 계산하지
  않으며 dog_size는 추정하지 않는다. 실제 계정 정보나 사용자가 입력할 설정이 아니다.
- 카테고리는 앱과 같은 이름·아이콘의 62×55 타일을 2행으로 배치한다. 필터 순서는
  반려견 → 반경 → 주차 우선 → 검색 조건이며 프로필·반경 창도 앱 구성을 따른다.

## 확인 범위와 명령

```powershell
# repo backend/에서: 변경한 흐름 + 기존 discovery/API/프로세스 경계
uv run pytest -q tests/place/place/discovery tests/place/api tests/place/test_boundary.py tests/test_facility_discovery_api.py tests/test_main_stays_light.py

# repo 루트에서: 브라우저 상태 회귀 / 실제 Chrome headless 동작
npm --prefix tools/facility-review test
npm --prefix tools/facility-review run test:browser
```

브라우저 검증은 서버(8766)가 실행 중이어야 한다. 테스트는 Windows의 Chrome 기본 설치
경로를 사용한다. 테스트 캡처는 `screenshots/`에 쓰고 git에서 제외한다.
검색·다견 평가·반경 에코·카테고리 충돌·미지원 필수조건·늦은 응답·일반 이름 검색·작은 화면을
검증한다. production provider/DB 호출은 하지 않는다.

이번 작업에서는 배포·실제 개발 계정·Gemini·PostGIS를 함께 쓰는 종단 검증은 아직 하지 않았다.
Android의 AI 호출 구현과 release 전환은 다음 작업 범위다. 웹의 최초 검색·보완·확정은
구현했고, 운영 상태 저장은 Backend의 기존 `REDIS_URL`을 사용한다.

### 2026-09-06 로컬 검증 결과

- discovery·Place API·프로세스 경계·일반 검색·assistant API 회귀: 148 passed, 7 skipped.
  아래 묶음을 실행했다. 7개 skip은 PostGIS 연결 없음이며 DB 통합 성공으로 세지 않는다.

  ```powershell
  uv run pytest -q -rs tests/place/place/discovery tests/place/api tests/place/test_boundary.py tests/test_facility_discovery_api.py tests/test_main_stays_light.py tests/place/place/test_search_v2.py tests/place/place/test_name_search.py tests/test_assistant_api.py
  ```

- 이후 HTTP 오류 변환을 router로 옮기고 에코 불일치 검사를 추가한 변경: `tests/test_facility_discovery_api.py` 7 passed.
  위 전체 묶음과 중복되는 테스트이므로 개수를 합산하지 않는다.
- 프로필 전달 수정 후 JS 상태 테스트 7 passed, 웹 호스트 프로필 API 테스트 2 passed.
  `uv run pytest -q ../tools/facility-review/test_serve.py`로 기존 인증 API 조회와
  연결 변경 시 선택 분리, 오류 시 표본 미대체를 확인했다.
- 실제 Chrome headless: AI 검색·개별 평가·반경 변경·카테고리 충돌·미지원 필수조건·조용함 안내·
  오래된 응답 무시·일반 이름 검색·모바일 가로 넘침 없음·실서버 오류 시 표본 대체 없음 확인.
- 변경 Python 파일 ruff check/format, JS syntax, git diff whitespace 검사 통과.
- 소스 호출자는 가짜 Gemini/DB 또는 명시된 저장 표본이다. 실서비스 품질·실제 로그인·DB SQL
  실행·배포 성공을 이 결과에서 주장하지 않는다.

### 후속 선택 연결 검증

Geo의 기존 실호출 기록은 [Gemini holdout](https://github.com/rkbuhtig/DAENGS_geo/blob/c5d2b7f/docs/research/2026-09-02-place-intent-gemini-holdout.md)에 있다.
이번에는 그 검증을 반복하는 대신 새 후속 연결과 영향을 받는 계약을 확인했다.

- Backend/Place/기존 confirm·facet/프로세스 경계/웹 호스트: 103 passed, 4 skipped.
  skip은 PostGIS 연결이 필요한 테스트다. 실행 명령은 backend에서 다음과 같다.

  ```powershell
  uv run pytest -q tests/place/place/discovery tests/place/place/intent/test_confirmation.py tests/place/place/intent/test_lenses.py tests/place/api tests/place/test_boundary.py tests/test_facility_discovery_api.py tests/test_main_stays_light.py ../tools/facility-review/test_serve.py
  ```

- JS 상태 검사 8 passed. 원래 요청·검색 ID·revision·선택 요청이 어긋난 응답을 거부한다.
- Chrome에서 비용 보완 → 방향 확정, 기존 프로필·반경 유지, 응답 유실 뒤 동일 요청 재시도,
  검색어 변경 중 늦은 후속 응답 무시, 미지원 옵션 비활성화를 확인했다.
- Redis는 운영용 저장소를 구현했고 CAS 서비스의 동시 요청은 메모리 저장소로 검증했다.
  이번 로컬 실행에서 실제 Redis에 연결한 통합 검증·서버 배포는 수행하지 않았다.
