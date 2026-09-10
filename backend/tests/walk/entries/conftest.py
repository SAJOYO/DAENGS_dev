"""Fixtures scoped to entries tests; importing does not open a database."""

from tests.walk.support.pin_database import database  # noqa: F401 -- pytest fixture registration
