# skin — 피부 스크리닝

| 문서 | 내용 |
| --- | --- |
| [photo-safe-input-audit.md](photo-safe-input-audit.md) | 고정 후보의 앱 입력 경로·개발/운영 경계와 오프라인 검증 (#394) |

앱의 현재 경로는 인증된 `/app/screening/records` 생성 → 사진 업로드 →
`/app/screening/records/{id}/confirm`이다. confirm이 모델을 실행하고 기록을 보관한다.
`/screen/v1/screen`은 별도로 남아 있는 직접 multipart 경로다.
이 설명은 2026-09-10 백엔드 `8f8f913`와 앱 `13009bd`의 호출 코드를 대조한 결과다.

## 문서 경계

- 실행 방법·HTTP 계약은 `backend/src/daengs_screening/` 코드 옆 문서.
- 모델·전처리의 이유와 검증 상태는 이 폴더.
- 학습 실험 원본은 `gayeoniee/deeplearning_test`.
- 오케스트레이션 HANDOFF와 인가는 호출하는 쪽의 문서.
- 앱 사진 촬영/네모 UI 구현은 `SAJOYO/DAENGS_APP`.

스크리닝 소스는 원본에서 수정 후 동기화한다. 위 후보 감사는 서비스 모델 교체가 아니다.
