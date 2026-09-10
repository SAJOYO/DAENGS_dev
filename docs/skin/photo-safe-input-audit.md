# photo+safe 후보의 앱 입력 연결 감사

확인일: 2026-09-10. GitHub 소스 읽기와 개인 원본의 로컬 실행으로 확인했다.
백엔드 dev `8f8f913c80b5483de3e76a1ea26d283f1b001a23`,
앱 dev `13009bd122f263f1c686ba5e1db3f922a7e611a7` 기준이다.
서버 셸·실제 환경 값·폰 설치 상태는 확인하지 않았다.

후속: 이 감사에서 발견한 프레임 절대 위치 거절은 앱 PR #262와 백엔드 PR #409에서
수정되어 2026-09-10 `dev`에 병합됐다. 프레임 위치는 사용자가 고른 부위의 크롭 중심으로
쓰고, 크기 밴드만 촬영 품질 조건으로 남긴다. 위 SHA는 감사 시점의 근거이며 현재
`dev`의 SHA나 실기기 검증 결과를 뜻하지 않는다.

## 실제 앱 경로

`Photo.prepare` → `ScreeningRun.run` → 인증된 기록 생성 → 업로드 티켓으로 JPEG PUT →
`/app/screening/records/{id}/confirm` → 백엔드 `_run_model` →
`daengs_screening.service._agent().screen(image, box)`.

앱은 EXIF 방향 처리 후 긴 변 최대 1600, JPEG 품질 90으로 전송한다.
네모는 정규화 [x,y,w,h]이다. confirm은 결과와 사진 기록을 보관한다.
현재 앱 경로를 “인증 없는 /screen/v1/screen에 직접 전송하고 사진을 버린다”로 설명하면 틀린다.
직접 multipart 경로는 별도로 남아 있는 데모/콘솔 경로다.

근거: 앱 `screening/{Photo,ScreeningRun,ScreeningRecordApi}.kt`,
백엔드 `backend/src/daengs_backend/services/screening.py`.

## 개발과 운영 경계

| 대상 | 소스와 설정 | 이번 작업 |
|---|---|---|
| 개발 백엔드 | dev push/merge → Windows self-hosted 자동 배포 | 토픽 draft PR만 작성 |
| 운영 GCP | main 스냅샷, runbook에 따른 수동 반영 | 반영 없음 |
| 앱 debug | local.properties의 daengs.apiBaseUrl | 설정 변경 없음 |
| 앱 release | daengs.apiBaseUrlRelease; 비어 있으면 경고 후 개발 주소 fallback | 설정 변경 없음 |
| DB·사진 저장소 | 개발/운영 별도, confirm은 쓰기 동작 | 접속·업로드 없음 |
| 모델 | SCREENING_RELEASE_REPO / REVISION 또는 로컬 release 폴더 | HF 업로드·revision 교체 없음 |

근거: `.github/workflows/deploy.yml`, `docs/deploy/roadmap.md`, 앱 `app/build.gradle.kts`.
조회 시점 main SHA는 백엔드 `71ada6abcc4490a5be235c28132a032a108cea91`,
앱 `d9a95e72845c62598494b960327261e0493648c6`이다. 이것은 실행 중 서버/설치 APK의 SHA라는 뜻이 아니다.

## 후보가 아직 교체용 release가 아닌 이유

원본 실험의 두 epoch5 모델 확률을 50:50 평균하고 threshold .5로 고정했다.
reserved 5,720장 비교에서 clean F1 .8598→.8639, 이동 평균 .7589→.8008,
흐림+이동 .7241→.7560이었다. clean FN은 53장 감소했지만 FP는 31장 증가했다.
이는 annotation ROI 기반 1단계 비교다. 전체 서비스 모델과 같은 입력에서의 직접 비교가 아니다.

현재 서비스는 짧은 변 1080으로 변환한 뒤 f320을 쓰며, 후보는 roi_window + 384 letterbox를 쓴다.
여러 annotation을 포함하던 ROI를 한 사용자 네모로 바꾸는 것도 입력 분포 변경이다.
기존 보정·threshold·2단계 호출·응답의 confidence에 후보 점수를 바로 대입할 근거가 없다.

## 원본에서 먼저 만든 연결 검증

개인 원본 `src/photo_safe_candidate.py`는 서버에 붙지 않은 오프라인 전용이다.
두 가중치의 SHA를 고정하고 기존 `to_train_space/box_to_px/check_guide`를 재사용하며,
후보의 `roi_window`를 호출한다. 누락·NaN·범위 밖 네모는 거부하고,
기존 촬영 가이드 거절 시 모델을 돌리지 않는다. HTTP/DB/HF 다운로드와 앱 verdict를 추가하지 않았다.
응답은 research_only와 calibrated=false를 명시한 연구 점수다.

로컬 검증:
- `.venv/bin/python -m unittest discover -s tests -p test_photo_safe_candidate.py`: 4개 통과.
- `.venv/bin/python tools/check_photo_safe_upload.py`: 개발 사진 7장, 실제 고정 가중치,
  유한 확률·동일 가중 평균·서버 좌표 함수 AST 일치 확인.
- 700장 proxy 검증 완료. 수치와 한계는 아래 후속 기록에 기재한다.

7장 점검은 동작 확인이지 성능 평가가 아니다. Pillow의 1600 resize/JPEG90은 Android
Bitmap과 픽셀 단위로 같지 않다. 중앙 합성 네모도 실제 사용자가 그린 네모가 아니다.
reserved를 튜닝에 재사용하지 않았다. 팀 screening 사본은 변경하지 않았다.

## 순서와 승격 조건

1. 이 카드에서 실제 경로·환경 경계와 오프라인 proxy 점검을 기록한다.
2. 프레임의 절대 위치를 재촬영 사유로 보던 계약은 #262·#409에서 수정했다.
3. 개발용 앱이 만든 JPEG와 네모를 동의된 테스트 사진에서 확보하여 Android 전처리/좌표를 확인한다.
   기존 STEP36~43의 네모·탐지기 실험을 새로운 기법이라고 반복하지 않는다.
4. 별도 카드에서 개발 환경의 명시적 opt-in 후보 연결을 검토한다. 기존 release·기록 계약,
   인증, 2단계 호출, latency를 포함한다. 원본 수정 후 sync 규칙을 따른다.
5. 개발 검증과 사람 리뷰 이후에만 dev→main 스냅샷 및 운영 반영을 검토한다.

현재 원본 프로토타입은 로컬 작업물이다. 팀 런타임에 반영되거나 배포된 것으로 표시하지 않는다.
Projects 필드 접근은 현재 연결 도구가 지원하지 않아 미설정이며 Priority/Iteration을 임의 지정하지 않았다.

## 700장 proxy 점검 결과

개발 데이터에서 라벨별 100장(정상100/비정상600), 합성 중앙 네모 [.3,.3,.4,.4] 고정.
`tools/check_photo_safe_upload.py --per-class 100`로 700장 모두 완료했다.

| 모델 | macro-F1 | 비정상 recall | 정상 FP / 100 | 비정상 FN / 600 |
| --- | ---: | ---: | ---: | ---: |
| photo | .6095 | .7900 | 48 | 126 |
| safe | .6997 | .9333 | 56 | 40 |
| 고정 평균 | .6671 | .8817 | 52 | 71 |

CPU의 두 모델 score 호출 중앙값 .235초, p95 .257초였다. 로딩·업로드·DB·2단계를 제외한
이 PC의 실측이라 서버 전체 응답시간으로 주장하지 않는다.
이 표는 crop 위치·사진 codec·리사이즈가 함께 달라진 탐색 proxy다. 실제 사용자 박스가 아니며,
잘린 crop에 원본 라벨을 그대로 적용했다. 배포 기준으로 삼거나 safe 단독을 사후 채택하지 않는다.
정상 오탐도 커서 이 결과만으로 서비스 교체를 진행하지 않는다.

개발 서버 접근 방식은 사용자에게 확인 요청했다. 실제 JPEG·box를 확보하여 Android 픽셀/좌표
연결부터 검증한 후 별도 개발 연결 카드를 구체화한다. 본 카드의 오프라인 감사는 완료했다.
