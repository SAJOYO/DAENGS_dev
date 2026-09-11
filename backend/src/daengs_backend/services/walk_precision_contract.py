"""Bitwise evidence plus a coarse-coordinate cross-check; never modify the original raw rows."""

import math
import struct
from decimal import ROUND_HALF_EVEN, Decimal

from daengs_backend.schemas.walk_precision import VERSION
from daengs_backend.services.walk_motion_contract import MotionConflict, digest


def manifest_digest(manifest):
    return digest(list(manifest.model_dump().values()))


def chunk_digest(points):
    return digest([list(p.model_dump().values()) for p in points])


def evidence_digest(manifest_fingerprint, hashes):
    return digest([VERSION, manifest_fingerprint, hashes])


def refine_points(points, raw):
    result = []
    for p, r in zip(points, raw, strict=True):
        lat, lng = (struct.unpack("!d", bytes.fromhex(b))[0] for b in (p.lat_bits, p.lng_bits))
        acc = (
            None
            if p.accuracy_bits is None
            else struct.unpack("!f", bytes.fromhex(p.accuracy_bits))[0]
        )
        if (
            p.client_seq != r.client_seq
            or not math.isfinite(lat)
            or not math.isfinite(lng)
            or not -90 <= lat <= 90
            or not -180 <= lng <= 180
        ):
            raise MotionConflict("precision_coordinate_invalid")

        def coarse(x):
            return Decimal(str(x)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)

        if coarse(lat) != coarse(r.lat) or coarse(lng) != coarse(r.lng):
            raise MotionConflict("precision_raw_mismatch")
        # Accuracy went through the original Float-to-JSON path. Signed zero is preserved in
        # the extension even when the old JSONB number lost that sign.
        if (acc is None) != (r.accuracy_m is None):
            raise MotionConflict("precision_accuracy_mismatch")
        if acc is not None and (
            not math.isfinite(acc)
            or acc < 0
            or acc != struct.unpack("!f", struct.pack("!f", r.accuracy_m))[0]
        ):
            raise MotionConflict("precision_accuracy_mismatch")
        # Preserve binary64 including signed zero. Decimal(str(...)) would alter its identity.
        result.append(r.model_copy(update={"lat": lat, "lng": lng, "accuracy_m": acc}))
    return result
