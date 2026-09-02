"""routers/app_auth.py + services/app_auth.py — 앱 회원 인증.

카카오 검증 자체는 test_kakao.py 가 봅니다. 여기서 보는 것은 **그 다음**입니다 —
회원을 만드는가, 개인정보가 암호화되어 들어가는가, 토큰이 어떻게 나가는가,
그리고 **관리자와 앱 회원이 서로의 API 로 넘어가지 못하는가**.

`verify_id_token` 은 바꿔 끼웁니다. 네트워크에 나가지 않게 하려는 것도 있지만,
"검증을 통과했을 때 무슨 일이 일어나는가"만 보려는 것이 더 큽니다.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fakes import (
    FakeAdmin,
    FakeAppUser,
    FakePet,
    FakeSession,
    FakeWalk,
    FakeWalkPet,
    FakeWalkPointChunk,
    Store,
    install,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.crypto import blind_index, decrypt
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.kakao import (
    KakaoIdentity,
    KakaoIdTokenInvalidError,
    KakaoUnavailableError,
)
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.routers import app_auth as app_auth_router
from daengs_backend.routers import pet as pet_router
from daengs_backend.routers import walk as walk_router
from daengs_backend.services import app_auth as app_auth_service

KAKAO_ID = 987654321


def _walk(owner_id: uuid.UUID, pet_id: uuid.UUID) -> FakeWalk:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    return FakeWalk(
        app_user_id=owner_id,
        client_session_id=uuid.uuid4(),
        started_at=now,
        ended_at=now,
        pets=[FakeWalkPet(pet_id=pet_id)],
        points=[
            FakeWalkPointChunk(
                seq_from=0,
                seq_to=0,
                point_count=1,
                payload={"v": 1, "pts": [[0, 0, 0, 37.5, 127.0, None, 0]]},
            )
        ],
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    return install(Store(FakeAdmin()), monkeypatch)


@pytest.fixture
def identity() -> KakaoIdentity:
    return KakaoIdentity(kakao_id=KAKAO_ID, email="dog@daengs.test", nonce=None)


@pytest.fixture(autouse=True)
def _fake_kakao(
    identity: KakaoIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검증을 통과한 것으로 칩니다. 개별 테스트가 다시 덮어쓸 수 있습니다."""

    async def verify(token, *, expected_nonce=None):
        return identity

    monkeypatch.setattr(app_auth_service, "verify_id_token", verify)


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(app_auth_router.router)
    test_app.include_router(pet_router.router)
    test_app.include_router(walk_router.router)

    @test_app.get("/_app_only")
    async def _app_only(user: CurrentAppUser) -> dict[str, str]:
        return {"ok": str(user.app_user_id)}

    async def _fake_session() -> FakeSession:
        return FakeSession()

    test_app.dependency_overrides[get_session] = _fake_session
    return test_app


@pytest.fixture
def client(app: FastAPI, store: Store) -> TestClient:
    return TestClient(app)


def _login(client: TestClient, id_token: str = "any-id-token"):
    return client.post("/auth/app/kakao", json={"id_token": id_token})


class TestKakaoLogin:
    def test_최초_로그인이면_회원이_생긴다(
        self, client: TestClient, store: Store
    ) -> None:
        res = _login(client)

        assert res.status_code == 200
        assert store.app_users[KAKAO_ID].kakao_id == KAKAO_ID

    def test_토큰이_본문으로_나온다(self, client: TestClient) -> None:
        """관리자와 정반대입니다. 네이티브 앱에는 쿠키 저장소가 없습니다."""
        body = _login(client).json()

        assert body["access_token"] and body["refresh_token"]
        assert body["token_type"] == "Bearer"

    def test_쿠키는_굽지_않는다(self, client: TestClient) -> None:
        assert _login(client).cookies == {}

    def test_이메일이_암호화되어_저장된다(
        self, client: TestClient, store: Store
    ) -> None:
        """**평문이 DB 로 가면 안 됩니다.** 이 카드의 개인정보 처리 전부가 여기 걸립니다."""
        _login(client)
        user = store.app_users[KAKAO_ID]

        assert user.email_enc is not None
        assert b"dog@daengs.test" not in user.email_enc
        assert decrypt(user.email_enc) == "dog@daengs.test"

    def test_이메일_blind_index_가_같이_저장된다(
        self, client: TestClient, store: Store
    ) -> None:
        """암호문으로는 WHERE 를 걸 수 없어서 검색용 해시가 따로 필요합니다."""
        _login(client)
        assert store.app_users[KAKAO_ID].email_hash == blind_index("dog@daengs.test")

    def test_다시_로그인해도_회원이_늘지_않는다(
        self, client: TestClient, store: Store
    ) -> None:
        _login(client)
        first = store.app_users[KAKAO_ID].id
        _login(client)

        assert len(store.app_users) == 1
        assert store.app_users[KAKAO_ID].id == first

    def test_이메일_동의를_안_받으면_비워_둔다(
        self, client: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**email_hash 가 UNIQUE 라 빈 문자열을 넣으면 두 번째 회원부터 막힙니다.**"""

        async def verify(token, *, expected_nonce=None):
            return KakaoIdentity(kakao_id=KAKAO_ID, email=None, nonce=None)

        monkeypatch.setattr(app_auth_service, "verify_id_token", verify)
        _login(client)

        user = store.app_users[KAKAO_ID]
        assert user.email_enc is None
        assert user.email_hash is None

    def test_정지된_회원은_403(self, client: TestClient, store: Store) -> None:
        """401 이 아닙니다. 카카오가 이미 본인 확인을 해 줬고 다시 해도 소용없습니다."""
        store.add_app_user(FakeAppUser(kakao_id=KAKAO_ID, status="suspended"))

        assert _login(client).status_code == 403

    def test_탈퇴한_회원이_다시_로그인하면_되살아난다(
        self, client: TestClient, store: Store
    ) -> None:
        """카카오 회원번호가 UNIQUE 라 새 행을 못 만듭니다. 거부하면 영영 못 들어옵니다."""
        store.add_app_user(FakeAppUser(kakao_id=KAKAO_ID, status="withdrawn"))

        assert _login(client).status_code == 200
        assert store.app_users[KAKAO_ID].status == "active"

    def test_같은_이메일로_다른_카카오_계정이면_409(
        self, client: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.add_app_user(
            FakeAppUser(kakao_id=111, email_hash=blind_index("dog@daengs.test"))
        )

        assert _login(client).status_code == 409

    def test_못_믿을_id_token_이면_401(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def verify(token, *, expected_nonce=None):
            raise KakaoIdTokenInvalidError

        monkeypatch.setattr(app_auth_service, "verify_id_token", verify)
        assert _login(client).status_code == 401

    def test_카카오가_응답이_없으면_503(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**401 로 뭉개면 안 됩니다.** 앱이 '로그인 실패'로 알아듣고 다시 시도하는데,
        다시 해도 똑같이 실패합니다."""

        async def verify(token, *, expected_nonce=None):
            raise KakaoUnavailableError

        monkeypatch.setattr(app_auth_service, "verify_id_token", verify)
        assert _login(client).status_code == 503


class TestSessionFlow:
    def test_재발급하면_refresh_가_바뀐다(self, client: TestClient) -> None:
        """회전입니다. 앱은 받은 값으로 저장된 것을 덮어써야 합니다."""
        old = _login(client).json()["refresh_token"]

        res = client.post("/auth/app/refresh", json={"refresh_token": old})

        assert res.status_code == 200
        assert res.json()["refresh_token"] != old

    def test_로그아웃한_토큰으로는_재발급이_안_된다(
        self, client: TestClient
    ) -> None:
        token = _login(client).json()["refresh_token"]
        assert (
            client.post("/auth/app/logout", json={"refresh_token": token}).status_code
            == 204
        )

        res = client.post("/auth/app/refresh", json={"refresh_token": token})
        assert res.status_code == 401

    def test_모르는_refresh_는_401(self, client: TestClient) -> None:
        res = client.post("/auth/app/refresh", json={"refresh_token": "made-up"})
        assert res.status_code == 401

    def test_me_는_이메일을_복호화해서_준다(self, client: TestClient) -> None:
        access = _login(client).json()["access_token"]

        body = client.get(
            "/auth/app/me", headers={"Authorization": f"Bearer {access}"}
        ).json()

        assert body["kakao_id"] == KAKAO_ID
        assert body["email"] == "dog@daengs.test"

    def test_이름표는_처음에_없다(self, client: TestClient) -> None:
        """None 은 "아직 안 정했다" 입니다. 서버가 대신 지어 주지 않습니다."""
        access = _login(client).json()["access_token"]

        body = client.get(
            "/auth/app/me", headers={"Authorization": f"Bearer {access}"}
        ).json()

        assert body["room_name"] is None

    def test_이름표를_정하면_남는다(self, client: TestClient) -> None:
        access = _login(client).json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}

        saved = client.patch(
            "/auth/app/me", json={"room_name": "네옹이네"}, headers=headers
        )

        assert saved.status_code == 200
        assert saved.json()["room_name"] == "네옹이네"
        assert client.get("/auth/app/me", headers=headers).json()["room_name"] == "네옹이네"

    def test_공백만_보내면_되돌린다(self, client: TestClient) -> None:
        """빈 이름표를 걸 수는 없습니다.

        빈 문자열로 저장하면 "아직 안 정했다" 와 "정해서 지웠다" 가 같은 값이 되어
        앱이 무엇을 그릴지 못 정합니다. **되돌리기는 None 입니다.**
        """
        access = _login(client).json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}
        client.patch("/auth/app/me", json={"room_name": "네옹이네"}, headers=headers)

        cleared = client.patch("/auth/app/me", json={"room_name": "   "}, headers=headers)

        assert cleared.json()["room_name"] is None

    def test_이름표는_20자까지다(self, client: TestClient) -> None:
        """이름표가 방 그림 위에 걸리는 자리라 더 길면 방을 덮습니다."""
        access = _login(client).json()["access_token"]

        res = client.patch(
            "/auth/app/me",
            json={"room_name": "가" * 21},
            headers={"Authorization": f"Bearer {access}"},
        )

        assert res.status_code == 422

    def test_로그인하지_않으면_이름표를_못_고친다(self, client: TestClient) -> None:
        assert client.patch("/auth/app/me", json={"room_name": "남의방"}).status_code == 401

    def test_탈퇴하면_개인정보가_지워지고_세션이_끊긴다(
        self, client: TestClient, store: Store
    ) -> None:
        body = _login(client).json()
        access, refresh = body["access_token"], body["refresh_token"]

        res = client.post(
            "/auth/app/withdraw", headers={"Authorization": f"Bearer {access}"}
        )

        assert res.status_code == 204
        user = store.app_users[KAKAO_ID]
        assert user.status == "withdrawn"
        assert user.email_enc is None and user.email_hash is None
        # 세션도 같이 끊겨야 합니다 — status 는 '새 로그인'만 막습니다.
        assert client.post(
            "/auth/app/refresh", json={"refresh_token": refresh}
        ).status_code == 401

    def test_탈퇴_뒤_남은_access_token으로_강아지를_만들_수_없다(
        self, client: TestClient
    ) -> None:
        access = _login(client).json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}
        assert client.post("/auth/app/withdraw", headers=headers).status_code == 204

        response = client.post(
            "/app/pets",
            headers=headers,
            json={"name": "고아가 될 아이", "breed": "mix"},
        )

        assert response.status_code == 401

    def test_탈퇴_뒤_남은_access_token으로_산책과_좌표를_만들_수_없다(
        self, client: TestClient
    ) -> None:
        access = _login(client).json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}
        assert client.post("/auth/app/withdraw", headers=headers).status_code == 204
        now = datetime(2026, 9, 2, 5, tzinfo=UTC)

        response = client.post(
            "/app/walks",
            headers=headers,
            json={
                "client_session_id": str(uuid.uuid4()),
                "pet_ids": [],
                "started_at": now.isoformat(),
                "ended_at": now.isoformat(),
                "points": [
                    {
                        "client_seq": 0,
                        "chain_index": 0,
                        "at": now.isoformat(),
                        "lat": "37.5",
                        "lng": "127.0",
                        "is_mock": False,
                    }
                ],
            },
        )

        assert response.status_code == 401

    def test_탈퇴하면_내_강아지와_산책_좌표만_지운다(
        self, client: TestClient, store: Store
    ) -> None:
        access = _login(client).json()["access_token"]
        owner = store.app_users[KAKAO_ID]
        mine = FakePet(app_user_id=owner.id, name="네옹", breed="poodle")
        store.pets.append(mine)
        my_walk = _walk(owner.id, mine.id)
        store.walks.append(my_walk)

        other = store.add_app_user(FakeAppUser(kakao_id=111))
        theirs = FakePet(app_user_id=other.id, name="두찌", breed="maltese")
        store.pets.append(theirs)
        their_walk = _walk(other.id, theirs.id)
        store.walks.append(their_walk)

        response = client.post(
            "/auth/app/withdraw", headers={"Authorization": f"Bearer {access}"}
        )

        assert response.status_code == 204
        assert mine not in store.pets
        assert my_walk not in store.walks
        # 좌표는 산책 소유 행 안에 있으므로 산책과 함께 도달 불가능해집니다.
        assert all(walk.id != my_walk.id for walk in store.walks)
        assert theirs in store.pets
        assert their_walk in store.walks
        assert their_walk.points

    def test_탈퇴_뒤_재로그인해도_강아지와_산책은_복원되지_않는다(
        self, client: TestClient, store: Store
    ) -> None:
        access = _login(client).json()["access_token"]
        owner = store.app_users[KAKAO_ID]
        pet = FakePet(app_user_id=owner.id, name="네옹", breed="poodle")
        store.pets.append(pet)
        store.walks.append(_walk(owner.id, pet.id))

        assert client.post(
            "/auth/app/withdraw", headers={"Authorization": f"Bearer {access}"}
        ).status_code == 204
        assert _login(client).status_code == 200

        assert store.app_users[KAKAO_ID].status == "active"
        assert [row for row in store.pets if row.app_user_id == owner.id] == []
        assert [row for row in store.walks if row.app_user_id == owner.id] == []

    def test_탈퇴_데이터_삭제가_실패하면_전체를_롤백한다(
        self, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = store.add_app_user(
            FakeAppUser(kakao_id=KAKAO_ID, email_enc=b"cipher", email_hash="hash")
        )
        session = FakeSession()

        async def delete_walks(_session, _app_user_id):
            return 1

        async def fail_pet_delete(_session, _app_user_id):
            raise RuntimeError("pet delete failed")

        monkeypatch.setattr(
            app_auth_service.walk_repo, "delete_all_for_owner", delete_walks
        )
        monkeypatch.setattr(
            app_auth_service.pet_service, "delete_all_for_owner", fail_pet_delete
        )

        with pytest.raises(RuntimeError, match="pet delete failed"):
            asyncio.run(app_auth_service.withdraw(session, app_user_id=user.id))

        assert session.rollbacks == 1
        assert session.commits == 0
        assert user.status == "active"
        assert user.email_enc == b"cipher"


class TestSubjectSeparation:
    """관리자와 앱 회원이 서로의 API 로 넘어가지 못해야 합니다 (D-016).

    test_auth_routes.py 의 거울상입니다. 저쪽은 앱 토큰이 관리자 API 로 못 들어오는지,
    여기는 관리자 토큰이 앱 API 로 못 들어오는지 봅니다.
    """

    def test_관리자_토큰으로_앱_me_를_부르면_401(self, client: TestClient) -> None:
        admin_token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, "ADMIN")

        res = client.get(
            "/auth/app/me", headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert res.status_code == 401

    def test_관리자_토큰은_권한을_안_거는_앱_엔드포인트도_못_지난다(
        self, client: TestClient
    ) -> None:
        admin_token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, "ADMIN")

        res = client.get(
            "/_app_only", headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert res.status_code == 401

    def test_앱_토큰은_앱_엔드포인트를_지난다(self, client: TestClient) -> None:
        """대조군입니다. 위 둘이 그냥 401 을 내는 것과 구분하려면 필요합니다."""
        access = _login(client).json()["access_token"]

        res = client.get("/_app_only", headers={"Authorization": f"Bearer {access}"})

        assert res.status_code == 200
