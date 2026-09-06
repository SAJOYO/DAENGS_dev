"""Facility continuation ownership and transport. Place alone interprets plans."""

import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
from redis.exceptions import RedisError

from daengs_backend.repositories.facility_sessions import (
    MAX_SESSION_BYTES,
    TTL_SECONDS,
    RedisFacilitySessions,
)
from daengs_backend.schemas.facility_discovery import (
    FacilityActionRequest,
    FacilityDiscoveryRequest,
    FacilityDiscoveryResponse,
    FacilityInternalResponse,
)

MAX_RESPONSE_BYTES = 512 * 1024


class FacilityDiscoveryError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def require_active_facility_owner(user_id):
    # Release the account row lock before any provider/Place HTTP call.
    from daengs_backend.core.database import SessionLocal
    from daengs_backend.repositories import app_user as app_user_repo

    async with SessionLocal() as db, db.begin():
        if await app_user_repo.get_active_for_update(db, user_id) is None:
            raise FacilityDiscoveryError("facility_login_required")


class FacilityDiscoveryService:
    def __init__(self, client=None, *, store=None, base_url=None, timeout=None):
        self._client = client
        self._store = store
        self._base_url = base_url
        self._timeout = timeout

    def store(self):
        if self._store is None:
            from daengs_backend.config import settings

            if not settings.redis_url:
                raise FacilityDiscoveryError("facility_session_unavailable")
            self._store = RedisFacilitySessions(settings.redis_url)
        return self._store

    async def _storage(self, operation, *args):
        try:
            return await getattr(self.store(), operation)(*args)
        except (RedisError, OSError) as exc:
            raise FacilityDiscoveryError("facility_session_unavailable") from exc

    async def search(
        self, request: FacilityDiscoveryRequest, owner: str
    ) -> FacilityDiscoveryResponse:
        self.store()
        envelope = await self._exchange("", request.model_dump(mode="json"), request)
        expires = time.time() + TTL_SECONDS
        result = envelope.result.model_copy(
            update={
                "revision": 1,
                "expires_at": datetime.fromtimestamp(expires, UTC),
                "action_request": None,
            }
        )
        saved = self._encode(owner, expires, envelope.continuation, result)
        if not await self._storage("create", result.search_id, saved):
            raise FacilityDiscoveryError("facility_conflict")
        return result

    async def act(self, action: FacilityActionRequest, owner: str) -> FacilityDiscoveryResponse:
        previous, saved, result = await self._load(action.search_id, owner)
        if result.action_request == action:
            return result
        if result.revision != action.expected_revision:
            raise FacilityDiscoveryError("facility_conflict")
        choice = action.action
        if choice.type == "confirm":
            valid = any(lens.id == choice.lens_id for lens in result.lenses)
        else:
            valid = any(
                signal.id == choice.signal_id
                and signal.state == "needs_selection"
                and any(
                    option.id == choice.option_id and option.availability == "proxy"
                    for option in signal.options
                )
                for signal in result.signals
            )
        if not valid:
            raise FacilityDiscoveryError("facility_invalid_action")
        payload = {
            "request": result.request.model_dump(mode="json"),
            "search_id": str(result.search_id),
            "continuation": saved["continuation"],
            "action": action.action.model_dump(mode="json"),
        }
        envelope = await self._exchange("/actions", payload, result.request)
        if envelope.result.search_id != result.search_id:
            raise FacilityDiscoveryError("facility_invalid_response")
        updated = envelope.result.model_copy(
            update={
                "revision": result.revision + 1,
                "expires_at": result.expires_at,
                "action_request": action,
            }
        )
        value = self._encode(owner, saved["expires"], envelope.continuation, updated)
        if saved["expires"] <= time.time():
            raise FacilityDiscoveryError("facility_expired")
        if await self._storage("replace", result.search_id, previous, value):
            return updated
        _, _, current = await self._load(action.search_id, owner)
        if current.action_request == action:
            return current
        raise FacilityDiscoveryError("facility_conflict")

    async def _load(self, search_id, owner):
        raw = await self._storage("get", search_id)
        if raw is None:
            raise FacilityDiscoveryError("facility_expired")
        try:
            saved = json.loads(raw)
            if saved["owner"] != owner or saved["expires"] <= time.time():
                raise FacilityDiscoveryError("facility_expired")
            result = FacilityDiscoveryResponse.model_validate(saved["result"])
            return raw, saved, result
        except (ValueError, KeyError, TypeError) as exc:
            raise FacilityDiscoveryError("facility_session_unavailable") from exc

    @staticmethod
    def _encode(owner, expires, continuation, result):
        if len(result.model_dump_json().encode()) > 256 * 1024:
            raise FacilityDiscoveryError("facility_invalid_response")
        raw = json.dumps(
            {
                "owner": owner,
                "expires": expires,
                "continuation": continuation,
                "result": result.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(raw.encode()) > MAX_SESSION_BYTES:
            raise FacilityDiscoveryError("facility_invalid_response")
        return raw

    async def _exchange(self, suffix, payload, request):
        if self._client is not None:
            return await self._send(self._client, suffix, payload, request)
        async with httpx.AsyncClient() as client:
            return await self._send(client, suffix, payload, request)

    async def _send(self, client, suffix, payload, request):
        if self._base_url is None or self._timeout is None:
            from daengs_backend.config import settings

            base_url = self._base_url or settings.place_search_base_url
            timeout = self._timeout or settings.facility_discovery_timeout_ms / 1000
        else:
            base_url, timeout = self._base_url, self._timeout
        url = base_url.rstrip("/") + "/internal/place/facility-discovery" + suffix
        try:
            async with asyncio.timeout(timeout):
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers={"X-Request-ID": str(request.client_request_id)},
                    timeout=timeout,
                ) as response:
                    if response.status_code == 503:
                        raise FacilityDiscoveryError("facility_unavailable")
                    if response.status_code == 504:
                        raise FacilityDiscoveryError("facility_timeout")
                    if suffix and response.status_code == 422:
                        raise FacilityDiscoveryError("facility_invalid_action")
                    if not response.is_success:
                        raise FacilityDiscoveryError("facility_upstream_error")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("response exceeds facility budget")
            result = FacilityInternalResponse.model_validate_json(body)
            if result.result.request != request:
                raise ValueError("facility request echo mismatch")
            return result
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise FacilityDiscoveryError("facility_timeout") from exc
        except httpx.RequestError as exc:
            raise FacilityDiscoveryError("facility_unavailable") from exc
        except ValueError as exc:
            raise FacilityDiscoveryError("facility_invalid_response") from exc


def get_facility_discovery_service() -> FacilityDiscoveryService:
    return FacilityDiscoveryService()
