# 장면 서술 실행 경로

- `diary_scene_input.py`: 스탬프 → 장면 재료 + 선택적 행동 앵커. API·LLM 호출 없음.
- `daengs_backend.services.walk_diary_slot_writing`: 공통 입력·출력 검증, 한 번의 LLM 호출,
  일반 장면 본문 교체와 특별한 순간 원문 보존.
- `diary_board_receipt.py`: 기존 prepend 영수증과 새 replace 영수증을 읽는 저장 계약.

DEV `backend/`에서 다음 검사로 확인한다. 실제 API·Gemini를 호출하지 않는다.

```powershell
uv run pytest -q tests/walk/diary/test_diary_scene_writing.py tests/walk/diary/test_diary_board_slot_writing.py tests/walk/diary/test_diary_board_receipt.py tests/walk/diary/test_diary_slots.py
```

기존 `POST /app/walks/{walk_id}/diary-slots/preview`의 `generate: true`와
`storyboard`의 `walk-diary-board-v1` 생성이 같은 writer를 사용한다.
기존 기능 활성화 설정·인증·Gemini 키가 필요하며 새 환경변수는 없다.
입력·출력 JSON, 보존과 남은 정책은 [장면 서술 계약](../../../docs/walk/scene-writing.md)을 따른다.
