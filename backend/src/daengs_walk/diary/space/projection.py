"""Finite saved-provider projections; no text generation or spatial re-interpretation.

Port of Geo's dev_context_projection, bound to the current Dev input contract.
Other providers need their own projector before occupying a stamp's slots.
"""

import math
from dataclasses import dataclass

from daengs_walk.diary.contracts.input import SavedBackground, UserRecord, digest
from daengs_walk.diary.contracts.output import BackgroundPiece


@dataclass(frozen=True)
class Projection:
    pieces: tuple[BackgroundPiece, ...] = ()
    reason: str | None = None
    rejected_rows: tuple[int, ...] = ()


def project_background(saved: SavedBackground, core) -> Projection:
    if saved.provider == "kakao-local":
        from daengs_walk.diary.space.kakao import project_kakao_background

        return project_kakao_background(saved, core)
    if saved.provider in {"sgis", "data-go-kr-parks", "data-go-kr-commerce", "egis-rivers"}:
        from daengs_walk.diary.space.public import project_public_background

        return project_public_background(saved, core)
    if (
        saved.provider != "place-search"
        or saved.payload_schema not in {"walk-entry-context-v1", "walk-entry-context-v2"}
        or saved.policy_version != saved.payload_schema
        or saved.tags != ("space",)
        or saved.temporal_basis != "lookup_snapshot"
    ):
        return Projection(reason="unsupported_projection")
    payload = saved.payload
    if (
        payload is None
        or payload.get("geometry") != "registered_location"
        or payload.get("visit_confirmed") is not False
        or payload.get("radius_m") != 250
        or payload.get("coverage") != "selected_place_categories_only"
        or not isinstance(payload.get("items"), list)
        or len(payload["items"]) > 30
    ):
        return Projection(reason="invalid_provider_payload")
    pin = core.pin_payload if isinstance(core, UserRecord) else None
    basis = pin["method"] if pin is not None else "original_location"
    if payload.get("location_basis", basis) != basis:
        return Projection(reason="invalid_location_basis")
    pieces, rejected = [], []
    for index, item in enumerate(payload["items"]):
        try:
            place = item["place"]
            distance = place["distance_m"]
            key = place["key"]
            if (
                isinstance(distance, bool)
                or not isinstance(distance, (int, float))
                or not math.isfinite(distance)
                or not 0 <= distance <= 250
                or item["kind"] not in {"leisure", "cafe", "restaurant"}
                or not all(
                    isinstance(s, str) and 0 < len(s) <= 200 and s.strip()
                    for s in (place["name"], key["source"], key["ref"])
                )
            ):
                raise ValueError("invalid place row")
            facts = {
                "name": place["name"],
                "query_kind": item["kind"],
                "source_ref": {"source": key["source"], "ref": key["ref"]},
                "distance_m": distance,
                "reference": "registered_location",
                "relation": "distance_only_not_entry_or_visit",
                "calculation": "server_reported_not_recomputed",
                "temporal_basis": "lookup_snapshot",
                "position_method": core.anchor.method,
                "location_basis": basis,
                "coverage": "selected_place_categories_only",
            }
            if pin is not None:
                facts.update(
                    uncertainty_m=pin.get("uncertainty_m"),
                    uncertainty_basis=pin.get("uncertainty_basis"),
                )
            pieces.append(
                BackgroundPiece(
                    id="piece:" + digest({"background_id": saved.id, "facts": facts}),
                    background_id=saved.id,
                    kind="space_relation",  # Absolute dong/address is a different projector.
                    schema_version="place-nearby-v1",
                    facts=facts,
                )
            )
        except (KeyError, TypeError, ValueError):
            rejected.append(index)
    return Projection(tuple(pieces), None if pieces else "no_valid_features", tuple(rejected))


def piece_identity(piece: BackgroundPiece):
    """Only deduplicate a provider's actual entity ID, never a repeated place type/name."""
    return (piece.kind, digest(piece.facts["source_ref"]))


def piece_rank(piece: BackgroundPiece, saved: SavedBackground):
    # Area aggregates use their footprint; a missing point distance must not become zero.
    return (
        piece.facts.get("distance_m", piece.facts.get("radius_m", 0)),
        -saved.retrieved_at.timestamp(),
        saved.id,
        piece.id,
    )
