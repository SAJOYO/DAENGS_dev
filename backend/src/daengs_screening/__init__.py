"""반려견 피부 병변 스크리닝 — 사진 한 장 → 정상/이상 + 병변 6종 분포.

**진단이 아닙니다.** "이건 좀 의심되니 병원 가보세요" 까지가 목적입니다.

⚠️ **여기(패키지 최상단)에서 무거운 것을 import 하지 마세요.** `daengs_backend.main`
   이 `service.py` 를 통해 이 패키지를 import 하는데, torch 가 딸려 오면
   `tests/test_main_stays_light.py` 가 깨집니다 (D-021).

⚠️ 이 패키지는 **원본 저장소의 사본**입니다 —
   [gayeoniee/deeplearning_test](https://github.com/gayeoniee/deeplearning_test).
   고칠 일이 생기면 원본을 고치고 `backend/tools/sync_screening.py` 로 가져오세요.
   자세한 규칙은 `CLAUDE.md`.
"""
