"""Model-facing projection for the experimental interval ledger; no allocation policy."""

from daengs_walk.diary.contracts.input import Anchor
from daengs_walk.diary.relational.writer_meaning import event_anchor_view, present, vocabulary


def interval_writer_view(relation):
    if relation["kind"] == "end_near_start":
        return {
            "id": relation["id"],
            "kind": relation["kind"],
            "start": event_anchor_view(Anchor.model_validate(relation["start_anchor"])),
            "end": event_anchor_view(Anchor.model_validate(relation["end_anchor"])),
            "distance_m": relation["distance_m"],
            "tolerance_m": relation["tolerance_m"],
            "scope": {"kind": "session_endpoints"},
        }
    result = {
        "id": relation["id"],
        "kind": relation["kind"],
        "support_started_at": relation["support_started_at"],
        "support_ended_at": relation["support_ended_at"],
        **present(
            representative_event_at=relation["representative_event_at"],
            event_time_precision=vocabulary(
                relation["event_time_basis"], {None: None, "detector_estimate": "estimated"}
            )
            if relation["representative_event_at"] is not None
            else None,
            certainty=vocabulary(
                relation.get("source_status"),
                {
                    None: None,
                    "candidate": "inferred",
                    "confirmed": "supported",
                    "verified": "supported",
                },
            ),
        ),
        "scope": {"kind": "walk_movement_support_interval"},
    }
    if relation["kind"] == "retrace":
        result["prior_path"] = {
            "relationship": "earlier_route_in_this_walk",
            "matched_reverse_m": relation["prior_path"]["matched_reverse_m"],
        }
    return result
