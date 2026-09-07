"""Resolve authenticated current session snapshots, in either arrival order."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .common import client_uuid, identifier, natural, require


@dataclass(frozen=True)
class SessionSource:
    kind: Literal["WALK", "GAME"]
    owner_id: str
    client_walk_session_id: str
    server_session_id: str
    started_ms: int
    pet_ids: frozenset[str]

    def __post_init__(self):
        require(self.kind in {"WALK", "GAME"}, "invalid_session_kind")
        identifier(self.owner_id)
        identifier(self.server_session_id)
        natural(self.started_ms)
        object.__setattr__(self, "client_walk_session_id", client_uuid(self.client_walk_session_id))
        require(not isinstance(self.pet_ids, (str, bytes)), "invalid_participant_collection")
        object.__setattr__(self, "pet_ids", frozenset(self.pet_ids))
        for pet in self.pet_ids:
            identifier(pet)


@dataclass(frozen=True)
class ActivitySessionLink:
    owner_id: str
    client_walk_session_id: str
    walk: SessionSource | None
    game: SessionSource | None
    status: Literal["WAITING_FOR_WALK", "WALK_ONLY", "LINKED", "CONFLICT"]
    conflicts: tuple[str, ...] = ()


def resolve_session_links(sources: Iterable[SessionSource]) -> tuple[ActivitySessionLink, ...]:
    """Deterministic snapshot resolution, not a mutable registry or authorization check.

    Repeated identical inputs are harmless. A server ID reused under another link or
    changed content under the same source identity is rejected. Cross-domain metadata
    disagreement is represented as CONFLICT while preserving both source snapshots.
    """
    identities = {}
    grouped: dict[tuple[str, str], dict[str, SessionSource]] = {}
    for source in sources:
        identity = (source.kind, source.server_session_id)
        require(identity not in identities or identities[identity] == source, "session_id_conflict")
        identities[identity] = source
        pair = grouped.setdefault((source.owner_id, source.client_walk_session_id), {})
        require(source.kind not in pair or pair[source.kind] == source, "session_link_conflict")
        pair[source.kind] = source
    links = []
    for (owner, client), pair in sorted(grouped.items()):
        walk, game = pair.get("WALK"), pair.get("GAME")
        conflicts = []
        if walk and game:
            if walk.started_ms != game.started_ms:
                conflicts.append("started_ms_mismatch")
            if walk.pet_ids != game.pet_ids:
                conflicts.append("participants_mismatch")
            status = "CONFLICT" if conflicts else "LINKED"
        else:
            status = "WALK_ONLY" if walk else "WAITING_FOR_WALK"
        links.append(ActivitySessionLink(owner, client, walk, game, status, tuple(conflicts)))
    return tuple(links)
