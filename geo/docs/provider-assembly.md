# 공급자 조립 현황

> 이 문서는 "코드가 존재한다"와 "현재 제품에 꽂혀 있다"를 구분한다. 지도 중심 서비스라
> 공급자를 바꿔 실험할 수 있게, **현재 조립**, **선택 이유**, **교체 지점**, **검증 결과**를 같이 남긴다.

최종 갱신: 2026-08-24 · 관련 결정: [#11, #13, #21, #39~#44, #46~#51](decisions/README.md)

결정 #51 이후 경로 어댑터와 진실성 계약은 유지하지만 해당 기능을 현재 제품
차별점으로 활성화하지 않는다. 커뮤니티 근거는 결정 #63 으로 기각해 이 표에서 뺐다. 아래 표의 `fake`·`none`은 이 parked 상태까지 포함한 현재 조립이다.

## 현재 조립

| 표면 / capability | 현재 선택 | 상태 | 실패·미설정 시 | 선택 이유 |
|---|---|---|---|---|
| 웹 인터랙티브 지도 (`/dev`) | **NAVER Dynamic Map** | 키 입력 후 활성 | 키 누락·SDK 로드 실패 시 OSM 개발 폴백 | 가장 먼저 사람이 만지는 지도 표면을 실물로 검증 |
| Android 인터랙티브 지도 | **NAVER Android Map SDK 3.23.3** | 앱 조립·APK 빌드 완료, 실기기 smoke 대기 | key id 누락 시 설정 안내 표면 | 현재 위치·pinned 검색 계약의 기준 구현 |
| 챗봇 카드 정적 지도 | **NAVER Static Map** | 서버 프록시 구현 완료 | `preview_url=null` 또는 프록시 404 | 넉넉한 호출량, 헤더 인증 secret을 서버에 보관 |
| 시설 반경 검색·거리 | **PostGIS + 자체 적재 데이터** | 활성 | 외부 지도 검색으로 대체하지 않음 | 결과 집합을 공급자 검색 순위와 쿼터에 종속시키지 않음 |
| 지오코딩·좌표 복구 | `none` | **보류** | 좌표 있는 시설만 검색 | 약 5% 결손은 복구 가능하지만 현재 검증 범위보다 작업이 큼 |
| 도보 경로 | `fake` 기본, TMAP 어댑터 구현됨 | 실제 공급자 미선택 | estimate | 이번 조립은 지도 표면까지만 |
| 자동차 경로 | `fake` | 실제 어댑터 미구현 | estimate | 이번 조립 범위 밖 |
| 대중교통 경로 | `fake` | 실제 어댑터 미구현 | estimate | 이번 조립 범위 밖 |
| 지도앱 따라가기 | NAVER·Kakao·TMAP 딥링크 | 활성 | 링크만 제공 | 내비게이션을 직접 만들지 않음 |

`fake`는 개발 계산식이고 실제 공급자가 아니다. 응답에서도 `measured`가 아니라 `estimate`로 나간다.

## 설정 조립도

```text
웹 지도       DAENGS_MAP_PROVIDER=naver
                 └─ GET /map/client-config → 공개 key id만 브라우저 전달

Android 지도  DAENGS_NAVER_NCP_KEY_ID
                 └─ BuildConfig → NAVER SDK, 패키지 com.daengs.geo 등록 필요

정적 지도     DAENGS_STATIC_MAP_PROVIDER=naver
                 └─ GET /map/static → 서버가 NAVER 호출 → PNG 반환

시설 검색     PostGIS (공급자 설정과 무관)

지오코딩       DAENGS_GEOCODE_PROVIDER=none
경로           *_ROUTE_PROVIDER=fake

실제 외부 호출 DAENGS_USAGE_POLICY=deny-all | dev
                 └─ 기본 거부. dev도 요청·시간당 고정 한도 안에서만 호출
```

네이버 브라우저 지도에는 `DAENGS_NAVER_NCP_KEY_ID`만 노출된다. 이 값은 SDK URL에 들어가는
공개 식별자다. `DAENGS_NAVER_NCP_KEY`는 정적 지도 서버 호출에만 쓰며 브라우저로 보내지 않는다.
브라우저 SDK 로딩은 공식 문서의 `ncpKeyId` + callback 방식을 따른다:
[NAVER Maps JavaScript API 시작하기](https://navermaps.github.io/maps.js.ncp/docs/tutorial-2-Getting-Started.html).

## 왜 이 조립인가

1. **지도가 메인이라 지도부터 진짜로 만든다.** 검색·정책이 맞아도 지도 표면이 가짜면 마커 밀도,
   화면 이동, 선 겹침 같은 제품 판단을 할 수 없다.
2. **공급자 선택을 묶지 않는다.** 네이버 지도를 골랐다고 지오코딩·검색·도보 경로까지 네이버로
   강제하지 않는다. capability별 설정과 실패 방식은 독립이다.
3. **자체 검색 결과가 기준이다.** 지도는 `results`를 그릴 뿐이고, 공급자가 후보 집합을 다시 만들지 않는다.
4. **실험 폴백은 제품 폴백과 다르다.** OSM은 `/dev`에서 키 없이 UI를 검증하기 위한 장치다.
   운영 지도 공급자 장애를 OSM으로 조용히 감추겠다는 정책이 아니다.

NAVER Web 서비스 URL 불일치는 SDK 로드 실패가 아니라 인증 실패 타일로 나타날 수 있다. 이 경우
OSM으로 조용히 내리지 않고 등록 호스트와 실제 접속 호스트를 맞춘다.

## 공급자 교체 실험 규칙

공급자를 바꿀 때 폴더를 재구성하지 않고 아래만 바꾼다.

1. 이 표에서 실험할 표면 한 칸을 정한다.
2. 해당 provider 어댑터와 환경변수만 변경한다.
3. 같은 시설 `results`·마커 순서·딥링크 입력을 유지한다.
4. 아래 검증표에 날짜, 공급자, 결과를 남긴다.
5. 채택하면 decisions에 한 줄을 추가하고 이 문서의 `현재 선택`을 갱신한다.

비교할 최소 지표:

- 첫 지도 표시 시간과 실패율
- 마커 10~20개 및 폴리라인 여러 개의 가독성
- 모바일 웹에서 지도 클릭·이동·줌 감각
- 정적 카드의 한글 라벨·마커 가독성
- 호출량·비용·도메인 제한 운영 난이도

## 검증 로그

| 날짜 | 조립 | 환경 | 결과 | 남은 일 |
|---|---|---|---|---|
| 2026-08-21 | NAVER Dynamic + Static / PostGIS search | 로컬, 키 미입력 | `/dev` 20곳·마커·지도 클릭 및 OSM 폴백, 브라우저 오류 없음 | 키 입력 후 실제 NAVER 타일·정적 PNG smoke test |
| 2026-08-21 | NAVER Dynamic + Static / PostGIS search | `127.0.0.1`, 실제 키 | Dynamic 타일·20개 마커와 Static PNG 200(34,344 bytes) 확인 | 운영 도메인 등록 후 같은 smoke test |
| 2026-08-21 | NAVER Android SDK / PostGIS search | Android SDK 37, JVM 25 | debug APK·요청 계약 테스트 빌드 성공 | Android 패키지 등록 후 실기기 지도·위치 smoke test |

## 실행 확인

Naver Cloud 애플리케이션에서 Dynamic Map과 Static Map을 활성화하고 개발 Web 서비스 URL을
**포트와 경로 없이** `http://127.0.0.1`로 등록한 뒤 `.env`에 두 값을 넣는다. `localhost`와
`127.0.0.1`은 인증상 다른 호스트이므로, 등록한 쪽과 브라우저 주소를 맞춘다.

```dotenv
DAENGS_MAP_PROVIDER=naver
DAENGS_STATIC_MAP_PROVIDER=naver
DAENGS_GEOCODE_PROVIDER=none
DAENGS_USAGE_POLICY=dev
DAENGS_NAVER_NCP_KEY_ID=...
DAENGS_NAVER_NCP_KEY=...
```

확인 순서:

```text
GET /map/client-config   provider=naver, naver_key_id 존재, secret 없음
GET http://127.0.0.1:8000/dev
                         NAVER 배지와 실제 지도, 클릭 시 출발지 변경
GET /map/static?...      image/png과 검색 결과 마커
```

`DAENGS_USAGE_POLICY`를 생략하면 실제 Static Map·TMAP·OpenAI 호출은 기본 거부된다. `dev`는
무제한 우회가 아니라 프로세스별 제한 정책이다. Static Map 100회/시간, 실측 경로 60회/시간,
OpenAI 파싱 30회/시간이며 요청 하나의 실측 경로는 최대 4회다.

키가 없으면 `/dev` 상단에 `OSM · NAVER key 없음`이 표시되어야 한다.
