"""Canonical claim values shared by spatial interpretation and slot adapters."""


def normalize(value):
    """JSON numeric spelling (40 vs 40.0) is not a distinct assertion."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value
