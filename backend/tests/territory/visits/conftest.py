"""Disposable database fixtures are opened only by tests that request them."""

from tests.territory.support.database import (  # noqa: F401 -- fixture registration
    actors,
    database,
)
