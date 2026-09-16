"""나가기를 **한 흐름으로** 끝까지 본다 (docs/co-care.md §3 「퇴장 · 내보내기」).

다른 파일들은 나가기의 조각을 하나씩 본다 — `test_pet_identity.py` 는 권한과 행 정리,
`test_co_care_group_reads.py` 는 케어, `test_walk_group_reads.py` 는 산책. 조각마다 초록인데
**사람이 실제로 겪는 순서로 이으면 구멍이 보이는** 자리가 있어서, 여기서는 나가는 사람의
화면을 처음부터 끝까지 한 테스트 안에서 따라간다:

    나간다 → 내 목록에 아이가 그대로 있나 → 열리나 → 내 이름·사진이 그대로인가
          → 내가 적은 기록이 남아 읽히나 → 그룹 기록은 끊겼나
          → **나간 뒤에 쌓인 것까지** 끊겼나 → 남은 사람들은 멀쩡한가

이 파일이 지키는 문장 하나:

> 나가기는 **관계만 끊는다.** 행도 이름도 사진도 기록도 옮기거나 지우지 않는다.

DB 는 쓰지 않는다. 트랜잭션 경계(중간에 터지면 셋 다 안 일어난 것이 되는 것)는 가짜
저장소로 증명할 수 없어 `test_pet_membership_postgres.py` 가 일회용 PostgreSQL 로 본다.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakeIdentity,
    FakePet,
    FakeWalk,
    FakeWalkPet,
    FakeWalkPointChunk,
    Store,
    install,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import walk_group as walk_group_repo
from daengs_backend.repositories.walk_group import WalkSummaryNumbers
from daengs_backend.routers import care_event as care_router
from daengs_backend.routers import pet as pet_router
from daengs_backend.routers import pet_member as pet_member_router
from daengs_backend.routers import pet_walks as pet_walks_router
from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.services.walk_session.chunk import encode_chunk

A = uuid.uuid4()  # 그룹 주보호자 (앵커 행의 대표)
B = uuid.uuid4()  # 자기 강아지를 **연결해** 참여한 공동 보호자 — 나가는 사람
C = uuid.uuid4()  # 같은 그룹의 또 다른 연결 참여자 — 남는 사람
J = uuid.uuid4()  # 연결 **없이** 참여한 돌보미 (아래 두 번째 흐름)
O = uuid.uuid4()  # J 가 돌보는 아이의 대표

# ⚠️ **오늘 날짜를 박으면 안 된다.** `GET /app/care-events/today` 는 서울 자정 경계로
#    「오늘」을 정하고(`care_event.DAY_TIMEZONE`), 미래 시각은 `CareEventCreate` 가 422 로
#    막는다. 그래서 **오늘 서울 안이면서 지금보다 과거**로 둔다
#    (`test_co_care_group_reads.py` 의 같은 주석과 같은 이유 — 고정 날짜를 박았다가 그날이
#    지나며 아홉 건이 한꺼번에 깨진 적이 있다).
_SEOUL = ZoneInfo("Asia/Seoul")
_NOW = datetime.now(UTC)
_SEOUL_MIDNIGHT = datetime.combine(
    _NOW.astimezone(_SEOUL).date(), time(0, 1), tzinfo=_SEOUL
).astimezone(UTC)

MORNING = max(_SEOUL_MIDNIGHT, _NOW - timedelta(hours=2))
EVENING = _NOW - timedelta(minutes=1)


@dataclass
class FakeCareEvent:
    pet_id: uuid.UUID
    kind: str
    occurred_at: datetime
    actor_app_user_id: uuid.UUID | None = None
    note: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    client_event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = Store(FakeAdmin(login_id="admin", password_hash="x", name="관리자", role="OWNER"))
    install(s, monkeypatch)
    for kakao, uid, nick in [
        (7001, A, "아빠"), (7002, B, "엄마"), (7003, C, "이모"),
        (7004, J, "제이"), (7005, O, "오"),
    ]:
        s.add_app_user(FakeAppUser(kakao_id=kakao, id=uid, nickname=nick))

    # 산책 공동 조회 대역 (`test_walk_group_reads.py` 와 같은 모양). SQL 자체는 실DB 몫이다.
    async def list_group_page(session, pet_ids, *, limit, before=None):
        wanted = set(pet_ids)
        rows = sorted(
            (w for w in s.walks if wanted & set(w.pet_ids)),
            key=lambda w: (w.started_at, w.id),
            reverse=True,
        )
        if before is not None:
            rows = [w for w in rows if (w.started_at, w.id) < before]
        return rows[:limit]

    async def get_in_group(session, pet_ids, walk_id):
        wanted = set(pet_ids)
        return next(
            (w for w in s.walks if w.id == walk_id and wanted & set(w.pet_ids)), None
        )

    async def latest_numbers(session, walk_ids):
        return {wid: WalkSummaryNumbers(0, 0) for wid in set(walk_ids)}

    monkeypatch.setattr(walk_group_repo, "list_group_page", list_group_page)
    monkeypatch.setattr(walk_group_repo, "get_in_group", get_in_group)
    monkeypatch.setattr(walk_group_repo, "latest_numbers", latest_numbers)
    return s


def _user(store: Store, app_user_id: uuid.UUID):
    return next(u for u in store.app_users.values() if u.id == app_user_id)


def client_as(uid: uuid.UUID) -> TestClient:
    """나가는 사람이 실제로 여는 화면 전부를 한 앱에 올린다 — 목록·보호자·케어·산책."""
    app = FastAPI()
    app.include_router(pet_router.router)
    app.include_router(pet_member_router.router)
    app.include_router(care_router.router)
    app.include_router(pet_walks_router.router)
    app.dependency_overrides[
        next(iter(CurrentAppUser.__metadata__)).dependency
    ] = lambda: AppPrincipal(app_user_id=uid)
    return TestClient(app, raise_server_exceptions=False)


def add_walk(store: Store, owner: uuid.UUID, pets: list[FakePet], started: datetime) -> FakeWalk:
    uploads = [
        WalkPointUpload(
            client_seq=i,
            chain_index=0,
            at=started + timedelta(seconds=i),
            lat=Decimal("37.497800") + Decimal(i) / Decimal(10000),
            lng=Decimal("127.027500"),
            accuracy_m=5.0,
        )
        for i in range(2)
    ]
    walk = FakeWalk(
        app_user_id=owner,
        client_session_id=uuid.uuid4(),
        started_at=started,
        ended_at=started + timedelta(minutes=30),
        pets=[FakeWalkPet(pet_id=p.id) for p in pets],
        points=[
            FakeWalkPointChunk(
                seq_from=0, seq_to=1, point_count=2, payload=encode_chunk(uploads)
            )
        ],
    )
    store.walks.append(walk)
    return walk


def cards(uid: uuid.UUID) -> dict[str, dict]:
    res = client_as(uid).get("/app/pets")
    assert res.status_code == 200, res.text
    return {p["id"]: p for p in res.json()["pets"]}


def today(uid: uuid.UUID, pet_id: uuid.UUID):
    return client_as(uid).get(f"/app/care-events/today?pet_id={pet_id}")


def walk_ids(uid: uuid.UUID, pet_id: uuid.UUID) -> list[str]:
    res = client_as(uid).get(f"/app/pets/{pet_id}/walks")
    assert res.status_code == 200, res.text
    return [w["id"] for w in res.json()["walks"]]


@pytest.fixture
def group(store: Store) -> tuple[FakePet, FakePet, FakePet]:
    """세 사람이 같은 실제 강아지를 나눠 보는 모양. 수락이 멤버십과 연결을 같이 만든다.

        A 의 `롱이씨`(앵커)  ── B 의 `롱롱씨` ── C 의 `롱냥이`

    **B 는 앵커 행과 C 의 행 둘 다의 돌보미다.** 한 사람이 그룹 안 여러 행의 돌보미인 것은
    연결의 정상 모양이고, 나가기가 요청받은 행 하나만 지우면 남은 행의 멤버십으로 그룹을
    계속 읽는다 — 그래서 이 흐름은 행이 셋이어야 의미가 있다.
    """
    a_pet = FakePet(
        app_user_id=A, name="롱이씨", breed="dog_pug",
        health_conditions="슬개골 탈구", photo_storage_key="pets/a.jpg",
    )
    b_pet = FakePet(
        app_user_id=B, name="롱롱씨", breed="dog_beagle",
        health_conditions="없음", photo_storage_key="pets/b.jpg",
        photo_updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    c_pet = FakePet(app_user_id=C, name="롱냥이", breed="dog_corgi")
    identity = FakeIdentity(owner_pet_id=a_pet.id)
    a_pet.identity_id = b_pet.identity_id = c_pet.identity_id = identity.id
    store.pets += [a_pet, b_pet, c_pet]
    store.pet_identities.append(identity)
    store.pet_members += [(a_pet.id, B), (a_pet.id, C), (c_pet.id, B)]
    return a_pet, b_pet, c_pet


async def test_연결한_공동_보호자가_자기_카드_id_로_나가는_한_흐름(
    store: Store, group
):
    """**연결해서 참여한 사람이 나간다.** 앱이 보낼 수 있는 `petId` 는 자기 카드 id 하나다.

    한 테스트에서 열둘을 잇는다 — ① 그룹 **모든 행**의 멤버십이 사라지고 ② 연결이 풀리고
    ③ 쓸모가 없어진 그룹 행이 정리되고 ④ 자기 `pets` 행은 남고 ⑤ 개인 이름·사진이 그대로고
    ⑥ 자기 목록에 그 아이가 그대로 있고 ⑦ 열리고 ⑧ 자기 기록이 남아 읽히고 ⑨ 그룹 기록은
    끊기고 ⑩ **나간 뒤에 쌓인 것까지** 끊기고 ⑪ 남은 사람들은 멀쩡하다.

    ③ 은 마지막 연결 참여자가 나갈 때 일어난다 — 행이 하나로 줄면 "여럿이 같은 아이" 라는
    뜻이 없어지므로, 이 흐름은 B 가 나간 뒤 C 까지 나가는 데까지 간다.
    """
    a_pet, b_pet, c_pet = group

    # B 는 앵커 행을 대표 강아지로 세워 뒀다 — 나가면 못 보게 되는 행이다.
    _user(store, B).primary_pet_id = a_pet.id

    store.care_events += [
        FakeCareEvent(pet_id=a_pet.id, kind="meal", occurred_at=MORNING, actor_app_user_id=A),
        FakeCareEvent(pet_id=b_pet.id, kind="snack", occurred_at=MORNING, actor_app_user_id=B),
    ]
    a_walk = add_walk(store, A, [a_pet], MORNING)
    b_walk = add_walk(store, B, [b_pet], MORNING + timedelta(minutes=40))

    # 나가기 전 — 셋이 같은 하루를 본다.
    before = today(B, b_pet.id).json()
    assert (before["meal"], before["snack"]) == (1, 1)
    assert walk_ids(B, b_pet.id) == [str(b_walk.id), str(a_walk.id)]

    # ── 나간다. `petId` 는 **자기 카드 id** 다 (앵커 행 id 는 앱에 안 내려간다) ──
    assert client_as(B).delete(f"/app/pets/{b_pet.id}/members/{B}").status_code == 204

    # ① 그룹의 **모든 행**에서 B 의 돌보미 행이 사라진다. 한 행만 지우면 남은 행의
    #    멤버십으로 그룹을 계속 읽는다 — 이 흐름이 잡는 회귀다.
    assert (a_pet.id, B) not in store.pet_members
    assert (c_pet.id, B) not in store.pet_members
    assert [row for row in store.pet_members if row[1] == B] == []

    # ② 연결이 풀린다. ③ 아직은 아니다 — A·C 의 행이 남아 그룹은 여전히 뜻이 있다.
    assert b_pet.identity_id is None
    assert a_pet.identity_id == c_pet.identity_id is not None
    assert store.pet_identities != []

    # ④ 행은 그대로 있다. 나가기는 아무것도 지우거나 옮기지 않는다.
    assert b_pet in store.pets
    assert b_pet.app_user_id == B

    # ⑤ 개인 이름·사진도 그대로다. 기록이 이 행에 매달려 있으므로 여기가 흔들리면
    #    나간 사람의 앨범이 빈다.
    assert b_pet.name == "롱롱씨"
    assert b_pet.photo_storage_key == "pets/b.jpg"
    assert b_pet.photo_updated_at == datetime(2026, 8, 1, tzinfo=UTC)

    # ⑥ 자기 목록에 그 아이가 그대로 있다. 이제 **자기 행의 값**을 본다 — 연결 동안
    #    화면에 그려지던 주보호자의 공통 정보(견종·지병)가 원래 자기 것으로 돌아온다.
    card = cards(B)[str(b_pet.id)]
    assert card["name"] == "롱롱씨"
    assert (card["breed"], card["health_conditions"]) == ("dog_beagle", "없음")
    assert (card["is_owner"], card["is_group_owner"]) == (True, True)
    assert card["has_other_carers"] is False
    assert card["has_photo"] is True
    # 대표 강아지가 못 보는 행을 가리키지 않게 수선된다.
    assert _user(store, B).primary_pet_id == b_pet.id
    assert card["is_primary"] is True

    # ⑦ 열린다 — 보호자 화면에 자기 혼자 대표로 뜬다.
    members = client_as(B).get(f"/app/pets/{b_pet.id}/members")
    assert members.status_code == 200
    assert [(m["app_user_id"], m["is_owner"]) for m in members.json()["members"]] == [
        (str(B), True)
    ]

    # ⑧ 자기가 적은 기록은 자기 카드에 그대로 남아 읽힌다.
    mine = today(B, b_pet.id).json()
    assert (mine["snack"], len(mine["events"])) == (1, 1)
    assert walk_ids(B, b_pet.id) == [str(b_walk.id)]
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks/{b_walk.id}").status_code == 200

    # ⑨ 그룹 기록은 끊긴다 — 자기 카드로 물어도, 앵커 행 id 로 직접 물어도.
    assert mine["meal"] == 0, "나간 사람이 그룹 케어를 계속 본다"
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks/{a_walk.id}").status_code == 404
    assert today(B, a_pet.id).status_code == 404
    assert client_as(B).get(f"/app/pets/{a_pet.id}/walks").status_code == 404
    assert client_as(B).get(f"/app/pets/{c_pet.id}/walks").status_code == 404
    assert str(a_pet.id) not in cards(B) and str(c_pet.id) not in cards(B)

    # ⑩ 나간 **뒤에** 쌓인 것도 안 보인다. 끊기는 것은 "그때까지" 가 아니다 —
    #    공동 조회의 pet 묶음이 자기 행 하나로 줄었기 때문이다.
    store.care_events.append(
        FakeCareEvent(pet_id=a_pet.id, kind="meal", occurred_at=EVENING, actor_app_user_id=A)
    )
    later = add_walk(store, A, [a_pet], EVENING)
    after = today(B, b_pet.id).json()
    assert (after["meal"], after["snack"]) == (0, 1)
    assert walk_ids(B, b_pet.id) == [str(b_walk.id)]
    assert client_as(B).get(f"/app/pets/{b_pet.id}/walks/{later.id}").status_code == 404

    # ⑪ 남은 사람들은 멀쩡하다. A·C 는 서로를 계속 보고, 나간 B 의 행만 안 섞인다.
    a_today = today(A, a_pet.id).json()
    assert (a_today["meal"], a_today["snack"]) == (2, 0), "나간 사람의 기록이 계속 섞인다"
    assert walk_ids(A, a_pet.id) == [str(later.id), str(a_walk.id)]
    assert walk_ids(C, c_pet.id) == [str(later.id), str(a_walk.id)]
    assert client_as(C).get(f"/app/pets/{c_pet.id}/members").status_code == 200
    assert [
        m["app_user_id"] for m in client_as(A).get(f"/app/pets/{a_pet.id}/members").json()["members"]
    ] == [str(A), str(C)]
    assert (a_pet.id, C) in store.pet_members
    assert _user(store, A).primary_pet_id is None  # 남의 대표는 안 건드린다

    # ③ 마지막 연결 참여자까지 나가면 그룹 행이 정리된다 — 행이 하나로 줄면
    #    "여럿이 같은 아이" 라는 뜻이 없어지므로 연결 이전과 똑같은 모양으로 돌아간다.
    assert client_as(C).delete(f"/app/pets/{c_pet.id}/members/{C}").status_code == 204
    assert store.pet_identities == [], "혼자 남은 그룹이 안 정리됐다"
    assert a_pet.identity_id is None and c_pet.identity_id is None
    assert {p.id for p in store.pets} == {a_pet.id, b_pet.id, c_pet.id}


async def test_연결_없이_참여한_돌보미가_나가는_한_흐름(store: Store):
    """**연결 없이 참여한 사람**은 그 아이의 행이 자기 것이 아니다 — 거기서 갈린다.

    연결한 사람은 나가도 **자기 행이 남아** 목록에 카드가 그대로 있고 자기가 적은 기록을
    계속 읽는다(위 흐름 ④~⑧). 연결 없이 참여한 사람에게는 남을 행이 없어 **카드가 목록에서
    사라지고 그 아이의 기록에 아예 닿지 못한다** — 자기가 적은 것도 포함이다. 기록은 사람이
    아니라 **강아지 행**에 매달려 있고, 나가기는 기록을 옮기거나 지우지 않기 때문이다.
    그래서 그 기록은 **지워지는 것이 아니라 남은 보호자 쪽에 그대로 남는다.**

    여기서 지우거나 옮기도록 만들면 남의 집 기록의 한쪽이 사라진다.
    """
    pet = FakePet(app_user_id=O, name="맥스", breed="믹스")
    store.pets.append(pet)
    store.pet_members.append((pet.id, J))
    _user(store, J).primary_pet_id = pet.id

    store.care_events += [
        FakeCareEvent(pet_id=pet.id, kind="meal", occurred_at=MORNING, actor_app_user_id=O),
        FakeCareEvent(pet_id=pet.id, kind="snack", occurred_at=MORNING, actor_app_user_id=J),
    ]
    walk = add_walk(store, J, [pet], MORNING)

    assert today(J, pet.id).json()["meal"] == 1
    assert walk_ids(J, pet.id) == [str(walk.id)]

    # 이 사람의 카드 id 는 **대표의 행 id** 그대로다 — 연결이 없으니 자기 행이 없다.
    assert client_as(J).delete(f"/app/pets/{pet.id}/members/{J}").status_code == 204

    assert store.pet_members == []
    assert pet in store.pets and pet.app_user_id == O
    assert store.pet_identities == []  # 연결이 없었으므로 정리할 그룹도 없다

    # 카드가 목록에서 사라지고, 그 아이의 어떤 경로에도 못 닿는다.
    assert cards(J) == {}
    assert today(J, pet.id).status_code == 404
    assert client_as(J).get(f"/app/pets/{pet.id}/walks").status_code == 404
    assert client_as(J).get(f"/app/pets/{pet.id}/walks/{walk.id}").status_code == 404
    assert client_as(J).get(f"/app/pets/{pet.id}/members").status_code == 404
    # 가리킬 수 없게 된 대표 강아지는 비워진다 — 남은 아이가 없다.
    assert _user(store, J).primary_pet_id is None

    # **기록은 지워지지 않았다.** J 가 적은 케어도 J 가 한 산책도 대표 쪽에 그대로 있다.
    owner_today = today(O, pet.id).json()
    assert (owner_today["meal"], owner_today["snack"]) == (1, 1)
    assert walk_ids(O, pet.id) == [str(walk.id)]
    assert len(store.care_events) == 2 and len(store.walks) == 1
