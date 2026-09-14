"""Pure space-tool projection and receipt checks; no provider or writer runtime."""

from copy import deepcopy

NAME = "get_space_details"
LEGACY_VERSION = "diary-space-details-v1"
VERSION = "diary-space-details-v2"
CORE_ROLES = ("location_label", "point_land_cover", "regional_environment")
MAX_DETAILS = 2
MAX_MODEL_CALLS = 2
INSTRUCTION = """기본 materials만으로 충분하면 바로 작성한다.
available_details는 조회 후보이며 아직 본문의 근거가 아니다.
필요한 주변 배경만 get_space_details로 한 번에 최대 2개 확인할 수 있다.
반환된 materials의 관계 범위 안에서 골라 쓰며, 조회하지 않은 후보는 인용하지 않는다.
공원과 피복이 함께 있어도 같은 공간·내부·방문 관계로 합치지 않는다."""


def initial_input(payload, *, version=VERSION):
    if version not in {LEGACY_VERSION, VERSION} or (
        version == LEGACY_VERSION and "narration" in payload
    ):
        raise ValueError("space tool version does not support this context")
    core, available = [], []
    for item in payload["materials"]:
        if item["role"] in CORE_ROLES:
            core.append(deepcopy(item))
        else:
            topic = (
                "주변 공원"
                if item.get("material", {}).get("배경") == "공원"
                else "주변 업종 분포"
                if item["role"] == "area_statistics"
                else "주변 공간 관계"
            )
            available.append({"id": item["id"], "topic": topic})
    return {
        "materials": core,
        **({"available_details": available} if available else {}),
        **({"narration": deepcopy(payload["narration"])} if "narration" in payload else {}),
    }


def declaration(payload):
    candidates = initial_input(payload).get("available_details", [])
    if not candidates:
        return None
    return {
        "name": NAME,
        "description": (
            "현재 카드의 주변 배경 후보에서 서술에 필요한 상세와 확인된 관계만 조회한다. "
            "새 장소 검색이나 두 대상 사이의 관계 추론은 하지 않는다. 생략 가능하며 한 번만 호출한다."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "material_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": [m["id"] for m in candidates]},
                    "minItems": 1,
                    "maxItems": MAX_DETAILS,
                }
            },
            "required": ["material_ids"],
            "additionalProperties": False,
        },
    }


def lookup(payload, arguments):
    """Only invocation-local normalized material is addressable; no card/coordinate args."""
    candidates = {m["id"]: m for m in payload["materials"] if m["role"] not in CORE_ROLES}
    ids = arguments.get("material_ids") if isinstance(arguments, dict) else None
    if (
        not isinstance(arguments, dict)
        or set(arguments) != {"material_ids"}
        or not isinstance(ids, list)
        or not 1 <= len(ids) <= MAX_DETAILS
        or any(not isinstance(ref, str) or ref not in candidates for ref in ids)
        or len(ids) != len(set(ids))
    ):
        return {"status": "invalid_arguments"}
    return {"status": "ok", "materials": [deepcopy(candidates[ref]) for ref in ids]}


def validate_trace(payload, trace):
    """Return the seen aliases; stored receipts use the same pure, versioned contract."""
    if (
        not isinstance(trace, dict)
        or set(trace)
        != {"version", "initial_input", "model_calls", "tool_calls", "public_api_calls"}
        or trace["version"] not in {LEGACY_VERSION, VERSION}
        or trace["initial_input"] != initial_input(payload, version=trace["version"])
        or type(trace["model_calls"]) is not int
        or not 1 <= trace["model_calls"] <= MAX_MODEL_CALLS
        or trace["public_api_calls"] != 0
        or not isinstance(trace["tool_calls"], list)
        or len(trace["tool_calls"]) > 1
        or (trace["model_calls"] == 2 and not trace["tool_calls"])
    ):
        raise ValueError("invalid space tool receipt")
    seen = {m["id"] for m in trace["initial_input"]["materials"]}
    for call in trace["tool_calls"]:
        if (
            not isinstance(call, dict)
            or set(call) != {"name", "arguments", "result"}
            or call["name"] != NAME
            or call["result"] != lookup(payload, call["arguments"])
        ):
            raise ValueError("space tool result changed")
        seen.update(m["id"] for m in call["result"].get("materials", []))
    return seen


def validate_citations(payload, references, trace, evidence_ids):
    seen = validate_trace(payload, trace)
    if not set(evidence_ids) <= {references[key] for key in seen} or (
        trace["tool_calls"] and trace["model_calls"] != 2
    ):
        raise ValueError("space cited unread material")
