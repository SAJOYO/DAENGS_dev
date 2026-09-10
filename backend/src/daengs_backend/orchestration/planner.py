"""Deterministic route planning around the semantic decision (D-041, Card 2B).

Two responsibilities, both deterministic:

1. Resolve the approved machine-readable routing signal (`requested_capability`,
   routing doc §1) before any LLM call. It is a routing signal, never
   authorization (D-036), and no new deterministic signals are invented here.
2. Assemble the real Card 1 RoutePlan from a SemanticRoutingDecision using only
   the trusted query/context: Training/Life carry the exact original query, Walk
   and Place coordinates come only from context.location, handoff reasons are
   fixed, and missing coordinates produce an exclusive CLARIFY.

**Payloads are built by an exhaustive per-capability branch, never by a fallback**
(D-051). Until v7 the loop read `if capability in {"training","life"}: … else:
{lat, lon}`, so every capability that was not Training or Life silently received a
WalkPayload shape. That was correct only while Walk was the sole coordinate
capability; the moment `place` became selectable it would have handed Place a
payload with no `query`, failing PlacePayload validation and surfacing as a
top-level FAILED on exactly the queries Place was added to answer. The `else`
below therefore raises: a new ExecuteName must state its payload here or stop the
request loudly, never inherit another capability's shape.

**The general-answer fallback reaches a plan two ways, both behind one flag** (D-057).
(1) A planner rule: when the semantic decision selects nothing at all — no capability,
no handoff — and `general_fallback` is on, the plan becomes exactly one `general`
request carrying the same trusted payload Life gets (question + resolved dog facts).
(2) Since `semantic-router-ko-v9` the router may also select `general` *in addition to*
a specialized destination, so a care or health worry mixed into a weather/venue/
institution utterance is not silently dropped (#277 measured exactly that loss). The
router never uses it to replace Training/Life/Walk/Place. With the flag off the planner
strips `general` from the decision, so production builds the plans it built before.
`general` orders last, never needs coordinates, and the explicit
`requested_capability` signal is untouched — `general` is not a resolvable signal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from daengs_backend.orchestration.contracts import (
    SCREENING_HISTORY_LIMIT,
    RoutePlan,
    RouterKind,
)
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    SemanticRoutingDecision,
)

# Requests are emitted in this order regardless of the order the router listed them.
# The model's list order is not stable — a live v7 probe returned both ["place","walk"]
# and ["walk","place"] for the same shape of query — and that order is user-visible,
# because `aggregate_results` builds the "[산책] … [장소] …" sections from it. Two
# identical questions should not produce two differently-ordered answers. The frozen
# router benchmark is unaffected either way: `_semantic_plan_key` compares requests as
# a multiset. Order follows the `CapabilityName` declaration order.
_GENERAL = "general"
_EXECUTION_ORDER = ("training", "life", "walk", "place", _GENERAL)
# The names the router (and the explicit signal) may select. `general` is executable but
# never selectable — it only ever enters a plan through the fallback rule below, so it is
# excluded here on purpose: `requested_capability="general"` is an unresolved signal.
_EXECUTE_NAMES = frozenset(name for name in _EXECUTION_ORDER if name != _GENERAL)
_EXECUTION_INDEX = {name: index for index, name in enumerate(_EXECUTION_ORDER)}
# Capabilities whose payload carries trusted coordinates. Missing coordinates make
# the whole plan a CLARIFY, so this set is what the coordinate gate reads.
_NEEDS_COORDINATES = frozenset({"walk", "place"})
_QUESTION_CAPABILITIES = frozenset({"training", "life"})
# The verdicts `ScreeningContext` allows. Kept as a literal set rather than read off the
# contract so a widened contract cannot silently widen what the planner copies (#283).
_SCREENING_VERDICTS = frozenset({"normal", "abnormal", "retake"})
_HANDOFF_REASONS = {
    "skin": "image_upload_required",
    "gait": "video_upload_required",
}
# The assistant contract's South Korea box. Place's own service accepts a wider box
# (lat 32~40 · lng 123~133); the public boundary deliberately stays the stricter one
# so Place cannot loosen validation for everyone else (discovery-migration.md §5).
_COORDINATE_BOUNDS = (("lat", 33.0, 39.0), ("lon", 124.0, 132.0))


def resolve_deterministic_route(
    *, requested_capability: str | None, query: str, context: dict[str, Any]
) -> RoutePlan | None:
    """Return a deterministic RoutePlan when the approved signal resolves it, else None.

    Since v7, `place` is an ordinary member of `_EXECUTE_NAMES`, so the explicit signal
    and the semantic path build the identical plan through `assemble_route_plan` —
    PR #196 needed a separate Place branch here only because the shared assembler had
    no Place payload rule yet. One code path is the point: a Place request assembled
    two different ways is a Place request that can drift.
    """
    if requested_capability is None:
        return None
    if requested_capability in _EXECUTE_NAMES:
        decision = SemanticRoutingDecision(execute=[requested_capability], handoffs=[])
    elif requested_capability in _HANDOFF_REASONS:
        decision = SemanticRoutingDecision(execute=[], handoffs=[requested_capability])
    else:
        # An unresolved signal does not fail the request; semantic routing decides.
        return None
    return assemble_route_plan(
        decision,
        query=query,
        context=context,
        router=RouterKind.DETERMINISTIC,
        model=None,
        prompt_version=None,
    )


def assemble_route_plan(
    decision: SemanticRoutingDecision,
    *,
    query: str,
    context: dict[str, Any],
    router: RouterKind,
    model: str | None = ROUTER_MODEL_ID,
    prompt_version: str | None = PROMPT_VERSION,
    general_fallback: bool = False,
) -> RoutePlan:
    """Build the real Card 1 RoutePlan using only trusted query/context values.

    `model`/`prompt_version` describe how the selection was reached, not what was selected.
    The deterministic caller above passes None for both because it calls no model at all —
    they are not "unknown", they are "there was none", and the console renders that
    difference (#238).

    `general_fallback` defaults to off so that every existing caller — including the
    frozen router-benchmark runners, which score the *router's* decision — keeps
    building exactly the plan it built before (#279). Production passes
    `settings.general_fallback`.
    """
    needs_coordinates = _NEEDS_COORDINATES.intersection(decision.execute)
    missing = _missing_coordinates(context) if needs_coordinates else []
    if missing:
        # CLARIFY is exclusive (O-8): nothing executes and nothing hands off first.
        # One gate for the whole selection — a mixed Place+Walk turn with no
        # coordinates must not run half of itself.
        return RoutePlan.model_validate(
            {
                "requests": [],
                "handoffs": [],
                "clarify": {
                    "question": _clarify_question(missing, needs=needs_coordinates),
                    "missing": missing,
                },
                "router": router,
                "model": model,
                "prompt_version": prompt_version,
            }
        )

    selected: list[str] = list(decision.execute)
    if not general_fallback:
        # Flag off: the router may name `general` (v9 destination, D-057), but production
        # builds exactly the plan it built before the fallback existed — strip it. A
        # `general`-only decision therefore becomes the old empty plan (FAILED), not an answer.
        selected = [name for name in selected if name != _GENERAL]
    elif (
        not selected
        and not decision.handoffs
        and decision.social_intent is None  # never reaches here in practice; belt and braces
    ):
        # The fallback rule (module docstring). One request, and only when the router
        # chose nothing: a specialized selection is never padded with `general` by rule —
        # the router adds it explicitly when a care intent is mixed in (D-057 ①).
        selected = [_GENERAL]

    requests: list[dict[str, Any]] = []
    # An unrecognized name sorts last rather than raising here, so the precise
    # "no payload rule" error below is what surfaces instead of an index error.
    for capability in sorted(
        selected, key=lambda name: _EXECUTION_INDEX.get(name, len(_EXECUTION_ORDER))
    ):
        payload = _payload_for(capability, query=query, context=context)
        requests.append({"capability": capability, "payload": payload, "timeout_ms": None})

    return RoutePlan.model_validate(
        {
            "requests": requests,
            "handoffs": [
                {"target": target, "reason": _HANDOFF_REASONS[target]}
                for target in decision.handoffs
            ],
            "clarify": None,
            "router": router,
            "model": model,
            "prompt_version": prompt_version,
        }
    )


def _payload_for(capability: str, *, query: str, context: dict[str, Any]) -> dict[str, Any]:
    """The payload for one capability. Exhaustive by design — see the module docstring.

    Every branch reads only the original query text and the already-validated
    `context.location`. Nothing here is derived from model output, and Place gets the
    user's exact words: `PlacePayload` does not strip whitespace because the Place
    service grounds its own interpretation in literal spans of the original query.
    """
    if capability in _QUESTION_CAPABILITIES:
        payload: dict[str, Any] = {"question": query}
        if capability == "life":
            dog = _dog_context(context)
            if dog is not None:
                payload["dog"] = dog
            screening = _screening_context(context)
            if screening is not None:
                payload["screening"] = screening
            history = screening_history(context)
            if history is not None:
                payload["screening_history"] = history
        return payload
    if capability == _GENERAL:
        # Same rule as Life: the exact question plus the trusted dog facts, never a
        # coordinate — the fallback is not allowed to answer Walk's or Place's question.
        #
        # **The screening verdict deliberately stops here.** `general` answers without
        # retrieved evidence (D-057), and its own refusal codes already send symptoms and
        # diagnosis away (`adapters/general.py` `diagnosis`). Handing an ungrounded answerer
        # the fact that a skin check came back `abnormal` invites exactly the sentence that
        # refusal exists to prevent. Life gets it because Life answers from ordinances and
        # subsidy documents, and those are what "이런 경우 지원이 있어요" is made of.
        payload = {"question": query}
        dog = _dog_context(context)
        if dog is not None:
            payload["dog"] = dog
        # Today's care log goes to the fallback and nowhere else (#344): "did I give the
        # medication this morning" is a general question, and Life's documents do not care.
        care_log = _care_log_context(context)
        if care_log is not None:
            payload["care_log"] = care_log
        # Same rule again for confirmed vet spend (#353 Task 7): "피부로 1년간 얼마 썼지"
        # is a general question too, and Life's ordinances do not carry that answer either.
        vet_spend = _vet_spend_context(context)
        if vet_spend is not None:
            payload["vet_spend"] = vet_spend
        return payload
    if capability == "walk":
        location = context["location"]
        return {"lat": location["lat"], "lon": location["lon"]}
    if capability == "place":
        location = context["location"]
        return {"query": query, "lat": location["lat"], "lon": location["lon"]}
    # A destination the router can now emit but the planner has no payload rule for.
    # Failing here is the point: the alternative is silently sending some other
    # capability's payload shape (D-051).
    raise ValueError(f"no payload rule for capability {capability!r}")


def _dog_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the trusted dog facts, dropping anything the caller did not resolve.

    Same rule as ``context.location``: only the caller's structured values reach a payload,
    never model output. A malformed or empty entry yields None rather than an error, because
    a missing profile must not turn an answerable question into a failed request — Life
    answers without it exactly as it did before B4.
    """
    dog = context.get("dog")
    if not isinstance(dog, Mapping):
        return None
    resolved: dict[str, Any] = {}
    breed = dog.get("breed")
    if isinstance(breed, str) and breed.strip():
        resolved["breed"] = breed
    age_months = dog.get("age_months")
    if isinstance(age_months, int) and not isinstance(age_months, bool) and age_months >= 0:
        resolved["age_months"] = age_months
    # Care facts (#331): the same whitelist rule, one field at a time — a malformed care
    # value drops that field, not the breed next to it. ``on_medication`` passes only as
    # ``True``; ``False`` would claim a fact the profile cannot state (blank = unknown).
    feeding_style = dog.get("feeding_style")
    if feeding_style in ("free", "scheduled"):
        resolved["feeding_style"] = feeding_style
    health_conditions = dog.get("health_conditions")
    if isinstance(health_conditions, str) and health_conditions.strip():
        resolved["health_conditions"] = health_conditions.strip()[:200]
    if dog.get("on_medication") is True:
        resolved["on_medication"] = True
    return resolved or None


_CARE_LOG_COUNTS = ("meal", "medication", "snack", "walk")
_CARE_LOG_LAST = ("last_meal_at", "last_medication_at", "last_snack_at")
_CARE_LOG_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CARE_LOG_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _care_log_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read today's care summary, dropping anything the caller did not resolve (#344).

    Same rule as ``_dog_context``: only the caller's structured values reach a payload,
    never model output, and a malformed field drops that field rather than the request.
    The caller is ``routers/assistant.py`` `_with_dog_context`, which already proved ownership
    and reduced the day to counts and last times (``services/care_log_context``).

    **This whitelist knows no ``note`` and no event list.** ``CareLogContext`` would reject
    them downstream, but the reason is upstream of the type: the owner's free text must not
    sit next to the prompt's instructions, and a summary is all the answer needs. A summary
    without a single count is no summary — it yields None, not an empty block.
    """
    care_log = context.get("care_log")
    if not isinstance(care_log, Mapping):
        return None
    day = care_log.get("day")
    if not isinstance(day, str) or not _CARE_LOG_DAY.match(day):
        return None
    resolved: dict[str, Any] = {"day": day}
    for kind in _CARE_LOG_COUNTS:
        count = care_log.get(kind)
        if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 200:
            resolved[kind] = count
    for key in _CARE_LOG_LAST:
        clock = care_log.get(key)
        if isinstance(clock, str) and _CARE_LOG_CLOCK.match(clock):
            resolved[key] = clock
    if not any(kind in resolved for kind in _CARE_LOG_COUNTS):
        return None
    return resolved


_VET_SPEND_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _vet_spend_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the trusted recent vet-spend summary, dropping anything the caller did not
    resolve (#353 Task 7). Same whitelist rule as ``_care_log_context``: only the caller's
    structured values reach a payload, never model output, and a malformed field drops
    that field (or, for ``last_visit``, the whole block — a partial last visit is not a
    fact worth stating) rather than the request.

    The caller is ``routers/assistant.py`` `_with_dog_context`, which already proved
    ownership and narrowed confirmed ``vet_visits`` rows to this shape
    (``services/vet_spend_context``).

    **This whitelist knows no ``reason_detail``, no ``raw_ocr_items``, and no
    ``hospital_address``.** ``VetSpendContext`` would reject the first as an unknown field
    downstream, but the reason is upstream of the type — the same reason
    ``_care_log_context`` never learns ``note``.
    """
    vet_spend = context.get("vet_spend")
    if not isinstance(vet_spend, Mapping):
        return None
    month_total = vet_spend.get("month_total_krw")
    visit_count = vet_spend.get("visit_count_30d")
    last_visit = vet_spend.get("last_visit")
    if (
        not isinstance(month_total, int)
        or isinstance(month_total, bool)
        or month_total < 0
        or not isinstance(visit_count, int)
        or isinstance(visit_count, bool)
        or visit_count < 0
        or not isinstance(last_visit, Mapping)
    ):
        return None
    resolved_last = _vet_last_visit(last_visit)
    if resolved_last is None:
        return None
    resolved: dict[str, Any] = {
        "month_total_krw": month_total,
        "visit_count_30d": visit_count,
        "last_visit": resolved_last,
    }
    by_reason = vet_spend.get("by_reason_12m")
    if isinstance(by_reason, Mapping):
        filtered = {
            key: value
            for key, value in by_reason.items()
            if isinstance(key, str)
            and key.strip()
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if filtered:
            resolved["by_reason_12m"] = filtered
    return resolved


def _vet_last_visit(last_visit: Mapping[str, Any]) -> dict[str, Any] | None:
    """The three required facts plus the two optional contact fields, or None if any
    required fact is missing or malformed."""
    date_value = last_visit.get("date")
    reason = last_visit.get("reason")
    total_krw = last_visit.get("total_krw")
    if (
        not isinstance(date_value, str)
        or not _VET_SPEND_DATE.match(date_value)
        or not isinstance(reason, str)
        or not reason.strip()
        or not isinstance(total_krw, int)
        or isinstance(total_krw, bool)
        or total_krw < 0
    ):
        return None
    resolved: dict[str, Any] = {"date": date_value, "reason": reason, "total_krw": total_krw}
    hospital = last_visit.get("hospital")
    if isinstance(hospital, str) and hospital.strip():
        resolved["hospital"] = hospital
    phone = last_visit.get("phone")
    if isinstance(phone, str) and phone.strip():
        resolved["phone"] = phone
    return resolved


def _screening_context(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the recorded screening verdict, dropping anything the caller did not resolve.

    Same rule as ``_dog_context``: only the caller's structured values reach a payload,
    never model output, and a malformed entry yields None rather than an error. The caller
    is ``routers/assistant.py`` `_with_screening_context`, which already proved ownership
    and narrowed the record to two fields (#307).

    **This function must never learn a third field.** The lesion name is wrong 56.6% of the
    time on holdout (D-023) and `stage1` is uncalibrated, so what keeps that defence standing
    is that no code path speaks either — invariant 15 in `docs/orchestration/contracts.md`.
    Widening the whitelist here would not raise; it would just quietly put a wrong lesion
    name in a prompt.
    """
    screening = context.get("screening")
    if not isinstance(screening, Mapping):
        return None
    verdict = screening.get("verdict")
    if verdict not in _SCREENING_VERDICTS:
        return None
    days_ago = screening.get("days_ago")
    if not isinstance(days_ago, int) or isinstance(days_ago, bool) or days_ago < 0:
        return None
    return {"verdict": verdict, "days_ago": days_ago}


def screening_history(context: dict[str, Any]) -> dict[str, Any] | None:
    """Read the earlier screenings of the same dog, entry by entry (#79 3번).

    The same whitelist as ``_screening_context``, applied per entry, and for the same reason:
    a list of the narrow thing is only narrow while every element goes through the filter.
    Invariant 15 does not weaken with count — widening this loop would not raise either.

    **Bad entries are dropped, not raised on, and the list is truncated rather than refused.**
    The caller already capped it (``services/screening_context.py``), so an over-long list
    here means a bug upstream — but failing the request would turn an answerable question
    into an error over a defect the user cannot see or fix. Truncating keeps the contract's
    promise (`ScreeningHistory` would reject the long list downstream) without that cost.

    **공개인 것은 `aggregate` 도 같은 좁힘을 지나야 하기 때문입니다.** 답변에 붙는 이력 절이
    payload 와 다른 경로로 컨텍스트를 읽으면 좁힘이 두 벌이 되고, 한쪽만 넓어져도 아무것도
    안 깨집니다 — 능력 이름 사본이 셋이던 자리(#269)가 만든 습관입니다.
    """
    history = context.get("screening_history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        return None
    entries: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        verdict = entry.get("verdict")
        if verdict not in _SCREENING_VERDICTS:
            continue
        days_ago = entry.get("days_ago")
        if not isinstance(days_ago, int) or isinstance(days_ago, bool) or days_ago < 0:
            continue
        entries.append({"verdict": verdict, "days_ago": days_ago})
        if len(entries) == SCREENING_HISTORY_LIMIT:
            break
    return {"entries": entries} if entries else None


def _missing_coordinates(context: dict[str, Any]) -> list[str]:
    """Which trusted coordinate keys are absent or outside the assistant's box.

    Out-of-range is treated as missing rather than clamped: a coordinate we do not
    trust is not a coordinate, and silently moving the user somewhere inside the box
    would answer confidently about the wrong place.
    """
    location = context.get("location")
    if not isinstance(location, Mapping):
        return ["location.lat", "location.lon"]
    missing = []
    for key, low, high in _COORDINATE_BOUNDS:
        value = location.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not low <= value <= high
        ):
            missing.append(f"location.{key}")
    return missing


def _clarify_question(missing: list[str], *, needs: frozenset[str] | set[str]) -> str:
    """The CLARIFY sentence, chosen by what the missing coordinates were for.

    Only the wording varies — `clarify.missing` carries the same keys either way, and
    the frozen router benchmark compares that list, never this text.
    """
    if "walk" in needs and "place" in needs:
        if len(missing) == 2:
            return "지금 위치의 위도와 경도를 알려주시면 산책 조건과 주변 장소를 함께 찾아볼게요."
        if missing == ["location.lat"]:
            return "지금 위치의 위도를 알려주세요."
        return "지금 위치의 경도를 알려주세요."
    if "place" in needs:
        if len(missing) == 2:
            return "장소를 찾을 위치의 위도와 경도를 알려주세요."
        if missing == ["location.lat"]:
            return "장소를 찾을 위치의 위도를 알려주세요."
        return "장소를 찾을 위치의 경도를 알려주세요."
    if len(missing) == 2:
        return "산책할 위치의 위도와 경도를 알려주세요."
    if missing == ["location.lat"]:
        return "현재 위치의 위도를 알려주세요."
    return "현재 위치의 경도를 알려주세요."


__all__ = ["assemble_route_plan", "resolve_deterministic_route"]
