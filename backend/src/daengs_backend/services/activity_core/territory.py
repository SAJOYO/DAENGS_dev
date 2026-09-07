"""Replay committed ownership facts, independently of scoring and walk completion.

The source adapter supplies a contiguous season stream and an authoritative cut.
These are future producer/storage obligations, not inferred from timestamps or SQL IDs.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from .common import ProjectionIdentity, identifier, natural, require


@dataclass(frozen=True)
class TerritoryCoverage:
    season_id: str
    season_starts_ms: int
    season_ends_ms: int
    coverage_start_ms: int
    base_revision: int = 0

    def __post_init__(self):
        identifier(self.season_id)
        for value in (
            self.season_starts_ms,
            self.season_ends_ms,
            self.coverage_start_ms,
            self.base_revision,
        ):
            natural(value)
        require(
            self.season_starts_ms <= self.coverage_start_ms < self.season_ends_ms,
            "invalid_season_coverage",
        )


@dataclass(frozen=True)
class ConfirmedCut:
    season_id: str
    revision: int
    through_ms: int

    def __post_init__(self):
        identifier(self.season_id)
        natural(self.revision)
        natural(self.through_ms)


@dataclass(frozen=True)
class InitialOwner:
    site_id: str
    pet_id: str
    verified: bool = False

    def __post_init__(self):
        identifier(self.site_id)
        identifier(self.pet_id)
        require(type(self.verified) is bool, "invalid_certification")


@dataclass(frozen=True)
class TerritoryStatEvent:
    season_id: str
    event_id: str
    revision: int
    at_ms: int
    kind: Literal["INITIALIZED", "OWNERSHIP_CHANGED", "CERTIFIED", "UNCHANGED", "SEASON_CLOSED"]
    site_id: str | None = None
    pet_id: str | None = None
    previous_pet_id: str | None = None
    game_session_id: str | None = None
    claim_id: str | None = None
    verified: bool = False
    initial_owners: tuple[InitialOwner, ...] = ()

    def __post_init__(self):
        identifier(self.season_id)
        identifier(self.event_id)
        natural(self.revision)
        require(self.revision > 0, "invalid_event_revision")
        natural(self.at_ms)
        require(type(self.verified) is bool, "invalid_certification")
        require(
            self.kind
            in {
                "INITIALIZED",
                "OWNERSHIP_CHANGED",
                "CERTIFIED",
                "UNCHANGED",
                "SEASON_CLOSED",
            },
            "invalid_territory_event_kind",
        )
        object.__setattr__(
            self,
            "initial_owners",
            tuple(
                sorted(
                    self.initial_owners,
                    key=lambda owner: owner.site_id,
                )
            ),
        )
        if self.kind in {"INITIALIZED", "SEASON_CLOSED"}:
            require(
                all(
                    value is None
                    for value in (
                        self.site_id,
                        self.pet_id,
                        self.previous_pet_id,
                        self.game_session_id,
                        self.claim_id,
                    )
                )
                and not self.verified,
                "unexpected_ownership_fields",
            )
        else:
            for value in (self.site_id, self.pet_id, self.game_session_id, self.claim_id):
                identifier(value)
            if self.previous_pet_id is not None:
                identifier(self.previous_pet_id)
        require(self.kind == "INITIALIZED" or not self.initial_owners, "unexpected_baseline")
        require(
            len({owner.site_id for owner in self.initial_owners}) == len(self.initial_owners),
            "duplicate_baseline_site",
        )


@dataclass(frozen=True)
class HoldingPeriod:
    # A tuple avoids delimiter collisions in opaque source IDs.
    period_id: tuple[str, str, str]  # season, start event, site
    pet_id: str
    site_id: str
    started_ms: int
    ended_ms: int | None
    verified_from_ms: int | None
    origin: Literal["ACQUIRED", "IMPORTED"]
    takeover: bool
    start_revision: int
    end_event_id: str | None
    game_session_id: str | None
    claim_id: str | None


@dataclass(frozen=True)
class TerritoryProjection:
    identity: ProjectionIdentity
    coverage: TerritoryCoverage
    cut: ConfirmedCut
    periods: tuple[HoldingPeriod, ...]
    peaks: tuple[tuple[str, int], ...]
    events: tuple[TerritoryStatEvent, ...]


def project_territory(
    events: Iterable[TerritoryStatEvent],
    *,
    identity: ProjectionIdentity,
    coverage: TerritoryCoverage,
    cut: ConfirmedCut,
) -> TerritoryProjection:
    """Replay the complete covered stream through exactly cut.revision, atomically.

    Duplicate deliveries may be unordered; missing revisions and conflicting payloads
    fail without exposing partial state. INIT includes the full baseline, even if empty.
    """
    require(cut.season_id == coverage.season_id, "cut_season_mismatch")
    require(cut.through_ms >= coverage.coverage_start_ms, "cut_before_coverage")
    revisions: dict[int, TerritoryStatEvent] = {}
    identities = {}
    for event in events:
        require(event.season_id == coverage.season_id, "event_season_mismatch")
        require(coverage.base_revision < event.revision <= cut.revision, "event_outside_cut")
        require(
            event.revision not in revisions or revisions[event.revision] == event,
            "event_revision_conflict",
        )
        require(
            event.event_id not in identities or identities[event.event_id] == event,
            "event_identity_conflict",
        )
        revisions[event.revision] = event
        identities[event.event_id] = event
    ordered = tuple(revisions[key] for key in sorted(revisions))
    require(bool(ordered), "initialization_required")
    require(len(ordered) == cut.revision - coverage.base_revision, "event_gap")
    periods: list[HoldingPeriod] = []
    active: dict[str, int] = {}
    counts: dict[str, int] = {}
    peaks: dict[str, int] = {}
    last_ms = coverage.coverage_start_ms
    closed = False

    def open_period(event, site, pet, *, imported=False, verified=False, takeover=False):
        require(site not in active, "site_already_owned")
        active[site] = len(periods)
        periods.append(
            HoldingPeriod(
                (coverage.season_id, event.event_id, site),
                pet,
                site,
                event.at_ms,
                None,
                event.at_ms if verified else None,
                "IMPORTED" if imported else "ACQUIRED",
                takeover,
                event.revision,
                None,
                None if imported else event.game_session_id,
                None if imported else event.claim_id,
            )
        )
        counts[pet] = counts.get(pet, 0) + 1
        peaks[pet] = max(peaks.get(pet, 0), counts[pet])

    def close_period(site, event):
        index = active.pop(site)
        period = periods[index]
        periods[index] = replace(period, ended_ms=event.at_ms, end_event_id=event.event_id)
        counts[period.pet_id] -= 1

    for offset, event in enumerate(ordered, start=1):
        require(event.revision == coverage.base_revision + offset, "event_gap")
        require(not closed, "event_after_season_closed")
        require(last_ms <= event.at_ms <= cut.through_ms, "event_time_reversed_or_unconfirmed")
        if offset == 1:
            require(event.kind == "INITIALIZED", "initialization_required")
            require(event.at_ms == coverage.coverage_start_ms, "baseline_time_mismatch")
            for owner in event.initial_owners:
                open_period(
                    event, owner.site_id, owner.pet_id, imported=True, verified=owner.verified
                )
        elif event.kind == "SEASON_CLOSED":
            require(event.at_ms == coverage.season_ends_ms, "season_close_time_mismatch")
            for site in tuple(active):
                close_period(site, event)
            closed = True
        else:
            require(event.kind != "INITIALIZED", "repeated_initialization")
            require(event.at_ms < coverage.season_ends_ms, "event_outside_season")
            index = active.get(event.site_id)
            old = periods[index] if index is not None else None
            require(
                event.previous_pet_id == (old.pet_id if old else None),
                "previous_owner_mismatch",
            )
            if event.kind == "OWNERSHIP_CHANGED":
                require(old is None or old.pet_id != event.pet_id, "ownership_did_not_change")
                if old:
                    close_period(event.site_id, event)
                open_period(
                    event,
                    event.site_id,
                    event.pet_id,
                    verified=event.verified,
                    takeover=old is not None,
                )
            else:
                require(old is not None and old.pet_id == event.pet_id, "current_owner_mismatch")
                if event.kind == "CERTIFIED":
                    require(
                        event.verified and old.verified_from_ms is None, "invalid_certification"
                    )
                    periods[index] = replace(old, verified_from_ms=event.at_ms)
                else:
                    require(
                        event.verified == (old.verified_from_ms is not None),
                        "unchanged_certification_mismatch",
                    )
        last_ms = event.at_ms
    require(cut.through_ms < coverage.season_ends_ms or closed, "season_close_required")
    return TerritoryProjection(
        identity, coverage, cut, tuple(periods), tuple(sorted(peaks.items())), ordered
    )


def holding_time(period: HoldingPeriod, *, from_ms: int, to_ms: int) -> tuple[int, int]:
    """Half-open overlap (total, verified). to_ms must come from a confirmed cut."""
    natural(from_ms)
    natural(to_ms)
    require(from_ms <= to_ms, "invalid_window")
    start = max(period.started_ms, from_ms)
    end = min(period.ended_ms if period.ended_ms is not None else to_ms, to_ms)
    total = max(0, end - start)
    verified = (
        0
        if period.verified_from_ms is None
        else max(
            0,
            end - max(start, period.verified_from_ms),
        )
    )
    return total, verified


@dataclass(frozen=True)
class TerritorySummary:
    identity: ProjectionIdentity
    season_id: str
    pet_id: str
    coverage_start_ms: int
    confirmed_through_ms: int
    source_revision: int
    acquisition_count: int
    takeover_count: int
    held_site_ms: int
    verified_held_site_ms: int
    owned_site_count: int
    peak_owned_site_count: int
    periods: tuple[HoldingPeriod, ...]


def summarize_territory(projection: TerritoryProjection, *, pet_id: str) -> TerritorySummary:
    """Covered-season totals through the proven cut, not lifetime or wall-clock now."""
    identifier(pet_id)
    periods = tuple(period for period in projection.periods if period.pet_id == pet_id)
    spans = [
        holding_time(
            period,
            from_ms=projection.coverage.coverage_start_ms,
            to_ms=projection.cut.through_ms,
        )
        for period in periods
    ]
    return TerritorySummary(
        projection.identity,
        projection.coverage.season_id,
        pet_id,
        projection.coverage.coverage_start_ms,
        projection.cut.through_ms,
        projection.cut.revision,
        sum(period.origin == "ACQUIRED" for period in periods),
        sum(period.takeover for period in periods),
        sum(total for total, _ in spans),
        sum(verified for _, verified in spans),
        sum(period.ended_ms is None for period in periods),
        dict(projection.peaks).get(pet_id, 0),
        periods,
    )
