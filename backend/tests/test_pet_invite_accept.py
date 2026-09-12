"""묶음 수락 — 원자성·멱등 영수증·IDOR·연결 후보 재검증 (MVP 결정 §2 · §8).

DB 는 쓰지 않습니다 (`test_pet_members.py` 와 같은 규칙).

이 파일이 지키는 문장 둘:

> **하나라도 실패하면 아무 변경도 남지 않는다.** 부분 수락은 없다.
>
> 연결하면 이후 요청에 쓸 id 는 **받는 사람 자신의 행**이다 (`display_pet_id`).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakeIdentity,
    FakeInvite,
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

OWNER = uuid.uuid4()  # 초대하는 쪽
GUEST = uuid.uuid4()  # 받는 쪽
STRANGER = uuid.uuid4()

OWNER_KAKAO = 4001
GUEST_KAKAO = 4002


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
def invited(store: Store) -> list[FakePet]:
    rows = [
        FakePet(app_user_id=OWNER, name="맥스", breed="dog_beagle"),
        FakePet(app_user_id=OWNER, name="코코", breed="dog_pug"),
    ]
    store.pets += rows
    return rows


def issue(pet_ids: list[uuid.UUID]) -> str:
    res = client_as(OWNER).post(
        "/app/pet-invites", json={"pet_ids": [str(i) for i in pet_ids]}
    )
    assert res.status_code == 201, res.text
    return res.json()["token"]


def accept(user: uuid.UUID, token: str, links: list[dict] | None = None):
    body: dict = {"token": token}
    if links is not None:
        body["links"] = links
    return client_as(user).post("/app/pet-invites/accept", json=body)


def _all_join(pets: list[FakePet]) -> list[dict]:
    """묶음의 모든 항목을 "연결 없이 참여" 로 고른 선택값.

    두 마리 이상 묶음은 **모든 항목의 선택값**이 와야 합니다 (MVP 결정 §2) — 토큰만 보내는
    옛 계약으로는 못 받습니다.
    """
    return [{"pet_id": str(p.id), "link_to_pet_id": None} for p in pets]


# ── 구 앱 호환 ─────────────────────────────────────────────────────────────


async def test_links_없는_요청은_전부_연결_없이_참여다(store: Store, invited):
    """구 앱의 `{"token": ...}` 요청이 지금까지와 똑같이 동작해야 합니다 (MVP 결정 §9)."""
    res = accept(GUEST, issue([invited[0].id]))
    assert res.status_code == 200
    body = res.json()
    assert body["pet_id"] == str(invited[0].id)
    assert body["name"] == "맥스"
    assert body["pets"][0]["result"] == "joined"
    assert (invited[0].id, GUEST) in store.pet_members


async def test_최상위_앵커는_앵커_항목과_같다(store: Store, invited):
    token = issue([invited[0].id, invited[1].id])
    body = accept(GUEST, token, _all_join(invited)).json()
    anchor = next(p for p in body["pets"] if p["invited_pet_id"] == str(invited[0].id))
    assert body["pet_id"] == anchor["display_pet_id"]
    assert body["name"] == anchor["name"]


# ── 구 앱이 **다중** 초대를 받았을 때 ─────────────────────────────────────
#
# 토큰만 보내는 옛 계약은 "전부 연결 없이 참여" 라는 뜻입니다. 한 마리 초대에서는 그것이
# 유일한 선택지라 맞지만, 여러 마리 묶음에서는 사용자가 고를 것이 있는데 구 앱이 그 화면을
# 못 그립니다 — 그대로 통과시키면 모르는 사이에 전부 새 강아지로 들어옵니다.


async def test_구_앱이_묶음을_토큰만으로_수락하면_409(store: Store, invited):
    res = accept(GUEST, issue([invited[0].id, invited[1].id]))
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "link_selection_required"
    assert set(detail["missing_pet_ids"]) == {str(invited[0].id), str(invited[1].id)}


async def test_묶음을_토큰만으로_수락하면_아무_변경도_없다(store: Store, invited):
    token = issue([invited[0].id, invited[1].id])
    accept(GUEST, token)
    assert store.pet_members == []
    assert store.pet_invites[0].accepted_by is None
    assert all(p.identity_id is None for p in store.pets)


async def test_선택이_일부만_와도_409(store: Store, invited):
    """빠진 항목을 알려 줍니다 — 새 앱이 무엇을 더 보내야 하는지 알 수 있게."""
    res = accept(
        GUEST,
        issue([invited[0].id, invited[1].id]),
        [{"pet_id": str(invited[0].id), "link_to_pet_id": None}],
    )
    assert res.status_code == 409
    assert res.json()["detail"]["missing_pet_ids"] == [str(invited[1].id)]


async def test_전부_연결_없이_라고_명시하면_통과한다(store: Store, invited):
    """`link_to_pet_id: null` 도 **선택**입니다 — 빠진 것과 다릅니다."""
    res = accept(
        GUEST,
        issue([invited[0].id, invited[1].id]),
        [
            {"pet_id": str(invited[0].id), "link_to_pet_id": None},
            {"pet_id": str(invited[1].id), "link_to_pet_id": None},
        ],
    )
    assert res.status_code == 200
    assert {p["result"] for p in res.json()["pets"]} == {"joined"}


async def test_한_마리_묶음은_토큰만으로도_된다(store: Store, invited):
    """구 앱 호환의 경계 — 고를 것이 하나뿐이라 옛 계약이 그대로 맞습니다 (MVP 결정 §9)."""
    assert accept(GUEST, issue([invited[0].id])).status_code == 200


async def test_자식_줄_없는_옛_초대도_토큰만으로_된다(store: Store, invited):
    """배포 창의 옛 초대는 앵커 하나짜리 묶음이라 위와 같습니다."""
    token = "옛토큰2"
    store.pet_invites.append(
        FakeInvite(
            pet_id=invited[0].id,
            invited_by=OWNER,
            token_hash=hash_refresh_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    assert accept(GUEST, token).status_code == 200


async def test_초대에_없는_아이를_보내면_422_에_code_가_실린다(store: Store, invited):
    res = accept(
        GUEST,
        issue([invited[0].id, invited[1].id]),
        [
            {"pet_id": str(invited[0].id), "link_to_pet_id": None},
            {"pet_id": str(uuid.uuid4()), "link_to_pet_id": None},
        ],
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "unknown_invited_pet"
    assert store.pet_members == []


async def test_같은_대상을_두_번_연결하면_422_에_code_가_실린다(store: Store, invited):
    mine = FakePet(app_user_id=GUEST, name="내아이", breed="믹스")
    store.pets.append(mine)
    res = accept(
        GUEST,
        issue([invited[0].id, invited[1].id]),
        [
            {"pet_id": str(invited[0].id), "link_to_pet_id": str(mine.id)},
            {"pet_id": str(invited[1].id), "link_to_pet_id": str(mine.id)},
        ],
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "duplicate_link_target"
    assert mine.identity_id is None


# ── 묶음 수락 ──────────────────────────────────────────────────────────────


async def test_연결과_참여가_섞인_일괄_수락(store: Store, invited):
    mine = FakePet(app_user_id=GUEST, name="내맥스", breed="믹스")
    store.pets.append(mine)
    token = issue([invited[0].id, invited[1].id])

    res = accept(
        GUEST,
        token,
        [
            {"pet_id": str(invited[0].id), "link_to_pet_id": str(mine.id)},
            {"pet_id": str(invited[1].id), "link_to_pet_id": None},
        ],
    )
    assert res.status_code == 200
    by_invited = {p["invited_pet_id"]: p for p in res.json()["pets"]}

    linked = by_invited[str(invited[0].id)]
    assert linked["result"] == "linked"
    # **이후 요청에 쓸 id 는 내 행**입니다 — 초대에 담겼던 아이가 아닙니다.
    assert linked["display_pet_id"] == str(mine.id)
    assert linked["name"] == "내맥스"

    joined = by_invited[str(invited[1].id)]
    assert joined["result"] == "joined"
    assert joined["display_pet_id"] == str(invited[1].id)

    assert mine.identity_id is not None
    assert invited[0].identity_id == mine.identity_id
    assert invited[1].identity_id is None


async def test_연결하면_그룹_주보호자는_초대한_쪽이다(store: Store, invited):
    mine = FakePet(app_user_id=GUEST, name="내맥스", breed="믹스")
    store.pets.append(mine)
    accept(
        GUEST,
        issue([invited[0].id]),
        [{"pet_id": str(invited[0].id), "link_to_pet_id": str(mine.id)}],
    )
    identity = next(i for i in store.pet_identities if i.id == mine.identity_id)
    assert identity.owner_pet_id == invited[0].id


async def test_이미_구성원인_항목은_충족으로_지나간다(store: Store, invited):
    store.pet_members.append((invited[0].id, GUEST))
    res = accept(GUEST, issue([invited[0].id, invited[1].id]), _all_join(invited))
    results = {p["invited_pet_id"]: p["result"] for p in res.json()["pets"]}
    assert results[str(invited[0].id)] == "already_member"
    assert results[str(invited[1].id)] == "joined"


async def test_내가_대표인_항목이_섞이면_나머지는_진행된다(store: Store, invited):
    mine = FakePet(app_user_id=GUEST, name="내아이", breed="믹스")
    store.pets.append(mine)
    # 받는 사람이 대표인 아이를 초대에 담을 수는 없지만, 승계로 그렇게 될 수 있습니다.
    token = issue([invited[0].id, invited[1].id])
    invited[1].app_user_id = GUEST
    store.pet_invites[0].invited_by = OWNER

    res = accept(GUEST, token, _all_join(invited))
    # 주보호자가 바뀌었으므로 묶음 전체가 410 입니다 — 이것이 묶음 불변성입니다.
    assert res.status_code == 410


async def test_전부_내_아이면_409_다(store: Store, invited):
    """한 마리 초대의 옛 계약("이미 이 아이의 대표입니다")을 지킵니다 (MVP 결정 §9)."""
    token = issue([invited[0].id])
    invited[0].app_user_id = GUEST
    store.pet_invites[0].invited_by = GUEST

    res = accept(GUEST, token)
    assert res.status_code == 409
    assert "대표" in res.json()["detail"]


# ── 원자성 ─────────────────────────────────────────────────────────────────


async def test_하나라도_실패하면_커밋하지_않는다(store: Store, invited):
    """두 번째 항목의 연결이 부적격이면 **트랜잭션을 커밋하지 않습니다.**

    원자성의 실제 보장은 `session.commit()` 이 함수당 한 번이라는 구조입니다 —
    커밋 전에 예외가 나면 요청 세션이 닫히면서 그때까지의 쓰기가 전부 버려집니다
    (`core/database.py::get_session`: "커밋하지 않은 변경은 롤백됩니다").

    ⚠️ **가짜 저장소에는 트랜잭션이 없어서** 이미 append 된 `pet_members` 가 되돌아가는
    것을 여기서 볼 수는 없습니다 — 그래서 그 대신 **커밋이 안 일어났다**는 것과, 커밋해야
    만 의미가 생기는 값(영수증)이 비어 있다는 것을 봅니다. 진짜 DB 에서의 되돌림은
    `test_pet_membership_postgres.py` 층의 몫입니다.
    """
    ok = FakePet(app_user_id=GUEST, name="가능", breed="믹스")
    shared = FakePet(app_user_id=GUEST, name="공동보호자있음", breed="믹스")
    store.pets += [ok, shared]
    store.pet_members.append((shared.id, STRANGER))
    token = issue([invited[0].id, invited[1].id])

    session = FakeSession(store)
    with pytest.raises(member_service.LinkNotAllowedError) as caught:
        await member_service.accept_invite(
            session,
            GUEST,
            token,
            {invited[0].id: ok.id, invited[1].id: shared.id},
        )
    assert caught.value.reason == "has_other_carers"

    assert session.commits == 0
    assert store.pet_invites[0].accepted_by is None


async def test_부적격_연결은_409_로_나간다(store: Store, invited):
    """위 테스트의 HTTP 짝 — 라우터가 `reason` 을 그대로 실어 냅니다."""
    shared = FakePet(app_user_id=GUEST, name="공동보호자있음", breed="믹스")
    store.pets.append(shared)
    store.pet_members.append((shared.id, STRANGER))

    res = accept(
        GUEST,
        issue([invited[0].id]),
        [{"pet_id": str(invited[0].id), "link_to_pet_id": str(shared.id)}],
    )
    assert res.status_code == 409
    assert res.json()["detail"]["reason"] == "has_other_carers"
    assert shared.identity_id is None


# ── 멱등 영수증 ────────────────────────────────────────────────────────────


async def test_같은_사람의_재시도는_연결_매핑까지_복원한다(store: Store, invited):
    mine = FakePet(app_user_id=GUEST, name="내맥스", breed="믹스")
    store.pets.append(mine)
    token = issue([invited[0].id, invited[1].id])
    links = [
        {"pet_id": str(invited[0].id), "link_to_pet_id": str(mine.id)},
        {"pet_id": str(invited[1].id), "link_to_pet_id": None},
    ]
    first = accept(GUEST, token, links).json()

    # 응답을 못 받았다고 치고 **같은 요청을 그대로** 다시 보냅니다.
    again = accept(GUEST, token, links)
    assert again.status_code == 200
    assert again.json()["pets"] == first["pets"]
    assert again.json()["pet_id"] == first["pet_id"]


async def test_재시도는_links_없이_보내도_같은_결과다(store: Store, invited):
    """영수증은 **그때의 매핑**을 돌려줍니다 — 이번 요청 내용을 다시 보지 않습니다."""
    mine = FakePet(app_user_id=GUEST, name="내맥스", breed="믹스")
    store.pets.append(mine)
    token = issue([invited[0].id])
    first = accept(
        GUEST, token, [{"pet_id": str(invited[0].id), "link_to_pet_id": str(mine.id)}]
    ).json()

    again = accept(GUEST, token).json()
    assert again["pets"] == first["pets"]
    assert again["pets"][0]["result"] == "linked"


async def test_재시도가_멤버십을_두_번_안_만든다(store: Store, invited):
    token = issue([invited[0].id])
    accept(GUEST, token)
    accept(GUEST, token)
    assert store.pet_members.count((invited[0].id, GUEST)) == 1


async def test_다른_사람의_재시도는_404(store: Store, invited):
    token = issue([invited[0].id])
    accept(GUEST, token)
    assert accept(STRANGER, token).status_code == 404


# ── IDOR · 연결 후보 재검증 ────────────────────────────────────────────────


async def test_남의_강아지를_연결_대상으로_넣으면_404(store: Store, invited):
    """**409 가 아니라 404 입니다** — 409 면 그 id 가 존재한다는 사실이 샙니다."""
    theirs = FakePet(app_user_id=STRANGER, name="남의것", breed="믹스")
    store.pets.append(theirs)

    res = accept(
        GUEST,
        issue([invited[0].id]),
        [{"pet_id": str(invited[0].id), "link_to_pet_id": str(theirs.id)}],
    )
    assert res.status_code == 404
    assert theirs.identity_id is None
    assert store.pet_members == []


async def test_없는_강아지를_연결_대상으로_넣어도_404(store: Store, invited):
    res = accept(
        GUEST,
        issue([invited[0].id]),
        [{"pet_id": str(invited[0].id), "link_to_pet_id": str(uuid.uuid4())}],
    )
    assert res.status_code == 404


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("linked", "already_linked"),
        ("farewelled", "farewelled"),
        ("carers", "has_other_carers"),
    ],
)
async def test_연결_후보_조건을_서버가_다시_본다(store: Store, invited, setup, reason):
    """미리보기가 후보를 내려 줬다고 믿으면, 그 사이 바뀐 상태를 못 잡습니다."""
    target = FakePet(app_user_id=GUEST, name="대상", breed="믹스")
    store.pets.append(target)
    if setup == "linked":
        other = FakePet(app_user_id=GUEST, name="다른그룹", breed="믹스")
        identity = FakeIdentity(owner_pet_id=other.id)
        other.identity_id = target.identity_id = identity.id
        store.pets.append(other)
        store.pet_identities.append(identity)
    elif setup == "farewelled":
        target.farewell_on = date(2026, 1, 1)
    else:
        store.pet_members.append((target.id, STRANGER))

    res = accept(
        GUEST,
        issue([invited[0].id]),
        [{"pet_id": str(invited[0].id), "link_to_pet_id": str(target.id)}],
    )
    assert res.status_code == 409
    assert res.json()["detail"]["reason"] == reason


async def test_초대에_없는_강아지를_보내면_422(store: Store, invited):
    res = accept(
        GUEST,
        issue([invited[0].id]),
        [{"pet_id": str(uuid.uuid4()), "link_to_pet_id": None}],
    )
    assert res.status_code == 422


async def test_같은_대상을_두_번_연결하면_422(store: Store, invited):
    mine = FakePet(app_user_id=GUEST, name="내아이", breed="믹스")
    store.pets.append(mine)
    res = accept(
        GUEST,
        issue([invited[0].id, invited[1].id]),
        [
            {"pet_id": str(invited[0].id), "link_to_pet_id": str(mine.id)},
            {"pet_id": str(invited[1].id), "link_to_pet_id": str(mine.id)},
        ],
    )
    assert res.status_code == 422
    assert mine.identity_id is None


# ── 묶음 불변성 ────────────────────────────────────────────────────────────


async def test_강아지가_지워지면_수락도_410(store: Store, invited):
    token = issue([invited[0].id, invited[1].id])
    invite_id = store.pet_invites[0].id
    store.pet_invite_pets = [
        r
        for r in store.pet_invite_pets
        if not (r.invite_id == invite_id and r.pet_id == invited[1].id)
    ]

    # 구성 변경이 **선택 검사보다 먼저** 걸려야 합니다 — 구성이 깨진 묶음에 대고
    # "선택을 더 보내라" 고 하면 앱이 영영 못 고칩니다.
    assert accept(GUEST, token, _all_join(invited)).status_code == 410
    assert store.pet_members == []


async def test_배웅하면_수락도_410(store: Store, invited):
    token = issue([invited[0].id])
    invited[0].farewell_on = date(2026, 9, 1)
    assert accept(GUEST, token).status_code == 410


async def test_만료된_토큰은_410_이고_행이_지워진다(store: Store, invited):
    token = issue([invited[0].id])
    store.pet_invites[0].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert accept(GUEST, token).status_code == 410
    assert store.pet_invites == []


async def test_자식_줄_없는_옛_초대도_수락된다(store: Store, invited):
    """배포 창에서 옛 코드가 만든 초대입니다 (MVP 결정 §9)."""
    token = "옛토큰"
    store.pet_invites.append(
        FakeInvite(
            pet_id=invited[0].id,
            invited_by=OWNER,
            token_hash=hash_refresh_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    res = accept(GUEST, token)
    assert res.status_code == 200
    assert res.json()["pets"][0]["display_pet_id"] == str(invited[0].id)


# ── 상한 ───────────────────────────────────────────────────────────────────


async def test_연결하면_마릿수가_안_늘어_상한에_안_걸린다(store: Store, invited):
    """5마리를 가진 사람도 **연결이면** 받을 수 있습니다 (MVP 결정 §4)."""
    mine = [FakePet(app_user_id=GUEST, name=f"내{i}", breed="믹스") for i in range(5)]
    store.pets += mine

    res = accept(
        GUEST,
        issue([invited[0].id]),
        [{"pet_id": str(invited[0].id), "link_to_pet_id": str(mine[0].id)}],
    )
    assert res.status_code == 200
    assert mine[0].identity_id is not None


async def test_연결_없이_참여하면_상한에_걸린다(store: Store, invited):
    store.pets += [
        FakePet(app_user_id=GUEST, name=f"내{i}", breed="믹스") for i in range(5)
    ]
    res = accept(GUEST, issue([invited[0].id]))
    assert res.status_code == 409
    assert store.pet_invites[0].accepted_by is None


async def test_묶음_전체를_반영한_뒤_상한을_본다(store: Store, invited):
    """항목마다 세면 두 마리째에서야 걸려, **첫 마리는 이미 들어간 채**가 됩니다.

    커밋 전에 걸리는지를 봅니다 — 가짜 저장소의 롤백 한계는 위
    `test_하나라도_실패하면_커밋하지_않는다` 의 설명과 같습니다.
    """
    store.pets += [
        FakePet(app_user_id=GUEST, name=f"내{i}", breed="믹스") for i in range(4)
    ]
    token = issue([invited[0].id, invited[1].id])

    session = FakeSession(store)
    with pytest.raises(member_service.PetLimitError):
        await member_service.accept_invite(
            session, GUEST, token, {invited[0].id: None, invited[1].id: None}
        )
    assert session.commits == 0
    assert store.pet_invites[0].accepted_by is None


async def test_그룹_보호자는_중복_제거해_다섯_명이다(store: Store, invited):
    """pet 행별로 세면 연결할 때마다 그룹 인원이 상한을 넘어 늘어납니다 (MVP 결정 §4)."""
    for _ in range(4):
        store.pet_members.append((invited[0].id, uuid.uuid4()))

    res = accept(GUEST, issue([invited[0].id]))
    assert res.status_code == 409
    assert "보호자" in res.json()["detail"]


async def test_그룹_전체에서_센다(store: Store, invited):
    """연결된 두 행에 각각 보호자가 있으면 합집합이 상한입니다."""
    a_pet = invited[0]
    b_pet = FakePet(app_user_id=STRANGER, name="연결된행", breed="믹스")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = b_pet.identity_id = identity.id
    store.pets.append(b_pet)
    store.pet_identities.append(identity)
    store.pet_members.append((a_pet.id, STRANGER))
    for _ in range(2):
        store.pet_members.append((b_pet.id, uuid.uuid4()))

    # 그룹 보호자: OWNER · STRANGER · 돌보미 둘 = 4명. GUEST 가 들어오면 5명이라 통과.
    assert accept(GUEST, issue([a_pet.id])).status_code == 200
