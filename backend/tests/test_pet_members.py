"""공동 돌봄 — 구성원 판정과 초대·승계·퇴장의 규칙 (docs/co-care.md).

DB 는 쓰지 않습니다 (`test_care_events.py` 와 같은 규칙). 트리거의 증명은
`test_pet_membership_postgres.py` 에 있습니다 — 여기서 보는 것은 **규칙**입니다.
"""

import uuid

import pytest
from fakes import FakeAdmin, FakePet, Store, install

from daengs_backend.repositories import pet as pet_repo

OWNER = uuid.uuid4()
CARER = uuid.uuid4()
STRANGER = uuid.uuid4()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(FakeAdmin(login_id="admin", password_hash="x", name="관리자", role="OWNER"))
    install(s, monkeypatch)
    return s


@pytest.fixture
def pet(store: Store) -> FakePet:
    p = FakePet(app_user_id=OWNER, name="맥스", breed="믹스")
    store.pets.append(p)
    return p


async def test_owner_is_accessible(store: Store, pet: FakePet):
    assert await pet_repo.get_accessible(None, OWNER, pet.id) is pet


async def test_carer_is_accessible(store: Store, pet: FakePet):
    store.pet_members.append((pet.id, CARER))
    assert await pet_repo.get_accessible(None, CARER, pet.id) is pet


async def test_stranger_is_not_accessible(store: Store, pet: FakePet):
    assert await pet_repo.get_accessible(None, STRANGER, pet.id) is None


async def test_get_owned_still_owner_only(store: Store, pet: FakePet):
    """돌보미는 get_owned 로는 안 잡혀야 한다 — 수정·삭제가 그것을 쓴다."""
    store.pet_members.append((pet.id, CARER))
    assert await pet_repo.get_owned(None, CARER, pet.id) is None


async def test_count_accessible_includes_carer_pets(store: Store, pet: FakePet):
    """미니룸 상한은 '내 방에 서는 아이 수' 다 — 돌보미로 참여한 아이도 센다."""
    mine = FakePet(app_user_id=CARER, name="네오", breed="푸들")
    store.pets.append(mine)
    store.pet_members.append((pet.id, CARER))
    assert await pet_repo.count_accessible(None, CARER) == 2
    assert await pet_repo.count_for_owner(None, CARER) == 1
