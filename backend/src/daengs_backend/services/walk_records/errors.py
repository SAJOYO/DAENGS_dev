"""Shared entry errors; no dependency on versioned writers or background jobs."""


class EntryNotFound(Exception):
    pass


class EntryConflict(Exception):
    pass


class EntryInvalid(Exception):
    pass


class EntryDeleted(Exception):
    pass


class EntryWritesDisabled(Exception):
    pass


class EntryUpgradeRequired(Exception):
    pass
