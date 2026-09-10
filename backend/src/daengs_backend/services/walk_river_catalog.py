"""EGIS polygons supply distance; river standard rows remain separate metadata."""

from datetime import date

from pyproj import Transformer
from shapely import get_num_coordinates
from shapely.errors import ShapelyError
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import nearest_points, transform

from daengs_backend.services import walk_area_catalog as catalog
from daengs_backend.services.walk_public_http import PublicSourceError, get_json
from daengs_walk.diary_input import digest

STANDARD = "https://api.data.go.kr/openapi/tn_pubr_public_river_info_api"
EGIS = "https://api.mcee.go.kr/geoserver/wfs"
TO_WEB = Transformer.from_crs(5179, 3857, always_xy=True)
FROM_WEB = Transformer.from_crs(3857, 5179, always_xy=True)


def standard_row(raw):
    return {
        "id": catalog.label(raw["rvrCd"]),
        "name": catalog.label(raw["rvrNm"]),
        "reference_date": date.fromisoformat(raw["dataCrtrYmd"]).isoformat(),
    }


async def standard_metadata(transport, key):
    """Optional name metadata cannot prevent publishing independently valid EGIS shapes."""
    try:
        if not key.strip():
            raise PublicSourceError("provider_not_configured")
        raw, receipts = await catalog.pages(transport, STANDARD, key, {})
        rows, rejected = catalog.unique_rows(raw, standard_row)
        return {
            "rows": rows,
            "pages": receipts,
            "rejected_rows": rejected,
            "status": "partial" if rejected else "known" if rows else "empty",
            "reason": "invalid_standard_rows" if rejected else None,
        }
    except PublicSourceError as exc:
        reason = exc.reason
    except (ValueError, KeyError, TypeError, OverflowError):
        reason = "invalid_standard_response"
    return {
        "rows": [],
        "pages": [],
        "rejected_rows": 0,
        "status": "unavailable",
        "reason": reason,
    }


async def refresh(transport, key, path, point, radius):
    region = catalog.area(point, radius)
    x, y = catalog.xy(point)
    clip = box(x - radius, y - radius, x + radius, y + radius)
    bounds = TO_WEB.transform_bounds(*clip.bounds, densify_pts=21)
    body = await get_json(
        transport,
        EGIS,
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "typeNames": "me:adm_river",
            "outputFormat": "application/json",
            "count": 300,
            "srsName": "EPSG:3857",
            "bbox": ",".join(str(v) for v in bounds) + ",urn:ogc:def:crs:EPSG::3857",
        },
        limit=8_000_000,
    )
    if not isinstance(body, dict):
        raise PublicSourceError("invalid_river_response")
    features = body["features"]
    if (
        body.get("type") != "FeatureCollection"
        or body.get("crs", {}).get("properties", {}).get("name")
        not in {
            "urn:ogc:def:crs:EPSG::3857",
            "EPSG:3857",
        }
        or not isinstance(features, list)
        or len(features) >= 300
        or body.get("numberMatched") != len(features)
        or body.get("numberReturned") != len(features)
    ):
        raise PublicSourceError("river_geometry_incomplete_or_wrong_crs")

    def feature_row(feature):
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            raise TypeError("invalid river feature")
        try:
            return normalize_feature(feature)
        except ShapelyError:
            raise ValueError("invalid river geometry") from None

    def normalize_feature(feature):
        geometry = shape(feature["geometry"])
        if (
            geometry.geom_type not in {"Polygon", "MultiPolygon"}
            or geometry.is_empty
            or not geometry.is_valid
        ):
            raise ValueError("invalid river geometry")
        if get_num_coordinates(geometry) > 100_000:
            raise ValueError("river geometry too large")
        projected = transform(FROM_WEB.transform, geometry).intersection(clip)
        if projected.is_empty:
            return None
        if not projected.is_valid or projected.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("invalid clipped river geometry")
        return {
            "id": catalog.label(feature["id"]),
            "name": catalog.label(feature["properties"]["name"]),
            "geometry": mapping(projected),
        }

    rows, rejected = catalog.unique_rows(features, feature_row)
    standard = await standard_metadata(transport, key)
    return catalog.publish(
        path,
        "river",
        region,
        rows,
        rejected=rejected,
        receipts=[digest(body)],
        extra={
            "geometry_crs": "EPSG:5179",
            "geometry_reference_date": None,
            "standard": standard,
        },
    )


def nearby(value, point):
    if not catalog.covers(value, point, 250):
        raise PublicSourceError("outside_catalog_coverage")
    if value.get("geometry_crs") != "EPSG:5179":
        raise ValueError("invalid river CRS")
    standard = value["standard"]
    # Old v1 catalogs did not carry a separate metadata status.
    standard_status = standard.get("status") or (
        "partial" if standard["rejected_rows"] else "known" if standard["rows"] else "empty"
    )
    anchor = Point(*catalog.xy(point))
    found = []
    for row in value["rows"]:
        geometry = shape(row["geometry"])
        if (
            not geometry.is_valid
            or geometry.is_empty
            or geometry.geom_type not in {"Polygon", "MultiPolygon"}
        ):
            raise ValueError("invalid stored river geometry")
        distance = anchor.distance(geometry)
        if distance > 250:
            continue
        nearest = nearest_points(anchor, geometry)[1]
        lng, lat = catalog.REVERSE.transform(nearest.x, nearest.y)
        # A matching name is not an entity join; never substitute standard endpoints for shape.
        matches = [r for r in standard["rows"] if r["name"] == row["name"]]
        found.append(
            {
                "id": row["id"],
                "name": row["name"],
                "distance_m": round(distance, 1),
                "nearest_point": {"lat": lat, "lng": lng},
                "geometry_sha256": digest(row["geometry"]),
                "standard_name_matches": matches[:5],
                "standard_name_match_count": len(matches),
            }
        )
    found.sort(key=lambda row: (row["distance_m"], row["id"]))
    return {
        "format": "public-river-nearby-v1",
        "query_point": point,
        "items": found[:3],
        "radius_m": 250,
        "geometry": "egis_river_polygon",
        "geometry_crs": "EPSG:5179",
        "geometry_reference_date": None,
        "catalog_area": value["area"],
        "catalog_sha256": value["sha256"],
        "coverage": "egis_catalog_geometry",
        "complete": value["rejected_rows"] == 0 and len(found) <= 3,
        "standard_coverage": "name_lookup_not_identity_join",
        "standard_rejected_rows": standard["rejected_rows"],
        "standard_status": standard_status,
        "standard_reason": standard.get("reason"),
        "relation": "geometry_distance_not_bank_path_or_visit",
    }
