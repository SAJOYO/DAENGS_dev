"""`services/gait.py::annotate` — can_confirm/can_delete/created_by, 한 번에 (Task 19).

DB 에는 붙지 않습니다. `pet_repo.owners_by_ids` · `pet_member_repo.members_in` ·
`app_user_repo.nicknames_by_ids` 세 리포지토리 함수를 호출 횟수를 세는 대역으로
바꿔치기해서, "목록 크기와 무관하게 쿼리 세 번" 이라는 주장을 **직접 잽니다** —
행마다 부르면 N+1 이 되는 자리라 세는 것 자체가 회귀 테스트입니다.
"""

from __future__ import annotations

import uuid

import pytest

from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import pet_member as pet_member_repo
from daengs_backend.services import gait as gait_service

PET = uuid.uuid4()
OWNER = uuid.uuid4()
CARER = uuid.uuid4()
STRANGER = uuid.uuid4()  # 기록이 있지만 구성원은 아닌 사람 — 닉네임을 물으면 안 된다


class _Rec:
    """서비스가 실제로 읽는 필드만 가진 GaitRecord 대역."""

    def __init__(self, *, id=None, pet_id=PET, status="PENDING", actor_app_user_id=None):
        self.id = id or uuid.uuid4()
        self.pet_id = pet_id
        self.status = status
        self.actor_app_user_id = actor_app_user_id


@pytest.fixture()
def counting_repos(monkeypatch):
    """세 리포지토리 함수를 진짜와 같은 답을 내는 대역으로 바꾸고, 호출 횟수를 셉니다."""
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
    """**목록 크기와 무관하게 쿼리 세 번.** N+1 이었다면 이 카운터가 30 근처로 뛴다."""
    records = [_Rec(actor_app_user_id=CARER) for _ in range(30)]

    result = await gait_service.annotate(None, OWNER, records)

    assert len(result) == 30
    assert counting_repos == {
        "owners_by_ids": 1,
        "members_in": 1,
        "nicknames_by_ids": 1,
    }


async def test_owner_can_confirm_and_delete_pending_record(counting_repos):
    rec = _Rec(status="PENDING", actor_app_user_id=CARER)
    result = await gait_service.annotate(None, OWNER, [rec])
    assert result[rec.id] == {"can_confirm": True, "can_delete": True, "created_by": "돌보미"}


async def test_actor_can_confirm_but_not_delete(counting_repos):
    """업로더 본인은 확정할 수 있지만(자기 업로드), 삭제는 대표만 — `soft_delete` 의
    경계는 이 태스크에서 안 건드렸다."""
    rec = _Rec(status="PENDING", actor_app_user_id=CARER)
    result = await gait_service.annotate(None, CARER, [rec])
    assert result[rec.id]["can_confirm"] is True
    assert result[rec.id]["can_delete"] is False
    assert result[rec.id]["created_by"] == "돌보미"


async def test_other_carer_cannot_confirm_or_delete(counting_repos):
    """올린 사람도 대표도 아니면 둘 다 False — 구성원이어서 **읽을 수는 있어도**
    확정·삭제 자격은 없다."""
    rec = _Rec(status="PENDING", actor_app_user_id=CARER)
    result = await gait_service.annotate(None, STRANGER, [rec])
    assert result[rec.id] == {"can_confirm": False, "can_delete": False, "created_by": "돌보미"}


async def test_confirmed_record_cannot_be_confirmed_again(counting_repos):
    """상태가 PENDING 이 아니면 업로더·대표라도 can_confirm 은 False — 이미 끝난 전이다."""
    rec = _Rec(status="UPLOADED", actor_app_user_id=CARER)
    result = await gait_service.annotate(None, CARER, [rec])
    assert result[rec.id]["can_confirm"] is False


async def test_withdrawn_actor_created_by_is_none(counting_repos):
    """업로더가 지금은 구성원이 아니면(탈퇴 등) `created_by` 는 None — `actor_label` 과
    같은 "지금도 구성원일 때만" 규칙."""
    rec = _Rec(status="DONE", actor_app_user_id=STRANGER)
    result = await gait_service.annotate(None, OWNER, [rec])
    assert result[rec.id]["created_by"] is None


async def test_legacy_record_without_actor_has_no_created_by(counting_repos):
    """이 칸이 생기기 전의 옛 기록(actor_app_user_id NULL) — 업로더를 모르니 None."""
    rec = _Rec(status="DONE", actor_app_user_id=None)
    result = await gait_service.annotate(None, OWNER, [rec])
    assert result[rec.id]["created_by"] is None
    # NULL 인 짝은 애초에 묻지 않는다 — actor_labels 에 안 실린다.
    assert counting_repos["nicknames_by_ids"] == 0


async def test_empty_list_short_circuits(counting_repos):
    assert await gait_service.annotate(None, OWNER, []) == {}
    assert counting_repos == {"owners_by_ids": 0, "members_in": 0, "nicknames_by_ids": 0}
