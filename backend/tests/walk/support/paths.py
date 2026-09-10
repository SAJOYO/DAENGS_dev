"""Filesystem anchors for Walk tests and disposable database fixtures."""

from pathlib import Path

TESTS = Path(__file__).resolve().parents[2]
BACKEND = TESTS.parent
REPO = BACKEND.parent
WALK_FIXTURES = TESTS / "walk" / "fixtures"
