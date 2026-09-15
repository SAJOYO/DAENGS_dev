"""Point background identity shared by comparison and narrative delivery.

Source object IDs remain on facts; they do not distinguish equal point labels.
Surrounding objects and query areas keep their own identity rules.
"""

POINT_ATTRIBUTES = {"road": "name", "land_cover": "classification"}


def point_meaning(family, value):
    return {"attribute": value.get(POINT_ATTRIBUTES[family]), "layer": value.get("layer")}
