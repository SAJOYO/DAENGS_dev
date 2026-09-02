"""Canonical 산책 구간을 공간 일기의 원재료인 Cellophane으로 칠한다.

이 모듈은 좌표를 실제 지상 미터 폭의 붓으로 칠해 산책별 셀 맵을 만든다. DB,
HTTP, Place, Journey를 모르며 입력으로 받은 canonical segment만 사용한다.
"""

import hashlib
import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from daengs_walk.evidence import WalkEvidenceBundle
from daengs_walk.facts import CanonicalSegment
from daengs_walk.hex_grid import GRID_VERSION, Cell, hex_cell, mercator, metres_per_unit

PAINT_VERSION = 2
NEIGHBOUR_FACTOR = math.sqrt(3)
CANONICAL_RADIUS_U = 8.0
CANONICAL_SAMPLE_STEP_M = 1.5


@dataclass(frozen=True)
class PaintSpec:
    """한 장을 재현하고 서로 다른 계산 세대를 구별하는 조건."""

    paint_version: int
    grid_version: str
    radius_u: float
    profile_name: str
    profile_fp: str
    sample_step_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius_u", float(self.radius_u))
        object.__setattr__(self, "sample_step_m", float(self.sample_step_m))
        if self.paint_version < 1:
            raise ValueError("paint_version must be positive")
        if not self.grid_version or not self.profile_name or not self.profile_fp:
            raise ValueError("grid and profile identity must not be empty")
        if not math.isfinite(self.radius_u) or self.radius_u <= 0:
            raise ValueError("radius_u must be a finite positive number")
        if not math.isfinite(self.sample_step_m) or self.sample_step_m <= 0:
            raise ValueError("sample_step_m must be a finite positive number")

    @property
    def fingerprint(self) -> str:
        blob = json.dumps(
            {
                "grid_version": self.grid_version,
                "paint_version": self.paint_version,
                "profile_fp": self.profile_fp,
                "radius_u": self.radius_u,
                "sample_step_m": self.sample_step_m,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class BrushProfile:
    """중심에서 멀어질수록 옅어지는 실제 지상 미터 단위의 붓 단면."""

    name: str
    bands: tuple[float, ...]
    weights: tuple[float, ...]
    smooth: bool = False
    _pairs: tuple[tuple[float, float], ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("profile name must not be empty")
        if len(self.bands) != len(self.weights) or not self.bands:
            raise ValueError("bands and weights must have the same non-zero length")
        if any(not math.isfinite(band) or band <= 0 for band in self.bands):
            raise ValueError("bands must be finite positive numbers")
        if any(a >= b for a, b in zip(self.bands, self.bands[1:], strict=False)):
            raise ValueError("bands must be strictly increasing")
        if any(not math.isfinite(weight) or not 0 <= weight <= 1 for weight in self.weights):
            raise ValueError("weights must be finite numbers between zero and one")
        if self.weights[0] <= 0:
            raise ValueError("the centre weight must be positive")
        if any(a < b for a, b in zip(self.weights, self.weights[1:], strict=False)):
            raise ValueError("weights must not increase away from the centre")
        object.__setattr__(self, "_pairs", tuple(zip(self.bands, self.weights, strict=True)))

    @property
    def reach_m(self) -> float:
        return self.bands[-1]

    @property
    def fingerprint(self) -> str:
        blob = f"{self.bands}|{self.weights}|{self.smooth}"
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def weight_at(self, distance_m: float) -> float:
        if not math.isfinite(distance_m) or distance_m < 0:
            raise ValueError("distance_m must be a finite non-negative number")
        if distance_m > self._pairs[-1][0]:
            return 0.0
        if not self.smooth:
            for band, weight in self._pairs:
                if distance_m <= band:
                    return weight
            return 0.0

        previous_band, previous_weight = 0.0, self._pairs[0][1]
        for band, weight in self._pairs:
            if distance_m <= band:
                position = (distance_m - previous_band) / (band - previous_band)
                return previous_weight + (weight - previous_weight) * position
            previous_band, previous_weight = band, weight
        return 0.0


NARROW_STEP = BrushProfile("계단 3·8·20", (3.0, 8.0, 20.0), (1.0, 0.45, 0.15))


def paint_spec(
    radius_u: float,
    profile: BrushProfile,
    sample_step_m: float,
) -> PaintSpec:
    return PaintSpec(
        paint_version=PAINT_VERSION,
        grid_version=GRID_VERSION,
        radius_u=radius_u,
        profile_name=profile.name,
        profile_fp=profile.fingerprint,
        sample_step_m=sample_step_m,
    )


CANONICAL_PAINT_SPEC = paint_spec(
    CANONICAL_RADIUS_U,
    NARROW_STEP,
    CANONICAL_SAMPLE_STEP_M,
)


@dataclass(frozen=True)
class Cellophane:
    """한 산책의 시간 질량과 최대 접근 세기를 담은 셀 맵."""

    walk_id: uuid.UUID
    at: datetime
    radius_u: float
    profile: str
    occupancy: dict[Cell, float]
    peak: dict[Cell, float]
    paint_version: int
    grid_version: str
    profile_fp: str
    sample_step_m: float
    paint_fp: str

    @property
    def spec(self) -> PaintSpec:
        return PaintSpec(
            paint_version=self.paint_version,
            grid_version=self.grid_version,
            radius_u=self.radius_u,
            profile_name=self.profile,
            profile_fp=self.profile_fp,
            sample_step_m=self.sample_step_m,
        )

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("cellophane timestamp must include a timezone")
        spec = self.spec
        object.__setattr__(self, "radius_u", spec.radius_u)
        object.__setattr__(self, "sample_step_m", spec.sample_step_m)
        if self.paint_fp != spec.fingerprint:
            raise ValueError("paint_fp does not match the Cellophane calculation spec")
        if self.occupancy.keys() != self.peak.keys():
            raise ValueError("occupancy and peak must describe the same cells")
        if any(not math.isfinite(value) or value < 0 for value in self.occupancy.values()):
            raise ValueError("occupancy must contain finite non-negative values")
        if any(not math.isfinite(value) or not 0 < value <= 1 for value in self.peak.values()):
            raise ValueError("peak must contain finite values greater than zero and at most one")


def brush_stamp(
    lat: float,
    lng: float,
    radius_u: float,
    profile: BrushProfile,
) -> list[tuple[Cell, float]]:
    """점 하나가 실제 지상 미터 폭의 붓으로 남기는 셀별 원시 세기."""
    home_q, home_r = hex_cell(lat, lng, radius_u)
    scale = metres_per_unit(lat)
    reach_u = profile.reach_m / scale
    reach = math.ceil(reach_u / (NEIGHBOUR_FACTOR * radius_u)) + 1
    x, y = mercator(lat, lng)
    span = radius_u * NEIGHBOUR_FACTOR
    rise = radius_u * 1.5
    limit_sq = reach_u * reach_u
    result: list[tuple[Cell, float]] = []
    for dq in range(-reach, reach + 1):
        q = home_q + dq
        for dr in range(max(-reach, -dq - reach), min(reach, -dq + reach) + 1):
            r = home_r + dr
            dx = span * (q + r * 0.5) - x
            dy = rise * r - y
            distance_sq = dx * dx + dy * dy
            if distance_sq > limit_sq:
                continue
            weight = profile.weight_at(math.sqrt(distance_sq) * scale)
            if weight > 0:
                result.append(((q, r), weight))
    return result or [((home_q, home_r), profile.weights[0])]


def paint_sheet(
    walk_id: uuid.UUID,
    at: datetime,
    segments: Sequence[CanonicalSegment],
    spec: PaintSpec = CANONICAL_PAINT_SPEC,
    profile: BrushProfile = NARROW_STEP,
) -> Cellophane:
    """Canonical 구간만 칠하고 전체 관측 시간 질량을 정확히 보존한다."""
    if spec.profile_fp != profile.fingerprint:
        raise ValueError("paint spec and brush profile do not match")

    occupancy: dict[Cell, float] = {}
    peak: dict[Cell, float] = {}
    expected_mass = 0.0
    for segment in segments:
        if not math.isfinite(segment.dt) or segment.dt <= 0:
            raise ValueError("segment dt must be a finite positive number")
        if not math.isfinite(segment.dist) or segment.dist < 0:
            raise ValueError("segment distance must be a finite non-negative number")
        expected_mass += segment.dt
        pieces = max(1, math.ceil(segment.dist / spec.sample_step_m))
        share = segment.dt / pieces
        for index in range(pieces):
            fraction = (index + 0.5) / pieces
            lat = segment.a.lat + (segment.b.lat - segment.a.lat) * fraction
            lng = segment.a.lng + (segment.b.lng - segment.a.lng) * fraction
            stamp = brush_stamp(lat, lng, spec.radius_u, profile)
            total_weight = math.fsum(weight for _, weight in stamp)
            if total_weight <= 0:
                raise RuntimeError("brush stamp has no positive mass")
            for cell, raw_weight in stamp:
                occupancy[cell] = occupancy.get(cell, 0.0) + share * raw_weight / total_weight
                peak[cell] = max(peak.get(cell, 0.0), raw_weight)

    actual_mass = math.fsum(occupancy.values())
    if not math.isclose(actual_mass, expected_mass, rel_tol=1e-12, abs_tol=1e-9):
        raise RuntimeError("Cellophane occupancy did not preserve canonical segment time")
    return Cellophane(
        walk_id=walk_id,
        at=at,
        radius_u=spec.radius_u,
        profile=spec.profile_name,
        occupancy=occupancy,
        peak=peak,
        paint_version=spec.paint_version,
        grid_version=spec.grid_version,
        profile_fp=spec.profile_fp,
        sample_step_m=spec.sample_step_m,
        paint_fp=spec.fingerprint,
    )


def build_cellophane(evidence: WalkEvidenceBundle) -> Cellophane:
    """산책 측정 결과를 제품에서 사용하는 단 하나의 canonical spec으로 칠한다."""
    return paint_sheet(
        walk_id=evidence.facts.walk_id,
        at=evidence.facts.started_at,
        segments=evidence.segments,
    )
