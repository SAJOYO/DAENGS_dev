"""Road-name-only normalization; building addresses are never a fallback."""

import re


def road_name(value):
    if isinstance(value, str) and re.fullmatch(r"[가-힣A-Za-z0-9·.\-]+(?:로|길)", value.strip()):
        return value.strip()
    return None
