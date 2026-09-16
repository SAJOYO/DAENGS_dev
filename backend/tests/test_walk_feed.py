"""산책 기록 통합 목록 — `GET /app/pet-walks` (docs/co-care.md 「산책 기록 통합 목록」).

#539 공동 조회(`test_walk_group_reads.py`)의 가짜 저장소와 그림을 그대로 씁니다. 조건 SQL 은 이
파일이 증명하지 못하므로 `test_walk_feed_postgres.py` 가 진짜 PostgreSQL 에서 따로 봅니다.

그림:

    A 의 `롱이씨`(a_pet) ── 연결 ── B 의 `롱롱씨`(b_pet)     A = 그룹 주보호자, B = 연결 참여
    O 의 `맥스`(s_pet) ── J 는 연결 없이 참여한 돌보미
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import test_walk_group_reads as group_reads
from fakes import FakePet, Store
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_walk_group_reads import ITEM_KEYS, T0, A, B, C, J, O, X, add_analysis, add_walk

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import walk_group as walk_group_repo
from daengs_backend.routers import pet_member as pet_member_router
from daengs_backend.routers import pet_walks as pet_walks_router
from daengs_backend.services import walk_group as walk_group_service

FEED_ITEM_KEYS = ITEM_KEYS | {"weather_code", "is_day", "temperature_c", "route_preview"}


def _matches(walk, f: walk_group_repo.WalkFeedFilter) -> bool:
    """`repositories/walk_group._feed_conditions` 의 가짜판. SQL 은 postgres 테스트가 봅니다."""
    if not set(f.pet_ids) & set(walk.pet_ids):
        return False
    if f.actor_ids is not None and walk.app_user_id not in f.actor_ids:
        return False
    if f.exclude_actor is not None and walk.app_user_id == f.exclude_actor:
        return False
    if f.started_from is not None and walk.started_at < f.started_from:
        return False
    if f.started_before is not None and walk.started_at >= f.started_before:
        return False
    if f.months is not None and walk.started_at.astimezone(ZoneInfo(f.tz)).month not in f.months:
        return False
    if f.weather_codes or f.weather_missing:
        in_codes = walk.weather_code is not None and walk.weather_code in f.weather_codes
        missing = f.weather_missing and walk.weather_code is None
        if not (in_codes or missing):
            return False
    return True


#: #539 공동 조회 테스트의 가짜 저장소·그림을 그대로 씁니다(같은 이름으로 걸어야 pytest 가 찾습니다).
store = group_reads.store
linked = group_reads.linked
solo = group_reads.solo


@pytest.fixture
def feed(store: Store, monkeypatch: pytest.MonkeyPatch) -> Store:
    def latest_distance(walk_id):
        rows = sorted((a for a in store.walk_analyses if a.walk_id == walk_id), key=lambda a: (a.derived_at, a.id))
        return rows[-1].moving_distance_m if rows else 0

    async def list_feed_page(session, f, *, limit, before=None):
        rows = sorted((w for w in store.walks if _matches(w, f)), key=lambda w: (w.started_at, w.id), reverse=True)
        if before is not None:
            rows = [w for w in rows if (w.started_at, w.id) < before]
        return rows[:limit]

    async def feed_totals(session, f):
        rows = [w for w in store.walks if _matches(w, f)]
        return walk_group_repo.WalkFeedTotals(
            count=len(rows),
            distance_m=sum(latest_distance(w.id) for w in rows),
            duration_s=sum(int((w.ended_at - w.started_at).total_seconds()) for w in rows),
        )

    monkeypatch.setattr(walk_group_repo, "list_feed_page", list_feed_page)
    monkeypatch.setattr(walk_group_repo, "feed_totals", feed_totals)
    return store


def client_as(uid: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(pet_walks_router.router)
    app.include_router(pet_walks_router.feed_router)
    app.include_router(pet_member_router.router)
    app.dependency_overrides[next(iter(CurrentAppUser.__metadata__)).dependency] = lambda: AppPrincipal(app_user_id=uid)
    return TestClient(app, raise_server_exceptions=False)


def get_feed(uid: uuid.UUID, **params) -> dict:
    response = client_as(uid).get("/app/pet-walks", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(body: dict) -> list[str]:
    return [w["id"] for w in body["walks"]]


# ── 범위: 내 산책 · 공동 보호자 산책 · 논리 그룹 ──────────────────────────────


def test_내_산책과_공동_보호자_산책이_한_목록에_최근_순으로_나온다(feed: Store, linked):
    a_pet, b_pet = linked
    wa = add_walk(feed, A, [a_pet], T0)
    wb = add_walk(feed, B, [b_pet], T0 + timedelta(hours=1))

    body = get_feed(B)
    assert ids(body) == [str(wb.id), str(wa.id)]
    by_id = {w["id"]: w for w in body["walks"]}
    assert by_id[str(wb.id)]["is_mine"] is True
    assert by_id[str(wa.id)]["is_mine"] is False
    assert by_id[str(wa.id)]["actor"] == {"app_user_id": str(A), "nickname": "에이"}
    assert ids(get_feed(A)) == [str(wb.id), str(wa.id)]


def test_연결_없이_참여한_돌보미도_대표의_산책을_본다(feed: Store, solo: FakePet):
    walk = add_walk(feed, O, [solo], T0)
    assert ids(get_feed(J)) == [str(walk.id)]


def test_무관한_사용자에게는_아무것도_안_나온다(feed: Store, linked, solo):
    a_pet, _ = linked
    add_walk(feed, A, [a_pet], T0)
    add_walk(feed, O, [solo], T0)

    body = get_feed(X)
    assert body["walks"] == [] and body["totals"] == {"count": 0, "distance_m": 0, "duration_s": 0}
    assert body["carers"] == [] and body["next_cursor"] is None


def test_exclude_mine_은_내가_올린_산책만_뺀다(feed: Store, linked):
    a_pet, b_pet = linked
    wa = add_walk(feed, A, [a_pet], T0)
    add_walk(feed, B, [b_pet], T0 + timedelta(hours=1))

    body = get_feed(B, exclude_mine="true")
    assert ids(body) == [str(wa.id)]
    assert body["totals"]["count"] == 1


def test_태그된_강아지는_요청자_화면의_강아지_id_로_바뀐다(feed: Store, linked):
    a_pet, b_pet = linked
    both = add_walk(feed, A, [a_pet, b_pet], T0)

    b_walks = get_feed(B)["walks"]
    assert ids({"walks": b_walks}) == [str(both.id)]  # 그룹의 두 행에 태그돼도 한 번
    assert b_walks[0]["pet_ids"] == [str(b_pet.id)]
    assert get_feed(A)["walks"][0]["pet_ids"] == [str(a_pet.id)]


def test_연결된_개인_행에만_돌보미인_사람은_자기_행_산책만_본다(feed: Store, linked):
    a_pet, b_pet = linked
    feed.pet_members.append((b_pet.id, C))  # 연결 전 B 의 강아지를 돌보던 사람
    add_walk(feed, A, [a_pet], T0)
    wb = add_walk(feed, B, [b_pet], T0 + timedelta(hours=1))

    assert ids(get_feed(C)) == [str(wb.id)]


# ── 조건: 강아지 · 보호자 · 기간 · 계절 · 날씨 ──────────────────────────────


def test_강아지_필터는_그_카드의_그룹만_읽는다(feed: Store, linked):
    a_pet, b_pet = linked
    other = FakePet(app_user_id=B, name="두부", breed="믹스")
    feed.pets.append(other)
    wa = add_walk(feed, A, [a_pet], T0)
    wo = add_walk(feed, B, [other], T0 + timedelta(hours=1))

    assert ids(get_feed(B, pet_id=str(b_pet.id))) == [str(wa.id)]
    assert ids(get_feed(B, pet_id=str(other.id))) == [str(wo.id)]
    assert ids(get_feed(B, pet_id=[str(b_pet.id), str(other.id)])) == [str(wo.id), str(wa.id)]


def test_볼_수_없는_강아지_id_는_범위를_넓히지_않는다(feed: Store, linked, solo):
    """IDOR — 남의 강아지 id 를 보내도 그 산책이 안 나오고, 전체로 넓혀지지도 않는다."""
    a_pet, b_pet = linked
    add_walk(feed, A, [a_pet], T0)
    add_walk(feed, O, [solo], T0)

    only_foreign = get_feed(B, pet_id=str(solo.id))
    assert only_foreign["walks"] == [] and only_foreign["totals"]["count"] == 0
    assert ids(get_feed(B, pet_id=[str(solo.id), str(b_pet.id)])) == ids(get_feed(B, pet_id=str(b_pet.id)))
    assert get_feed(B, pet_id=str(uuid.uuid4()))["walks"] == []


def test_보호자_필터는_여러_명을_함께_고른다(feed: Store, linked):
    a_pet, b_pet = linked
    feed.pet_members.append((a_pet.id, C))
    wa = add_walk(feed, A, [a_pet], T0)
    wb = add_walk(feed, B, [b_pet], T0 + timedelta(hours=1))
    wc = add_walk(feed, C, [a_pet], T0 + timedelta(hours=2))

    assert ids(get_feed(B, actor_id=str(A))) == [str(wa.id)]
    assert ids(get_feed(B, actor_id=[str(A), str(C)])) == [str(wc.id), str(wa.id)]
    assert ids(get_feed(B, actor_id=str(B))) == [str(wb.id)]
    assert get_feed(B, actor_id=str(X))["walks"] == []


def test_기간은_요청자_시간대의_날짜로_자른다(feed: Store, linked):
    a_pet, _b_pet = linked
    # 2026-09-10 15:30 UTC = 2026-09-11 00:30 KST
    late = add_walk(feed, A, [a_pet], datetime(2026, 9, 10, 15, 30, tzinfo=UTC))

    assert ids(get_feed(B, date_from="2026-09-11", tz="Asia/Seoul")) == [str(late.id)]
    assert get_feed(B, date_from="2026-09-11", tz="UTC")["walks"] == []
    assert ids(get_feed(B, date_through="2026-09-10", tz="UTC")) == [str(late.id)]
    assert get_feed(B, date_through="2026-09-10", tz="Asia/Seoul")["walks"] == []


def test_계절과_날씨_조건(feed: Store, linked):
    a_pet, _b_pet = linked
    clear = add_walk(feed, A, [a_pet], datetime(2026, 9, 1, 3, tzinfo=UTC))
    clear.weather_code = 0
    rainy = add_walk(feed, A, [a_pet], datetime(2026, 7, 1, 3, tzinfo=UTC))
    rainy.weather_code = 61
    unknown = add_walk(feed, A, [a_pet], datetime(2026, 12, 1, 3, tzinfo=UTC))

    assert ids(get_feed(B, month=[9, 10, 11], tz="Asia/Seoul")) == [str(clear.id)]
    assert ids(get_feed(B, weather_code=[61])) == [str(rainy.id)]
    assert ids(get_feed(B, weather_missing="true")) == [str(unknown.id)]
    assert ids(get_feed(B, weather_code=[0], weather_missing="true")) == [str(unknown.id), str(clear.id)]


# ── 보호자 후보 ────────────────────────────────────────────────────────────


def test_보호자_후보는_여러_강아지에서_사람_단위로_중복_제거된다(feed: Store, linked):
    a_pet, b_pet = linked
    second = FakePet(app_user_id=A, name="두부", breed="믹스")
    feed.pets.append(second)
    feed.pet_members.append((second.id, B))

    carers = get_feed(A)["carers"]
    assert [c["app_user_id"] for c in carers] == [str(A), str(B)]
    me, b = carers
    assert me["is_me"] is True and b["is_me"] is False
    assert b["nickname"] == "비"
    assert set(me["pet_ids"]) == {str(a_pet.id), str(second.id)}
    assert set(b["pet_ids"]) == {str(a_pet.id), str(second.id)}

    # B 에게는 같은 두 사람이 B 화면의 카드 id(롱롱씨·두부)로 한 번씩 나온다.
    b_view = get_feed(B)["carers"]
    assert [c["app_user_id"] for c in b_view] == [str(B), str(A)]
    assert all(set(c["pet_ids"]) == {str(b_pet.id), str(second.id)} for c in b_view)


def test_보호자_후보는_조건과_무관하게_볼_수_있는_강아지_전부_기준이다(feed: Store, linked):
    _a, b_pet = linked
    body = get_feed(B, actor_id=str(X), pet_id=str(b_pet.id))
    assert {c["app_user_id"] for c in body["carers"]} == {str(A), str(B)}


# ── 정렬 · 커서 · 합계 ─────────────────────────────────────────────────────


def test_커서로_끊어_읽으면_빠짐도_겹침도_없다(feed: Store, linked):
    a_pet, _b_pet = linked
    made = [add_walk(feed, A if i % 2 else B, [a_pet], T0 - timedelta(hours=i // 2)) for i in range(7)]
    expected = [str(w.id) for w in sorted(made, key=lambda w: (w.started_at, w.id), reverse=True)]

    seen, cursor = [], None
    while True:
        params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
        body = get_feed(B, **params)
        seen += ids(body)
        cursor = body["next_cursor"]
        assert body["totals"]["count"] == 7  # 페이지가 아니라 조건 전체
        if cursor is None:
            break
    assert seen == expected


def test_합계는_조건_전체의_횟수_최신_세대_거리_시간이다(feed: Store, linked):
    a_pet, b_pet = linked
    measured = add_walk(feed, A, [a_pet], T0)
    add_analysis(feed, measured, 900, 1500, T0 + timedelta(hours=1))
    add_analysis(feed, measured, 1234, 1600, T0 + timedelta(hours=2))  # 최신 세대만
    add_walk(feed, A, [a_pet], T0 - timedelta(days=1))  # 계산 전 — 거리 0
    mine = add_walk(feed, B, [b_pet], T0 + timedelta(hours=3))
    add_analysis(feed, mine, 500, 600, T0 + timedelta(hours=4))

    everything = get_feed(B, limit=1)["totals"]
    assert everything == {"count": 3, "distance_m": 1734, "duration_s": 3 * 1800}
    shared_only = get_feed(B, exclude_mine="true", limit=1)["totals"]
    assert shared_only == {"count": 2, "distance_m": 1234, "duration_s": 2 * 1800}


# ── 카드 값 ────────────────────────────────────────────────────────────────


def test_카드에_날씨와_썸네일_경로가_실리고_상세_좌표는_없다(feed: Store, linked):
    a_pet, _b_pet = linked
    walk = add_walk(feed, A, [a_pet], T0, points=120)
    walk.weather_code, walk.is_day = 3, True

    item = get_feed(B)["walks"][0]
    assert set(item) == FEED_ITEM_KEYS
    assert (item["weather_code"], item["is_day"], item["temperature_c"]) == (3, True, None)
    segments = item["route_preview"]
    assert len(segments) == 1
    assert 2 <= len(segments[0]) <= walk_group_service.PREVIEW_MAX_POINTS + 1
    assert all(set(p) == {"lat", "lng"} for p in segments[0])
    assert segments[0][0]["lat"] == pytest.approx(37.4978)
    assert segments[0][-1]["lat"] == pytest.approx(37.4978 + 119 / 10000)
    assert set(get_feed(B)) == {"walks", "totals", "carers", "next_cursor"}


def test_좌표가_없는_산책의_썸네일은_빈_목록이다(feed: Store, linked):
    a_pet, _b_pet = linked
    walk = add_walk(feed, A, [a_pet], T0)
    walk.points = []
    assert get_feed(B)["walks"][0]["route_preview"] == []


# ── 나가기 · 내보내기 · 탈퇴 ────────────────────────────────────────────────


def test_내보내진_보호자는_그룹_산책이_통합_목록에서_사라진다(feed: Store, linked):
    a_pet, b_pet = linked
    add_walk(feed, A, [a_pet], T0)
    wb = add_walk(feed, B, [b_pet], T0 + timedelta(hours=1))

    assert client_as(A).delete(f"/app/pets/{a_pet.id}/members/{B}").status_code == 204

    body = get_feed(B)
    assert ids(body) == [str(wb.id)]
    assert {c["app_user_id"] for c in body["carers"]} == {str(B)}
    assert get_feed(B, pet_id=str(a_pet.id))["walks"] == []


def test_탈퇴한_보호자에게는_아무것도_안_나온다(feed: Store, linked):
    a_pet, b_pet = linked
    add_walk(feed, A, [a_pet], T0)
    feed.pet_members = [row for row in feed.pet_members if row[1] != B]
    feed.pets.remove(b_pet)

    assert get_feed(B)["walks"] == []


# ── 입력 검증 · #539 하위 호환 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {"tz": "Mars/Olympus"},
        {"month": 13},
        {"weather_code": 100},
        {"date_from": "2026-09-11", "date_through": "2026-09-10"},
        {"cursor": "!!!"},
        {"limit": 51},
    ],
)
def test_읽을_수_없는_조건은_422(feed: Store, linked, params):
    assert client_as(B).get("/app/pet-walks", params=params).status_code == 422


def test_강아지별_공동_조회_계약은_그대로다(feed: Store, linked):
    """#539 `GET /app/pets/{pet_id}/walks` 의 키와 `pet_ids`(행 id) 는 바뀌지 않는다."""
    a_pet, b_pet = linked
    both = add_walk(feed, A, [a_pet, b_pet], T0)

    body = client_as(B).get(f"/app/pets/{b_pet.id}/walks").json()
    assert set(body) == {"pet_id", "walks", "next_cursor"}
    assert set(body["walks"][0]) == ITEM_KEYS
    assert set(body["walks"][0]["pet_ids"]) == {str(a_pet.id), str(b_pet.id)}
    detail = client_as(B).get(f"/app/pets/{b_pet.id}/walks/{both.id}")
    assert detail.status_code == 200 and set(detail.json()) == ITEM_KEYS | {"points"}
