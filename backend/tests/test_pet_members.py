"""공동 돌봄 — 구성원 판정과 초대·승계·퇴장의 규칙 (docs/co-care.md).

DB 는 쓰지 않습니다 (`test_care_events.py` 와 같은 규칙). 트리거의 증명은
`test_pet_membership_postgres.py` 에 있습니다 — 여기서 보는 것은 **규칙**입니다.
"""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAdmin, FakePet, FakeSession, Store, install

from daengs_backend.repositories import pet as pet_repo
from daengs_backend.schemas.pet import PetUpsert
from daengs_backend.schemas.walk import WalkUpload
from daengs_backend.services import care_event as care_service
from daengs_backend.services import dog_context
from daengs_backend.services import pet as pet_service
from daengs_backend.services import walk as walk_service

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
    """하루 요약은 아빠의 산책도 센다.

    **산책을 진짜 쓰기 경로로 만듭니다.** `store.walks` 에 직접 얹으면 `pet_repo.accessible_ids`
    를 되돌려도 이 테스트가 통과합니다 — 실제로는 그러면 아빠가 맥스를 태그한 산책이 **아예
    만들어지지 않아** 요약이 셀 것이 없습니다. 두 변경(`walk.count_for_pet_between` 의 소유자
    조건 삭제 + 태그 검사의 구성원 전환)이 **같이** 있어야 1 이 나옵니다.

    산책의 **소유**는 여전히 사람 것입니다 — 아래 목록이 그것을 지킵니다.
    """
    store.pet_members.append((pet.id, CARER))
    session = FakeSession(store)
    walk, created = await walk_service.upload_walk(
        session,
        CARER,
        WalkUpload(
            client_session_id=uuid.uuid4(),
            pet_ids=[pet.id],
            started_at=datetime(2026, 9, 9, 8, 0, tzinfo=SEOUL),
            ended_at=datetime(2026, 9, 9, 8, 40, tzinfo=SEOUL),
        ),
    )
    assert created and walk.pet_ids == [pet.id], "돌보미가 그 아이를 태그하지 못했다"

    summary = await care_service.day_summary(session, OWNER, pet.id, day=date(2026, 9, 9))
    assert summary.walks == 1

    # 소유는 안 옮겼다 — 그 산책은 대표의 산책 목록에 안 뜬다.
    assert await walk_service.list_walks(session, OWNER) == []
    assert [w.id for w in await walk_service.list_walks(session, CARER)] == [walk.id]


async def test_carer_sees_the_dog_in_the_pet_list(store: Store, pet: FakePet):
    """돌보미의 `GET /app/pets` 에 그 아이가 보인다.

    안 보이면 기록·조회를 열어 놔도 앱이 그 아이를 못 고릅니다 — 기능이 있는데 못 찾는
    상태가 됩니다 (docs/co-care.md §2).
    """
    store.pet_members.append((pet.id, CARER))
    pets, _primary = await pet_service.list_pets(None, CARER)
    assert [p.id for p in pets] == [pet.id]

    outsider, _ = await pet_service.list_pets(None, STRANGER)
    assert outsider == []


async def test_miniroom_cap_counts_carer_pets(store: Store):
    """상한은 소유가 아니라 **내 방에 서는 아이 수** 다 (docs/co-care.md §2 끝).

    소유로 세면 돌보미로 참여한 아이가 안 세어져, 방에 상한을 넘는 마릿수가 섭니다.
    """
    # 대표로 4마리 + 돌보미로 1마리 = 방에 5마리.
    for i in range(pet_service.MAX_PETS_PER_USER - 1):
        store.pets.append(FakePet(app_user_id=CARER, name=f"내아이{i}", breed="믹스"))
    theirs = FakePet(app_user_id=OWNER, name="맥스", breed="믹스")
    store.pets.append(theirs)
    store.pet_members.append((theirs.id, CARER))

    assert await pet_repo.count_for_owner(None, CARER) == pet_service.MAX_PETS_PER_USER - 1
    assert await pet_repo.count_accessible(None, CARER) == pet_service.MAX_PETS_PER_USER

    body = PetUpsert(name="여섯째", breed="믹스")
    with pytest.raises(pet_service.PetLimitReachedError):
        await pet_service.create_pet(None, CARER, body)
