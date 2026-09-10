"""Fixtures scoped to context tests; importing does not open a database."""

from tests.walk.support.entry_context import state  # noqa: F401 -- pytest fixture registration
