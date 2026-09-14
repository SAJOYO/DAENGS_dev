"""Explicit historical writer binding; wrapping a callable never selects a contract."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class LegacySlotWriter:
    writer: Callable | None = None
