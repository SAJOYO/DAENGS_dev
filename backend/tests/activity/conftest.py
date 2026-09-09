"""Activity schema fixtures scoped to these tests; no database at import."""

from tests.activity.support.database import clock, database  # noqa: F401
from tests.territory.support.database import actors  # noqa: F401
from tests.territory.support.database import database as territory_database  # noqa: F401
