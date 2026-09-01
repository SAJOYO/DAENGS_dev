"""Thin translations from DAENGS domain boundaries to CapabilityResult."""

from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter
from daengs_backend.orchestration.adapters.training import TrainingCapabilityAdapter
from daengs_backend.orchestration.adapters.walk import WalkCapabilityAdapter

__all__ = ["LifeCapabilityAdapter", "TrainingCapabilityAdapter", "WalkCapabilityAdapter"]
