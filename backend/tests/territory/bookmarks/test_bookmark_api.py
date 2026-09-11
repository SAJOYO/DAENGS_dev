"""HTTP + real PostgreSQL: private bookmarks without a game season or registered dog."""

import uuid

import pytest
from sqlalchemy import text

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.models.app_user import AppUser
from daengs_backend.models.territory_bookmark import TerritoryBookmark
from tests.territory.bookmarks.conftest import MIGRATION, VERIFY, headers, sql_file

ROOT = "/app/territory/bookmarks"
SITE = "territory-site:hex-v1:140:1:2"


def site(index):
    return f"territory-site:hex-v1:140:{index}:2"


async def request(api, method="GET", site_id=None, member=None):
    return await api.client.request(
        method,
        ROOT + ("/" + site_id if site_id else ""),
        headers=headers(member or api.members[0]),
    )


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
@pytest.mark.parametrize(
    "kind", ["missing", "invalid", "admin", "unknown", "suspended", "withdrawn"]
)
async def test_authentication(api, database, method, kind):
    member = api.members[0]
    if kind in {"suspended", "withdrawn"}:
        async with database() as db:
            (await db.get(AppUser, member)).status = kind
            await db.commit()
    auth = {
        "missing": {},
        "invalid": {"Authorization": "Bearer invalid"},
        "admin": {
            "Authorization": "Bearer " + create_access_token(member, SubjectType.ADMIN, "ADMIN")
        },
        "unknown": headers(uuid.uuid4()),
    }.get(kind, headers(member))
    response = await api.client.request(
        method, ROOT if method == "GET" else ROOT + "/" + SITE, headers=auth
    )
    assert response.status_code == 401
    assert api.lookup_calls == 0


async def test_private_neutral_bookmark_lifecycle_and_retry(api, database):
    assert (await request(api)).json() == {"items": [], "total_count": 0, "limit": 20}
    first = await request(api, "PUT", SITE)
    assert first.status_code == 200
    saved = first.json()
    assert saved["is_bookmarked"] and saved["total_count"] == 1 and saved["limit"] == 20
    assert saved["created_at"] is not None
    api.unavailable = True
    assert (await request(api, "PUT", SITE)).json() == saved  # No provider needed for retry.
    assert api.lookup_calls == 1
    assert (await request(api, member=api.members[1])).json()["items"] == []
    api.unavailable = False
    await request(api, "PUT", SITE, member=api.members[1])
    page = (await request(api)).json()
    assert page["total_count"] == 1
    assert page["items"] == [
        {
            "site_id": SITE,
            "created_at": saved["created_at"],
            "location": {"lat": 37.5, "lng": 127.0},
            "location_status": "AVAILABLE",
        }
    ]
    assert (await request(api, "DELETE", SITE, member=api.members[1])).json()["total_count"] == 0
    assert (await request(api)).json()["total_count"] == 1
    deleted = (await request(api, "DELETE", SITE)).json()
    assert deleted == {
        "site_id": SITE,
        "created_at": None,
        "is_bookmarked": False,
        "total_count": 0,
        "limit": 20,
    }
    assert (await request(api, "DELETE", SITE)).json() == deleted
    async with database() as db:
        # Saving does not create any ownership rows or require a registered dog.
        assert await db.scalar(text("SELECT to_regclass('territory_claim_sites')")) is None


async def test_limit_existing_retry_and_released_slot(api, database):
    async with database() as db:
        db.add_all(
            [TerritoryBookmark(app_user_id=api.members[0], site_id=site(i)) for i in range(20)]
        )
        await db.commit()
    response = await request(api, "PUT", site(20))
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "bookmark_limit_reached",
        "limit": 20,
        "total_count": 20,
    }
    assert api.lookup_calls == 0
    assert (await request(api, "PUT", SITE)).status_code == 200
    await request(api, "DELETE", SITE)
    assert (await request(api, "PUT", site(20))).json()["total_count"] == 20
    page = (await request(api)).json()
    assert len(page["items"]) == 20
    assert page["items"][0]["site_id"] == site(20)  # Latest save first.


@pytest.mark.parametrize("site_id", ["wrong", "territory-site:hex-v1:140:x:2", "x" * 97])
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_invalid_site_format(api, site_id, method):
    assert (await request(api, method, site_id)).status_code == 422
    assert api.lookup_calls == 0


@pytest.mark.parametrize("mode,code", [("missing", 404), ("unavailable", 503)])
async def test_unresolvable_new_site_does_not_save(api, mode, code):
    setattr(api, mode, True)
    response = await request(api, "PUT", SITE)
    assert response.status_code == code
    assert (await request(api)).json()["total_count"] == 0


@pytest.mark.parametrize("mode,status", [("missing", "NOT_FOUND"), ("unavailable", "UNAVAILABLE")])
async def test_saved_list_survives_location_failure_and_can_remove(api, mode, status):
    await request(api, "PUT", SITE)
    setattr(api, mode, True)
    page = (await request(api)).json()
    assert page["total_count"] == 1 and page["items"][0]["location_status"] == status
    assert page["items"][0]["location"] is None
    assert (await request(api, "DELETE", SITE)).json()["total_count"] == 0


async def test_withdrawal_during_place_io_is_rechecked(api, database):
    async def withdraw():
        async with database() as db:
            (await db.get(AppUser, api.members[0])).status = "withdrawn"
            await db.commit()

    api.hook = withdraw
    assert (await request(api, "PUT", SITE)).status_code == 401
    async with database() as db:
        assert await db.scalar(text("SELECT count(*) FROM territory_bookmarks")) == 0


async def test_withdrawal_cleanup_reactivation_and_replayed_migration(api, database):
    await request(api, "PUT", SITE)
    await request(api, "PUT", SITE, member=api.members[1])
    async with database() as db:
        # Existing data survives upgrade replay and fresh-init replay.
        for path in (MIGRATION, MIGRATION, "db/init/33_territory_bookmarks.sql", VERIFY):
            await sql_file(db, path)
        assert await db.scalar(text("SELECT count(*) FROM territory_bookmarks")) == 2
        (await db.get(AppUser, api.members[0])).status = "withdrawn"
        await db.commit()
    assert (await request(api)).status_code == 401
    async with database() as db:
        (await db.get(AppUser, api.members[0])).status = "active"
        await db.commit()
    assert (await request(api)).json()["total_count"] == 0
    assert (await request(api, member=api.members[1])).json()["total_count"] == 1
    async with database() as db:
        await db.delete(await db.get(AppUser, api.members[1]))
        await db.commit()
        assert await db.scalar(text("SELECT count(*) FROM territory_bookmarks")) == 0


async def test_list_uses_read_only_auth_snapshot(api, monkeypatch):
    from daengs_backend.repositories import territory_bookmark as repo

    original = repo.list_all

    async def check(db, owner):
        assert await db.scalar(text("SHOW transaction_read_only")) == "on"
        assert await db.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        return await original(db, owner)

    monkeypatch.setattr(repo, "list_all", check)
    assert (await request(api)).status_code == 200
