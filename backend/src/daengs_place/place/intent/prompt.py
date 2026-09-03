"""Place intent proposer prompt와 Structured Outputs schema 조립."""

import json
from typing import Any

from daengs_place.place.intent.contract import (
    LLMIntentOutput,
    ProposalDisposition,
    ProposalReason,
    SearchModeId,
)
from daengs_place.place.planning.contract import CapabilityId, PlaceKind
from daengs_place.place.planning.intents import ActivityId, IntentRole, SearchObjectId
from daengs_place.place.planning.purpose import PURPOSE_CATALOG

_GEMINI_SEMANTIC_CONCEPTS = (
    "semantic.quiet",
    "semantic.cheap",
    "semantic.dog_interest",
)


def proposer_instructions() -> str:
    purposes = [
        {
            "purpose_id": spec.purpose_id.value,
            "description": spec.description,
            "kinds": [kind.value for kind in spec.kinds],
        }
        for spec in PURPOSE_CATALOG
    ]
    return "\n".join(
        (
            "너는 반려견 동반 장소 검색의 자연어 의미 제안기다.",
            "검색을 실행하거나 장소 사실을 만들지 말고, 사용자 원문에 드러난 intent만 제안하라.",
            "search mode는 다음 순서로 판정한다. 먼저 사용자가 찾는 place kind, purpose, 또는 '조용한 곳' 같은 target형 semantic이 있는지 찾는다.",
            "명시적 장소 target이 하나라도 있으면 추천·선택 표현이 함께 있어도 directed_search다. 이는 장소 종류 안에서 결과를 골라 달라는 뜻이지 검색 대상 자체의 위임이 아니다.",
            "명시적 장소 target이 없고 사용자가 어떤 장소 종류를 찾을지 시스템에 맡긴 긍정적 근거가 있을 때만 open_discovery다. activity와 object는 장소 target이 아니므로 goal만 있고 장소 선택을 맡기면 open_discovery일 수 있다.",
            '"추천해줘"나 "골라줘"라는 동사만으로 open_discovery를 만들지 마라. "아무거나", "목적은 없어", "뭐 할까", "갈 곳이 애매해"처럼 장소 선택 범위까지 맡긴 근거와 함께 해석하라.',
            "goal·preference·negated·excluded·hypothetical·relational proposal만 있고 별도의 장소 선택 위임이 없으면 directed_search다. 그 proposal의 quote를 open_discovery 근거로 재사용하지 마라.",
            "사용자가 두 개 이상의 명시적 장소 대안을 고민하면 각 대안을 directed_search interpretation으로 나누고 ambiguous로 출력한다. 대안 선택 질문을 open_discovery로 합치지 마라.",
            "open_discovery는 위임 근거를 evidence에 복사해야 하며 required_target을 함께 출력하지 않는다. 주차·조용함·활동 같은 명시 조건은 proposal로 계속 보존한다.",
            "가정·망설임·preference-only 표현을 대상 부족이라는 이유로 open_discovery로 올리지 마라. 선택 위임도 목적도 없는 인사·무관 발화는 abstained다.",
            "required_target은 사용자가 실제로 찾는 장소에만 쓴다. 단어가 등장했다는 이유만으로 target으로 만들지 마라.",
            "goal은 사용자가 실제로 하려는 활동이나 그 활동의 객체에만 쓴다. activity와 object를 required_target이나 required_condition에 넣지 마라.",
            "analogy=비유·유사성, excluded=제외 요구, negated=부정된 언급, hypothetical=가정·망설임, relational=숙소 안 카페처럼 다른 대상과의 관계다.",
            "required_condition은 반드시 지켜야 한다고 말한 사실 조건, preference는 있으면 좋다고 말한 조건이다.",
            "여러 jointly requested 조건은 한 interpretation 안에 둔다. 서로 양립하지 않는 대안 해석만 별도 interpretation으로 나눈다.",
            "play나 buy처럼 장소 종류가 아닌 행동은 activity로, dog_toy처럼 구매·이용 대상은 object로 제안하고 목적지를 임의로 확정하지 마라.",
            "'-고 싶어'처럼 실제 욕구를 말한 activity와 object는 goal이다. '-다면' 같은 가정이나 '-나 갈까' 같은 망설임 속 모든 intent는 goal/required_target으로 올리지 말고 hypothetical을 유지하라.",
            "허용 semantic은 semantic.quiet, semantic.cheap, semantic.dog_interest뿐이다. 근처·가까운 같은 공간 표현으로 semantic.proximity를 만들지 마라.",
            "'조용한 곳/조용한 카페'의 quiet는 target형 semantic이라 required_target이다. cheap과 dog_interest는 preference로 보존한다. 이런 느낌을 장소의 확인된 사실로 만들지 마라.",
            "정확한 kind가 있으면 같은 뜻의 더 넓은 purpose를 덧붙이지 마라. 예를 들어 hospital에 healthcare를, gallery에 culture를 추가하지 마라.",
            "근거 quote는 사용자 원문에서 연속된 문자열을 정확히 복사한다. offset을 확신하면 Python 문자열 인덱스의 [start,end)를 쓰고, 아니면 둘 다 null로 둔다.",
            "명확한 한 해석은 proposed, 대안이 둘 이상이면 ambiguous와 multiple_plausible_readings, 의미 근거가 부족하거나 안전하게 제안할 수 없으면 abstained를 쓴다.",
            "confidence 숫자, source, origin, locked, relaxable, SQL, 검색 gate를 출력하지 마라.",
            '예: "카페 같은 분위기"의 cafe는 analogy이지 required_target이 아니다.',
            '예: "병원 갈 정도는 아니야"의 hospital은 negated이다.',
            '예: "숙소에 카페가 있으면"의 lodging은 required_target이고 cafe는 relational이다.',
            '예: "강아지 장난감을 산다면"의 buy와 dog_toy는 둘 다 hypothetical이며 장소 target은 없다.',
            '예: "오늘 심심한데 네가 추천해봐"는 open_discovery이고 근거는 "네가 추천해봐"다.',
            '예: "오늘 뭐 할까"나 "갈 곳이 애매해"는 장소 선택을 맡기는 말이므로 open_discovery다.',
            '예: "강아지가 좋아하는 거 있는 곳"은 semantic.dog_interest preference이며 장소 종류를 임의로 확정하지 않는다.',
            '예: "조용한 카페 추천해줘"는 cafe 목적이 명시됐으므로 directed_search이며 quiet와 cafe를 보존한다.',
            '예: "주차되는 식당 네가 골라줘"는 restaurant 안에서 고르는 directed_search이며 parking은 required_condition이다.',
            '예: "강아지랑 갈 숙소 알아서 골라줘"와 "미술관 중에 네가 추천해줘"는 각각 lodging, gallery가 명시된 directed_search다.',
            '예: "강아지 장난감 사고 싶어 네가 추천해줘"는 장소 target 없이 buy와 dog_toy goal을 보존한 open_discovery다.',
            '예: "조용한 곳"은 directed_search의 semantic.quiet required_target이고 "주차되면 좋겠어"는 directed_search의 parking preference다.',
            '예: "카페나 갈까"의 cafe는 hypothetical이며 open_discovery가 아니다.',
            '예: "강아지랑 놀고 싶어"는 play goal만 있는 directed_search이고, 추천을 맡긴 별도 표현이 있을 때만 open_discovery다.',
            '예: "카페도 좋고 산책도 좋은데 어디가 좋을까"는 cafe와 outing의 두 directed_search 대안을 가진 ambiguous다.',
            '예: "병원 갈 정도는 아니야"는 hospital negated만 보존한 directed_search이고, "카페 말고 산책할 곳"은 cafe excluded와 outing required_target이다.',
            '예: "산책할 곳"은 purpose outing이고 kind leisure로 바꾸지 않는다. "숙소"는 purpose lodging이고 kind stay로 바꾸지 않는다.',
            "canonical kinds: "
            + json.dumps([kind.value for kind in PlaceKind], ensure_ascii=False),
            "purpose catalog: " + json.dumps(purposes, ensure_ascii=False),
            "roles: " + json.dumps([role.value for role in IntentRole], ensure_ascii=False),
            "activities: "
            + json.dumps([activity.value for activity in ActivityId], ensure_ascii=False),
            "objects: "
            + json.dumps([object_id.value for object_id in SearchObjectId], ensure_ascii=False),
            "boolean capability: " + CapabilityId.OPERATIONS_PARKING.value,
        )
    )


def strict_output_schema() -> dict[str, Any]:
    """Pydantic schema를 OpenAI strict Structured Outputs의 required 형태로 고정한다."""

    schema = LLMIntentOutput.model_json_schema()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            value.pop("title", None)
            value.pop("discriminator", None)
            if "oneOf" in value:
                value["anyOf"] = value.pop("oneOf")
            if "const" in value:
                value["enum"] = [value.pop("const")]
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["required"] = list(properties)
                value["additionalProperties"] = False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return schema


def gemini_output_schema() -> dict[str, Any]:
    """Flash-Lite가 안정적으로 받는 평면 adapter schema.

    중첩 discriminated union은 Gemini가 schema complexity 오류로 거절할 수 있다. provider
    응답만 평면화하고, 서버가 이를 원래 typed Pydantic 계약으로 다시 조립해 검증한다.
    """

    proposal = {
        "type": "object",
        "properties": {
            "role": {"type": "string", "enum": [role.value for role in IntentRole]},
            "intent_type": {
                "type": "string",
                "enum": [
                    "kind",
                    "purpose",
                    "boolean_capability",
                    "semantic",
                    "activity",
                    "object",
                ],
            },
            "kind": {"type": "string", "enum": [kind.value for kind in PlaceKind]},
            "purpose_id": {
                "type": "string",
                "enum": [spec.purpose_id.value for spec in PURPOSE_CATALOG],
            },
            "capability_id": {
                "type": "string",
                "enum": [CapabilityId.OPERATIONS_PARKING.value],
            },
            "value": {"type": "boolean"},
            "concept_id": {
                "type": "string",
                "enum": list(_GEMINI_SEMANTIC_CONCEPTS),
                "description": "원문에 직접 근거한 허용 semantic만 사용한다.",
            },
            "activity_id": {
                "type": "string",
                "enum": [activity.value for activity in ActivityId],
            },
            "object_id": {
                "type": "string",
                "enum": [object_id.value for object_id in SearchObjectId],
            },
            "quote": {"type": "string"},
            "start": {"type": "integer", "minimum": 0},
            "end": {"type": "integer", "minimum": 0},
        },
        "required": ["role", "intent_type", "quote"],
    }
    return {
        "type": "object",
        "properties": {
            "disposition": {
                "type": "string",
                "enum": [value.value for value in ProposalDisposition],
            },
            "interpretations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "search_mode": {
                            "type": "string",
                            "enum": [mode.value for mode in SearchModeId],
                            "description": (
                                "명시적 place target이 있으면 directed_search. target이 없고 장소 "
                                "선택 범위를 시스템에 맡긴 긍정적 근거가 있을 때만 open_discovery. "
                                "goal/preference/negative/hypothetical만으로는 open_discovery가 아니다."
                            ),
                        },
                        "search_mode_quote": {"type": "string"},
                        "search_mode_start": {"type": "integer", "minimum": 0},
                        "search_mode_end": {"type": "integer", "minimum": 0},
                        "proposals": {
                            "type": "array",
                            "minItems": 0,
                            "items": proposal,
                        }
                    },
                    "required": ["search_mode", "proposals"],
                },
            },
            "reason": {
                "type": "string",
                "enum": ["none", *(value.value for value in ProposalReason)],
            },
        },
        "required": ["disposition", "interpretations", "reason"],
    }
