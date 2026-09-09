"""Adapt real canonical observations to geo's shared scene selector. No simulator inputs."""

from datetime import datetime

from daengs_walk.storyboard_selection import SelectionPolicy, select_nodes

LABELS = {"sniffing": "킁킁", "excretion": "배설", "barking": "짖기", "note": "특별한 순간"}


def scene_inputs(evidence, entries, *, session_id, pet_id=None, references=()):
    start = evidence.facts.started_at
    nodes, offset, block, previous = [], 0.0, -1, None

    def node(fix):
        return {
            "route_m": offset,
            "block": block,
            "elapsed_s": (fix.at - start).total_seconds(),
            "observation": {
                "client_seq": fix.client_seq,
                "chain_index": fix.chain_index,
                "at": fix.at.isoformat(),
                "lat": fix.lat,
                "lng": fix.lng,
            },
            "location": {
                "lat": fix.lat,
                "lng": fix.lng,
                "accuracy_m": fix.accuracy_m,
                "captured_at": fix.at.isoformat(),
            },
        }

    for segment in evidence.segments:
        if previous != (segment.chain_index, segment.a.client_seq):
            block += 1
            nodes.append(node(segment.a))
        offset += segment.dist if segment.moving else 0
        end = node(segment.b)
        end.update(
            speed=segment.dist / segment.dt,
            duration_s=segment.dt,
            start_s=(segment.a.at - start).total_seconds(),
        )
        nodes.append(end)
        previous = (segment.chain_index, segment.b.client_seq)
    projected = []
    for entry in entries:
        content = entry.get("content")
        if content is None:
            continue
        at = datetime.fromisoformat(content["recorded_at"])
        elapsed = (at - start).total_seconds()
        # Route position requires an observed segment containing the event, never a gap bridge.
        containing = next(
            (n for n in nodes if "start_s" in n and n["start_s"] <= elapsed <= n["elapsed_s"]), None
        )
        code = content["behavior_code"] if content["kind"] == "behavior" else "note"
        projected.append(
            {
                "id": entry["id"],
                "revision": entry["revision"],
                "pet_id": content.get("pet_id"),
                "accepted": True,
                "kind": content["kind"],
                "behavior_code": content.get("behavior_code"),
                "label": LABELS[code],
                "note": content.get("note") or "",
                "elapsed_s": elapsed,
                "accepted_distance_m": containing["route_m"] if containing else 0,
                "location": content.get("location"),
                "route_known": containing is not None,
            }
        )
        pin = entry.get("pin")
        if pin is not None:
            projected[-1]["pin"] = {
                "revision": entry["pin_revision"],
                **{
                    k: pin[k]
                    for k in (
                        "resolution_id",
                        "state",
                        "method",
                        "target_at",
                        "point",
                        "uncertainty_m",
                        "uncertainty_basis",
                    )
                },
            }
            # Pin-based lookup is entry-owned; do not claim it covers the raw route.
            projected[-1]["route_known"] = False
    projected.sort(key=lambda e: (e["elapsed_s"], e["id"]))
    selection = select_nodes(
        nodes,
        projected,
        SelectionPolicy(),
        references,
        session_id=session_id,
        pet_id=pet_id,
        started_at=start,
    )
    selection["boundary_observations"] = {
        "start": nodes[0]["observation"] if nodes else None,
        "end": nodes[-1]["observation"] if nodes else None,
    }
    selection["entry_anchors"] = [
        {
            "id": "entry:" + e["id"],
            "location": e["pin"]["point"],
            "location_basis": e["pin"]["method"],
        }
        for e in projected
        if e.get("pin", {}).get("point") is not None
    ][:8]
    return projected, selection
