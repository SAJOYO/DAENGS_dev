"""Project saved Kakao Local responses without promoting nearby places to visits."""

import math

from daengs_walk.diary_background import Projection
from daengs_walk.diary_input import digest
from daengs_walk.diary_output import BackgroundPiece


def project_kakao_background(saved, core):
    payload = saved.payload
    if (
        saved.payload_schema != "scene-kakao-context-v1"
        or saved.policy_version != saved.payload_schema
        or saved.tags != ("space",)
        or saved.temporal_basis != "lookup_snapshot"
        or not isinstance(payload, dict)
        or core.anchor.point is None
        or payload.get("query_point") != core.anchor.point.model_dump(mode="json")
        or payload.get("radius_m") != 250
    ):
        return Projection(reason="invalid_kakao_background")
    pieces, rejected = [], []

    def piece(kind, schema, facts):
        facts = {
            **facts,
            "temporal_basis": "lookup_snapshot",
            "position_method": core.anchor.method,
            "interpretation": "현재 조회한 등록 정보. 산책 당시 영업·방문·진입·행동은 확인하지 않음.",
        }
        return BackgroundPiece(
            id="piece:" + digest({"background_id": saved.id, "facts": facts}),
            background_id=saved.id,
            kind=kind,
            schema_version=schema,
            facts=facts,
        )

    try:
        responses = payload["search_responses"]
        if not isinstance(responses, list) or len(responses) > 3:
            raise ValueError("invalid search responses")
        for response in responses:
            category = response["category"]
            if category not in {"park", "CE7", "FD6"} or len(response["body"]["documents"]) > 15:
                raise ValueError("invalid search category")
            for row in response["body"]["documents"]:
                index = len(pieces) + len(rejected)
                try:
                    distance = float(row["distance"])
                    name, identity = row["place_name"], row["id"]
                    if (
                        not math.isfinite(distance)
                        or not 0 <= distance <= 250
                        or not all(
                            isinstance(v, str) and 0 < len(v.strip()) <= 200
                            for v in (name, identity)
                        )
                        or (category == "park" and "공원" not in row.get("category_name", ""))
                        or (category != "park" and row.get("category_group_code") != category)
                    ):
                        raise ValueError("invalid place row")
                    pieces.append(
                        piece(
                            "space_relation",
                            "kakao-place-nearby-v1",
                            {
                                "name": name,
                                "source_ref": {"source": "kakao-local", "ref": identity},
                                "distance_m": distance,
                                "reference": "registered_location",
                                "relation": "distance_only_not_entry_or_visit",
                                "calculation": "provider_reported_not_recomputed",
                                "category": row["category_name"],
                                "coverage": "selected_place_categories_only",
                            },
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    rejected.append(index)
        address = payload.get("address_response")
        if address is not None:
            for row in address.get("documents", [])[:1]:
                value = row.get("address") or {}
                parts = [value.get(f"region_{i}depth_name") for i in (1, 2, 3)]
                if not all(isinstance(v, str) and 0 < len(v.strip()) <= 100 for v in parts):
                    continue
                # Kakao's address is a legal-dong reference, not SGIS administrative-dong evidence.
                pieces.append(
                    piece(
                        "place_reference",
                        "kakao-address-v1",
                        {
                            "address": " ".join(parts),
                            "address_type": "legal_dong",
                            "source_ref": {"source": "kakao-local-address", "ref": " ".join(parts)},
                            "reference": "coordinate_reverse_geocoded_legal_dong",
                        },
                    )
                )
    except (KeyError, ValueError, TypeError, AttributeError):
        return Projection(reason="invalid_kakao_payload")
    return Projection(tuple(pieces), None if pieces else "no_valid_features", tuple(rejected))
