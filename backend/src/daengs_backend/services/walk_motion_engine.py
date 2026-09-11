"""Frozen motion-v1 replay, ported from APP #319 (SegmentPolicy/MotionEstimator).

No DB, defaults, wall-clock duration or legacy filtering. Input is a validated sealed journal.
Distances use the existing stored raw coordinates; this cannot recover pre-upload precision.
"""

import json
import math
import struct
from collections import Counter, deque
from dataclasses import dataclass

from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
from daengs_backend.services.walk_motion_contract import validate_manifest, validate_observations


def _float32(value):
    if value is None:
        return None
    try:
        return struct.unpack("!f", struct.pack("!f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def _bits(value):
    return None if value is None else struct.unpack("!f", bytes.fromhex(value))[0]


def _seconds(start, end):
    return (end - start) / 1_000_000_000.0


@dataclass(frozen=True)
class Point:
    raw: object
    observation: MotionObservation

    @property
    def seq(self):
        return self.observation.client_seq

    @property
    def time(self):
        return self.observation.elapsed_realtime_nanos

    @property
    def accuracy(self):
        # Android RecordedFix.accuracyM is a Float even though the raw wire uses JSON numbers.
        return _float32(self.raw.accuracy_m)

    def distance(self, other):
        lat1, lat2 = math.radians(float(self.raw.lat)), math.radians(float(other.raw.lat))
        dlat = lat2 - lat1
        dlng = math.radians(float(other.raw.lng) - float(self.raw.lng))
        h = min(
            1.0,
            max(
                0.0,
                math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2,
            ),
        )
        return 6_371_000.0 * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))

    def position(self, config):
        if self.raw.is_mock:
            return "MOCK"
        if not (-90 <= float(self.raw.lat) <= 90 and -180 <= float(self.raw.lng) <= 180):
            return "INVALID"
        accuracy = self.accuracy
        if accuracy is None:
            return "UNCERTAIN"
        if not math.isfinite(accuracy) or accuracy < 0:
            return "INVALID"
        return "UNCERTAIN" if accuracy > config["maxPositionAccuracyM"] else "USABLE"

    def same_measurement(self, other):
        # Kotlin data-class equality: NaNs compare equal, but signed zeros differ. Receipt fields
        # and sequence are deliberately excluded, just as MotionPolicyEngine.sameMeasurement.
        def normalized(bits):
            value = _bits(bits)
            return "nan" if value is not None and math.isnan(value) else bits

        return (
            self.raw.at == other.raw.at
            and float(self.raw.lat).hex() == float(other.raw.lat).hex()
            and float(self.raw.lng).hex() == float(other.raw.lng).hex()
            and (None if self.accuracy is None else self.accuracy.hex())
            == (None if other.accuracy is None else other.accuracy.hex())
            and self.raw.is_mock == other.raw.is_mock
            and all(
                normalized(getattr(self.observation, k))
                == normalized(getattr(other.observation, k))
                for k in (
                    "speed_mps_bits",
                    "speed_accuracy_mps_bits",
                    "bearing_degrees_bits",
                    "bearing_accuracy_degrees_bits",
                )
            )
            and self.observation.provider == other.observation.provider
        )


class Estimator:
    def __init__(self, config):
        self.config = config
        self.window = deque()

    def estimate(self, point):
        c = self.config
        reasons = set()
        position = point.position(c)
        if position != "USABLE":
            reasons.add(
                {"UNCERTAIN": "POSITION_UNCERTAIN", "INVALID": "INVALID_POSITION", "MOCK": "MOCK"}[
                    position
                ]
            )
        while self.window and _seconds(self.window[0].time, point.time) > c["windowSeconds"]:
            self.window.popleft()
        previous = self.window[0] if self.window and position == "USABLE" else None
        dt = _seconds(previous.time, point.time) if previous else 0.0
        displacement = previous.distance(point) if previous else 0.0
        uncertainty = (previous.accuracy + point.accuracy) / max(dt, 0.000001) if previous else 0.0
        coordinate = (
            displacement / dt
            if previous
            and dt >= c["minCoordinateSeconds"]
            and displacement > (previous.accuracy + point.accuracy) * c["noiseRadiusFactor"]
            else None
        )
        speed, source, quality = None, "UNKNOWN", "UNKNOWN"
        device = _bits(point.observation.speed_mps_bits)
        accuracy = _bits(point.observation.speed_accuracy_mps_bits)
        if point.raw.is_mock:
            pass
        elif device is None:
            reasons.add("SPEED_MISSING")
        elif not math.isfinite(device) or device < 0:
            reasons.add("SPEED_INVALID")
        elif accuracy is not None and (
            not math.isfinite(accuracy) or accuracy < 0 or accuracy > c["maxSpeedAccuracyMps"]
        ):
            reasons.add("SPEED_UNCERTAIN")
        elif accuracy is None:
            reasons.add("SPEED_UNCERTAIN")
            if coordinate is not None and abs(device - coordinate) > max(
                c["speedAgreementMps"], uncertainty
            ):
                reasons.add("SPEED_CONFLICT")
            else:
                speed, source, quality = (
                    device,
                    "DEVICE",
                    "ESTIMATED" if coordinate is not None else "UNVERIFIED",
                )
        else:
            speed, source, quality = device, "DEVICE", "TRUSTED"
        if speed is None and coordinate is not None and not point.raw.is_mock:
            speed, source, quality = coordinate, "COORDINATE_WINDOW", "ESTIMATED"
        if position == "USABLE":
            self.window.append(point)
            while len(self.window) > c["windowSize"]:
                self.window.popleft()
        movement = (
            "UNKNOWN"
            if speed is None
            else "HIGH_SPEED_SUSPECTED"
            if speed > c["maxWalkingSpeedMps"]
            else "UNKNOWN"
            if quality == "UNVERIFIED"
            else "STILL"
            if speed <= c["stationarySpeedMps"]
            else "MOVING"
        )
        return {
            "position_quality": position,
            "speed_mps": speed,
            "speed_source": source,
            "speed_quality": quality,
            "movement": movement,
            "reasons": sorted(reasons),
        }


class Segments:
    def __init__(self, config):
        self.config = config
        self.anchor = self.last_usable = None
        self.barriers = set()
        self.needs_reentry = False
        self.reentry_count = 0
        self.reentry_since = None
        self.distance = 0.0
        self.count = 0

    def barrier(self, reason, high_speed=False):
        self.anchor = self.last_usable = None
        self.barriers.add(reason)
        self.needs_reentry |= high_speed
        self.reentry_count = 0
        self.reentry_since = None

    def skip(self, point, reasons, speed=None):
        return {
            "client_seq": point.seq,
            "segment_id": None,
            "from_seq": None,
            "connection": "SKIP",
            "distance_use": "EXCLUDE",
            "distance_delta_m": 0.0,
            "estimated_segment_speed_mps": speed,
            "reasons": sorted(set(reasons) | self.barriers),
        }

    def decide(self, point, estimate):
        c = self.config
        reasons = set(estimate["reasons"])
        speed = estimate["speed_mps"]
        if speed is not None and speed > c["maxWalkingSpeedMps"]:
            self.barrier("HIGH_SPEED", True)
            return self.skip(point, reasons | {"HIGH_SPEED"})
        if estimate["position_quality"] != "USABLE":
            self.reentry_count, self.reentry_since = 0, None
            return self.skip(point, reasons)
        if self.last_usable and _seconds(self.last_usable.time, point.time) > c["maxGapSeconds"]:
            self.barrier("OBSERVATION_GAP")
        self.last_usable = point
        if self.needs_reentry:
            plausible = speed is not None and estimate["speed_quality"] != "UNVERIFIED"
            if not plausible:
                self.reentry_count, self.reentry_since = 0, None
            else:
                if self.reentry_count == 0:
                    self.reentry_since = point.time
                self.reentry_count = min(c["reentrySamples"], self.reentry_count + 1)
            if (
                not plausible
                or self.reentry_count < c["reentrySamples"]
                or _seconds(self.reentry_since, point.time) < c["reentrySeconds"]
            ):
                return self.skip(point, reasons | {"REENTRY_PENDING"})
            self.needs_reentry = False
        anchor = self.anchor
        if anchor is None:
            self.anchor = point
            self.count += 1
            reasons |= self.barriers or {"FIRST_POINT"}
            self.barriers.clear()
            return {
                "client_seq": point.seq,
                "segment_id": self.count - 1,
                "from_seq": None,
                "connection": "START_NEW",
                "distance_use": "EXCLUDE",
                "distance_delta_m": 0.0,
                "estimated_segment_speed_mps": None,
                "reasons": sorted(reasons),
            }
        delta = anchor.distance(point)
        dt = _seconds(anchor.time, point.time)
        segment_speed = delta / dt
        if max(0.0, delta - anchor.accuracy - point.accuracy) / dt > c["maxWalkingSpeedMps"]:
            self.barrier("HIGH_SPEED", True)
            return self.skip(point, reasons | {"HIGH_SPEED"}, segment_speed)
        if delta > c["maxJumpM"]:
            self.barrier("JUMP")
            return self.skip(point, reasons | {"JUMP"}, segment_speed)
        floor = max(c["minDistanceM"], (anchor.accuracy + point.accuracy) * c["noiseRadiusFactor"])
        if delta <= 0 or delta < floor:
            return self.skip(point, reasons | {"BELOW_NOISE_FLOOR"}, segment_speed)
        self.anchor = point
        self.distance += delta
        return {
            "client_seq": point.seq,
            "segment_id": self.count - 1,
            "from_seq": anchor.seq,
            "connection": "CONTINUE",
            "distance_use": "INCLUDE",
            "distance_delta_m": delta,
            "estimated_segment_speed_mps": segment_speed,
            "reasons": sorted(reasons),
        }


def replay(manifest: MotionManifest, observations: list[MotionObservation], raw, on_step=None):
    """Return accepted original sequence references and exact closed recording time, not moving time."""
    validate_manifest(manifest)
    if len(raw) != manifest.point_count or [p.client_seq for p in raw] != list(range(len(raw))):
        raise ValueError("motion_raw_range")
    validate_observations(manifest, observations, raw)
    config = json.loads(manifest.policy.config_json)
    estimator, segments = Estimator(config), Segments(config)
    paths, reasons = [], Counter()

    def barrier(reason):
        estimator.window.clear()
        segments.barrier(reason)

    for epoch_index, epoch in enumerate(manifest.epochs):
        if epoch_index:
            barrier("PAUSE")
            barrier("SOURCE_CHANGED")
        previous = None
        for i in range(epoch.first_ingress_seq, epoch.target_ingress_seq + 1):
            point = Point(raw[i], observations[i])
            obs, time = point.observation, point.time
            received = obs.received_elapsed_nanos
            excluded = None
            if time is None or received is None or received < time:
                excluded = "INVALID_TIME"
                barrier(excluded)
            elif (
                not obs.recording_eligible
                or time < epoch.started_elapsed_nanos
                or time > epoch.ended_elapsed_nanos
            ):
                excluded = "OUTSIDE_ACTIVE_INTERVAL"
            elif previous and time <= previous.time:
                excluded = (
                    "OUT_OF_ORDER"
                    if time < previous.time
                    else "DUPLICATE"
                    if previous.same_measurement(point)
                    else "SAME_TIME_CONFLICT"
                )
            if excluded:
                estimate = {
                    "position_quality": point.position(config),
                    "speed_mps": None,
                    "speed_source": "UNKNOWN",
                    "speed_quality": "UNKNOWN",
                    "movement": "UNKNOWN",
                    "reasons": [excluded],
                }
                decision = segments.skip(point, {excluded})
            else:
                if previous and _seconds(previous.time, time) > config["maxGapSeconds"]:
                    barrier("OBSERVATION_GAP")
                previous = point
                estimate = estimator.estimate(point)
                decision = segments.decide(point, estimate)
                why = decision["reasons"]
                if (
                    "HIGH_SPEED" in why
                    and decision["connection"] == "SKIP"
                    and "REENTRY_PENDING" not in why
                ) or "JUMP" in why:
                    estimator.window.clear()
            reasons.update(decision["reasons"])
            if decision["connection"] == "START_NEW":
                paths.append([point.seq])
            elif decision["connection"] == "CONTINUE":
                paths[-1].append(point.seq)
            if on_step:
                on_step({"estimate": estimate, "decision": decision})
        barrier("PAUSE")
    duration = sum(e.ended_elapsed_nanos - e.started_elapsed_nanos for e in manifest.epochs)
    return {
        "distance_m": segments.distance,
        "recording_duration_nanos": duration,
        "active_duration_millis": duration // 1_000_000,
        "point_count": len(raw),
        "segment_count": segments.count,
        "segments": paths,
        "reason_counts": dict(sorted(reasons.items())),
    }
