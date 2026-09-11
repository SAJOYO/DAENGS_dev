import asyncio
import uuid

import pytest
from sqlalchemy import text

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.models.app_user import AppUser
from daengs_backend.models.place_bookmark import PlaceBookmark
from tests.place_bookmarks.conftest import headers, sql_file

ROOT = "/app/places/bookmarks"
KEY = {"source": "kcisa", "ref": "시설 / #? 한글"}


async def call(api, method="GET", key=KEY, owner=None):
    kwargs = {"json": key} if method == "PUT" else {"params": key} if method == "DELETE" else {}
    return await api.client.request(
        method, ROOT, headers=headers(owner or api.members[0]), **kwargs
    )


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "POST"])
@pytest.mark.parametrize(
    "kind", ["missing", "invalid", "admin", "unknown", "suspended", "withdrawn"]
)
async def test_authentication(api, database, method, kind):
    owner = api.members[0]
    if kind in {"suspended", "withdrawn"}:
        async with database() as db:
            (await db.get(AppUser, owner)).status = kind
            await db.commit()
    auth = {
        "missing": {},
        "invalid": {"Authorization": "Bearer invalid"},
        "admin": {
            "Authorization": "Bearer " + create_access_token(owner, SubjectType.ADMIN, "ADMIN")
        },
        "unknown": headers(uuid.uuid4()),
    }.get(kind, headers(owner))
    response = await api.client.request(
        method,
        ROOT + ("/search" if method == "POST" else ""),
        headers=auth,
        params=KEY if method == "DELETE" else None,
        json=KEY if method == "PUT" else {"filters": {}} if method == "POST" else None,
    )
    assert response.status_code == 401
    assert not api.calls


async def test_private_idempotent_lifecycle_and_unavailable_removal(api):
    saved = await call(api, "PUT")
    assert saved.status_code == 200 and saved.json()["total_count"] == 1
    api.unavailable = True
    assert (await call(api, "PUT")).json() == saved.json()
    assert len(api.calls) == 1
    assert (await call(api, owner=api.members[1])).json()["items"] == []
    failed = await api.client.post(
        ROOT + "/search", headers=headers(api.members[0]), json={"filters": {}}
    )
    assert failed.status_code == 503
    assert (await call(api)).json()["total_count"] == 1
    assert (await call(api, "DELETE")).json()["total_count"] == 0
    assert (await call(api, "DELETE")).json()["total_count"] == 0


async def test_whole_owned_keyset_passes_to_lookup_not_search_page(api):
    await call(api, "PUT")
    await call(api, "PUT", {"source": "kto", "ref": "remote"})
    await call(api, "PUT", {"source": "kto", "ref": "other-owner"}, owner=api.members[1])
    filters = {"name_query": "먼 지역"}
    response = await api.client.post(
        ROOT + "/search", headers=headers(api.members[0]), json={"filters": filters}
    )
    assert response.status_code == 200
    assert {k.ref for k in api.calls[-1][0]} == {KEY["ref"], "remote"}
    assert api.calls[-1][1] == filters and response.json()["total_count"] == 2


async def test_no_new_missing_record_and_saved_missing_remains_removable(api):
    api.missing = True
    assert (await call(api, "PUT")).status_code == 404
    assert (await call(api)).json()["total_count"] == 0
    api.missing = False
    await call(api, "PUT")
    api.missing = True
    response = await api.client.post(
        ROOT + "/search", headers=headers(api.members[0]), json={"filters": {}}
    )
    assert response.json()["missing_keys"] == [KEY]
    assert (await call(api, "DELETE")).json()["total_count"] == 0


async def test_capacity_lock_and_duplicate_concurrent_requests(api, database):
    async with database() as db:
        db.add_all(
            [
                PlaceBookmark(app_user_id=api.members[0], source="kcisa", ref=str(i), name="시설")
                for i in range(199)
            ]
        )
        await db.commit()
    # Separate sessions + real owner lock; only one final slot exists.
    results = await asyncio.gather(
        call(api, "PUT", {"source": "kto", "ref": "last-a"}),
        call(api, "PUT", {"source": "kto", "ref": "last-b"}),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert (await call(api)).json()["total_count"] == 200


async def test_withdrawal_during_lookup_is_rechecked_and_cleanup_replay(api, database):
    await call(api, "PUT")

    async def withdraw():
        async with database() as db:
            (await db.get(AppUser, api.members[0])).status = "withdrawn"
            await db.commit()

    api.hook = withdraw
    assert (await call(api, "PUT", {"source": "kto", "ref": "new"})).status_code == 401
    async with database() as db:
        assert await db.scalar(text("SELECT count(*) FROM place_bookmarks")) == 0
        await sql_file(db, "db/migrations/2026-09-11_place_bookmarks.sql")
        await sql_file(db, "db/migrations/verify_2026-09-11_place_bookmarks.sql")
        await db.commit()


@pytest.mark.parametrize(
    "key",
    [
        {"source": "fake", "ref": "1"},
        {"source": "kcisa", "ref": ""},
        {"source": "kto", "ref": "x" * 257},
    ],
)
async def test_invalid_key_never_reaches_lookup(api, key):
    assert (await call(api, "PUT", key)).status_code == 422
    assert (await call(api, "DELETE", key)).status_code == 422
    assert not api.calls
