"""Compatibility import; provider implementation lives with walk backgrounds."""

from daengs_backend.services.walk_background.providers.sgis import SgisSource, sgis

__all__ = ["SgisSource", "sgis"]
