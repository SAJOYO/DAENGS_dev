# 장면과 선택적 행동의 서술 구조

> 실제 보드 API의 기본 작성기는 [카드 오케스트레이션](card-orchestration.md)으로 전환했다.
> 아래 결합 작성 계약은 기존 미리보기와 과거 영수증을 읽는 경로에 남는다.

`diary-scene-writing-v3`는 선택된 스탬프를 **장면 + 선택적 행동**으로 투영한다.
일반 장면은 완성된 본문을 받으며, 특별한 순간·사진 기록은 원문을 보조하는 문장만 받는다.
이전 기본 보드 발행과 미리보기는 같은 입력 조립·출력 검사·본문 조립을 사용했다.

## 입력과 출력

아래는 형식을 설명하는 예시다. 실제 입력에는 해당 스탬프의 재료만 들어간다.

```json
{
  "scene_id": "scene-1",
  "mode": "scene",
  "scene": {
    "where": [],
    "route_pattern": [
      {
        "id": "material-1",
        "role": "scene_motion",
        "facts": {
          "material": {"이전 경로와의 관계": "방금 지나온 구간을 되짚는 이동"},
          "relation": {"kind": "scene_within_pattern_support", "meaning": "이 장면 지점을 포함하는 관측 구간"},
          "subject": "recording_device",
          "action_meaning": "not_inferred"
        }
      }
    ],
    "environment": []
  },
  "action": {
    "id": "action-1",
    "kind": "sniffing",
    "material": {"무엇을": "냄새 맡기"}
  }
}
```

행동핀이 없으면 `action: null`이다. 공간·동선 패턴·환경 중 일부가 비어도 된다.
행동만 있으면 그 행동도 작성 입력이 될 수 있다. 재료도 행동도 없으면 기존 기본 본문을 유지한다.
전체 요청은 `format: scene-and-optional-action-v1`, 슬롯 `revision`, `scenes` 배열이다.

```json
{
  "scenes": [
    {
      "scene_id": "scene-1",
      "text": "지나온 길을 되짚던 중 냄새를 맡았다.",
      "evidence_ids": ["material-1"],
      "action_id": "action-1"
    }
  ]
}
```

출력은 한 번의 구조화된 LLM 호출로 받는다. `text`는 최대 220자다.
모든 입력 장면이 정확히 한 번 있어야 하며, 인용은 그 장면의 전달 재료에 한정한다.
본문이 있고 행동 앵커가 있으면 정확한 `action_id`가 필요하고, 행동이 없으면 null이어야 한다.
비어 있는 본문은 인용·행동 ID도 비워야 하며 해당 장면의 기본 본문을 유지한다.
ID 검사는 문장 안의 의미·행동 서술 품질까지 보증하지 않는다. 실제 Gemini 품질 평가는 후속이다.

## 특별한 순간과 기존 저장본

자유 서술·사진 기록의 `mode`는 `preserve_original`이다. 원문은 모델에 보내지 않고,
공간·환경 보조 문장을 원문 앞에 붙인다. 동선 재료는 이 모드의 작성 입력에 넣지 않는다.
원문의 공백·개행, 모든 장면의 core·anchor·ID·제목·순서는 유지한다.
일반 장면의 `mode: scene`에서는 생성된 `text`를 본문으로 사용해 기본 문장을 중복 덧붙이지 않는다.
실패·시간 초과·잘못된 인용은 기존 기본 보드로 복구한다.

기존 JSONB의 `background` 필드명은 영수증 호환을 위해 남긴다. 새 영수증은
`composition: replace`와 선택적 `action_id`로 생성 본문과 행동 연결을 구분한다.
예전 `prepend` 방식은 필드 생략 기본값으로 읽으며 기존 JSON·해시를 바꾸지 않는다.
발행 본문은 저장 영수증과 일치하는지 확인하며, 설정이나 writer가 바뀌어도 재조회만으로 재생성하지 않는다.
APP의 공개 일기 본문·인용 응답 계약, DB 스키마, 외부 API 수집은 변경하지 않았다.

## 다음 단계

이번에는 슬롯 용량·후보 적격성·장면 선정 정책을 변경하지 않았다.
현재 스탬프의 재료 전달은 `diary_scene_input.scene_input` 한 경계에 모았다.
일반 장면에서는 현재 적재본을 전달하며, 전체/일부 전달의 최종 정책은 여기서 후속 비교한다.
공간 이탈·재진입 빈도에 관한 추가 규칙은 없다.

실제 Gemini 문장 품질, 기존 관측과 동선 패턴의 중복·공존·용량 정책, 운영 활성화와 폰 화면 확인이 남는다.
실행 검사와 코드 위치는 [코드 옆 안내](../../backend/src/daengs_walk/SCENE_WRITING.md)에 있다.
