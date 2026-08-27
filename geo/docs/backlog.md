# 백로그 — 갈래에 안 붙는 미결

갈래에 붙는 미결은 각 `explorations/*/*.md` 하단 체크리스트로.

## 팀에 요청/확인
- [ ] PostGIS 확장 (팀 PostgreSQL 인스턴스) — 로컬은 docker-compose로 검증 완료
- [ ] 아키텍처 그림에 산책 세션 API를 Agent Router 바깥으로 반영
- [ ] 반려견 프로필 조회 API/스키마 (`profile_version` 포함) — `contracts/dog-profile.md` 인터페이스 제안
- [ ] 보행체크 AI와의 경계: 산책이 수집한 GPS/속도 데이터를 넘겨주는지, 완전 분리인지
- [ ] 팀 레포 구조(모노/멀티) — 병원 에이전트를 팀 FastAPI에 넣을지, 이 레포가 별도 서비스인지

## 이 레포에서 정할 것
- [ ] **위치 프라이버시 정책** — 기기 로컬 원본도 포함. 시작/종료 좌표뿐 아니라 시설
      encounter 순서의 재식별, 원본·파생 사실별 보관 기간, 동의·삭제 UI
- [x] 산책 세션 API·저장·`WalkFacts`·시설 occurrence 집계 구현
- [x] Android foreground service가 위치수집 생명주기 소유
- [x] Android Room 원본 fix 영속 저장·세션 cascade 삭제 경계
- [x] Android 위치 배치 업로드 — 명시적 종료 후 일괄, 전체 재전송 (결정 #60)
- [ ] Android process-death 복구 — 안 닫힌 세션(#55)을 어떻게 다룰지. 서버는 그 세션을
      영영 finish 받지 못하므로 stale OPEN 의 TTL·tombstone 정책이 짝이다
- [ ] 업로드 주기와 실행 보장 (배터리 vs 유실) — 현재는 종료 직후 1회, 실패하면 재시도 없음.
      WorkManager 는 업로드 의미가 아니라 실행 보장 장치로 들어간다
- [ ] GPS 품질·어뷰징 검증 임계값 (accuracy, 속도, 점프, 가속도)
- [ ] 병원 **영업시간 데이터 출처** — 공공데이터에 없음. 수기·제휴만 남았다 (홈페이지 추출은 #63 기각)
- [ ] 검색 후처리로 후보가 빠질 때 반경 안의 다음 후보를 refill하는 방식
- [ ] Android 실기기 위치·지도·백그라운드 smoke test와 release 서버 설정
- [x] PostGIS 통합 테스트를 포함한 CI (`.github/workflows/ci.yml`)
- [ ] 딥링크 스킴 이름 (`daengs://` 가안)
- [ ] **`NAVER_MAP_NCP_KEY_ID` 가 실제 값이면 Robolectric 테스트 4개가 깨진다.**
      `DaengsApplication.onCreate` 가 키가 있을 때만 `NaverMapSdk` 를 초기화하는데 그게 JVM
      에서 `VerifyError` 로 죽는다. 지금 초록인 건 CI·기본 설정에 키가 없어서다 — 키를 가진
      사람 머신에서는 빨간불이다
- [ ] **`local.properties` 의 빈 값이 `.env` 의 실제 값을 이긴다.** `configured()` 가
      `getProperty()` 의 `""` 를 값으로 받아 폴백을 멈춘다. 키를 `.env` 에 넣어둬도 앱이 못 쓴다

## 현재 코어에 필요한 키·환경
- [ ] NAVER Cloud Maps Dynamic/Static Map 키와 Android 패키지·웹 서비스 URL 등록
- [ ] 공공데이터포털 행안부 동물병원·동물약국 조회서비스 활용신청 및 운영키 등록

## parked 기능을 다시 채택할 때만
- [ ] 카카오 REST 키 (지오코딩용. `kakao-category.md` 는 #64 기각)
- [ ] TMAP 키·요금 재확인 (`transport-snapshot.md` 도보)
- [ ] 자동차·대중교통 실측 provider 선정
- [ ] 이동 중 알림 빈도와 프로필 기반 권장 시간·거리 규칙 결정
