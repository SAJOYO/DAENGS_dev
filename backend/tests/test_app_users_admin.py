"""회원 조회 — services/app_user_admin.py 와 routers/app_user_admin.py (A2 · #211).

conftest.py 대로 DB 에는 붙지 않습니다. `Store` 가 `app_users` · `pets` 를 대신하고,
**`*_enc` 는 진짜 암호문입니다** (tests/fakes.py 의 `FakeAppUser` 주석) — 서비스가
`core/crypto.py` 로 만든 결과가 그대로 들어오므로, 평문이 새는지 테스트가 볼 수 있습니다.

**여기서 지키려는 것 셋:**

  ① 원문과 `email_hash` 가 응답에 안 실린다 — 실려도 화면은 멀쩡해 보인다
  ② 검색이 가입과 **같은 `blind_index`** 를 지난다 — 정규화가 갈리면 0건인데 에러도 없다
  ③ 탈퇴·미동의 회원에서 500 이 안 난다 — `*_enc` 가 `None` 인 정상 상태다

①은 뚫려도 조용하고, ②는 "검색이 안 되네" 로만 보이고, ③은 실제 데이터가 있어야만
드러납니다. 셋 다 로컬에서 눈으로 보기 어려운 것들입니다.

**②는 처음에 반대로 적었다가 고쳤습니다** — `services/app_auth.py` 만 보고 "정규화를
안 한다"고 읽었는데, 정규화는 `core/crypto.py` 의 `blind_index` **안에** 있습니다
(`strip().lower()`). 가입도 검색도 그 함수를 지나므로 어긋날 자리가 없습니다.
"""

import uuid
from typing import Annotated

import pytest
from fakes import FakeAppUser, FakePet, FakeSession, Store, install
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.crypto import blind_index, encrypt
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Principal, current_admin
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.routers import app_user_admin as router_module
from daengs_backend.services import app_user_admin as service

EMAIL = "Daengs.Team@Example.com"
PHONE = "010-1234-5678"
NAME = "김유나"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    from fakes import FakeAdmin

    return install(Store(FakeAdmin()), monkeypatch)


@pytest.fixture
def member(store: Store) -> FakeAppUser:
    """이메일·전화·이름이 다 있는 정상 회원. 암호문은 진짜입니다."""
    user = store.add_app_user(
        FakeAppUser(
            kakao_id=100001,
            email_enc=encrypt(EMAIL),
            email_hash=blind_index(EMAIL),
            phone_enc=encrypt(PHONE),
            name_enc=encrypt(NAME),
            room_name="네옹이네",
            nickname="네옹집사",
        )
    )
    store.pets.append(
        FakePet(app_user_id=user.id, name="네옹", breed="포메라니안")
    )
    return user


@pytest.fixture
def session(store: Store) -> FakeSession:
    return FakeSession(store)


class TestMasking:
    """가리는 규칙 자체. 순수 함수라 DB 도 세션도 필요 없습니다."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("daengs@example.com", "da***@example.com"),
            # 로컬 파트가 짧으면 통째로 가립니다 — 2자를 남기면 그게 전부입니다.
            ("ab@example.com", "***@example.com"),
            ("a@example.com", "***@example.com"),
            # 카카오가 준 값이라 이메일 모양을 우리가 보장하지 못합니다.
            ("not-an-email", "no***"),
        ],
    )
    def test_이메일(self, raw: str, expected: str) -> None:
        assert service.mask_email(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("01012345678", "***-****-5678"),
            # 구분자가 섞여 와도 같은 모양이어야 합니다.
            ("010-1234-5678", "***-****-5678"),
            ("+82 10 1234 5678", "***-****-5678"),
            ("12", "***"),
        ],
    )
    def test_전화(self, raw: str, expected: str) -> None:
        assert service.mask_phone(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("홍길동", "홍**"), ("김유나", "김**"), ("이서", "이*"), ("김", "*")],
    )
    def test_이름(self, raw: str, expected: str) -> None:
        assert service.mask_name(raw) == expected

    def test_앞자리를_남기되_원문_길이만큼만(self) -> None:
        """`*` 개수가 길이를 그대로 흘리는 것은 의도입니다 — 이름 길이는 식별에
        거의 쓸모가 없고, 고정 길이로 만들면 화면에서 이름 없는 회원과 헷갈립니다."""
        assert service.mask_name("남궁민수") == "남***"


class TestFind:
    async def test_이메일_정확_일치로_찾는다(self, session, member) -> None:
        (view,) = await service.find(session, email=EMAIL)  # type: ignore[arg-type]
        assert view.user.id == member.id

    @pytest.mark.parametrize(
        "typed",
        [
            EMAIL.lower(),
            EMAIL.upper(),
            f"  {EMAIL}  ",
        ],
    )
    async def test_대소문자와_앞뒤_공백은_알아서_맞는다(
        self, session, member, typed: str
    ) -> None:
        """**정규화는 `core.crypto.blind_index` 안에 있습니다** (`strip().lower()`).

        가입도 검색도 같은 함수를 지나므로 어긋날 자리가 없습니다. 그래서 **부르는
        쪽에서 따로 정규화하지 마세요** — 두 곳이 되는 순간 한쪽만 고치는 날이 옵니다.
        """
        (view,) = await service.find(session, email=typed)  # type: ignore[arg-type]
        assert view.user.id == member.id

    async def test_부분_검색은_되지_않는다(self, session, member) -> None:
        """HMAC 이라 원천적으로 안 됩니다. 화면이 이것을 안내해야 합니다."""
        assert await service.find(session, email="Daengs.Team") == []  # type: ignore[arg-type]
        assert await service.find(session, email="@Example.com") == []  # type: ignore[arg-type]

    async def test_없으면_빈_목록(self, session, member) -> None:
        assert await service.find(session, email="nobody@example.com") == []  # type: ignore[arg-type]

    async def test_카카오_번호로도_찾는다(self, session, member) -> None:
        (view,) = await service.find(session, kakao_id=member.kakao_id)  # type: ignore[arg-type]
        assert view.user.id == member.id

    async def test_조건이_없으면_전체_목록이_아니라_예외(self, session) -> None:
        """조용히 "전체 조회"로 해석되면 이 화면이 회원 명부가 됩니다."""
        with pytest.raises(ValueError):
            await service.find(session)  # type: ignore[arg-type]

    async def test_탈퇴한_회원은_이메일로_안_나온다(self, session, store, member) -> None:
        """탈퇴가 `email_enc` 와 `email_hash` 를 **둘 다** 지웁니다 (app_auth.py).

        그래서 "탈퇴 요청 처리" 는 탈퇴 *전에* 찾는 일이고, 뒤에는 `kakao_id` 뿐입니다.
        """
        member.email_enc = None
        member.email_hash = None
        member.status = "withdrawn"

        assert await service.find(session, email=EMAIL) == []  # type: ignore[arg-type]
        (view,) = await service.find(session, kakao_id=member.kakao_id)  # type: ignore[arg-type]
        assert view.email_masked is None


class TestDetail:
    async def test_반려견을_같이_준다(self, session, member) -> None:
        view = await service.get_detail(session, member.id)  # type: ignore[arg-type]
        assert view is not None
        assert [p.name for p in view.pets] == ["네옹"]

    async def test_없는_회원은_None(self, session, member) -> None:
        assert await service.get_detail(session, uuid.uuid4()) is None  # type: ignore[arg-type]

    async def test_동의를_안_받은_항목은_None_이고_500_이_아니다(
        self, session, store
    ) -> None:
        """카카오에서 이메일 동의를 못 받으면 `email_enc` 가 처음부터 비어 있습니다."""
        bare = store.add_app_user(FakeAppUser(kakao_id=100002))

        view = await service.get_detail(session, bare.id)  # type: ignore[arg-type]
        assert view is not None
        assert (view.email_masked, view.phone_masked, view.name_masked) == (
            None,
            None,
            None,
        )

    async def test_열리지_않는_암호문은_숨기지_않고_올린다(
        self, session, store
    ) -> None:
        """`***` 로 가리면 **AES 키가 갈렸다는 사실이 마스킹 뒤에 숨습니다.**

        이 저장소에서 AES 키를 잃는 것은 복구가 없는 사고라(CLAUDE.md) 드러나야 합니다.
        """
        broken = store.add_app_user(
            FakeAppUser(kakao_id=100003, email_enc=b"\x01not-a-valid-ciphertext")
        )

        with pytest.raises(Exception):
            await service.get_detail(session, broken.id)  # type: ignore[arg-type]


class TestHttpBoundary:
    @pytest.fixture
    def app(self, store: Store) -> FastAPI:
        test_app = FastAPI()
        test_app.include_router(router_module.router)

        async def _fake_session() -> FakeSession:
            return FakeSession(store)

        test_app.dependency_overrides[get_session] = _fake_session
        return test_app

    @pytest.fixture
    def as_role(self, app: FastAPI, store: Store):
        def _make(role: str) -> TestClient:
            principal = Principal(admin_id=store.admin.id, role=role)

            async def _fake_admin() -> Principal:
                return principal

            app.dependency_overrides[current_admin] = _fake_admin
            return TestClient(app)

        return _make

    def test_로그인한_관리자면_누구나_본다(self, as_role, member) -> None:
        """`Perm.READ` 라 VIEWER 까지 통과합니다. 나가는 값이 전부 가려져서입니다 —
        여기를 `pii:read` 로 잠그면 API 는 열려 있는데 화면만 안 보이는 계정이 생깁니다."""
        for role in ("ADMIN", "OPERATOR", "CURATOR", "ANALYST", "VIEWER"):
            res = as_role(role).get("/admin/app-users", params={"email": EMAIL})
            assert res.status_code == 200, role

    def test_원문과_해시가_응답에_없다(self, as_role, member) -> None:
        """이 카드에서 제일 조용히 뚫리는 자리입니다.

        `email_hash` 가 같이 나가면 후보값을 HMAC 해서 맞춰 볼 수 있어, 마스킹 자체가
        무의미해집니다 (03_auth.sql 의 pepper 주석).
        """
        body = as_role("ADMIN").get("/admin/app-users", params={"email": EMAIL}).text

        assert EMAIL not in body
        assert PHONE not in body
        assert NAME not in body
        assert member.email_hash not in body
        for leaked in ("email_enc", "phone_enc", "name_enc", "email_hash"):
            assert leaked not in body

    def test_상세에도_원문이_없다(self, as_role, member) -> None:
        body = as_role("ADMIN").get(f"/admin/app-users/{member.id}").text

        assert EMAIL not in body
        assert NAME not in body
        assert member.email_hash not in body
        # 강아지 이름은 개인정보가 아니라 그대로 나갑니다 (05_pets.sql).
        assert "네옹" in body

    def test_가려진_값은_나간다(self, as_role, member) -> None:
        row = as_role("ADMIN").get("/admin/app-users", params={"email": EMAIL}).json()[0]

        assert row["email_masked"] == "Da***@Example.com"
        assert row["phone_masked"] == "***-****-5678"
        assert row["name_masked"] == "김**"
        assert row["kakao_id"] == member.kakao_id

    def test_조건이_둘이면_422(self, as_role, member) -> None:
        res = as_role("ADMIN").get(
            "/admin/app-users", params={"email": EMAIL, "kakao_id": 100001}
        )
        assert res.status_code == 422

    def test_조건이_없으면_422(self, as_role) -> None:
        """조건 없는 호출이 전체 목록이 되지 않아야 합니다."""
        assert as_role("ADMIN").get("/admin/app-users").status_code == 422

    def test_못_찾으면_404_가_아니라_빈_목록(self, as_role, member) -> None:
        res = as_role("ADMIN").get(
            "/admin/app-users", params={"email": "nobody@example.com"}
        )
        assert res.status_code == 200
        assert res.json() == []

    def test_없는_회원_상세는_404(self, as_role) -> None:
        res = as_role("ADMIN").get(f"/admin/app-users/{uuid.uuid4()}")
        assert res.status_code == 404

    def test_응답에_닉네임이_실린다(self, as_role, member) -> None:
        """**이 화면에서 회원을 알아보는 거의 유일한 값입니다.**

        `*_masked` 가 전부 None 인 계정에서는 이 칸이 없으면 한 줄이
        "UUID · 숫자 · None · None · None" 입니다.
        """
        row = (
            as_role("ADMIN")
            .get("/admin/app-users", params={"nickname": "집사"})
            .json()[0]
        )

        assert row["nickname"] == member.nickname

    def test_닉네임과_이메일을_같이_주면_422(self, as_role, member) -> None:
        res = as_role("ADMIN").get(
            "/admin/app-users", params={"nickname": "집사", "email": EMAIL}
        )
        assert res.status_code == 422

    def test_빈_닉네임은_422(self, as_role, member) -> None:
        """조건 없는 호출이 전체 목록이 되는 뒷문이 되면 안 됩니다."""
        res = as_role("ADMIN").get("/admin/app-users", params={"nickname": ""})
        assert res.status_code == 422


class TestNicknameSearch:
    """`?nickname=` — **이 앱키에서 실제로 도는 유일한 검색.**

    우리 카카오 앱키가 사업자 등록이 아니라 프로젝트 팀 것이라 이메일 동의를 못 받고,
    그래서 `email_hash` 가 전 회원 NULL 입니다. `?email=` 은 영영 아무것도 못 찾습니다.
    """

    async def test_부분_일치로_찾는다(self, session, member) -> None:
        """닉네임은 평문이라 조각 검색이 됩니다 (`email_hash` 는 HMAC 이라 안 됩니다)."""
        (view,) = await service.find(session, nickname="집사")  # type: ignore[arg-type]
        assert view.user.id == member.id

    async def test_대소문자를_안_가린다(self, session, store) -> None:
        store.add_app_user(FakeAppUser(kakao_id=100002, nickname="NeoDog"))

        (view,) = await service.find(session, nickname="neodog")  # type: ignore[arg-type]

        assert view.user.nickname == "NeoDog"

    async def test_여러_명이_나올_수_있다(self, session, store, member) -> None:
        """앞의 둘과 다릅니다 — UNIQUE 조회가 아니라 부분 일치입니다."""
        store.add_app_user(FakeAppUser(kakao_id=100003, nickname="네옹집사2"))

        found = await service.find(session, nickname="집사")  # type: ignore[arg-type]

        assert len(found) == 2

    async def test_닉네임이_없는_회원은_안_걸린다(self, session, store) -> None:
        """이 칸보다 먼저 가입한 회원입니다. 다음 로그인에 발급됩니다."""
        store.add_app_user(FakeAppUser(kakao_id=100004, nickname=None))

        assert await service.find(session, nickname="집사") == []  # type: ignore[arg-type]

    async def test_없으면_빈_목록(self, session, member) -> None:
        assert await service.find(session, nickname="아무도아님") == []  # type: ignore[arg-type]

    def test_와일드카드를_이스케이프한다(self) -> None:
        """`%` 하나로 전 회원이 나오면 조건 없는 목록을 막아 둔 것이 무의미해집니다.

        **저장소 함수를 직접 봅니다.** 가짜 저장소는 부분 일치로 흉내 내서 어떤 코드를
        넣어도 통과하므로, 이스케이프가 실제로 도는지는 여기서만 확인됩니다.
        """
        assert app_user_repo.escape_like("%") == r"\%"
        assert app_user_repo.escape_like("_") == r"\_"
        assert app_user_repo.escape_like("네옹") == "네옹"

    def test_백슬래시를_먼저_바꾼다(self) -> None:
        """나중에 하면 앞에서 넣은 탈출 문자까지 다시 탈출해 `%` 가 도로 살아납니다."""
        assert app_user_repo.escape_like(r"\%") == r"\\\%"
