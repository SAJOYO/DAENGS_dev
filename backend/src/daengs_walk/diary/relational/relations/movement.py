"""Reuse verified device claims; preserve interval scope and source IDs."""


def movement_observations(frame, previous, catalog):
    """No dog actions or spatial labels; absence of a claim is not normal speed."""
    if (
        previous is None
        or catalog is None
        or frame["block"] is None
        or frame["block"] != previous["block"]
    ):
        return []
    from daengs_walk.diary.route.pin_context import MEANINGS

    left, at = previous["at_s"], frame["at_s"]
    result = []
    for claim in sorted(catalog.claims, key=lambda c: (c.get("event_s", c["start_s"]), c["id"])):
        if claim["block"] != frame["block"]:
            continue
        if "event_s" in claim:
            if not left <= claim["event_s"] < at:
                continue
            scope = {"at_pin_s": claim["event_s"] - at}
            when = f"현재 기록 {at - claim['event_s']:g}초 전"
        else:
            start, end = max(left, claim["start_s"]), min(at, claim["end_s"])
            if start >= end:
                continue
            scope = {"from_pin_s": start - at, "to_pin_s": end - at, "duration_s": end - start}
            when = f"현재 기록 {at - start:g}~{at - end:g}초 전"
        meaning = MEANINGS.get(claim["meaning"], claim["meaning"])
        result.append(
            {
                "id": f"motion{len(result) + 1}",
                "subject": "recording_device",
                "code": claim["meaning"],
                "meaning": meaning,
                "source_ids": [claim["id"]],
                "source_claim": claim,
                **scope,
                "text": f"{when}: {meaning}",
                "origin": "computed_observation",
            }
        )
    return result


def evaluate(frame, previous):
    from .contracts import slot

    if previous is None:
        return slot("not_applicable", "No preceding record interval")
    observations = frame.get("relation_observations")
    if observations is None:
        return slot("insufficient_evidence", "No comparable verified movement interval")
    items = [o for o in observations if o["code"] != "retrace"]
    return slot(
        "confirmed" if items else "no_change",
        "Verified device observations only; no claim does not imply normal pace",
        items,
    )
