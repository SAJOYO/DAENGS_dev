"""Owner-bound CAS coordination. Place prepares; commit precedes answer generation."""

import asyncio
import json
import time
from uuid import NAMESPACE_URL, uuid5

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
        return f"facility:conversation:v2:{session_id}"


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

    @staticmethod
    def initial_id(owner, request_id):
        # First-response loss is recoverable without having received a session ID.
        return uuid5(NAMESPACE_URL, f"daengs:facility:v2:{owner}:{request_id}")

    async def load(self, session_id, owner):
        raw = await self._storage("get", session_id)
        if raw is None:
            raise FacilityDiscoveryError("facility_expired")
        saved = json.loads(raw)
        if saved["owner"] != owner or saved["expires"] <= time.time():
            raise FacilityDiscoveryError("facility_expired")
        return raw, saved

    async def recover(self, request, owner):
        session_id = request.session_id or self.initial_id(owner, request.client_request_id)
        _, saved = await self.load(session_id, owner)
        if saved["pending"] is not None:
            raise FacilityDiscoveryError("facility_pending")
        if saved["response"] is None:
            raise FacilityDiscoveryError("facility_not_committed")
        return ConversationResponse.model_validate(saved["response"])

    async def turn(self, request, owner):
        session_id = request.session_id or self.initial_id(owner, request.client_request_id)
        request_data = request.model_dump(mode="json")
        if request.session_id is None:
            initial = {
                "owner": owner,
                "expires": time.time() + TTL_SECONDS,
                "revision": 0,
                "state": None,
                "response": None,
                "pending": None,
            }
            await self._storage("create", session_id, self.encode(initial))
        raw, saved = await self.load(session_id, owner)
        if saved.get("request") == request_data and saved.get("response"):
            return ConversationResponse.model_validate(saved["response"])
        if saved["revision"] != request.expected_revision:
            raise FacilityDiscoveryError("facility_conflict")
        if (
            saved["pending"] == str(request.client_request_id)
            and saved.get("lease_until", 0) > time.time()
        ):
            raise FacilityDiscoveryError("facility_pending")
        # A newer request at the same committed revision supersedes the old reservation.
        reserved = {
            **saved,
            "pending": str(request.client_request_id),
            "lease_until": time.time() + 90,
        }
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
                    "restore_filters": request.restore_filters,
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
                answer_status="pending" if request.mode == "chat" else "none",
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
        # Publish the commit now. Answer generation is a separate, revision-bound operation.
        return response

    async def answer(self, request, owner):
        raw, saved = await self.load(request.session_id, owner)
        response = saved["response"]
        if (
            response is None
            or saved["revision"] != request.revision
            or response["client_request_id"] != str(request.client_request_id)
        ):
            raise FacilityDiscoveryError("facility_conflict")
        if response["answer_status"] != "pending":
            return ConversationResponse.model_validate(response)
        if saved["pending"] is not None:
            raise FacilityDiscoveryError("facility_conflict")
        try:
            answer = await self.exchange(
                "answer",
                {
                    "query": saved["request"]["query"],
                    "committed_revision": request.revision,
                    "prepared": {"state": saved["state"], "receipt": response["receipt"]},
                },
            )
            if answer.get("revision") != request.revision:
                raise FacilityDiscoveryError("facility_invalid_response")
        except FacilityDiscoveryError:
            answer = {
                "text": "요청 처리 결과를 화면에서 확인해 주세요.",
                "source": "fallback",
                "revision": request.revision,
                "evidence_ids": [],
            }
        completed = {**response, "answer": answer, "answer_status": "ready"}
        # A new turn reservation/commit wins. This answer never increments or replaces its state.
        if not await self._storage(
            "replace", request.session_id, raw, self.encode({**saved, "response": completed})
        ):
            _, latest = await self.load(request.session_id, owner)
            if (
                latest["revision"] == request.revision
                and latest["response"]
                and latest["response"]["answer_status"] == "ready"
            ):
                return ConversationResponse.model_validate(latest["response"])
            raise FacilityDiscoveryError("facility_conflict")
        return ConversationResponse.model_validate(completed)

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
