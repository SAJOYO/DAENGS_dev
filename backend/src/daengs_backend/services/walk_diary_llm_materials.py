"""Allowlisted prose facts. Slot selection and source diagnostics stay upstream."""


def fields(value, names):
    # Never copy nested provider objects, even under a familiar field name.
    return {k: value[k] for k in names if type(value.get(k)) in (str, int, float)}


def location(facts):
    return fields(facts, ("dong", "sido", "sigungu", "address", "address_type"))


def material(item):
    facts, role = item["facts"], item["role"]
    relation = facts.get("relation")
    relation = relation if isinstance(relation, dict) else {}
    kind = relation.get("kind")
    if role == "scene_address_reference":
        result = {"material": location(facts), "relation": "기록 위치의 행정·주소 참조"}
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
    elif kind == "registered_park_point_distance":
        result = {
            "material": fields(facts.get("material", {}), ("공원명", "공원종류")),
            "relation": "공원 등록 지점까지의 거리. 경계·내부·방문 여부는 미확인",
            **fields(relation, ("distance_m",)),
        }
    elif kind == "registered_distribution_in_query_circle":
        result = {
            "material": fields(facts.get("material", {}), ("분포", "업종구성")),
            "relation": "조회 원 안의 등록 상가 분포. 영업·방문·혼잡 여부는 미확인",
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
