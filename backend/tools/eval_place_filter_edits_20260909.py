"""Bounded real provider evaluation with synthetic queries; never print credentials."""

import argparse
import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.edits import compile_edits
from daengs_place.place.filters.evaluation import evaluate_atoms
from daengs_place.place.filters.proposer import GeminiFilterProposer
from daengs_place.place.providers.gemini import GeminiIntentProposerError


class DiagnosticTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport()
        self.error = None

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if response.status_code >= 400:
            await response.aread()
            try:
                self.error = response.json().get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                self.error = "Non-JSON provider error"
        return response

    async def aclose(self):
        await self.inner.aclose()


def state(parking=None, exclusive=None, name=""):
    atoms = []
    for id, cap, value in [
        ("parking", "operations.parking", parking),
        ("exclusive", "pet_access.exclusive", exclusive),
    ]:
        if value is not None:
            atoms.append({"id": id, "capability": cap, "op": "eq", "value": value})
    return FilterState.model_validate(
        {
            "candidate_kinds": ["cafe", "restaurant"],
            "spatial": {"lat": 37.5, "lng": 127, "radius_m": 3000},
            "name_query": name,
            "hard": {"all": atoms},
            "dogs": [{"ref": "synthetic", "revision": "1", "dog_weight_kg": 10}],
        }
    )


CASES = [
    ("parking", "주차 가능한 곳만 찾아줘", state(), "parking_true"),
    ("intersection", "주차 가능하고 반려동물 전용인 곳만", state(), "both"),
    ("negative", "주차장이 없는 곳만 찾아줘", state(), "parking_false"),
    ("clear", "아까 주차 필수는 빼줘", state(parking=True), "clear"),
    ("indifferent", "주차 상관없어", state(parking=True), "clear"),
    ("keep", "근처 다른 데", state(parking=True, exclusive=True, name="댕스"), "keep"),
    ("prefer", "주차는 있으면 좋겠어", state(), "prefer"),
    ("inferred", "차로 갈 거야", state(), "prefer"),
    ("preserve", "반려동물 전용 조건도 추가해줘", state(parking=True), "both"),
    ("or", "주차 가능한 카페 또는 반려동물 전용 음식점", state(), "or"),
    (
        "or_with_common",
        "주차는 꼭 가능해야 하고, 카페이거나 반려동물 전용 음식점이면 돼",
        state(),
        "common_or",
    ),
    ("exclude", "카페는 빼고 음식점으로 찾아줘", state(), "restaurant"),
    ("unsupported", "주차 가능하고 조용한 곳만", state(), "unresolved"),
    ("indoor", "비 오니까 실내 동반 가능한 곳만", state(), "unresolved"),
    ("large_dog", "대형견이 반드시 들어갈 수 있는 곳", state(), "unresolved"),
    ("negated_clear", "주차 조건 빼달라는 게 아니야. 그대로 둬", state(parking=True), "keep"),
    ("double_negative", "주차가 안 되는 곳은 제외해줘", state(), "parking_true"),
    ("emoji", "🐕 주차 가능하고 반려동물 전용인 곳", state(), "both"),
    (
        "name_clear",
        "장소명 조건만 지우고 나머지는 그대로",
        state(parking=True, name="댕스"),
        "name_clear",
    ),
    ("radius", "반경 10km로 바꿔줘", state(parking=True), "unresolved"),
    ("new_and_reverse", "반려동물 전용이면서 주차장도 있는 곳으로", state(), "both"),
    ("new_or_reverse", "반려동물 전용 카페 아니면 주차 가능한 음식점", state(), "or_reverse"),
    (
        "new_negative",
        "주차 가능 여부가 아니라, 주차 불가인 곳만 보고 싶어",
        state(),
        "parking_false",
    ),
    ("new_clear", "기존 주차 조건을 해제해주세요", state(parking=True), "clear"),
    ("new_keep", "주차 조건은 해제하지 말고 그대로 유지해", state(parking=True), "keep"),
    (
        "new_name_clear",
        "이름 검색만 초기화해 줘. 주차 필수는 유지하고",
        state(parking=True, name="댕스"),
        "name_clear",
    ),
    ("new_unsupported", "주차장도 있고 목줄 없이 뛰어놀 수 있는 곳만", state(), "unresolved"),
    (
        "new_mixed_sign",
        "주차는 가능해야 하는데 반려동물 전용은 아니어야 해",
        state(),
        "parking_not_exclusive",
    ),
]


def accepts(s, kind, parking, exclusive):
    if kind not in s.candidate_kinds:
        return False
    if evaluate_atoms(s.hard.all, kind, parking, exclusive) is not True:
        return False
    return not s.hard.any or any(
        evaluate_atoms(b.all, kind, parking, exclusive) is True for b in s.hard.any
    )


def assess(base, compiled, expected):
    proposed = compiled.proposed_state
    if expected == "unresolved":
        return (
            compiled.status == "needs_resolution"
            and proposed is None
            and bool(compiled.proposal.unresolved)
        )
    if proposed is None:
        return False
    for key in ("spatial", "dogs", "unknown_policy", "result_policy"):
        if getattr(proposed, key) != getattr(base, key):
            return False
    if expected == "keep":
        return proposed == base
    if expected == "clear":
        return (
            not proposed.hard.all
            and not proposed.hard.any
            and not proposed.preferences
            and compiled.status == "ready"
        )
    if expected == "name_clear":
        return (
            proposed.name_query == ""
            and proposed.hard == base.hard
            and proposed.preferences == base.preferences
        )
    if proposed.name_query != base.name_query:
        return False
    if expected == "prefer":
        return (
            proposed.hard == base.hard
            and bool(proposed.preferences)
            and all(
                p.capability == "operations.parking" and p.value is True
                for p in proposed.preferences
            )
        )
    oracle = {
        "parking_true": lambda k, p, e: p,
        "parking_false": lambda k, p, e: not p,
        "both": lambda k, p, e: p and e,
        "or": lambda k, p, e: (k == "cafe" and p) or (k == "restaurant" and e),
        "or_reverse": lambda k, p, e: (k == "cafe" and e) or (k == "restaurant" and p),
        "parking_not_exclusive": lambda k, p, e: p and not e,
        "common_or": lambda k, p, e: p and (k == "cafe" or (k == "restaurant" and e)),
        "restaurant": lambda k, p, e: k == "restaurant",
    }[expected]
    return not proposed.preferences and all(
        accepts(proposed, k, p, e) == oracle(k, p, e)
        for k in ("cafe", "restaurant")
        for p in (False, True)
        for e in (False, True)
    )


async def main():
    args = argparse.ArgumentParser()
    args.add_argument("--limit", type=int, choices=range(1, 29), default=28)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--env-file", type=Path, required=True)
    opts = args.parse_args()
    raw = opts.env_file.read_text(encoding="utf-8-sig")
    match = re.search(r"(?im)^\s*(?:GEMINI(?:_API_KEY)?|제미나이)\s*[:=]\s*(.*?)\s*$", raw)
    if not match:
        raise SystemExit("Gemini credential label not found")
    key = match[1].strip().strip("\"'")
    from daengs_place.core.config import settings

    report = {
        "model": settings.gemini_model,
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "synthetic query -> real Gemini -> production compiler; no database or app HTTP",
        "cases": [],
    }
    output = opts.output
    if output.resolve() == opts.env_file.resolve() or output.exists():
        raise SystemExit("Choose a new output file; existing files are never overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    for id, query, base, expected in CASES[: opts.limit]:
        transport = DiagnosticTransport()
        provider = GeminiFilterProposer(
            key, settings.gemini_model, timeout_s=30, transport=transport
        )
        start = time.monotonic()
        item = {
            "id": id,
            "query": query,
            "expected": expected,
            "base_state": base.model_dump(mode="json"),
        }
        try:
            proposal = await provider.propose(query, base)
            item["proposal"] = proposal.model_dump(mode="json")
            compiled = compile_edits(base, query, proposal)
            item["compiled"] = compiled.model_dump(mode="json")
            item["pass"] = assess(base, compiled, expected)
        except (GeminiIntentProposerError, ValueError) as exc:
            item.update(
                {
                    "pass": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc).replace(key, "[REDACTED]")[:1000],
                }
            )
            cause = exc.__cause__
            if isinstance(cause, httpx.HTTPStatusError):
                item["http_status"] = cause.response.status_code
                item["provider_message"] = (transport.error or "").replace(key, "[REDACTED]")[:2000]
            elif cause is not None:
                item["cause"] = str(cause).replace(key, "[REDACTED]")[:1000]
        item["seconds"] = round(time.monotonic() - start, 2)
        report["cases"].append(item)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2).replace(key, "[REDACTED]") + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    k: item[k]
                    for k in (
                        "id",
                        "pass",
                        "seconds",
                        "error_type",
                        "error",
                        "http_status",
                        "provider_message",
                    )
                    if k in item
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if item.get("http_status") in (400, 401, 403, 404, 429):
            print(
                "Stopped on transport/configuration failure; no repeated paid requests", flush=True
            )
            break
        # Free-tier account has a 15 requests/minute limit. Stay below it, no automatic retries.
        await asyncio.sleep(5)
    print("RESULT", sum(c["pass"] for c in report["cases"]), "/", len(report["cases"]), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
