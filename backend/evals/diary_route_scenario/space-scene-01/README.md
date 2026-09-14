# 공간 맥락 조립 · 저장된 세 장면

2026-09-14. [결정과 구현 범위](../../../../docs/walk/diary-space-scene.md).

- `preview.html`: 선정 근거·역할·관계, 이전 입력과 새 첫 입력, 후보별 상세 반환을 펼쳐 본다.
- `preview.json`: 실제 공통 근거 조회와 모델 정규화를 사용한 고정 입력 및 조립 결과.

`public-02`의 저장 공공자료와 `narration-gemini-01`의 세 장면을 사용했다.
재료·동행·원자료 지문은 이전 실험과 같다. GPS와 행동 핀은 합성 시나리오다.
Gemini 0회, 공공 API 0회, 새 생성 문장 0개.

화면의 문장은 사람이 작성한 초안이다. 상세 반환은 선택지별 오프라인 예시이며 모델이
실제로 조회한 기록이 아니다. 새 서술 품질이나 실제 기기 산책을 검증한 결과로 읽지 않는다.
기존 실제 Gemini 결과는 수정하지 않았다.

도구는 `backend/tools/preview_diary_space_scene.py`다. 새 경로로 생성하고,
이 화면만 다시 렌더할 때는 `--output evals/diary_route_scenario/space-scene-01 --render-only`를 사용한다.
