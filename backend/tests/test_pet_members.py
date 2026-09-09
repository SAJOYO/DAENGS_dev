"""공동 돌봄 — 구성원 판정과 초대·승계·퇴장의 규칙 (docs/co-care.md).

DB 는 쓰지 않습니다 (`test_care_events.py` 와 같은 규칙). 트리거의 증명은
`test_pet_membership_postgres.py` 에 있습니다 — 여기서 보는 것은 **규칙**입니다.
"""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAdmin, FakePet, FakeWalk, FakeWalkPet, Store, install

from daengs_backend.repositories import pet as pet_repo
from daengs_backend.schemas.pet import PetUpsert
from daengs_backend.services import care_event as care_service
from daengs_backend.services import dog_context
from daengs_backend.services import pet as pet_service

SEOUL = ZoneInfo("Asia/Seoul")

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


# ── 호출부 판정 (docs/co-care.md §2) ──────────────────────────────────


async def test_carer_can_read_profile_but_not_edit(store: Store, pet: FakePet):
    """돌보미는 보고 기록한다. 수정·배웅·삭제·사진 교체는 대표만이다.

    사진 **조회**가 구성원 기준으로 옮겨 갔는지는 예외 종류로 봅니다 — 접근이 막히면
    `PetNotFoundError` 고, 통과한 뒤 사진이 없어서 막히면 `PetPhotoConflictError` 입니다.
    """
    store.pet_members.append((pet.id, CARER))

    with pytest.raises(pet_service.PetPhotoConflictError):
        await pet_service.photo_download_url(None, CARER, pet.id)

    body = PetUpsert(name="바뀐이름", breed="믹스")
    with pytest.raises(pet_service.PetNotFoundError):
        await pet_service.update_pet(None, CARER, pet.id, body)
    with pytest.raises(pet_service.PetNotFoundError):
        await pet_service.set_primary(None, CARER, pet.id)


async def test_stranger_cannot_read_photo(store: Store, pet: FakePet):
    """구성원이 아니면 사진 조회도 404 다 — 접근이 열린 것은 구성원까지다."""
    with pytest.raises(pet_service.PetNotFoundError):
        await pet_service.photo_download_url(None, STRANGER, pet.id)


async def test_carer_sees_dog_context(store: Store, pet: FakePet):
    """비서가 돌보미에게도 그 아이의 프로필로 답한다.

    채팅 접근(`repositories/chat.py`)이 구성원 기준으로 열렸으므로, 같은 요청의 프로필
    절반만 대표 기준으로 남으면 돌보미의 답변에서 지병·복약이 조용히 빠집니다.
    """
    pet.health_conditions = "슬개골 탈구"
    store.pet_members.append((pet.id, CARER))

    resolved = await dog_context.resolve(None, CARER, str(pet.id))
    assert resolved is not None and resolved["health_conditions"] == "슬개골 탈구"
    assert await dog_context.resolve(None, STRANGER, str(pet.id)) is None


async def test_day_summary_counts_other_members_walks(store: Store, pet: FakePet):
    """하루 요약은 아빠의 산책도 센다 — `repositories/walk.py` 의 소유자 조건을 뺀 결과다.

    산책의 소유는 여전히 사람 것이고(§"산책 쓰기는 안 건드린다"), 요약에서만 합쳐 보입니다.
    """
    store.pet_members.append((pet.id, CARER))
    store.walks.append(
        FakeWalk(
            app_user_id=CARER,
            client_session_id=uuid.uuid4(),
            started_at=datetime(2026, 9, 9, 8, 0, tzinfo=SEOUL),
            ended_at=datetime(2026, 9, 9, 8, 40, tzinfo=SEOUL),
            pets=[FakeWalkPet(pet_id=pet.id)],
        )
    )
    summary = await care_service.day_summary(None, OWNER, pet.id, day=date(2026, 9, 9))
    assert summary.walks == 1
