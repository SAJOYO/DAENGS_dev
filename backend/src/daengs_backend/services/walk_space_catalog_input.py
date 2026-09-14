"""Retain normalization fields before legacy nearby summaries and row rejection.

Pages keep provider pagination/metadata, including malformed and conflicting rows.
This is a second read path, not a reconstruction from lossy nearby summaries.
"""

from copy import deepcopy

from daengs_walk.value_contracts import digest

FIELDS = {
    "commerce": ("bizesId", "lat", "lon", "indsLclsCd", "indsMclsCd"),
    "park": ("manageNo", "parkNm", "parkSe", "latitude", "longitude", "parkAr", "referenceDate"),
}


def retain_page(raw, kind):
    page = deepcopy(raw.get("response", raw))
    body = page.get("body")
    if not isinstance(body, dict):
        return page
    items = body.get("items")
    rows = items.get("item") if isinstance(items, dict) else items
    if isinstance(rows, list):
        retained = [
            {key: row[key] for key in FIELDS[kind] if key in row} if isinstance(row, dict) else row
            for row in rows
        ]
        body["items"] = {**items, "item": retained} if isinstance(items, dict) else retained
    return page


def retained_fields(pages):
    return {"normalization_pages": pages, "normalization_sha256": digest(pages)}
