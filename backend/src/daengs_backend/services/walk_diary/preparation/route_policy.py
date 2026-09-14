"""Configured default for new preparation; explicit/stored policies keep their shape."""

from daengs_backend.config import settings
from daengs_walk.diary.route.patterns import RoutePatternBindingPolicy


def configured_route_patterns(policy):
    if settings.walk_diary_route_patterns_enabled and policy.route_patterns is None:
        return policy.model_copy(update={"route_patterns": RoutePatternBindingPolicy()})
    return policy
