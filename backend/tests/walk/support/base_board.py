"""Synthetic saved-input fixtures shared by base-board behavior tests."""

from daengs_backend.services.walk_diary.preparation.input import assemble_input
from daengs_backend.services.walk_diary.preparation.observations import prepare_observation_source
from daengs_walk.diary_board import BaseBoardPolicy, VerifiedBoardRoute
from daengs_walk.diary_stamps import StampPolicy
from tests.walk.support.observations import stored, uploaded


def policy(target=5, **updates):
    return BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=target), **updates)


def saved_case(samples=None, records=()):
    if samples is None:
        samples = [(i * 10, i * 20) for i in range(101)]
    walk, analysis, points = stored(uploaded(samples))
    observation = prepare_observation_source(walk, analysis)
    assembled = assemble_input(
        walk,
        analysis,
        records,
        [],
        None,
        [],
        observation_source=observation,
    )
    route = VerifiedBoardRoute(observation.route, observation.evidence)
    return assembled, route, points
