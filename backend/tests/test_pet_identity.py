"""논리 강아지 — 목록 접기·공통 정보 투영·그룹 관리 권한 (MVP 결정 §3~§6).

DB 는 쓰지 않습니다 (`test_pet_members.py` 와 같은 규칙). 여기서 보는 것은 **규칙**이고,
FK 의 삭제 동작·부분 UNIQUE 같은 스키마 보장은 `db/migrations/verify_*.sql` 과 변조
하네스(`tools/check_migration_verification.py`)가 봅니다.

이 파일 전체를 관통하는 그림 하나:

    A 의 `롱이씨`(101) ── 같은 실제 강아지 ── B 의 `롱롱씨`(202)
    A 가 그룹 주보호자(앵커는 101)         B 는 자기 행의 대표이지만 공동 보호자

A 는 `롱이씨` 카드 한 장, B 는 `롱롱씨` 카드 한 장을 봅니다. 견종·지병·배웅은 둘 다
101 의 값을 봅니다. **행도 기록도 합쳐지지 않습니다.**
"""

import uuid
from datetime import date

import pytest
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakeIdentity,
    FakePet,
    FakeSession,
    Store,
    install,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.routers import pet as pet_router
from daengs_backend.routers import pet_member as pet_member_router
from daengs_backend.schemas.pet import PetUpsert
from daengs_backend.services import dog_context
from daengs_backend.services import pet as pet_service
from daengs_backend.services import pet_identity as identity_service

A = uuid.uuid4()  # 초대한 쪽 = 그룹 주보호자
B = uuid.uuid4()  # 수락한 쪽 = 연결한 공동 보호자
STRANGER = uuid.uuid4()

A_KAKAO = 2001
B_KAKAO = 2002


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(FakeAdmin(login_id="admin", password_hash="x", name="관리자", role="OWNER"))
    install(s, monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=A_KAKAO, id=A))
    s.add_app_user(FakeAppUser(kakao_id=B_KAKAO, id=B))
    return s


def client_as(app_user_id: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(pet_router.router)
    app.include_router(pet_member_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=app_user_id)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def linked(store: Store) -> tuple[FakePet, FakePet]:
    """A 의 `롱이씨`와 B 의 `롱롱씨`가 이미 연결된 상태.

    B 는 A 의 행(101)의 **돌보미**이기도 합니다 — 수락이 멤버십과 연결을 같이 만들기
    때문입니다. 그래야 B 가 101 을 읽을 수 있고, 목록에 행이 둘 들어옵니다.
    """
    a_pet = FakePet(
        app_user_id=A,
        name="롱이씨",
        breed="dog_pug",
        health_conditions="슬개골 탈구",
        medications="관절 영양제",
        birth_date=date(2020, 3, 1),
        birth_date_kind="birthday",
        photo_storage_key="pets/a.jpg",
    )
    b_pet = FakePet(
        app_user_id=B,
        name="롱롱씨",
        breed="dog_beagle",
        health_conditions="없음",
        photo_storage_key="pets/b.jpg",
    )
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = identity.id
    b_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet]
    store.pet_identities.append(identity)
    store.pet_members.append((a_pet.id, B))
    return a_pet, b_pet


# ── 목록 접기 ──────────────────────────────────────────────────────────────


async def test_연결된_아이는_각자_카드_한_장만_본다(store: Store, linked):
    a_pet, b_pet = linked

    a_views, _ = await pet_service.list_pets(None, A)
    b_views, _ = await pet_service.list_pets(None, B)

    assert [v.display.id for v in a_views] == [a_pet.id]
    assert [v.display.id for v in b_views] == [b_pet.id]


async def test_B_의_접근_가능_행은_둘인데_카드는_하나다(store: Store, linked):
    """접는 것이 **서버의 일**이라는 회귀. 리포지토리는 행 둘을 그대로 돌려줍니다 —
    여기서 접지 않으면 마이에 카드 두 장, 미니룸에 두 마리가 뜹니다."""
    rows = await pet_repo.list_accessible(None, B)
    assert len(rows) == 2

    views, _ = await pet_service.list_pets(None, B)
    assert len(views) == 1


async def test_연결_안_된_아이는_표시용과_공통이_같은_행이다(store: Store):
    solo = FakePet(app_user_id=A, name="혼자", breed="dog_maltese")
    store.pets.append(solo)

    views, _ = await pet_service.list_pets(None, A)
    view = next(v for v in views if v.display.id == solo.id)
    assert view.display is view.common


# ── 사용자별 이름·사진 / 주보호자 공통 정보 ────────────────────────────────


async def test_이름과_사진은_각자_것이다(store: Store, linked):
    _a, b_pet = linked
    body = client_as(B).get("/app/pets").json()["pets"]
    assert [p["name"] for p in body] == ["롱롱씨"]
    assert body[0]["id"] == str(b_pet.id)
    assert body[0]["has_photo"] is True

    assert client_as(A).get("/app/pets").json()["pets"][0]["name"] == "롱이씨"


async def test_공통_정보는_그룹_주보호자_행에서_온다(store: Store, linked):
    """B 의 카드에 **B 의 견종·지병이 아니라 A 의 것**이 실립니다 (MVP 결정 §5).

    한 실제 강아지에 두 개의 의학 프로필이 생기는 것을 막는 자리입니다.
    """
    card = client_as(B).get("/app/pets").json()["pets"][0]
    assert card["breed"] == "dog_pug"
    assert card["health_conditions"] == "슬개골 탈구"
    assert card["medications"] == "관절 영양제"
    assert card["birth_date"] == "2020-03-01"


async def test_연결_전_B_의_원본_값은_안_지워진다(store: Store, linked):
    """투영은 **읽을 때만** 입니다 — 물리 병합 금지의 회귀."""
    _a, b_pet = linked
    assert b_pet.breed == "dog_beagle"
    assert b_pet.health_conditions == "없음"


async def test_배웅_상태도_주보호자_기준이다(store: Store, linked):
    a_pet, _b = linked
    a_pet.farewell_on = date(2026, 9, 1)
    card = client_as(B).get("/app/pets").json()["pets"][0]
    assert card["farewell_on"] == "2026-09-01"


# ── is_owner / is_group_owner ──────────────────────────────────────────────


async def test_B_는_행_대표지만_그룹_주보호자가_아니다(store: Store, linked):
    card = client_as(B).get("/app/pets").json()["pets"][0]
    assert card["is_owner"] is True
    assert card["is_group_owner"] is False


async def test_A_는_둘_다_참이다(store: Store, linked):
    card = client_as(A).get("/app/pets").json()["pets"][0]
    assert card["is_owner"] is True
    assert card["is_group_owner"] is True


async def test_연결_안_된_아이는_두_값이_언제나_같다(store: Store):
    """구 앱이 `is_group_owner` 를 몰라도 지금까지와 똑같이 도는 근거입니다."""
    store.pets.append(FakePet(app_user_id=A, name="혼자", breed="믹스"))
    card = client_as(A).get("/app/pets").json()["pets"][0]
    assert card["is_owner"] == card["is_group_owner"] is True


# ── 마릿수 상한 ────────────────────────────────────────────────────────────


async def test_연결하면_마릿수가_안_늘어난다(store: Store, linked):
    """B 는 행이 둘이지만 **논리 강아지는 하나**입니다 (MVP 결정 §4)."""
    assert await pet_repo.count_accessible(None, B) == 1
    assert await pet_repo.count_accessible(None, A) == 1


async def test_연결_없이_참여하면_하나_늘어난다(store: Store):
    a_pet = FakePet(app_user_id=A, name="맥스", breed="믹스")
    own = FakePet(app_user_id=B, name="내_아이", breed="믹스")
    store.pets += [a_pet, own]
    store.pet_members.append((a_pet.id, B))

    assert await pet_repo.count_accessible(None, B) == 2


async def test_상한은_논리_강아지로_센다(store: Store):
    """행으로 세면 연결한 사람만 자리를 두 칸 먹습니다. 5마리째 등록이 막히면 안 됩니다."""
    # B 가 논리 강아지 4마리(그중 하나는 연결이라 행은 5개)를 갖습니다.
    for index in range(3):
        store.pets.append(FakePet(app_user_id=B, name=f"내{index}", breed="믹스"))
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="믹스")
    b_pet = FakePet(app_user_id=B, name="롱롱씨", breed="믹스")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = b_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet]
    store.pet_identities.append(identity)
    store.pet_members.append((a_pet.id, B))

    assert len([p for p in store.pets if p.app_user_id == B]) == 4
    assert await pet_repo.count_accessible(None, B) == 4

    # 5마리째는 들어갑니다 — 행으로 셌으면 여기서 409 였습니다.
    pet, _primary = await pet_service.create_pet(
        FakeSession(store), B, PetUpsert(name="다섯째", breed="믹스")
    )
    assert pet.id is not None


# ── 그룹 관리 권한 가드 ────────────────────────────────────────────────────


async def test_연결된_공동_보호자는_전체_PUT_을_못_한다(store: Store, linked):
    """구 앱이 그대로 부를 수 있으므로 **서버가 막습니다** (MVP 결정 §5).

    막지 않으면 B 가 이름만 고치려 해도, 화면에 그려진 **A 의 공통 정보**가 B 의 원본
    행에 덮어써집니다.
    """
    _a, b_pet = linked
    res = client_as(B).put(
        f"/app/pets/{b_pet.id}",
        json={"name": "롱롱이", "breed": "dog_beagle"},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "not_group_owner"
    assert b_pet.name == "롱롱씨"
    assert b_pet.breed == "dog_beagle"


async def test_그룹_주보호자는_전체_PUT_을_한다(store: Store, linked):
    a_pet, _b = linked
    res = client_as(A).put(
        f"/app/pets/{a_pet.id}",
        json={"name": "롱이", "breed": "dog_pug", "health_conditions": "정상"},
    )
    assert res.status_code == 200
    assert a_pet.name == "롱이"
    assert a_pet.health_conditions == "정상"


async def test_공동_보호자는_이름만_바꿀_수_있다(store: Store, linked):
    """전체 PUT 이 409 인 것의 짝 — 자기 행의 이름은 계속 고칩니다 (MVP 결정 §6 권한표)."""
    _a, b_pet = linked
    res = client_as(B).patch(f"/app/pets/{b_pet.id}/display", json={"name": "롱롱이"})
    assert res.status_code == 200
    assert b_pet.name == "롱롱이"
    # 공통 정보는 그대로 A 의 것이 실립니다.
    assert res.json()["breed"] == "dog_pug"
    # B 의 원본 견종도 안 바뀌었습니다.
    assert b_pet.breed == "dog_beagle"


async def test_남의_강아지_이름은_못_바꾼다(store: Store, linked):
    a_pet, _b = linked
    res = client_as(B).patch(f"/app/pets/{a_pet.id}/display", json={"name": "내맘대로"})
    assert res.status_code == 404
    assert a_pet.name == "롱이씨"


async def test_연결된_공동_보호자는_강아지를_못_지운다(store: Store, linked):
    """`confirm=true` 로도 안 뚫립니다 — 지워지면 그룹이 함께 보던 기록의 한쪽이 사라집니다."""
    _a, b_pet = linked
    res = client_as(B).delete(f"/app/pets/{b_pet.id}?confirm=true")
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "not_group_owner"
    assert b_pet in store.pets


async def test_제3자는_여전히_404_다(store: Store, linked):
    """그룹 가드가 기존 정보 은닉을 약하게 만들지 않습니다."""
    _a, b_pet = linked
    assert client_as(STRANGER).delete(f"/app/pets/{b_pet.id}").status_code == 404
    assert (
        client_as(STRANGER).put(
            f"/app/pets/{b_pet.id}", json={"name": "x", "breed": "믹스"}
        ).status_code
        == 404
    )


# ── AI 건강 컨텍스트 ───────────────────────────────────────────────────────


async def test_비서는_주보호자의_건강정보를_쓴다(store: Store, linked):
    """같은 강아지를 두고 사람마다 다른 지병으로 답하면 안 됩니다 (MVP 결정 §5)."""
    _a, b_pet = linked
    resolved = await dog_context.resolve(None, B, str(b_pet.id))
    assert resolved["health_conditions"] == "슬개골 탈구"
    assert resolved["breed"] == "퍼그"
    assert resolved["on_medication"] is True


async def test_비서는_연결_안_된_아이에서는_그대로다(store: Store):
    solo = FakePet(
        app_user_id=A, name="혼자", breed="dog_beagle", health_conditions="없음"
    )
    store.pets.append(solo)
    resolved = await dog_context.resolve(None, A, str(solo.id))
    assert resolved["breed"] == "비글"
    assert resolved["health_conditions"] == "없음"


# ── 연결 수명주기 ──────────────────────────────────────────────────────────


async def test_공동_보호자가_나가면_연결도_풀린다(store: Store, linked):
    """멤버십만 지우고 연결이 남으면 **나간 사람이 공동 기록을 계속 봅니다** (MVP 결정 §6)."""
    a_pet, b_pet = linked
    await identity_service.detach_user(None, a_pet.identity_id, B)

    assert b_pet.identity_id is None
    assert a_pet.identity_id is None  # 혼자 남은 그룹은 정리됩니다
    assert store.pet_identities == []


async def test_앵커를_지우면_남은_행이_독립으로_돌아간다(store: Store, linked):
    """`pets.identity_id` 가 SET NULL 이라 **행도 기록도 안 지워집니다.**"""
    a_pet, b_pet = linked
    a_pet.photo_storage_key = None  # 사진 파기 경로는 이 테스트의 대상이 아닙니다
    res = client_as(A).delete(f"/app/pets/{a_pet.id}?confirm=true")
    assert res.status_code == 204

    assert b_pet in store.pets
    assert b_pet.identity_id is None
    assert store.pet_identities == []


async def test_공동_보호자_탈퇴가_그룹을_정리한다(store: Store, linked):
    a_pet, b_pet = linked
    b_pet.photo_storage_key = None  # 위와 같은 이유
    await pet_service.delete_all_for_owner(None, B)

    assert b_pet not in store.pets
    assert a_pet in store.pets
    assert a_pet.identity_id is None
    assert store.pet_identities == []


async def test_link_은_앵커를_초대한_쪽으로_둔다(store: Store):
    """그룹의 주보호자는 초대한 쪽이라는 제품 규칙의 회귀 (MVP 결정 §1)."""
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="믹스")
    b_pet = FakePet(app_user_id=B, name="롱롱씨", breed="믹스")
    store.pets += [a_pet, b_pet]

    identity_id = await identity_service.link(FakeSession(store), a_pet, b_pet)

    identity = next(i for i in store.pet_identities if i.id == identity_id)
    assert identity.owner_pet_id == a_pet.id
    assert a_pet.identity_id == b_pet.identity_id == identity_id
    assert await identity_service.require_group_owner(None, A, a_pet) is a_pet


async def test_group_pet_ids_는_그룹_전체를_편다(store: Store, linked):
    """케어·산책 공동 조회의 `IN` 목록입니다."""
    a_pet, b_pet = linked
    ids = await identity_service.group_pet_ids_of(None, b_pet)
    assert set(ids) == {a_pet.id, b_pet.id}


async def test_group_pet_ids_는_연결_안_된_아이에서_자기_하나다(store: Store):
    solo = FakePet(app_user_id=A, name="혼자", breed="믹스")
    store.pets.append(solo)
    assert await identity_service.group_pet_ids_of(None, solo) == [solo.id]


# ── 승계와 부분 UNIQUE ─────────────────────────────────────────────────────


async def test_연결된_보호자에게_승계하면_409_다(store: Store, linked):
    """**500 이 아니라 409 여야 합니다.**

    승계는 앵커 행의 `pets.app_user_id` 를 대상으로 옮깁니다. 그런데 B 는 이 그룹에 이미
    자기 행(202)을 갖고 있어서, 101 까지 B 것이 되면 한 사람이 한 그룹에 행 둘을 갖게 되어
    `pets_identity_one_per_user` 부분 UNIQUE 를 위반합니다 — 일회용 PostgreSQL 에서
    `duplicate key value violates unique constraint` 로 재현했습니다.
    """
    a_pet, _b = linked
    res = client_as(A).post(
        f"/app/pets/{a_pet.id}/owner", json={"app_user_id": str(B)}
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "linked_owner_transfer_unsupported"


async def test_승계가_막히면_아무것도_안_바뀐다(store: Store, linked):
    a_pet, b_pet = linked
    client_as(A).post(f"/app/pets/{a_pet.id}/owner", json={"app_user_id": str(B)})

    assert a_pet.app_user_id == A
    assert b_pet.app_user_id == B
    assert a_pet.identity_id == b_pet.identity_id
    identity = next(i for i in store.pet_identities if i.id == a_pet.identity_id)
    assert identity.owner_pet_id == a_pet.id
    assert store.pet_members == [(a_pet.id, B)]


async def test_연결_안_한_공동_보호자에게는_승계된다(store: Store):
    """기존 흐름은 그대로 돕니다 — 대상이 그룹에 자기 행이 없으면 충돌할 것이 없습니다."""
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="믹스")
    b_pet = FakePet(app_user_id=B, name="롱롱씨", breed="믹스")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = b_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet]
    store.pet_identities.append(identity)

    carer = uuid.uuid4()  # 연결 없이 참여한 사람
    store.pet_members += [(a_pet.id, B), (a_pet.id, carer)]

    res = client_as(A).post(
        f"/app/pets/{a_pet.id}/owner", json={"app_user_id": str(carer)}
    )
    assert res.status_code == 200
    assert a_pet.app_user_id == carer
    assert (a_pet.id, A) in store.pet_members
    # 앵커는 그대로라 그룹 주보호자가 새 대표로 따라갑니다.
    assert identity.owner_pet_id == a_pet.id


async def test_연결_안_된_아이의_승계는_그대로다(store: Store):
    solo = FakePet(app_user_id=A, name="혼자", breed="믹스")
    store.pets.append(solo)
    store.pet_members.append((solo.id, B))

    res = client_as(A).post(f"/app/pets/{solo.id}/owner", json={"app_user_id": str(B)})
    assert res.status_code == 200
    assert solo.app_user_id == B
    assert (solo.id, A) in store.pet_members


# ── 대표 강아지 선택 ────────────────────────────────────────────────────────
#
# **대표 강아지는 그룹 관리 권한이 아니라 내 계정의 표시 기본값입니다.**
# `app_users.primary_pet_id` 에 계정마다 한 칸이라 내가 무엇을 고르든 다른 보호자의
# 화면은 안 바뀝니다. 그래서 `set_primary` 는 소유가 아니라 구성원으로 잽니다.


def _user(store: Store, app_user_id: uuid.UUID):
    return next(u for u in store.app_users.values() if u.id == app_user_id)


def _primary_of(store: Store, app_user_id: uuid.UUID):
    return _user(store, app_user_id).primary_pet_id


async def test_연결된_공동보호자가_자기_행을_대표로_세운다(store: Store, linked):
    """B 의 목록에서 그 카드의 `id` 는 B 의 행이다 — 소유로 재도 통과하던 경우."""
    _a_pet, b_pet = linked

    res = client_as(B).put("/app/pets/primary", json={"pet_id": str(b_pet.id)})

    assert res.status_code == 204
    assert _primary_of(store, B) == b_pet.id


async def test_연결_없이_참여한_돌보미도_대표로_세운다(store: Store):
    """**이것이 소유로 재면 막히던 경우다.**

    연결 없이 참여하면 자기 행이 없어서, 목록의 그 카드는 대표의 행을 그대로 보여
    준다(`views_for`). 앱은 그 `id` 를 보내는데 `get_owned` 로 재면 늘 404 였다 —
    정작 수락 경로는 첫 참여자의 대표를 그 행으로 **이미** 세우고 있었다.
    """
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="dog_pug")
    store.pets.append(a_pet)
    store.pet_members.append((a_pet.id, B))

    res = client_as(B).put("/app/pets/primary", json={"pet_id": str(a_pet.id)})

    assert res.status_code == 204
    assert _primary_of(store, B) == a_pet.id


async def test_남의_대표는_안_바뀐다(store: Store):
    """계정마다 한 칸이라는 것을 값으로 확인한다."""
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="dog_pug")
    a_other = FakePet(app_user_id=A, name="둘째", breed="믹스")
    store.pets += [a_pet, a_other]
    store.pet_members.append((a_pet.id, B))
    _user(store, A).primary_pet_id = a_other.id

    assert client_as(B).put(
        "/app/pets/primary", json={"pet_id": str(a_pet.id)}
    ).status_code == 204

    assert _primary_of(store, B) == a_pet.id
    assert _primary_of(store, A) == a_other.id, "A 의 대표는 그대로여야 한다"


async def test_구성원이_아니면_404(store: Store):
    """**403 이 아니라 404 다** — 403 은 그 id 가 존재한다는 것을 알려 준다."""
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="dog_pug")
    store.pets.append(a_pet)  # STRANGER 는 구성원이 아니다

    res = client_as(STRANGER).put("/app/pets/primary", json={"pet_id": str(a_pet.id)})

    assert res.status_code == 404
    assert _primary_of(store, A) is None, "남의 대표도 안 세워져야 한다"


async def test_내보내진_뒤에는_대표로_못_세운다(store: Store):
    """멤버십이 사라지면 접근도 사라진다. 이미 세워 둔 값은 내보내기가 비운다."""
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="dog_pug")
    store.pets.append(a_pet)
    store.pet_members.append((a_pet.id, B))
    assert client_as(B).put(
        "/app/pets/primary", json={"pet_id": str(a_pet.id)}
    ).status_code == 204

    assert client_as(A).delete(
        f"/app/pets/{a_pet.id}/members/{B}"
    ).status_code == 204

    assert _primary_of(store, B) is None, "내보내면 접근 못 하는 아이를 가리키면 안 된다"
    assert client_as(B).put(
        "/app/pets/primary", json={"pet_id": str(a_pet.id)}
    ).status_code == 404


async def test_나간_뒤에도_대표로_못_세운다(store: Store):
    a_pet = FakePet(app_user_id=A, name="롱이씨", breed="dog_pug")
    store.pets.append(a_pet)
    store.pet_members.append((a_pet.id, B))
    assert client_as(B).put(
        "/app/pets/primary", json={"pet_id": str(a_pet.id)}
    ).status_code == 204

    assert client_as(B).delete(f"/app/pets/{a_pet.id}/members/{B}").status_code == 204

    assert _primary_of(store, B) is None
    assert client_as(B).put(
        "/app/pets/primary", json={"pet_id": str(a_pet.id)}
    ).status_code == 404
