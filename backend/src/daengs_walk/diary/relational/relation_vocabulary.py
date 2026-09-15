"""Model-facing relationship words, independent of calculation and selection.

Endpoint comparisons do not establish a continuous journey. Keep their vocabulary
distinct from flows supported by observations throughout an interval.
"""

ENDPOINT_WORDS = {
    "nearer": "closer_at_this_scene",
    "farther": "farther_at_this_scene",
    "same_distance": "comparable_distance",
    "same_characteristics": "shared_background",
    "different_characteristics": "contrasting_background",
}

FLOW_WORDS = {
    "distance_decrease": "drawing_closer",
    "distance_increase": "leaving_behind",
    "distance_valley": "drawing_closer_then_away",
    "passing": "passing_by",
    "distance_stable": "keeping_a_similar_distance",
    "alongside": "staying_alongside",
    "route_retrace": "retracing",
    "route_return": "coming_back",
    "route_turn": "turning_back",
    "route_straight": "carrying_on_straight",
}
