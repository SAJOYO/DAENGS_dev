"""Saved SGIS/park evidence -> bounded dictionaries, preserving point/address semantics."""

import math
from datetime import date

from daengs_walk.diary_background import Projection
from daengs_walk.diary_input import UserRecord, digest
from daengs_walk.diary_output import BackgroundPiece


def text(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        or value.lower() == "null"
    ):
        raise ValueError("invalid public label")
    return value


def project_public_background(saved, core):
    payload = saved.payload
    pin = core.pin_payload if isinstance(core, UserRecord) else None
    basis = pin["method"] if pin else "original_location"
    if (
        saved.payload_schema not in {"walk-entry-context-v1", "walk-entry-context-v2"}
        or saved.policy_version != saved.payload_schema
        or saved.tags != ("space",)
        or saved.temporal_basis != "lookup_snapshot"
        or not isinstance(payload, dict)
        or core.anchor.point is None
        or payload.get("query_point") != core.anchor.point.model_dump(mode="json")
        or payload.get("location_basis") != basis
    ):
        return Projection(reason="invalid_public_background")
    common = {
        "position_method": core.anchor.method,
        "location_basis": basis,
        "temporal_basis": "lookup_snapshot",
    }
    if pin:
        if any(payload.get(k) != pin.get(k) for k in ("uncertainty_m", "uncertainty_basis")):
            return Projection(reason="invalid_location_uncertainty")
        common.update(
            uncertainty_m=pin.get("uncertainty_m"), uncertainty_basis=pin.get("uncertainty_basis")
        )

    def piece(kind, schema, facts):
        facts = {**facts, **common}
        return BackgroundPiece(
            id="piece:" + digest({"background_id": saved.id, "facts": facts}),
            background_id=saved.id,
            kind=kind,
            schema_version=schema,
            facts=facts,
        )

    try:
        if saved.provider == "sgis":
            if (
                payload.get("format") != "sgis-dong-v1"
                or payload.get("address_type") != "administrative_dong"
            ):
                raise ValueError("invalid dong payload")
            row = payload["address"]
            parts = [text(row[k]) for k in ("sido_cd", "sgg_cd", "emdong_cd")]
            if not all(p.isdigit() for p in parts):
                raise ValueError("invalid dong code")
            return Projection(
                (
                    piece(
                        "place_reference",
                        "sgis-dong-v1",
                        {
                            "dong": text(row["emdong_nm"]),
                            "sido": text(row["sido_nm"]),
                            "sigungu": text(row["sgg_nm"]),
                            "address_type": "administrative_dong",
                            "source_ref": {"source": "sgis", "ref": ":".join(parts)},
                            "reference": "coordinate_reverse_geocoded_dong",
                        },
                    ),
                )
            )
        if (
            payload.get("format") != "public-park-nearby-v1"
            or payload.get("geometry") != "registered_point"
            or payload.get("visit_confirmed") is not False
            or payload.get("radius_m") != 250
            or payload.get("coverage") != "national_catalog_registered_points"
            or not isinstance(payload.get("items"), list)
            or len(payload["items"]) > 10
        ):
            raise ValueError("invalid park payload")
    except (ValueError, KeyError, TypeError):
        return Projection(reason="invalid_public_payload")
    pieces, rejected = [], []
    for index, row in enumerate(payload["items"]):
        try:
            values = [row[k] for k in ("latitude", "longitude", "distance_m")]
            if not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in values
            ):
                raise ValueError("invalid park position")
            lat, lng, distance = values
            if not 33 <= lat <= 39.5 or not 124 <= lng <= 132 or not 0 <= distance <= 250:
                raise ValueError("park outside radius")
            a, b = math.radians(core.anchor.point.lat), math.radians(lat)
            h = (
                math.sin((b - a) / 2) ** 2
                + math.cos(a)
                * math.cos(b)
                * math.sin(math.radians(lng - core.anchor.point.lng) / 2) ** 2
            )
            actual = 2 * 6371000 * math.asin(min(1, math.sqrt(h)))
            if abs(distance - actual) > 0.11 or actual > 250:
                raise ValueError("park distance differs from saved point")
            reference_date = date.fromisoformat(text(row["referenceDate"])).isoformat()
            pieces.append(
                piece(
                    "space_relation",
                    "public-park-nearby-v1",
                    {
                        "name": text(row["parkNm"]),
                        "park_kind": text(row["parkSe"]),
                        "source_ref": {"source": "data-go-kr-parks", "ref": text(row["manageNo"])},
                        "distance_m": distance,
                        "reference": "registered_park_point",
                        "relation": "distance_only_not_entry_or_visit",
                        "reference_date": reference_date,
                        "coverage": "national_catalog_registered_points",
                    },
                )
            )
        except (ValueError, KeyError, TypeError):
            rejected.append(index)
    return Projection(tuple(pieces), None if pieces else "no_valid_features", tuple(rejected))
