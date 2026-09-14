# 보호자 시점과 실제 동행을 연결한 일기

2026-09-14, DEV #520 후속. 기존 공간·행동 작성기를 유지하고 실제 모델 입력과 표현 계약을 바꿨다.

후속 [공간 맥락 조립](diary-space-scene.md)에서 공간 입력은 v8, 도구는 v3가 됐다.
공간 예시는 의미 선택·생략 사례로 교체했고 공통 narration과 행동·제목 전략은 보존했다.
아래 버전 표와 실제 Gemini 결과는 v7 실험 당시 기록이며 새 문장으로 교체하지 않았다.

## 왜 바꿨나

내부 공통 조회에는 동행 정보가 있었지만 공간 정규화에서 빠졌다. 공간 도구의 첫 입력도
재료·후보만 추렸고, 공간 프롬프트와 채택 검사는 반려견 이름을 금지했다.
따라서 입력 내부에 동행이 있다는 사실만으로 모델이 보호자의 산책을 쓸 수는 없었다.

기록한 보호자의 시점과 해당 산책의 실제 동행을 명시한다. 가상의 인격·감정·기억은 만들지 않는다.
이 맥락은 슬롯 용량을 차지하는 경쟁 재료가 아니라 공간·행동 작성의 공통 전제다.

## 실제 입력과 작성

`writing/context.py`의 공통 조회에서 버전을 가진 동행 정보를 준비한다.
순수 도메인 함수 `board/narration.py`가 허용된 필드만 정규화한다.

```json
{
  "narration": {
    "narrator": "이 산책을 기록한 보호자(나)",
    "companions": [{"name": "보리"}],
    "scope": "현재 장면"
  }
}
```

동행 목록은 실제 산책의 pet_ids에 한정한다. ID·좌표·메모·임의 메타데이터는 이 입력에 넣지 않는다.
이름이 없는 동행은 null로 보존하고 새 이름을 만들지 않는다.

- 공간: 기존 정규화 공간·환경 재료 + 공통 narration. 도구 초기 입력과 후속 대화에도 보존한다.
- 행동: 행동 핀이 있을 때만 기존 recorded_action 또는 actor/action + 공통 narration.
  동선이 연결되면 핀 시각의 결합 movement_context 최대 하나를 추가한다.
- 행동의 정확한 전달 검사도 동일한 narration 투영을 사용한다. 내용이 다른 맥락을 보내면 거절한다.
- 제목: 확정된 장면 본문 전체를 읽고 각 카드 제목을 쓰는 기존 정책을 유지한다.
- 메모: 모든 생성 입력에서 제외하고 독립 원문으로 조립한다. 메모만 바뀌면 생성 캐시를 재사용한다.

공간에는 실제 동행의 존재와 함께 산책한 큰 상황을 허용한다. 구체 행동·정지·감각·감정·과거 방문은
공통 시점에서 추론할 수 없다. 기존 공간의 동행 이름 거절은 새 계약에서 해제하고 과거 요청에는 유지한다.
임의 이름이나 모든 의미 오류를 자동 감지하는 검사를 새로 만든 것은 아니다.

공간·행동 프롬프트에 공통 시점 안내와 짧은 자료→표현 범위→문장 예시를 연결했다.
예시는 작성 방식을 보여주며 예시의 이름·사건을 새 근거로 쓰라는 지시가 아니다.
공간의 내부/근접 관계, 동까지만 표기, 공원의 일반 배경 표기는 유지한다.

## 버전과 읽기

| 계약 | 현재 | 보존 범위 |
| --- | --- | --- |
| 모델 입력 | diary-prose-input-v7 | 버전 표시 없는 과거 요청은 기존 v6 입력 그대로 |
| 작성 정책 | shared-orchestration-card-writing-v9 | 변경된 공간·행동 전략은 새 지문 사용 |
| 공간 도구 | diary-space-details-v2 | v1 실제 공급 기록 판독 유지 |
| 공통 맥락 | guardian-walk-context-v1 | 화자·동행·현재 장면만 투영 |

제목 프롬프트·투영이 같으므로 제목 전략 지문은 v6 기준을 유지한다. 본문이 실제로 바뀌면
기존 제목의 본문 의존성으로 갱신하고, 바뀌지 않은 제목을 버전 번호만으로 재호출하지 않는다.
과거 발행본을 자동 재생성하거나 새 narration을 덧씌우지 않는다.
저장 독자는 작성 런타임·SDK 없이 순수 입력/도구 검증만 사용한다.

## 실제 Gemini 비교

[비교 화면](../../backend/evals/diary_route_scenario/narration-gemini-01/preview.html),
[고정 입력·프롬프트](../../backend/evals/diary_route_scenario/narration-gemini-01/input.json),
[실제 카드 조립 결과](../../backend/evals/diary_route_scenario/narration-gemini-01/result.json).

public-02의 저장 공공자료와 합성 GPS·행동 핀을 사용했다. 공간 3장면과 행동 핀 1개다.
새 입력에서 narration만 빼면 이전 실험의 공간·동선·속도 재료와 완전히 같음을 확인했다.
왼쪽 문장은 space-action-gemini-01 결과를 재사용했다. 모델은 gemini-3.1-flash-lite로 동일하다.
실제 provider의 도구 왕복·정규화·응답 검증·frozen_card 조립기를 사용했다.
전체 발행 그래프나 실제 DB·앱 배포 실험은 아니다.

| 장면 | 새 공간 문장 |
| --- | --- |
| 풀밭 | 보리와 함께 양재2동의 풀밭을 걸었다. 산책하던 곳 근처에는 공원이 있었다. |
| 길 | 보리와 함께 양재2동의 길을 걸었다. 산책하던 곳 가까이에 공원이 있었다. |
| 숲 | 보리와 함께 산책하던 곳은 양재1동이었다. 근처에 공원이 있었고 숲이 자리하고 있었다. |

행동 핀이 있는 두 번째 장면의 새 행동 문장:

> 산책 걸음이 비교적 느렸던 때, 보리가 냄새를 맡았다.

이전의 “보리는 상대적으로 느린 걸음으로 곧게 이동하던 중 잠시 멈춰 서서 냄새를 맡았다”에서
근거 없는 정지와 기기 이동을 반려견의 직접 이동으로 귀속하던 표현이 이번 출력에서는 사라졌다.
직선은 쓰지 않고 상대 저속만 보충했다. 작성 예시와 거의 같은 문형이므로 일반적인 개선으로
단정하지 않는다.

공간에는 동행 맥락이 들어갔다. 하지만 풀밭 피복을 “풀밭을 걸었다”로 확대했고 세 장면의
문형이 반복된다. 숲 장면에도 장소 정보 나열이 남았다. 인용·형식 검사를 통과했지만
의미 정확성이나 일기 품질이 완성됐다는 뜻은 아니다.
입력 맥락과 프롬프트/예시를 함께 바꿨으므로 어느 하나의 순수 효과를 분리한 실험도 아니다.
새 결과는 수정하거나 재추첨하지 않고 그대로 보존했다.

총 Gemini 7회(공간 6·행동 1), 공간 내부 상세 조회 3회, 공공 API·제목·자동 재시도 0회.
공간은 모두 공원 상세 1개를 선택했다. 모델 입력 토큰은 합계 7,898이다.
프롬프트가 늘었고 두 번 왕복하므로 비용 절감 실험으로 해석하지 않는다.

## 확인과 재현

직접 영향받는 입력·도구·행동·저장·캐시 경계 11개 테스트 파일에서 150건 통과했다.
추가 실행기의 일반/압축 저장본 중복 유료 호출 방지 테스트 6건도 통과했다.
전체 스위트·실제 DB·운영 배포는 실행하지 않았다.
기존 Windows 실행 정책으로 막힌 저장소 전체 check를 이번 검사로 통과 처리하지 않는다.

```powershell
uv run --no-sync pytest -q tests/walk/diary/test_diary_narration.py tests/walk/diary/test_diary_context.py tests/walk/diary/test_diary_space_tools.py tests/walk/diary/test_diary_card_writing.py tests/walk/diary/test_diary_llm.py tests/walk/diary/test_diary_activity.py tests/walk/diary/test_diary_action_boundary.py tests/walk/diary/test_diary_movement_materials.py tests/walk/diary/test_diary_writing_boundaries.py tests/walk/diary/test_diary_title_cache.py tests/walk/diary/test_diary_service_package.py
uv run --no-sync pytest -q tests/walk/diary/test_diary_narration_preview.py
uv run --no-sync python -X utf8 tools/compare_diary_narration.py --output evals/diary_route_scenario/narration-gemini-01 --render-only
```

새 실험은 `--source`, `--comparison`, `--previous`, `--output`으로 준비한다.
명시적인 `--run --env-file <경로>`가 있어야 모델을 호출한다.
기존 attempt 파일이 있으면 성공·실패·중단 여부와 관계없이 재호출하지 않는다.
비교 화면의 개발자 영역은 실제 공간 초기 입력/상세 반환과 행동 입력을 표시한다.
일기 본문에 선택 사유·진단을 섞지 않는다.
