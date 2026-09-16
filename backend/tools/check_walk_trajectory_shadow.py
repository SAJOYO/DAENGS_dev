"""Offline, read-only diagnostic for a detached motion backup or shared fixture suite.

Run from backend: uv run --no-sync python -m tools.check_walk_trajectory_shadow FILE
No authentication credentials, network connection or database are used.
"""

import argparse
import json
from pathlib import Path

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_motion import CHUNK_SIZE, MotionManifest, MotionObservation
from daengs_backend.schemas.walk_precision import PrecisionManifest, PrecisionPoint
from daengs_backend.services.walk_metrics.trajectory_shadow import backup_fingerprint, replay_shadow
from daengs_backend.services.walk_session import precision_contract as precision
from daengs_backend.services.walk_session.finalize import walk_input_fingerprint
from daengs_backend.services.walk_session.motion_contract import MotionConflict, manifest_digest


def calculate_export(payload, *, step_batch_size=1):
    manifest = MotionManifest.model_validate(payload["manifest"])
    observations = [MotionObservation.model_validate(p) for p in payload["points"]]
    raw = [WalkPointUpload.model_validate(p) for p in payload["raw_points"]]
    if walk_input_fingerprint(raw) != manifest.raw_input_fingerprint:
        raise MotionConflict("trajectory_raw_fingerprint")
    if manifest_digest(manifest) != payload["manifest_fingerprint"]:
        raise MotionConflict("trajectory_manifest_fingerprint")
    metadata_fp = backup_fingerprint(manifest, observations)
    if metadata_fp != payload["evidence_fingerprint"]:
        raise MotionConflict("trajectory_evidence_fingerprint")
    precision_fp = None
    precision_fields = {
        "precision_manifest",
        "precision_points",
        "precision_manifest_fingerprint",
        "precision_fingerprint",
    }
    present = precision_fields & payload.keys()
    if present:
        if present != precision_fields:
            raise MotionConflict("trajectory_precision_incomplete")
        pm = PrecisionManifest.model_validate(payload["precision_manifest"])
        points = [PrecisionPoint.model_validate(p) for p in payload["precision_points"]]
        if (
            pm.client_session_id != manifest.client_session_id
            or pm.point_count != len(raw)
            or len(points) != len(raw)
            or pm.base_evidence_fingerprint != metadata_fp
            or precision.manifest_digest(pm) != payload["precision_manifest_fingerprint"]
            or [p.client_seq for p in points] != list(range(len(raw)))
        ):
            raise MotionConflict("trajectory_precision_manifest")
        precision_fp = precision.evidence_digest(
            precision.manifest_digest(pm),
            [
                precision.chunk_digest(points[i : i + CHUNK_SIZE])
                for i in range(0, len(points), CHUNK_SIZE)
            ],
        )
        if precision_fp != payload["precision_fingerprint"]:
            raise MotionConflict("trajectory_precision_fingerprint")
        raw = precision.refine_points(points, raw)
    return replay_shadow(
        manifest,
        observations,
        raw,
        owner_id="offline-diagnostic",
        precision_fingerprint=precision_fp,
        step_batch_size=step_batch_size,
    )


def summary(result):
    ledger = result.snapshot.ledger
    metrics = ledger.metrics()
    return {
        "measurement_id": result.snapshot.measurement_id,
        "coordinate_basis": result.snapshot.key.coordinate_basis,
        "walking_distance_m": metrics.walking_distance_m,
        "motion_distance_m": result.motion_distance_m,
        "motion_active_duration_ns": result.motion_active_duration_ns,
        "known_record_duration_ns": metrics.known_duration_ns,
        "paused_duration_ns": metrics.paused_duration_ns,
        "unlocated_duration_ns": metrics.unlocated_duration_ns,
        "unknown_duration_intervals": metrics.unknown_duration_intervals,
        "observed_run_count": len(result.observed_runs),
        "walking_section_count": len(result.walking_sections),
        "observed_uncredited_intervals": sum(
            i.continuity == "connected" and i.walking_use != "included" for i in ledger.intervals
        ),
        "last_walking_seq": (
            result.boundaries.last_walking.ingress_seq if result.boundaries.last_walking else None
        ),
        "last_observed_seq": (
            result.boundaries.last_observed.ingress_seq if result.boundaries.last_observed else None
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--case", help="Select one named case in a shared fixture suite")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--output", type=Path, help="Write summaries to JSON instead of stdout")
    parser.add_argument(
        "--details", action="store_true", help="Include full source refs and coordinates"
    )
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        cases = payload.get("cases", [payload])
        if args.case:
            cases = [c for c in cases if c.get("name") == args.case]
            if not cases:
                raise ValueError("trajectory_case_not_found")
        reports = []
        for case in cases:
            result = calculate_export(case, step_batch_size=args.batch_size)
            reports.append(
                {
                    "case": case.get("name"),
                    **summary(result),
                    **({"detail": result.model_dump(mode="json")} if args.details else {}),
                }
            )
        output = json.dumps(
            {"version": "walk-trajectory-shadow-report-v1", "reports": reports},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        if args.output:
            if args.output.resolve() == args.input.resolve():
                raise ValueError("trajectory_output_is_input")
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            print(output)
    except (OSError, ValueError, TypeError, KeyError) as error:
        # Pydantic errors can contain raw coordinates. Default diagnostics expose only a code/type.
        code = error.code if isinstance(error, MotionConflict) else type(error).__name__
        parser.exit(2, f"trajectory diagnostic failed: {code}\n")


if __name__ == "__main__":
    main()
