"""Allowlisted prose facts. Slot selection and source diagnostics stay upstream."""


def fields(value, names):
    # Never copy nested provider objects, even under a familiar field name.
    return {k: value[k] for k in names if type(value.get(k)) in (str, int, float)}


def location(facts):
    # Display policy: a confirmed dong only. Never fall back to a full address.
    dong = facts.get("dong")
    return {"dong": dong.strip()} if isinstance(dong, str) and dong.strip() else {}


def material(item):
    result = _material(item)
    if result is None:
        return None
    relation = item["facts"].get("relation", {})
    kind = relation.get("kind") if isinstance(relation, dict) else None
    role = item["role"]
    result["role"] = (
        "location_label"
        if role == "scene_address_reference"
        else "point_land_cover"
        if kind == "land_cover_at_query_point"
        else "area_statistics"
        if role == "scene_area_context"
        else "regional_environment"
        if role in {"regional_observation", "grid_temperature_observation"}
        else "spatial_relation"
    )
    return result


def _material(item):
    facts, role = item["facts"], item["role"]
    relation = facts.get("relation")
    relation = relation if isinstance(relation, dict) else {}
    kind = relation.get("kind")
    if role == "scene_address_reference":
        if not (dong := location(facts)):
            return None
        result = {"material": dong, "relation": "기록 위치의 동 이름"}
    elif role == "grid_temperature_observation":
        return {
            **fields(facts, ("temperature_c", "observation_age_s")),
            "relation": "기록 시각 이전의 해당 지역 격자 기온. 현장 체감·순간 직접 측정은 아님",
        }
    elif role == "regional_observation":
        return {
            **fields(facts, ("temperature_c", "wind_mps", "precipitation_mm", "sky")),
            "relation": "기록 시각을 포함하는 지역 관측. 개인의 체감·현장 직접 측정은 아님",
            **(
                {
                    "precipitation_meaning": "누적 구간 미확정. 순간 강도나 비가 오는 중으로 단정하지 않음"
                }
                if facts.get("precipitation_mm") is not None
                else {}
            ),
        }
    elif kind == "land_cover_at_query_point":
        result = {
            "material": fields(facts.get("material", {}), ("피복",)),
            "relation": "기록 좌표의 지도상 토지피복 분류. 현장 풍경·식생의 밀도는 미확인",
        }
    elif kind == "registered_park_point_distance" or (
        role == "scene_registered_point_distance"
        and facts.get("reference") == "registered_park_point"
    ):
        result = {
            "material": {"배경": "공원"},
            "relation": "근처에 공원 등록 지점이 있음. 경계·내부·방문 여부는 미확인",
        }
    elif kind == "registered_distribution_in_query_circle":
        distribution = {
            "등록 상가가 적은 구간": "등록 지점이 적음",
            "등록 상가가 모인 구간": "등록 지점이 모여 있음",
            "등록 상가가 산재한 구간": "등록 지점이 흩어져 있음",
        }.get(facts.get("material", {}).get("분포"))
        result = {
            "material": {
                **fields(facts.get("material", {}), ("업종구성",)),
                **({"조회영역_등록분포": distribution} if distribution else {}),
            },
            "relation": "조회 원 전체의 등록 상가 분포. 기록 지점의 길 모습·영업·방문·혼잡은 미확인",
            **fields(relation, ("radius_m", "nearest_registered_point_m")),
        }
    elif role == "scene_registered_point_distance":
        result = {
            "material": fields(facts, ("name", "park_type", "category", "query_kind")),
            "relation": "등록 지점까지의 거리. 경계·내부·방문 여부는 미확인",
            **fields(facts, ("distance_m",)),
        }
    elif role == "scene_geometry_distance":
        result = {
            "material": fields(facts, ("name",)),
            "relation": "하천 지도 형상까지의 거리. 둑길·진입·방문 여부는 미확인",
            **fields(facts, ("distance_m",)),
        }
    elif role == "scene_area_context":
        result = {
            "relation": "조회 원 안의 등록 업종 구성. 영업·방문·혼잡 여부는 미확인",
            **fields(facts, ("radius_m", "registered_count", "other_count")),
            "categories": [fields(c, ("name", "count")) for c in facts.get("categories", [])],
        }
    else:
        raise ValueError("unsupported diary prose material")
    if facts.get("temporal_basis") == "lookup_snapshot":
        result["time_meaning"] = "조회 시점의 등록·지도 정보. 산책 당시 상태는 미확인"
    # Estimated pin placement affects what can be asserted, unlike raw GPS provenance.
    if facts.get("uncertainty_m") is not None:
        result.update(fields(facts, ("uncertainty_m", "location_basis")))
    return result
