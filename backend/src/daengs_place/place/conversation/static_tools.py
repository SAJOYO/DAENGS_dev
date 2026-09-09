"""Built once on import. No field/state discovery tool or runtime catalog request."""

import json

from daengs_place.place.conversation.contract import TurnPlan
from daengs_place.place.filters.capabilities import CAPABILITIES
from daengs_place.place.planning.purpose import PURPOSE_CATALOG


def inline_schema(model):
    # Gemini's function schema accepts a subset. Runtime Pydantic validation retains
    # all bounds/extra-field checks; the model gets field types, enums and required keys.
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            value = {
                **definitions[value["$ref"].split("/")[-1]],
                **{k: v for k, v in value.items() if k != "$ref"},
            }
        omitted = {
            "title",
            "default",
            "additionalProperties",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "minimum",
            "maximum",
        }
        return {key: expand(item) for key, item in value.items() if key not in omitted}

    return expand(schema)


TURN_TOOL = {
    "type": "function",
    "name": "propose_facility_turn",
    "description": "현재 검색 조건에 대한 변경과 이번 요청의 목표를 한 번에 제안한다. 실제 실행·캐시는 서버가 결정한다.",
    "parameters": inline_schema(TurnPlan),
}

STATIC_INSTRUCTIONS = """시설 검색의 한 턴을 계획한다. propose_facility_turn을 정확히 한 번 호출한다.
필드와 조작 규칙은 이미 제공되어 있다. 필드 조회나 DB 조회 도구는 없다.
current_state가 조건의 원본이며 대화보다 우선한다. 언급하지 않은 조건을 유지한다.
아무 데나/하나 골라줘는 pick_one이며 기존 카테고리를 유지한다. 왜 추천했어는 explain이다.
명시적으로 카테고리를 바꾸면 candidate_kinds를 교체하고 그 범위와 충돌하는 기존 업종 조건과
선호 범위를 함께 수정한다. 수동 조건도 사용자의 명시적 요청이면 변경할 수 있다.
all은 AND, any는 OR 분기이고 각 분기 내부는 AND다. upsert는 ID별 추가·교체이며 remove는 ID별 해제다.
주차되는 곳만은 operations.parking eq true 필수 조건. 주차 없는 곳만은 eq false다.
주차 상관없음은 기존 주차 조건·선호 ID 제거다. 주차 있으면 좋음은 preferences이며 필수가 아니다.
이미 같은 조건이면 중복 ID를 만들지 않는다. null은 해제가 아니다. 생략은 유지다.
반경은 100~20000m, 업종은 최대 6개다. AND 조건은 최대 8개, OR 분기는 최대 4개,
각 분기 조건은 최대 8개이며 전체 조건은 최대 24개다. 선호는 최대 4개다.
name_query는 실제 장소명 부분 일치다. 요청 문장 전체를 넣지 않는다. 빈 문자열은 이름 조건 해제다.
반려동물 전용과 동반 가능은 다르다. 등록되지 않은 속성을 만들지 않는다.
조용함 등 지원하지 않는 요구를 검색 가능하다고 바꾸지 말고 clarify로 필요한 확인을 요청한다.
조건만 편집 요청은 edit_only이고 검색하지 않는다. refresh는 사용자가 새로고침을 명시했을 때만 true다.
visible_order는 사용자가 본 순서다. 두 번째 장소는 reference_index=2로 지정한다.
비교 등 아직 지원하지 않는 목표는 clarify로 범위를 설명하고 가능한 다음 행동을 묻는다.
이 도구는 계획 제안이다. 실행 성공, 장소 개수, 추천 이유를 이 단계에서 만들지 않는다.
""" + json.dumps(
    {
        "capabilities": [
            {
                "id": spec.id,
                "label": spec.label,
                "operators": spec.operators,
                "preference_values": spec.prefer_values,
            }
            for spec in CAPABILITIES
        ],
        "purposes": [spec.model_dump(mode="json") for spec in PURPOSE_CATALOG],
    },
    ensure_ascii=False,
)
