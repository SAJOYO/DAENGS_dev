"""다중 강아지 초대 — 묶음 생성·목록·취소·미리보기 (MVP 결정 §2 · §8).

DB 는 쓰지 않습니다 (`test_pet_members.py` 와 같은 규칙). 수락은 다음 커밋이라 여기서는
**보내는 쪽과 미리보기**만 봅니다.

이 파일이 지키는 문장 하나:

> 강아지 수와 무관하게 **묶음 하나 = 활성 초대 하나**이고, 취소·만료·사용 완료도 묶음
> 전체에 적용된다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakeIdentity,
    FakeInvite,
    FakeInvitePet,
    FakePet,
    FakeSession,
    Store,
    install,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.core.token import hash_refresh_token
from daengs_backend.routers import pet_member as pet_member_router
from daengs_backend.services import pet_member as member_service

OWNER = uuid.uuid4()
GUEST = uuid.uuid4()
STRANGER = uuid.uuid4()

OWNER_KAKAO = 3001
GUEST_KAKAO = 3002


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(FakeAdmin(login_id="admin", password_hash="x", name="관리자", role="OWNER"))
    install(s, monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=OWNER_KAKAO, id=OWNER, nickname="아빠"))
    s.add_app_user(FakeAppUser(kakao_id=GUEST_KAKAO, id=GUEST, nickname="엄마"))
    return s


def client_as(app_user_id: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(pet_member_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=app_user_id)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def pets(store: Store) -> list[FakePet]:
    rows = [
        FakePet(app_user_id=OWNER, name="맥스", breed="dog_beagle"),
        FakePet(app_user_id=OWNER, name="코코", breed="dog_pug"),
        FakePet(app_user_id=OWNER, name="루니", breed="믹스"),
    ]
    store.pets += rows
    return rows


def _bundle_of(store: Store, invite_id: uuid.UUID) -> set[uuid.UUID]:
    return {r.pet_id for r in store.pet_invite_pets if r.invite_id == invite_id}


# ── 묶음 생성 ──────────────────────────────────────────────────────────────


async def test_여러_마리를_토큰_하나에_담는다(store: Store, pets):
    res = client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(pets[1].id)]}
    )
    assert res.status_code == 201
    body = res.json()
    assert set(body["pet_ids"]) == {str(pets[0].id), str(pets[1].id)}
    assert body["token"]

    invite = store.pet_invites[0]
    assert invite.pet_count == 2
    assert _bundle_of(store, invite.id) == {pets[0].id, pets[1].id}


async def test_앵커는_첫_번째_아이다(store: Store, pets):
    """`pet_invites.pet_id` 는 NOT NULL 로 남아 승계·만료 청소가 봅니다."""
    client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[1].id), str(pets[0].id)]}
    )
    assert store.pet_invites[0].pet_id == pets[1].id


async def test_앵커도_자식_줄에_들어간다(store: Store, pets):
    """앵커만 부모 표에 있고 자식 표에 없으면 묶음 조회가 그 아이를 빠뜨립니다."""
    client_as(OWNER).post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]})
    invite = store.pet_invites[0]
    assert _bundle_of(store, invite.id) == {pets[0].id}


async def test_남의_강아지가_섞이면_404_고_아무것도_안_만든다(store: Store, pets):
    other = FakePet(app_user_id=GUEST, name="남의아이", breed="믹스")
    store.pets.append(other)

    res = client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(other.id)]}
    )
    assert res.status_code == 404
    assert store.pet_invites == []
    assert store.pet_invite_pets == []


async def test_연결된_아이는_그룹_주보호자만_초대한다(store: Store):
    """공동 보호자가 초대하면 그 그룹에 사람을 마음대로 들이게 됩니다 (MVP 결정 §6)."""
    a_pet = FakePet(app_user_id=GUEST, name="롱이씨", breed="믹스")
    b_pet = FakePet(app_user_id=OWNER, name="롱롱씨", breed="믹스")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = b_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet]
    store.pet_identities.append(identity)

    res = client_as(OWNER).post("/app/pet-invites", json={"pet_ids": [str(b_pet.id)]})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "not_group_owner"
    assert store.pet_invites == []


async def test_배웅한_아이는_못_담는다(store: Store, pets):
    """배웅 상태를 그룹 공통으로 옮기는 것은 후속이라, 그때까지 초대에 안 넣습니다."""
    pets[1].farewell_on = date(2026, 9, 1)
    res = client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(pets[1].id)]}
    )
    assert res.status_code == 409
    assert "배웅" in res.json()["detail"]
    assert store.pet_invites == []


async def test_같은_아이를_두_번_담으면_422(store: Store, pets):
    res = client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(pets[0].id)]}
    )
    assert res.status_code == 422


async def test_빈_목록은_422(store: Store, pets):
    assert client_as(OWNER).post("/app/pet-invites", json={"pet_ids": []}).status_code == 422


async def test_여섯_마리는_422(store: Store, pets):
    ids = [str(uuid.uuid4()) for _ in range(6)]
    assert client_as(OWNER).post("/app/pet-invites", json={"pet_ids": ids}).status_code == 422


# ── 활성 묶음 상한: 사람당 3 ───────────────────────────────────────────────


async def test_활성_묶음은_사람당_세_개다(store: Store, pets):
    """**강아지당이 아닙니다** (MVP 결정 §2). 강아지당이면 3마리로 9묶음이 살아 있습니다."""
    client = client_as(OWNER)
    for pet in pets:
        assert client.post("/app/pet-invites", json={"pet_ids": [str(pet.id)]}).status_code == 201

    res = client.post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]})
    assert res.status_code == 409
    assert len(store.pet_invites) == 3


async def test_다섯_마리_묶음도_자리_하나만_먹는다(store: Store, pets):
    """강아지 수와 무관하게 묶음 하나 = 활성 하나입니다."""
    client = client_as(OWNER)
    assert (
        client.post(
            "/app/pet-invites", json={"pet_ids": [str(p.id) for p in pets]}
        ).status_code
        == 201
    )
    assert client.post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]}).status_code == 201
    assert client.post("/app/pet-invites", json={"pet_ids": [str(pets[1].id)]}).status_code == 201
    assert client.post("/app/pet-invites", json={"pet_ids": [str(pets[2].id)]}).status_code == 409


async def test_수락된_묶음은_상한을_안_먹는다(store: Store, pets):
    client = client_as(OWNER)
    for pet in pets:
        client.post("/app/pet-invites", json={"pet_ids": [str(pet.id)]})
    store.pet_invites[0].accepted_by = GUEST
    store.pet_invites[0].accepted_at = datetime.now(UTC)

    assert client.post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]}).status_code == 201


async def test_만료된_묶음은_새_초대를_만들_때_지워진다(store: Store, pets):
    old = FakeInvite(
        pet_id=pets[0].id,
        invited_by=OWNER,
        token_hash="x" * 64,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    store.pet_invites.append(old)
    store.pet_invite_pets.append(FakeInvitePet(invite_id=old.id, pet_id=pets[0].id))

    client_as(OWNER).post("/app/pet-invites", json={"pet_ids": [str(pets[1].id)]})

    assert old.id not in {i.id for i in store.pet_invites}
    # 자식 줄도 CASCADE 로 같이 사라집니다.
    assert _bundle_of(store, old.id) == set()


# ── 구 경로 호환 ───────────────────────────────────────────────────────────


async def test_구_경로_생성이_한_마리_묶음을_만든다(store: Store, pets):
    """구 앱이 만든 초대도 새 앱 것과 구별 없이 다뤄져야 합니다 (MVP 결정 §9)."""
    res = client_as(OWNER).post(f"/app/pets/{pets[0].id}/invites")
    assert res.status_code == 201
    invite = store.pet_invites[0]
    assert invite.pet_count == 1
    assert _bundle_of(store, invite.id) == {pets[0].id}


async def test_구_경로_생성도_사람당_상한을_함께_센다(store: Store, pets):
    """두 경로가 같은 자리를 세지 않으면 구 앱으로 상한을 우회할 수 있습니다."""
    client = client_as(OWNER)
    client.post("/app/pet-invites", json={"pet_ids": [str(p.id) for p in pets]})
    client.post(f"/app/pets/{pets[0].id}/invites")
    client.post(f"/app/pets/{pets[1].id}/invites")

    assert client.post(f"/app/pets/{pets[2].id}/invites").status_code == 409


async def test_구_목록은_그_아이가_낀_묶음을_준다(store: Store, pets):
    """앵커가 아닌 아이로도 찾혀야 대표가 그 묶음을 취소할 수 있습니다."""
    client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(pets[1].id)]}
    )
    body = client_as(OWNER).get(f"/app/pets/{pets[1].id}/invites").json()
    assert len(body["invites"]) == 1
    assert "token" not in body["invites"][0]


async def test_구_취소는_묶음_전체를_지운다(store: Store, pets):
    client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(pets[1].id)]}
    )
    invite_id = store.pet_invites[0].id

    res = client_as(OWNER).delete(f"/app/pets/{pets[1].id}/invites/{invite_id}")
    assert res.status_code == 204
    assert store.pet_invites == []
    assert store.pet_invite_pets == []


async def test_묶음에_없는_아이로는_취소_못_한다(store: Store, pets):
    """URL 의 두 id 가 짝인지 보는 자리 — 없으면 남의 아이 경로로 아무 초대나 지웁니다."""
    client_as(OWNER).post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]})
    invite_id = store.pet_invites[0].id

    res = client_as(OWNER).delete(f"/app/pets/{pets[2].id}/invites/{invite_id}")
    assert res.status_code == 404
    assert len(store.pet_invites) == 1


# ── 사용자 단위 목록·취소 ─────────────────────────────────────────────────


async def test_내가_보낸_묶음_목록(store: Store, pets):
    client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(pets[1].id)]}
    )
    body = client_as(OWNER).get("/app/pet-invites").json()
    assert len(body["invites"]) == 1
    names = {p["name"] for p in body["invites"][0]["pets"]}
    assert names == {"맥스", "코코"}


async def test_목록은_토큰도_해시도_안_준다(store: Store, pets):
    client_as(OWNER).post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]})
    raw = client_as(OWNER).get("/app/pet-invites").text
    assert "token" not in raw
    assert store.pet_invites[0].token_hash not in raw


async def test_남의_묶음은_안_보인다(store: Store, pets):
    client_as(OWNER).post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]})
    assert client_as(GUEST).get("/app/pet-invites").json()["invites"] == []


async def test_사용자_단위_취소(store: Store, pets):
    client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(pets[0].id), str(pets[1].id)]}
    )
    invite_id = store.pet_invites[0].id
    assert client_as(OWNER).delete(f"/app/pet-invites/{invite_id}").status_code == 204
    assert store.pet_invites == []


async def test_남의_묶음은_못_지운다(store: Store, pets):
    client_as(OWNER).post("/app/pet-invites", json={"pet_ids": [str(pets[0].id)]})
    invite_id = store.pet_invites[0].id
    assert client_as(GUEST).delete(f"/app/pet-invites/{invite_id}").status_code == 404
    assert len(store.pet_invites) == 1


# ── 미리보기 · 연결 후보 ───────────────────────────────────────────────────


def _issue(store: Store, client: TestClient, pet_ids: list[uuid.UUID]) -> str:
    res = client.post("/app/pet-invites", json={"pet_ids": [str(i) for i in pet_ids]})
    assert res.status_code == 201
    return res.json()["token"]


async def test_미리보기가_담긴_아이를_보여_준다(store: Store, pets):
    token = _issue(store, client_as(OWNER), [pets[0].id, pets[1].id])
    body = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token}).json()

    assert body["invited_by_nickname"] == "아빠"
    assert {p["name"] for p in body["pets"]} == {"맥스", "코코"}


async def test_미리보기에_건강정보가_없다(store: Store, pets):
    """수락 전에는 구성원이 아닙니다 — 토큰 하나로 남의 집 지병을 읽으면 안 됩니다."""
    pets[0].health_conditions = "슬개골 탈구"
    pets[0].medications = "관절 영양제"
    token = _issue(store, client_as(OWNER), [pets[0].id])

    raw = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token}).text
    assert "슬개골" not in raw
    assert "영양제" not in raw


async def test_미리보기가_연결_후보를_같이_준다(store: Store, pets):
    mine = FakePet(app_user_id=GUEST, name="내_아이", breed="믹스")
    store.pets.append(mine)
    token = _issue(store, client_as(OWNER), [pets[0].id])

    body = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token}).json()
    assert [c["name"] for c in body["link_candidates"]] == ["내_아이"]


async def test_연결_후보에서_빠지는_넷(store: Store, pets):
    """네 조건 각각이 실제로 거르는지 (MVP 결정 §2)."""
    ok = FakePet(app_user_id=GUEST, name="가능", breed="믹스")
    shared = FakePet(app_user_id=GUEST, name="공동보호자있음", breed="믹스")
    linked_pet = FakePet(app_user_id=GUEST, name="이미연결", breed="믹스")
    farewelled = FakePet(app_user_id=GUEST, name="배웅함", breed="믹스", farewell_on=date(2026, 1, 1))
    not_mine = FakePet(app_user_id=STRANGER, name="남의것", breed="믹스")
    identity = FakeIdentity(owner_pet_id=linked_pet.id)
    linked_pet.identity_id = identity.id
    store.pets += [ok, shared, linked_pet, farewelled, not_mine]
    store.pet_identities.append(identity)
    store.pet_members.append((shared.id, STRANGER))

    token = _issue(store, client_as(OWNER), [pets[0].id])
    body = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token}).json()
    assert [c["name"] for c in body["link_candidates"]] == ["가능"]


async def test_이미_구성원인_아이는_표시된다(store: Store, pets):
    store.pet_members.append((pets[0].id, GUEST))
    token = _issue(store, client_as(OWNER), [pets[0].id, pets[1].id])

    body = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token}).json()
    flags = {p["name"]: p["already_member"] for p in body["pets"]}
    assert flags == {"맥스": True, "코코": False}


async def test_없는_토큰은_404(store: Store, pets):
    res = client_as(GUEST).post("/app/pet-invites/preview", json={"token": "없는토큰"})
    assert res.status_code == 404


async def test_만료된_토큰은_410_이고_지우지_않는다(store: Store, pets):
    """미리보기는 읽기만 합니다 — 여기서 지우면 뒤이은 수락 재시도가 404 를 받아
    "만료" 와 "없는 토큰" 이 뭉개집니다."""
    token = _issue(store, client_as(OWNER), [pets[0].id])
    store.pet_invites[0].expires_at = datetime.now(UTC) - timedelta(seconds=1)

    res = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token})
    assert res.status_code == 410
    assert len(store.pet_invites) == 1


async def test_남이_이미_쓴_토큰은_404(store: Store, pets):
    token = _issue(store, client_as(OWNER), [pets[0].id])
    store.pet_invites[0].accepted_by = STRANGER

    res = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token})
    assert res.status_code == 404


# ── 묶음 불변성 ────────────────────────────────────────────────────────────


async def test_강아지가_지워지면_묶음_전체가_410(store: Store, pets):
    """남은 강아지만 부분 수락하지 않습니다 (MVP 결정 §2)."""
    token = _issue(store, client_as(OWNER), [pets[0].id, pets[1].id])
    invite_id = store.pet_invites[0].id
    # `pet_invite_pets.pet_id` 의 CASCADE 자리 — 자식 줄만 조용히 사라집니다.
    store.pet_invite_pets = [
        r for r in store.pet_invite_pets if not (r.invite_id == invite_id and r.pet_id == pets[1].id)
    ]

    res = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token})
    assert res.status_code == 410
    assert "바뀌었" in res.json()["detail"]


async def test_주보호자가_바뀌면_묶음_전체가_410(store: Store, pets):
    token = _issue(store, client_as(OWNER), [pets[0].id, pets[1].id])
    pets[1].app_user_id = STRANGER

    res = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token})
    assert res.status_code == 410


async def test_배웅하면_묶음_전체가_410(store: Store, pets):
    token = _issue(store, client_as(OWNER), [pets[0].id])
    pets[0].farewell_on = date(2026, 9, 1)

    res = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token})
    assert res.status_code == 410


async def test_자식_줄이_없는_옛_초대는_앵커_하나로_본다(store: Store, pets):
    """마이그레이션이 서버보다 먼저 나가는 창에서 옛 코드가 만든 초대입니다
    (MVP 결정 §9). 구성이 바뀐 것이 아니므로 410 이면 안 됩니다."""
    token = "옛토큰"
    store.pet_invites.append(
        FakeInvite(
            pet_id=pets[0].id,
            invited_by=OWNER,
            token_hash=hash_refresh_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    body = client_as(GUEST).post("/app/pet-invites/preview", json={"token": token}).json()
    assert [p["name"] for p in body["pets"]] == ["맥스"]


async def test_bundle_pet_ids_가_구성_변경을_알려_준다(store: Store, pets):
    _issue(store, client_as(OWNER), [pets[0].id, pets[1].id])
    invite = store.pet_invites[0]

    ids, changed = await member_service.bundle_pet_ids(FakeSession(store), invite)
    assert set(ids) == {pets[0].id, pets[1].id}
    assert changed is False

    store.pet_invite_pets = [r for r in store.pet_invite_pets if r.pet_id != pets[1].id]
    ids, changed = await member_service.bundle_pet_ids(FakeSession(store), invite)
    assert changed is True
