"""Deterministic geometry/pace inputs shared by before/after boundary checks."""

from uuid import UUID

from daengs_walk.diary.board.models import BaseBoardPolicy, VerifiedBoardRoute
from daengs_walk.diary.contracts.input import DiaryInput, RouteVersion, digest
from daengs_walk.diary.route.movement import prepare_movement
from daengs_walk.diary.route.movement_policy import MovementPolicy
from daengs_walk.diary.route.observations import build_observation_pool
from daengs_walk.diary.selection.board import prepare_base_board
from daengs_walk.diary.selection.stamps import StampPolicy
from daengs_walk.evidence import analyze_walk
from daengs_walk.storyboard_input import route_nodes, scene_inputs
from tests.walk.support.route_patterns import route, scenarios


def results():
    cases = {key: value["source"] for key, value in scenarios().items()}
    cases["varied_pace"] = route(
        "varied",
        [
            (0, 0, 0),
            (60, 120, 0),
            (90, 120, 0),
            (150, 240, 0),
            (180, 261, 0),
            (240, 381, 0),
            (270, 501, 0),
            (330, 621, 0),
        ],
    )
    output = {}
    for name, spec in cases.items():
        evidence = analyze_walk(UUID(int=1), spec.started_at, spec.ended_at, spec.points)
        version = RouteVersion(
            status="ready",
            analysis_id="analysis",
            input_fingerprint=digest(name),
            calculation_version=evidence.facts.calculation_version,
        )
        pool = build_observation_pool(evidence, version)
        source = DiaryInput(
            owner_id="owner",
            walk_id=str(UUID(int=1)),
            client_session_id="session",
            started_at=spec.started_at,
            ended_at=spec.ended_at,
            pet_ids=(),
            evidence_origin=evidence.facts.evidence_origin,
            route=version,
            records=(),
            photos_status="not_available",
            observations=pool.observations,
            scene_policy_version="records-first-v1",
            writing_policy_version="test",
        )
        verified = VerifiedBoardRoute(version, evidence)
        board = prepare_base_board(
            source, BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=5)), route=verified
        )
        movement = prepare_movement(source, verified, MovementPolicy())
        output[name] = {
            "nodes": digest(route_nodes(evidence)),
            "observations": digest([o.model_dump(mode="json") for o in pool.observations]),
            "observation_ids": [o.id for o in pool.observations],
            "board": digest(board),
            "movement": digest(
                [
                    movement.source_revision,
                    movement.nodes,
                    movement.claims,
                    movement.baseline,
                    movement.policies,
                ]
            ),
            "storyboard": digest(scene_inputs(evidence, [], session_id="session")),
        }
    return output
