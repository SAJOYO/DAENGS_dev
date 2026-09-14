"""Convert retained catalog observations into diary normalization inputs."""

from daengs_backend.services.walk_background.catalogs import area as catalog
from daengs_walk.diary.space.materials import AreaInput
from daengs_walk.value_contracts import digest


def normalization_input(value, kind, point, radius_m):
    pages = value["normalization_pages"]
    if digest(pages) != value["normalization_sha256"]:
        raise ValueError("normalization catalog hash mismatch")
    if kind == "commerce" and not catalog.covers(value, point, radius_m):
        raise ValueError("normalization footprint is outside catalog coverage")
    return AreaInput(query_point=point, radius_m=radius_m, pages=pages)
