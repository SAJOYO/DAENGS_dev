"""Shared HTTP mapping for entry errors, independent of either version's router."""

from fastapi import HTTPException

from daengs_backend.services.walk_entry_errors import (
    EntryConflict,
    EntryInvalid,
    EntryNotFound,
    EntryUpgradeRequired,
)


async def translate(operation):
    try:
        return await operation
    except EntryNotFound:
        raise HTTPException(404, "산책 또는 강아지를 찾을 수 없습니다.") from None
    except EntryConflict as exc:
        raise HTTPException(409, {"code": "walk_entry_conflict", "message": str(exc)}) from None
    except EntryInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    except EntryUpgradeRequired:
        raise HTTPException(426, {"code": "walk_entry_upgrade_required"}) from None
