"""Small shared identity and validation vocabulary, independent of producers."""

from dataclasses import dataclass
from uuid import UUID

STATISTICS_VERSION = "activity-statistics.v1"


class StatisticsError(ValueError):
    """Stable contract violation code; callers must not publish a partial result."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise StatisticsError(code)


def identifier(value: str) -> None:
    require(isinstance(value, str) and bool(value.strip()), "empty_identity")


def natural(value: int) -> None:
    require(type(value) is int and value >= 0, "invalid_nonnegative_integer")


def client_uuid(value: str) -> str:
    identifier(value)
    try:
        return str(UUID(value))
    except ValueError as error:
        raise StatisticsError("invalid_client_uuid") from error


@dataclass(frozen=True)
class ProjectionIdentity:
    generation_id: str
    statistics_version: str = STATISTICS_VERSION

    def __post_init__(self):
        identifier(self.generation_id)
        require(self.statistics_version == STATISTICS_VERSION, "unsupported_statistics_version")
