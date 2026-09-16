"""Session time and selected-scene order are independent, reproducible inputs."""

from typing import Literal

from pydantic import Field, model_validator

from daengs_walk.value_contracts import Instant, ValueContract


class PhasePolicy(ValueContract):
    version: Literal["session-windows-v1"] = "session-windows-v1"
    departure_seconds: float = Field(default=120, ge=0)
    closing_seconds: float = Field(default=120, ge=0)
    # Short sessions: nearest boundary wins; equal distances remain in progress.
    overlap: Literal["nearest_boundary_tie_in_progress"] = "nearest_boundary_tie_in_progress"


class WalkTimeline(ValueContract):
    source_revision: str = Field(min_length=1)
    started_at: Instant
    ended_at: Instant
    phase_policy: PhasePolicy = Field(default_factory=PhasePolicy)

    @model_validator(mode="after")
    def ordered(self):
        if self.ended_at < self.started_at:
            raise ValueError("walk ends before it starts")
        return self


class ScenePosition(ValueContract):
    scene_id: str = Field(min_length=1)
    selected_scene_number: int = Field(ge=1)
    selected_scene_count: int = Field(ge=1)
    recorded_at: Instant
    timeline: WalkTimeline

    @model_validator(mode="after")
    def within_session(self):
        if self.selected_scene_number > self.selected_scene_count:
            raise ValueError("scene ordinal exceeds selected count")
        if not self.timeline.started_at <= self.recorded_at <= self.timeline.ended_at:
            raise ValueError("scene time is outside walk")
        return self

    @property
    def seconds_since_start(self):
        return (self.recorded_at - self.timeline.started_at).total_seconds()

    @property
    def seconds_until_end(self):
        return (self.timeline.ended_at - self.recorded_at).total_seconds()

    @property
    def phase(self) -> Literal["departure", "in_progress", "closing"]:
        elapsed, remaining = self.seconds_since_start, self.seconds_until_end
        policy = self.timeline.phase_policy
        departure, closing = (
            elapsed <= policy.departure_seconds,
            remaining <= policy.closing_seconds,
        )
        if departure and closing:
            return (
                "departure"
                if elapsed < remaining
                else "closing"
                if remaining < elapsed
                else "in_progress"
            )
        return "departure" if departure else "closing" if closing else "in_progress"

    def writer_view(self):
        return {
            "scene_id": self.scene_id,
            "selected_scene_number": self.selected_scene_number,
            "selected_scene_count": self.selected_scene_count,
            "recorded_at": self.recorded_at.isoformat(),
            "walk_started_at": self.timeline.started_at.isoformat(),
            "walk_ended_at": self.timeline.ended_at.isoformat(),
            "phase": self.phase,
            "seconds_since_start": self.seconds_since_start,
            "seconds_until_end": self.seconds_until_end,
            "is_walk_start": self.seconds_since_start == 0,
            "is_walk_end": self.seconds_until_end == 0,
        }


def scene_positions(source, scenes, *, policy=None):
    """Bind selected scene identities/times to the actual source, never fixture times."""
    scenes = sorted(scenes, key=lambda s: (s.anchor.event_at, s.id))
    if len({s.id for s in scenes}) != len(scenes):
        raise ValueError("duplicate selected scene")
    timeline = WalkTimeline(
        source_revision=source.revision(),
        started_at=source.started_at,
        ended_at=source.ended_at,
        phase_policy=policy or PhasePolicy(),
    )
    return {
        scene.id: ScenePosition(
            scene_id=scene.id,
            selected_scene_number=i,
            selected_scene_count=len(scenes),
            recorded_at=scene.anchor.event_at,
            timeline=timeline,
        )
        for i, scene in enumerate(scenes, 1)
    }
