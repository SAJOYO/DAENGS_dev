"""Thin translations from DAENGS domain boundaries to CapabilityResult."""

from daengs_backend.orchestration.adapters.care_log import CareLogCapabilityAdapter
from daengs_backend.orchestration.adapters.gait import GaitCapabilityAdapter
from daengs_backend.orchestration.adapters.general import GeneralCapabilityAdapter
from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter
from daengs_backend.orchestration.adapters.place import PlaceCapabilityAdapter
from daengs_backend.orchestration.adapters.skin import SkinCapabilityAdapter
from daengs_backend.orchestration.adapters.training import TrainingCapabilityAdapter
from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.adapters.walk import WalkCapabilityAdapter

__all__ = [
    "CareLogCapabilityAdapter",
    "GaitCapabilityAdapter",
    "GeneralCapabilityAdapter",
    "LifeCapabilityAdapter",
    "PlaceCapabilityAdapter",
    "SkinCapabilityAdapter",
    "TrainingCapabilityAdapter",
    "VetContactCapabilityAdapter",
    "WalkCapabilityAdapter",
]
