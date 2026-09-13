"""Request-scoped Place execution through the existing member facility service.

The authenticated owner and view stay outside model prompts. The public chat stores only a
bounded reference; the app retrieves the matching committed view through facility recovery.
"""

import time
from uuid import NAMESPACE_URL, UUID, uuid5

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    FacilitySessionPayload,
    OutcomeDetail,
    PlacePayload,
)
from daengs_backend.orchestration.facility_presentation import facility_error_message
from daengs_backend.schemas.assistant_facility import AssistantFacilityContext
from daengs_backend.schemas.facility_conversation import (
    ConversationAnswerRequest,
    ConversationRecoveryRequest,
    ConversationRequest,
    ConversationResponse,
)
from daengs_backend.services.facility_conversation import FacilityConversationService
from daengs_backend.services.facility_discovery import (
    FacilityDiscoveryError,
    require_active_facility_owner,
)


class FacilityCapabilityAdapter:
    capability = CapabilityName.PLACE

    def __init__(
        self, *, owner: str, view: AssistantFacilityContext, service: FacilityConversationService
    ):
        self.owner, self.view, self.service = owner, view, service

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, (PlacePayload, FacilitySessionPayload)):
            raise TypeError("facility execution requires PlacePayload")
        if (
            isinstance(payload, FacilitySessionPayload)
            and payload.facility_session_id != self.view.session_id
        ):
            raise ValueError("facility payload must name the bound view")
        try:
            await require_active_facility_owner(UUID(self.owner))
            response = await self._turn(payload)
            if response.answer_status == "pending":
                response = await self.service.answer(
                    ConversationAnswerRequest(
                        session_id=response.session_id,
                        revision=response.revision,
                        client_request_id=response.client_request_id,
                    ),
                    self.owner,
                )
        except FacilityDiscoveryError as exc:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ERROR,
                error=ErrorDetail(
                    kind=exc.code,
                    detail=facility_error_message(exc.code),
                ),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        return self._result(response, elapsed_ms=int((time.perf_counter() - started) * 1000))

    async def _turn(self, payload: PlacePayload | FacilitySessionPayload) -> ConversationResponse:
        view = self.view
        session_id, revision = view.session_id, view.expected_revision
        if session_id is None:
            if not isinstance(payload, PlacePayload):
                raise TypeError("a new facility search requires coordinates")
            # Stable bootstrap identity makes a lost first reply recoverable without a session ID.
            seed_id = uuid5(NAMESPACE_URL, f"daengs:assistant:facility:{view.client_request_id}")
            try:
                seed = await self.service.recover(
                    ConversationRecoveryRequest(client_request_id=seed_id), self.owner
                )
            except FacilityDiscoveryError as exc:
                if exc.code != "facility_expired":
                    raise
                seed = await self.service.turn(
                    ConversationRequest(
                        client_request_id=seed_id,
                        mode="bootstrap",
                        candidate_pools="v1",
                        manual={
                            "lat": payload.lat,
                            "lng": payload.lon,
                            "radius_m": 3000,
                            "kinds": ["cafe"],
                        },
                    ),
                    self.owner,
                )
            if seed.client_request_id not in {view.client_request_id, seed_id}:
                raise FacilityDiscoveryError("facility_conflict")
            session_id = seed.session_id
            # Re-enter the service even for a committed retry: it compares the full request,
            # so reusing an ID with a different utterance cannot silently return the old answer.
            revision = (
                seed.revision - 1
                if seed.client_request_id == view.client_request_id
                else seed.revision
            )
        return await self.service.turn(
            ConversationRequest(
                client_request_id=view.client_request_id,
                session_id=session_id,
                expected_revision=revision,
                mode="chat",
                query=payload.query,
                candidate_pools="v1",
                bookmark_commands=view.bookmark_commands,
                visible_order=[p.model_dump() for p in view.visible_order],
                visible_selected=view.visible_selected.model_dump()
                if view.visible_selected
                else None,
            ),
            self.owner,
        )

    @staticmethod
    def _result(response: ConversationResponse, *, elapsed_ms: int) -> CapabilityResult:
        receipt = response.receipt
        answer = (response.answer or {}).get("text") or "요청을 준비했어요."
        data = {
            "contract_version": "place-facility-v2",
            "answer": answer,
            "facility": {
                "session_id": str(response.session_id),
                "revision": response.revision,
                "client_request_id": str(response.client_request_id),
            },
        }
        failed = receipt.get("execution") == "failed"
        uncertain = receipt.get("action") in {
            "clarify",
            "await_confirmation",
            "reject",
            "unsupported",
        } or (receipt.get("goal") == "show" and not receipt.get("returned_count"))
        return CapabilityResult(
            capability=CapabilityName.PLACE,
            status=CapabilityStatus.ERROR
            if failed
            else CapabilityStatus.ABSTAINED
            if uncertain
            else CapabilityStatus.OK,
            data=data,
            error=ErrorDetail(kind="facility_search_failed", detail=answer) if failed else None,
            abstention=OutcomeDetail(
                code="facility_out_of_scope"
                if receipt.get("code") == "facility_out_of_scope"
                else "facility_needs_input",
                message=answer,
            )
            if uncertain and not failed
            else None,
            elapsed_ms=elapsed_ms,
        )
