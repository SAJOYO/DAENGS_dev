from daengs_place.place.bookmarks import BookmarkLookup, lookup_bookmarks
from daengs_place.place.contracts import PlaceRef
from daengs_place.place.conversation.contract import ExplorationState, NamedPlace, PrepareRequest
from daengs_place.place.conversation.service import ConversationService
from daengs_place.place.filters.contract import FilterState
from tests.place.integration.test_condition_filters import PREFIX, payload, refs, seed
from tests.place.support.database import db_session


async def test_real_sql_uses_bookmarks_known_and_excluded_before_twenty_place_limit():
    async with db_session() as db:
        await seed(db, [{"id": str(i), "distance_m": i + 1, "parking": True} for i in range(45)])
        keys = tuple(PlaceRef(source="kcisa", ref=PREFIX + str(i)) for i in range(45))
        filters = FilterState.model_validate(
            payload(
                candidate_kinds=["cafe"],
                unknown_policy="exclude",
                result_policy={"limit_per_kind": 20},
            )
        )
        exploration = ExplorationState(
            known=(NamedPlace(key=keys[20], name="알고 있는 곳"),),
            excluded=(NamedPlace(key=keys[21], name="제외한 곳"),),
        )
        result = await ConversationService().prepare(
            db,
            PrepareRequest(
                mode="restore",
                restore_filters=filters,
                restore_pool="new_candidates",
                candidate_pools="v1",
                bookmark_keys=keys[:20],
                restore_exploration=exploration,
            ),
        )
        assert refs(result.state.snapshot.result.groups[0].matched) == [
            str(i) for i in range(22, 42)
        ]
        assert result.receipt.remaining == "more"
        saved = await lookup_bookmarks(
            db,
            BookmarkLookup(
                keys=[key.model_dump() for key in keys[:22]], filters={"excluded_keys": [keys[21]]}
            ),
        )
        assert keys[20] in [h.place.key for h in saved.hits]
        assert keys[21] not in [h.place.key for h in saved.hits]
        assert saved.missing_keys == []
