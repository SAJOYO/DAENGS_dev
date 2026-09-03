# DAENGS Journey

장소 검색 결과를 선택한 뒤 기존 DAENGS_APP이 호출하는 `POST /journey` 서비스입니다.
Place 검색이나 산책 기록을 소유하지 않으며, 선택한 좌표까지의 단발 이동 스냅샷과 지도 앱
handoff만 반환합니다.

## 소유 범위

- 입력: 앱이 선택한 출발 좌표와 목적지 후보
- 결과: 이동 수단별 단발 스냅샷과 NAVER/Kakao/TMAP 앱 handoff
- 소유하지 않음: Place 검색, Dog/Owner Profile, 산책 기록, 실시간 위치

이주 기준점과 유지·제외 계약은 [UPSTREAM.md](UPSTREAM.md)에 고정합니다.
로컬 실행과 외부 route provider 설정은
[코드 옆 README](../../backend/src/daengs_journey/README.md)를 따릅니다.
