"""Fixtures scoped to photos tests; importing does not open a database."""

from tests.walk.support.photo_database import (
    photo_database,  # noqa: F401 -- pytest fixture registration
)
from tests.walk.support.pin_database import database  # noqa: F401 -- pytest fixture registration
