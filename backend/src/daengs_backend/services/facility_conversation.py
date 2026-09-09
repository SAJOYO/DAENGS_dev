"""Owner-bound CAS coordination. Place prepares; commit precedes answer generation."""

import asyncio
import json
import time
from uuid import uuid4

import httpx

from daengs_backend.repositories.facility_sessions import TTL_SECONDS, RedisFacilitySessions
from daengs_backend.schemas.facility_conversation import ConversationResponse
from daengs_backend.services.facility_discovery import (
    FacilityDiscoveryError,
    FacilityDiscoveryService,
)


class ConversationSessions(RedisFacilitySessions):
    @staticmethod
    def key(session_id):
        return f"facility:conversation:v1:{session_id}"


class FacilityConversationService(FacilityDiscoveryService):
    def store(self):
        if self._store is None:
            from daengs_backend.config import settings

            if not settings.redis_url:
                raise FacilityDiscoveryError("facility_session_unavailable")
            self._store = ConversationSessions(settings.redis_url)
        return self._store

    @staticmethod
    def encode(saved):
        raw = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > 1024 * 1024:
            raise FacilityDiscoveryError("facility_invalid_response")
        return raw

    async def turn(self, request, owner):
        session_id = request.session_id or uuid4()
        request_data = request.model_dump(mode="json")
        if request.session_id is None:
            saved = {
                "owner": owner,
                "expires": time.time() + TTL_SECONDS,
                "revision": 0,
                "state": None,
                "response": None,
                "pending": None,
            }
            raw = self.encode(saved)
            if not await self._storage("create", session_id, raw):
                raise FacilityDiscoveryError("facility_conflict")
        else:
            raw = await self._storage("get", session_id)
            if raw is None:
                raise FacilityDiscoveryError("facility_expired")
            saved = json.loads(raw)
            if saved["owner"] != owner or saved["expires"] <= time.time():
                raise FacilityDiscoveryError("facility_expired")
            if saved.get("request") == request_data and saved.get("response"):
                return ConversationResponse.model_validate(saved["response"])
        if saved["revision"] != request.expected_revision:
            raise FacilityDiscoveryError("facility_conflict")
        # A newer request at the same committed revision supersedes the old reservation.
        reserved = {**saved, "pending": str(request.client_request_id)}
        reservation = self.encode(reserved)
        if not await self._storage("replace", session_id, raw, reservation):
            raise FacilityDiscoveryError("facility_conflict")
        try:
            envelope = await self.exchange(
                "prepare",
                {
                    "mode": request.mode,
                    "query": request.query,
                    "manual": request.manual,
                    "previous": saved["state"],
                    "visible_order": request.visible_order,
                    "visible_selected": request.visible_selected,
                },
            )
            prepared = envelope["prepared"]
            state, receipt = prepared["state"], prepared["receipt"]
            revision = saved["revision"] + 1
            response = ConversationResponse(
                session_id=session_id,
                revision=revision,
                client_request_id=request.client_request_id,
                filters=state["filters"],
                search=envelope["search"],
                selected=receipt["selected"],
                display_order=state["snapshot"]["display_order"] if state["snapshot"] else [],
                receipt=receipt,
            )
            committed = {
                **saved,
                "revision": revision,
                "state": state,
                "pending": None,
                "request": request_data,
                "response": response.model_dump(mode="json"),
            }
            committed_raw = self.encode(committed)
            if not await self._storage("replace", session_id, reservation, committed_raw):
                raise FacilityDiscoveryError("facility_conflict")
        except BaseException:
            # Never restore over a newer reservation or commit, including cancellation.
            await asyncio.shield(self._storage("replace", session_id, reservation, raw))
            raise
        if request.mode == "chat":
            try:
                answer = await self.exchange(
                    "answer",
                    {"query": request.query, "committed_revision": revision, "prepared": prepared},
                )
                if answer.get("revision") != revision:
                    raise FacilityDiscoveryError("facility_invalid_response")
            except FacilityDiscoveryError:
                answer = {
                    "text": "요청 처리 결과를 화면에서 확인해 주세요.",
                    "source": "fallback",
                    "revision": revision,
                    "evidence_ids": [],
                }
            response = response.model_copy(update={"answer": answer})
            final = {**committed, "response": response.model_dump(mode="json")}
            if not await self._storage("replace", session_id, committed_raw, self.encode(final)):
                raise FacilityDiscoveryError("facility_conflict")
        elif await self._storage("get", session_id) != committed_raw:
            raise FacilityDiscoveryError("facility_conflict")
        return response

    async def exchange(self, step, payload):
        from daengs_backend.config import settings

        base = self._base_url or settings.place_search_base_url
        timeout = self._timeout or 75

        async def send(client):
            try:
                async with asyncio.timeout(timeout):
                    async with client.stream(
                        "POST",
                        base.rstrip("/") + "/internal/place/facility-conversation/" + step,
                        json=payload,
                        timeout=timeout,
                    ) as response:
                        if not response.is_success:
                            raise FacilityDiscoveryError(
                                "facility_invalid_action"
                                if response.status_code == 422
                                else "facility_upstream_error"
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 1024 * 1024:
                                raise ValueError("conversation response too large")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise TypeError("invalid envelope")
                return result
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise FacilityDiscoveryError("facility_timeout") from exc
            except httpx.RequestError as exc:
                raise FacilityDiscoveryError("facility_unavailable") from exc
            except (ValueError, TypeError) as exc:
                raise FacilityDiscoveryError("facility_invalid_response") from exc

        if self._client is not None:
            return await send(self._client)
        async with httpx.AsyncClient() as client:
            return await send(client)


def get_facility_conversation_service():
    return FacilityConversationService()
