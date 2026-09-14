"""Finite route-pattern meanings with observed support, pivot and calculation basis."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from daengs_walk.diary.contracts.input import DiaryContract, digest
from daengs_walk.diary.route.geometry import RoutePatternPolicy, extract_route_patterns

PATTERN_CASES = {
    "local_stay": {"이동 상태": "한곳 주변에 머묾"},
    "straight_run": {"경로 형태": "대체로 곧게 이어지는 이동"},
    "turn_left": {"방향 전환": "왼쪽으로 꺾이는 이동"},
    "turn_right": {"방향 전환": "오른쪽으로 꺾이는 이동"},
    "turn_reverse": {"방향 전환": "진행 방향이 반대로 바뀌는 이동"},
    "retrace": {"이전 경로와의 관계": "방금 지나온 구간을 되짚는 이동"},
}


class RoutePatternMaterial(DiaryContract):
    id: str
    kind: Literal["local_stay", "straight_run", "turn", "retrace"]
    case_id: str
    material: dict[str, str]
    subject: Literal["recording_device"] = "recording_device"
    support: dict[str, JsonValue]
    anchor: dict[str, JsonValue]
    metrics: dict[str, JsonValue]
    quality: dict[str, JsonValue]

    @model_validator(mode="after")
    def finite_meaning(self):
        expected = (
            "turn_" + str(self.metrics.get("direction")) if self.kind == "turn" else self.kind
        )
        if self.case_id != expected or self.material != PATTERN_CASES.get(expected):
            raise ValueError("route pattern differs from the finite dictionary")
        return self


class RoutePatternCatalog(DiaryContract):
    format: Literal["route-pattern-materials-v1"] = "route-pattern-materials-v1"
    source_revision: str
    dictionary_version: str = Field(default_factory=lambda: digest(PATTERN_CASES))
    policy: RoutePatternPolicy
    materials: tuple[RoutePatternMaterial, ...]
    shape_runs: tuple[dict[str, JsonValue], ...]
    quality_audit: tuple[dict[str, JsonValue], ...]


def normalize_route_patterns(route, source_revision, policy=None):
    policy = policy or RoutePatternPolicy()
    extracted = extract_route_patterns(route, policy)
    basis = {
        "source_revision": source_revision,
        "policy": policy.model_dump(mode="json"),
        "dictionary_version": digest(PATTERN_CASES),
    }
    materials = []
    for raw in extracted["materials"]:
        case = "turn_" + raw["metrics"]["direction"] if raw["kind"] == "turn" else raw["kind"]
        data = {k: raw[k] for k in ("kind", "subject", "support", "anchor", "metrics", "quality")}
        data.update(case_id=case, material=PATTERN_CASES[case])
        materials.append(RoutePatternMaterial(id="route-pattern:" + digest([basis, data]), **data))
    return RoutePatternCatalog(
        **basis,
        materials=tuple(materials),
        shape_runs=tuple(extracted["shape_runs"]),
        quality_audit=tuple(extracted["quality_audit"]),
    )


class RoutePatternBindingPolicy(DiaryContract):
    version: Literal["route-pattern-binding-v1"] = "route-pattern-binding-v1"
    geometry: RoutePatternPolicy = Field(default_factory=RoutePatternPolicy)
    # Retain the GEO post-walk attachment experiment's explicit pivot proximity.
    turn_near_s: float = Field(default=20, ge=0, le=120)
    focus_radius_m: float = Field(default=15, ge=0, le=100)
