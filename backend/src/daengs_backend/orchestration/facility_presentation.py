"""User wording for all shared facility exits, including failures before Place runs.

The domain owns factual answers. Operational codes stay structured; aggregation sees
only display text. No rewrite of another capability's answer or a medical warning.
"""

from daengs_backend.orchestration.contracts import CapabilityName

LOCATION_MESSAGE = "주변을 찾으려면 현재 위치가 필요해요. 위치를 확인한 뒤 다시 요청해 주세요."
UNKNOWN_RESULT = "처리 결과를 확인하지 못했어요. 잠시 뒤 다시 시도해 주세요."
ERROR_MESSAGES = {
    "facility_login_required": "다시 로그인해 주세요.",
    "facility_expired": "보던 검색이 오래됐어요. 다시 불러와 주세요.",
    "facility_conflict": "목록이 바뀌었어요. 지금 보이는 목록에서 다시 말해 주세요.",
    "facility_pending": "아직 찾고 있어요. 잠시 뒤 다시 시도해 주세요.",
    "facility_invalid_action": "원하는 장소나 조건을 다시 알려주세요.",
    "orchestration_timeout": "찾는 데 시간이 걸리고 있어요. 잠시 뒤 다시 시도해 주세요.",
}


def facility_error_message(code):
    return ERROR_MESSAGES.get(code, UNKNOWN_RESULT)


def present_facility(plan, results):
    """Called only for the new facility contract, before normal shared aggregation."""
    if plan.clarify and any(key.startswith("location.") for key in plan.clarify.missing):
        plan = plan.model_copy(
            update={"clarify": plan.clarify.model_copy(update={"question": LOCATION_MESSAGE})}
        )
    shown = []
    for result in results:
        # A committed search failure already carries a grounded domain explanation.
        if (
            result.capability == CapabilityName.PLACE
            and result.error
            and not (result.data and result.data.get("contract_version") == "place-facility-v2")
        ):
            result = result.model_copy(
                update={
                    "error": result.error.model_copy(
                        update={"detail": facility_error_message(result.error.kind)}
                    )
                }
            )
        shown.append(result)
    return plan, shown
