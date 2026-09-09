"""Authenticated, expiring edit sessions. Model output never owns revision or confirmation."""

import asyncio
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from daengs_backend.repositories.facility_sessions import TTL_SECONDS, RedisFacilitySessions
from daengs_backend.schemas.place_filter_edits import (
    CompiledFilterEdit,
    FilterEditAction,
    FilterEditRequest,
    FilterEditResponse,
)
from daengs_backend.services.facility_discovery import (
    FacilityDiscoveryError,
    FacilityDiscoveryService,
)


class FilterSessions(RedisFacilitySessions):
    @staticmethod
    def key(search_id):
        return f"place:filter-edit:v1:{search_id}"


class FilterEditService(FacilityDiscoveryService):
    def store(self):
        if self._store is None:
            from daengs_backend.config import settings

            if not settings.redis_url:
                raise FacilityDiscoveryError("facility_session_unavailable")
            self._store = FilterSessions(settings.redis_url)
        return self._store

    async def propose(self, request: FilterEditRequest, owner: str):
        self.store()
        raw = await self._exchange(
            "", {"query": request.query, "base_state": request.base_state}, request
        )
        try:
            compiled = CompiledFilterEdit.model_validate(raw)
            if compiled.base_state != request.base_state:
                raise ValueError("base state changed")
        except ValueError as exc:
            raise FacilityDiscoveryError("facility_invalid_response") from exc
        expires = time.time() + TTL_SECONDS
        result = FilterEditResponse(
            search_id=uuid4(),
            revision=1,
            request=request,
            compiled=compiled,
            expires_at=datetime.fromtimestamp(expires, UTC),
        )
        saved = self._encode(owner, expires, {}, result)
        if not await self._storage("create", result.search_id, saved):
            raise FacilityDiscoveryError("facility_conflict")
        return result

    async def _load_edit(self, search_id, owner):
        raw = await self._storage("get", search_id)
        if raw is None:
            raise FacilityDiscoveryError("facility_expired")
        try:
            saved = json.loads(raw)
            if saved["owner"] != owner or saved["expires"] <= time.time():
                raise FacilityDiscoveryError("facility_expired")
            return raw, saved, FilterEditResponse.model_validate(saved["result"])
        except (ValueError, KeyError, TypeError) as exc:
            raise FacilityDiscoveryError("facility_session_unavailable") from exc

    async def apply(self, action: FilterEditAction, owner: str):
        previous, saved, result = await self._load_edit(action.search_id, owner)
        if action.current_state != result.compiled.base_state:
            raise FacilityDiscoveryError("facility_conflict")
        if result.action_request == action:
            return result
        if result.revision != action.expected_revision or result.result is not None:
            raise FacilityDiscoveryError("facility_conflict")
        plan = result.compiled
        if plan.proposed_state is None or (
            plan.requires_confirmation and not action.confirm_changes
        ):
            raise FacilityDiscoveryError("facility_invalid_action")
        payload = {
            "query": result.request.query,
            "base_state": plan.base_state,
            "proposal": plan.proposal,
            "confirmed": action.confirm_changes,
            "revision": result.request.base_revision + 1,
            "search_request_id": str(action.client_request_id),
        }
        response = await self._exchange("/execute", payload, result.request)
        if (
            response.get("applied_state") != plan.proposed_state
            or response.get("execution_status") != "complete"
            or response.get("revision") != payload["revision"]
            or response.get("search_request_id") != payload["search_request_id"]
        ):
            raise FacilityDiscoveryError("facility_invalid_response")
        updated = result.model_copy(
            update={"revision": result.revision + 1, "action_request": action, "result": response}
        )
        value = self._encode(owner, saved["expires"], {}, updated)
        if saved["expires"] <= time.time():
            raise FacilityDiscoveryError("facility_expired")
        if await self._storage("replace", result.search_id, previous, value):
            return updated
        _, _, current = await self._load_edit(action.search_id, owner)
        if current.action_request == action:
            return current
        raise FacilityDiscoveryError("facility_conflict")

    async def _send(self, client, suffix, payload, request):
        if self._base_url is None or self._timeout is None:
            from daengs_backend.config import settings

            base = self._base_url or settings.place_search_base_url
            timeout = self._timeout or settings.facility_discovery_timeout_ms / 1000
        else:
            base, timeout = self._base_url, self._timeout
        try:
            async with asyncio.timeout(timeout):
                async with client.stream(
                    "POST",
                    base.rstrip("/") + "/internal/place/filter-edits" + suffix,
                    json=payload,
                    headers={"X-Request-ID": str(request.client_request_id)},
                    timeout=timeout,
                ) as response:
                    if response.status_code == 422:
                        raise FacilityDiscoveryError("facility_invalid_action")
                    if response.status_code == 504:
                        raise FacilityDiscoveryError("facility_timeout")
                    if response.status_code == 503:
                        raise FacilityDiscoveryError("facility_unavailable")
                    if not response.is_success:
                        raise FacilityDiscoveryError("facility_upstream_error")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 512 * 1024:
                            raise ValueError("filter edit response too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError("filter edit response must be object")
            return result
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise FacilityDiscoveryError("facility_timeout") from exc
        except httpx.RequestError as exc:
            raise FacilityDiscoveryError("facility_unavailable") from exc
        except (ValueError, TypeError) as exc:
            raise FacilityDiscoveryError("facility_invalid_response") from exc


def get_filter_edit_service():
    return FilterEditService()
