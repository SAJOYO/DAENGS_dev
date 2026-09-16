"""A recorder's viewpoint and session companions, separate from selected evidence."""

VERSION = "guardian-walk-context-v1"


def narration_context(walk_context):
    # Historical requests had no narrator contract. Keep their wire input unchanged.
    if not isinstance(walk_context, dict) or "narration_version" not in walk_context:
        return None
    if (
        walk_context["narration_version"] != VERSION
        or walk_context.get("record_kind") != "guardian_walk_diary"
        or not isinstance(walk_context.get("companions"), list)
    ):
        raise ValueError("invalid walk narration context")
    companions, identities = [], set()
    for companion in walk_context["companions"]:
        if not isinstance(companion, dict):
            raise TypeError("invalid walk companion")
        identity, name = companion.get("id"), companion.get("name")
        if (
            not isinstance(identity, str)
            or not identity
            or identity in identities
            or (name is not None and not isinstance(name, str))
        ):
            raise ValueError("invalid walk companion")
        identities.add(identity)
        companions.append({"name": name.strip() or None if name is not None else None})
    return {
        "narrator": "이 산책을 기록한 보호자(나)",
        "companions": companions,
        "scope": "현재 장면",
    }
