"""`services/screening.py::annotate` — can_confirm/can_delete/created_by, 한 번에 (Task 19).

`services/gait.py::annotate` 와 같은 요령입니다 — 세 리포지토리 함수(`pet_repo
.owners_by_ids` · `pet_member_repo.members_in` · `app_user_repo.nicknames_by_ids`)를
호출 횟수를 세는 대역으로 바꿔 "목록 크기와 무관하게 쿼리 세 번" 을 직접 잽니다.

다른 점은 확정이 **창작자 전용**(대표도 안 됨)이라는 것과, `pet_id IS NULL` 인
개인 기록이 있다는 것입니다.
"""

from __future__ import annotations

import uuid

import pytest

from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_member as pet_member_repo
from daengs_backend.services import screening as screening_service

PET = uuid.uuid4()
OWNER = uuid.uuid4()
CARER = uuid.uuid4()
STRANGER = uuid.uuid4()


class _Rec:
    """서비스가 실제로 읽는 필드만 가진 ScreeningRecord 대역."""

    def __init__(self, *, id=None, pet_id=PET, status="PENDING_UPLOAD", app_user_id=CARER):
        self.id = id or uuid.uuid4()
        self.pet_id = pet_id
        self.status = status
        self.app_user_id = app_user_id  # 창작자


@pytest.fixture()
def counting_repos(monkeypatch):
    calls = {"owners_by_ids": 0, "members_in": 0, "nicknames_by_ids": 0}

    async def owners_by_ids(session, pet_ids):
        calls["owners_by_ids"] += 1
        return {PET: OWNER} if PET in set(pet_ids) else {}

    async def members_in(session, pairs):
        calls["members_in"] += 1
        return {pair for pair in pairs if pair == (PET, CARER)}

    async def nicknames_by_ids(session, ids):
        calls["nicknames_by_ids"] += 1
        names = {OWNER: "오너", CARER: "돌보미"}
        return {uid: names.get(uid) for uid in ids}

    monkeypatch.setattr(pet_repo, "owners_by_ids", owners_by_ids)
    monkeypatch.setattr(pet_member_repo, "members_in", members_in)
    monkeypatch.setattr(app_user_repo, "nicknames_by_ids", nicknames_by_ids)
    return calls


async def test_query_count_is_constant_for_30_records(counting_repos):
    records = [_Rec() for _ in range(30)]
    result = await screening_service.annotate(None, OWNER, records)
    assert len(result) == 30
    assert counting_repos == {"owners_by_ids": 1, "members_in": 1, "nicknames_by_ids": 1}


async def test_creator_can_confirm_pending_own_record(counting_repos):
    rec = _Rec(status="PENDING_UPLOAD", app_user_id=CARER)
    result = await screening_service.annotate(None, CARER, [rec])
    assert result[rec.id]["can_confirm"] is True


async def test_owner_cannot_confirm_carers_record(counting_repos):
    """확정은 **창작자 전용** — gait 와 달리 대표도 못 한다
    (`services/screening.py::confirm_record`, "확정은 그대로 창작자만이다")."""
    rec = _Rec(status="PENDING_UPLOAD", app_user_id=CARER)
    result = await screening_service.annotate(None, OWNER, [rec])
    assert result[rec.id]["can_confirm"] is False
    # 그래도 지울 수는 있다 — 대표는 삭제 자격이 있다(get_deletable 과 같은 규칙).
    assert result[rec.id]["can_delete"] is True


async def test_other_carer_cannot_confirm_or_delete(counting_repos):
    rec = _Rec(status="PENDING_UPLOAD", app_user_id=CARER)
    result = await screening_service.annotate(None, STRANGER, [rec])
    assert result[rec.id] == {"can_confirm": False, "can_delete": False, "created_by": "돌보미"}


async def test_personal_record_has_no_created_by_and_no_owner_delete(counting_repos):
    """`pet_id IS NULL` 인 개인 기록 — "그 아이의 대표" 개념이 없다. created_by 는
    항상 None(구성원이라는 개념 자체가 없다), can_delete 는 창작자 여부만 본다."""
    rec = _Rec(pet_id=None, status="DONE", app_user_id=CARER)
    result = await screening_service.annotate(None, CARER, [rec])
    assert result[rec.id] == {"can_confirm": False, "can_delete": True, "created_by": None}
    # pet_id 가 없는 짝은 actor_labels 의 입력에서 걸러진다 — members_in 은 안 불린다.
    # (owners_by_ids 는 빈 집합으로라도 호출되지만, `repositories/pet.py::owners_by_ids`
    # 가 빈 목록에는 실제 쿼리를 안 낸다 — 그 보장은 그 함수 자신의 단위가 아니라
    # docstring 규칙이라 여기서는 재지 않는다.)
    assert counting_repos["members_in"] == 0
    assert counting_repos["nicknames_by_ids"] == 0


async def test_empty_list_short_circuits(counting_repos):
    assert await screening_service.annotate(None, OWNER, []) == {}
    assert counting_repos == {"owners_by_ids": 0, "members_in": 0, "nicknames_by_ids": 0}
