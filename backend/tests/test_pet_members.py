"""공동 돌봄 — 구성원 판정과 초대·승계·퇴장의 규칙 (docs/co-care.md).

DB 는 쓰지 않습니다 (`test_care_events.py` 와 같은 규칙). 트리거의 증명은
`test_pet_membership_postgres.py` 에 있습니다 — 여기서 보는 것은 **규칙**입니다.
"""

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.routers import pet_member as pet_member_router
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

#: `store.app_users` 는 kakao_id 로 키가 걸린 dict 입니다 (fakes.py). 초대 수락의
#: 부수효과(`primary_pet_id` 채우기)를 보려면 OWNER·CARER 가 그 dict 에도 있어야 합니다.
OWNER_KAKAO = 1001
CARER_KAKAO = 1002


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(FakeAdmin(login_id="admin", password_hash="x", name="관리자", role="OWNER"))
    install(s, monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=OWNER_KAKAO, id=OWNER))
    s.add_app_user(FakeAppUser(kakao_id=CARER_KAKAO, id=CARER))
    return s


def client_as(app_user_id: uuid.UUID) -> TestClient:
    """그 사람으로 인증을 통과한 클라이언트. `test_care_events.py` 의 `_client_for` 와 같은 요령 —
    라우터가 `CurrentAppUser` 로 잠겨 있으므로 그 의존성만 갈아 끼웁니다.
    """
    app = FastAPI()
    app.include_router(pet_member_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=app_user_id)
    return TestClient(app, raise_server_exceptions=False)


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


# ── 초대 생성 · 수락 (docs/co-care.md §3) ────────────────────────────


def _invite(store: Store, pet: FakePet, owner: uuid.UUID = OWNER) -> str:
    """대표가 초대를 만들고 평문 토큰을 얻는다."""
    r = client_as(owner).post(f"/app/pets/{pet.id}/invites")
    assert r.status_code == 201, r.text
    return r.json()["token"]


async def test_carer_cannot_invite(store: Store, pet: FakePet):
    store.pet_members.append((pet.id, CARER))
    assert client_as(CARER).post(f"/app/pets/{pet.id}/invites").status_code == 404


async def test_accept_makes_member(store: Store, pet: FakePet):
    token = _invite(store, pet)
    r = client_as(CARER).post("/app/pet-invites/accept", json={"token": token})
    assert r.status_code == 200
    assert (pet.id, CARER) in store.pet_members


async def test_accept_is_idempotent(store: Store, pet: FakePet):
    """카톡 링크는 두 번 거의 동시에 눌린다.

    ⚠️ 완전히 순차적인(첫 요청이 끝난 뒤 둘째가 시작하는) 재전송은 이걸로 못 봅니다 —
    첫 수락이 커밋되며 초대 행 자체를 지우므로(`services/pet_member.py` 의
    `InviteNotFoundError` 설명 — "이미 쓴 초대도 이것입니다"), 완전히 끝난 뒤의 재전송은
    토큰을 못 찾아 404 입니다. 그것과 다른 상황이 진짜 "두 번 눌림" 입니다: 두 요청이
    **동시에** 같은(아직 안 지워진) 초대 행을 읽어서, 하나가 먼저 구성원으로 넣고
    커밋하는 사이에 다른 하나도 그 초대를 들고 있는 경우입니다. 그때 나중 요청이
    보는 것이 `is_member() == True` 이고, 그 분기가 초대를 다시 지우지 않고 200 을
    돌려줍니다(`accept_invite` 의 `# 멱등입니다` 분기) — 그 경합을 여기서 흉내 냅니다.

    **상태코드만으로는 이 분기를 못 지킵니다.** `is_member()` 조기 반환이 지워지면
    흐름이 `member_repo.add` 로 떨어지는데, 가짜 `member_add` 는 이제 `(pet_id,
    app_user_id)` 중복을 진짜 DB 의 PK 처럼 `IntegrityError` 로 거절합니다
    (`fakes.py` — `admin_create` 의 `admin_users_login_id_key` 대역과 같은 요령).
    그 예외를 서비스가 잡지 않으므로 그 회귀는 200 이 아니라 500 으로 드러나
    상태코드 단언만으로는 안 잡히던 것을 잡습니다. 여기서는 그와 별개로
    구성원 행이 **정확히 하나**인지까지 봅니다 — 조기 반환이 살아 있으면 두 번째
    `add` 호출 자체가 없어 애초에 중복이 생기지 않는다는 것의 직접 증거입니다.
    """
    token = _invite(store, pet)
    store.pet_members.append((pet.id, CARER))  # 먼저 커밋된 동시 요청을 흉내 낸다
    r = client_as(CARER).post("/app/pet-invites/accept", json={"token": token})
    assert r.status_code == 200
    assert store.pet_members.count((pet.id, CARER)) == 1


async def test_unknown_token_is_404(store: Store, pet: FakePet):
    r = client_as(CARER).post("/app/pet-invites/accept", json={"token": "nope"})
    assert r.status_code == 404


async def test_expired_token_is_410(store: Store, pet: FakePet):
    token = _invite(store, pet)
    store.pet_invites[0].expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    r = client_as(CARER).post("/app/pet-invites/accept", json={"token": token})
    assert r.status_code == 410


async def test_invite_stale_after_owner_changes_is_410(store: Store, pet: FakePet):
    """그새 대표가 바뀌면(승계 등) 옛 대표가 뿌린 링크는 죽는다 (docs/co-care.md §3 표 3번).

    `invite.invited_by` 는 발급 당시의 대표를 담습니다. 승계로 `pets.app_user_id` 가
    바뀌면 그 값과 어긋나므로, 이 검사가 없으면 옛 대표의 초대로 새 구성원이 계속
    들어옵니다.
    """
    token = _invite(store, pet)
    pet.app_user_id = STRANGER  # 그 사이 대표가 바뀐 상태를 흉내 낸다
    r = client_as(CARER).post("/app/pet-invites/accept", json={"token": token})
    assert r.status_code == 410


async def test_owner_accepting_own_invite_is_409(store: Store, pet: FakePet):
    """트리거 ② 가 DB 에서도 막지만, 서비스가 먼저 거절해야 500 이 안 난다."""
    token = _invite(store, pet)
    r = client_as(OWNER).post("/app/pet-invites/accept", json={"token": token})
    assert r.status_code == 409


async def test_invite_limit_is_three(store: Store, pet: FakePet):
    for _ in range(3):
        _invite(store, pet)
    assert client_as(OWNER).post(f"/app/pets/{pet.id}/invites").status_code == 409


async def test_creating_invite_clears_expired(store: Store, pet: FakePet):
    _invite(store, pet)
    store.pet_invites[0].expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    _invite(store, pet)
    assert len(store.pet_invites) == 1


async def test_accept_fills_empty_primary_pet(store: Store, pet: FakePet):
    """등록한 강아지가 없는 신규 돌보미는 첫 수락에서 대표 강아지를 얻는다."""
    user = store.app_users[CARER_KAKAO]
    user.primary_pet_id = None
    token = _invite(store, pet)
    client_as(CARER).post("/app/pet-invites/accept", json={"token": token})
    assert user.primary_pet_id == pet.id


async def test_accept_respects_miniroom_limit(store: Store, pet: FakePet):
    """수락자가 이미 5마리면 6번째가 방에 못 선다."""
    for i in range(5):
        store.pets.append(FakePet(app_user_id=CARER, name=f"강아지{i}", breed="믹스"))
    token = _invite(store, pet)
    r = client_as(CARER).post("/app/pet-invites/accept", json={"token": token})
    assert r.status_code == 409


# ── 구성원 목록 · 퇴장 · 내보내기 (docs/co-care.md §3) ────────────────


async def test_owner_can_remove_carer(store: Store, pet: FakePet):
    store.pet_members.append((pet.id, CARER))
    r = client_as(OWNER).delete(f"/app/pets/{pet.id}/members/{CARER}")
    assert r.status_code == 204
    assert (pet.id, CARER) not in store.pet_members


async def test_carer_can_leave(store: Store, pet: FakePet):
    store.pet_members.append((pet.id, CARER))
    assert client_as(CARER).delete(f"/app/pets/{pet.id}/members/{CARER}").status_code == 204


async def test_carer_cannot_remove_another_carer(store: Store, pet: FakePet):
    other = uuid.uuid4()
    store.pet_members.extend([(pet.id, CARER), (pet.id, other)])
    assert client_as(CARER).delete(f"/app/pets/{pet.id}/members/{other}").status_code == 403


async def test_owner_cannot_remove_self(store: Store, pet: FakePet):
    """대표는 승계 엔드포인트로 가야 한다."""
    assert client_as(OWNER).delete(f"/app/pets/{pet.id}/members/{OWNER}").status_code == 409


async def test_leaving_clears_primary_pet(store: Store, pet: FakePet):
    """접근 못 하는 강아지를 primary 로 가리키면 앱 첫 화면이 깨진다."""
    user = store.app_users[CARER_KAKAO]
    store.pet_members.append((pet.id, CARER))
    user.primary_pet_id = pet.id
    mine = FakePet(app_user_id=CARER, name="네오", breed="푸들")
    store.pets.append(mine)

    client_as(CARER).delete(f"/app/pets/{pet.id}/members/{CARER}")
    assert user.primary_pet_id == mine.id


async def test_leaving_last_pet_nulls_primary(store: Store, pet: FakePet):
    user = store.app_users[CARER_KAKAO]
    store.pet_members.append((pet.id, CARER))
    user.primary_pet_id = pet.id
    client_as(CARER).delete(f"/app/pets/{pet.id}/members/{CARER}")
    assert user.primary_pet_id is None


async def test_owner_removing_carer_clears_carers_primary_pet(store: Store, pet: FakePet):
    """대표가 돌보미를 내보낼 때도 **비워지는 것은 나간 사람의** `primary_pet_id` 다.

    `remove_member` 가 `target_id` 대신 `app_user_id`(호출자)로 회귀하면, 두 자기-탈퇴
    테스트(`test_leaving_clears_primary_pet` · `test_leaving_last_pet_nulls_primary`)는
    `app_user_id == target_id` 라 회귀를 못 잡는다. 여기서는 호출자(대표)와 대상(돌보미)이
    달라야 그 구분이 선다 — 대표의 `primary_pet_id` 는 손대지 않았다는 것까지 본다.
    """
    carer = store.app_users[CARER_KAKAO]
    owner = store.app_users[OWNER_KAKAO]
    store.pet_members.append((pet.id, CARER))
    carer.primary_pet_id = pet.id
    owner_untouched = uuid.uuid4()
    owner.primary_pet_id = owner_untouched
    mine = FakePet(app_user_id=CARER, name="네오", breed="푸들")
    store.pets.append(mine)

    r = client_as(OWNER).delete(f"/app/pets/{pet.id}/members/{CARER}")
    assert r.status_code == 204
    assert carer.primary_pet_id == mine.id
    assert owner.primary_pet_id == owner_untouched


async def test_member_list_shows_owner_and_carer(store: Store, pet: FakePet):
    store.pet_members.append((pet.id, CARER))
    got = client_as(CARER).get(f"/app/pets/{pet.id}/members").json()
    assert [m["is_owner"] for m in got["members"]] == [True, False]


async def test_stranger_cannot_list_members(store: Store, pet: FakePet):
    """구성원만 볼 수 있다 — 접근 못 하는 사람에게는 404."""
    assert client_as(STRANGER).get(f"/app/pets/{pet.id}/members").status_code == 404


async def test_actor_label_for_member(store: Store, pet: FakePet):
    from daengs_backend.services import pet_member as member_service

    store.app_users[OWNER_KAKAO].nickname = "아빠"
    assert await member_service.actor_label(None, pet.id, OWNER) == "아빠"


async def test_actor_label_for_non_member_is_none(store: Store, pet: FakePet):
    """지금 구성원이 아니면 이름을 내지 않는다 — 탈퇴자의 새 닉네임이 옛 기록에 새는 것을 막는다."""
    from daengs_backend.services import pet_member as member_service

    assert await member_service.actor_label(None, pet.id, STRANGER) is None
