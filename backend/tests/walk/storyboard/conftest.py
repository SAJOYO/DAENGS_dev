"""Fixtures scoped to storyboard tests; importing does not open a database."""

from tests.walk.support.storyboard import live  # noqa: F401 -- pytest fixture registration
