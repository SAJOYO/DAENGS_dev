# 조사: 지도 제공사 요금·쿼터·코드 (카카오 vs 네이버, 2026-08-19)

> 확정 전까지 계속 참조하는 문서. **요금·쿼터는 자주 바뀐다.** 아래는 2026-08-19 확인분. 계약/키 발급 전 콘솔에서 재확인.

## 원칙

- 지도(타일·렌더링·지오코딩·길찾기·정적이미지)는 **빌린다.** 우리가 만드는 건 지도 위의 우리 데이터(POI·검색·영업시간·궤적·구역)뿐
- 백엔드가 제공사를 만지는 곳은 **정적 지도 URL + 지오코딩(적재)** 두 군데 → `MapProvider` 어댑터 3메서드로 얇게 감싼다. 그 이상 추상화 안 함
- 클라이언트 SDK는 팀 결정을 따른다. 백엔드는 어느 쪽이든 붙는다

## 요금 / 쿼터

### 카카오 (developers.kakao.com)

| API | 무료 | 초과 시 |
|---|---|---|
| 지도 SDK (웹/Android/iOS) | 30만 건/일 | 0.1원/건 |
| 로컬 API — 주소↔좌표, 키워드/카테고리 검색 | 각 10만 건/일 | 0.5원/건 |
| 정적 지도 (신규, 2026-07-21~) | 1,000건/일 | 2원/건 |
| 도보 / 대중교통 / 자전거 길찾기 (신규, 2026-07-21~) | 각 1,000건/일 | 10원/건 |

- **2026-07-21부터 개발자 계정당 첫 번째 활성화 앱에만 무료 쿼터.** 두 번째 앱부터 처음부터 유료 → 팀 계정 하나로 통일 필요
- 초과 사용은 비즈월렛 연결 + 유료 설정

### 네이버 클라우드 — Application Services > Maps

| API | 무료 (월) | 초과 시 |
|---|---|---|
| Web Dynamic Map | 1,000만 건 | 0.1원/건 |
| Mobile Dynamic Map | 1억 건 | 무료 |
| Static Map | 300만 건 | 2원/건 |
| Geocoding / Reverse Geocoding | 각 300만 건 | 0.5원/건 |
| Directions 5 | 6만 건 | 5원/건 |
| Directions 15 | 3,000건 | 20원/건 |

- 무료 이용량은 **대표 계정 1개**(개인=휴대폰 번호, 사업자=사업자번호)에만. 부계정은 첫 호출부터 과금
- 2025-03 공지로 **구 "AI·NAVER API > 지도" 상품**은 신규 차단 + 무료 종료. "네이버 지도 무료 끝났다"는 글은 그 얘기. 현행은 Application Services > Maps

## 기능 비교 (구조)

| | 카카오 | 네이버 |
|---|---|---|
| Android 마커 코드 | Label/LabelStyles/Layer 계층. 길지만 대량 마커 경쟁·순위 처리 내장 | `Marker().map = naverMap`. 단순 |
| Android 라이프사이클 | `mapView.resume()/pause()` 수동 | Fragment가 처리 |
| 웹 JS | 거의 동일 (`level` 작을수록 확대) | 거의 동일 (`zoom` 클수록 확대) |
| 지오코딩 | REST 키 하나. 응답 `x`=lng, `y`=lat | NCP 콘솔 + 헤더 2개 |
| 정적 지도 | REST 신규. 일 1,000 무료 | REST. 월 300만 무료. 마커 ≤20 |
| 도보 길찾기 | 신규 있음 | 자동차 위주 |
| 로컬 검색(동물병원) | 키워드/카테고리(HP8/PM9) 있음 | 지역 검색 있음 |

## 판단

- 우리 트래픽은 둘 다 **무료 구간 안**. 요금으로 못 가른다
- 정적 지도 볼륨(챗봇 카드)만 네이버가 압도적으로 여유 → 카드가 핵심 UX가 되면 정적 지도만 네이버로 갈아끼움 (어댑터 있으니 싼 변경)
- 길찾기는 백엔드가 안 함. 제공사 앱 딥링크로 넘긴다
- **로컬 검색 결과를 우리 DB에 저장하는 건 약관 확인 전 금지.** POI 원천은 공공데이터, 제공사 API는 표시·지오코딩용

## 코드 예시 (2026-08 확인, 카카오 SDK v2 2.15.0 / 네이버 3.23.3)

<details><summary>Android — 지도 + 마커 하나</summary>

카카오
```kotlin
implementation("com.kakao.maps.open:android:2.15.0")
KakaoMapSdk.init(this, "NATIVE_APP_KEY")            // Application
mapView.start(
    object : MapLifeCycleCallback() {
        override fun onMapDestroy() {}
        override fun onMapError(e: Exception) {}
    },
    object : KakaoMapReadyCallback() {
        override fun onMapReady(kakaoMap: KakaoMap) {
            val style  = kakaoMap.labelManager!!.addLabelStyles(
                LabelStyles.from(LabelStyle.from(R.drawable.ic_hospital)))
            val option = LabelOptions.from(LatLng.from(37.4979, 127.0276)).setStyles(style)
            kakaoMap.labelManager!!.layer!!.addLabel(option)
        }
        override fun getPosition() = LatLng.from(37.4979, 127.0276)
        override fun getZoomLevel() = 15
    })
```

네이버
```kotlin
implementation("com.naver.maps:map-sdk:3.23.3")     // + maven("https://repository.map.naver.com/archive/maven")
NaverMapSdk.getInstance(this).client = NaverMapSdk.NcpKeyClient("NCP_KEY_ID")
mapFragment.getMapAsync { naverMap ->
    naverMap.moveCamera(CameraUpdate.scrollTo(LatLng(37.4979, 127.0276)))
    Marker().apply { position = LatLng(37.4979, 127.0276); map = naverMap }
}
```
</details>

<details><summary>백엔드 REST</summary>

카카오 지오코딩
```http
GET https://dapi.kakao.com/v2/local/search/address.json?query=서울 강남구 테헤란로 152
Authorization: KakaoAK {REST_KEY}
```
네이버 정적 지도
```http
GET https://naveropenapi.apigw.ntruss.com/map-static/v2/raster?w=600&h=300&center=127.0276,37.4979&level=16&markers=type:d|size:mid|pos:127.0276 37.4979
x-ncp-apigw-api-key-id: {ID}
x-ncp-apigw-api-key: {KEY}
```
</details>

## 출처
- https://developers.kakao.com/docs/latest/ko/getting-started/quota
- https://devtalk.kakao.com/t/api-notice-on-new-kakao-map-api-features-and-free-quota-policy/150222
- https://www.ncloud-forums.com/topic/99/ (Maps 요금 '23.1~)
- https://www.ncloud-forums.com/topic/129/ (무료 이용량 FAQ)
- https://www.ncloud.com/support/notice/all/1930 (구 지도 API 종료)
