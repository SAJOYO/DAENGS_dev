"""One optional detail lookup inside a single, cancellable writing invocation."""

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass

from daengs_backend.services.walk_diary import space_details
from daengs_backend.services.walk_diary.model_input import SpaceAnswer


@dataclass(frozen=True)
class ProseGeneration:
    value: dict | None
    trace: dict
    failure_code: str | None = None


async def write_space(payload, send):
    # The caller already normalized this card's admitted slots. Never retain a board,
    # source record, memo, raw provider response or another card in the tool's closure.
    from google.genai import types

    seed = deepcopy(payload)
    initial = space_details.initial_input(seed)
    declaration = space_details.declaration(seed)
    trace = {
        "version": space_details.VERSION,
        "initial_input": initial,
        "model_calls": 0,
        "tool_calls": [],
        "public_api_calls": 0,
    }
    contents = [
        types.Content(role="user", parts=[types.Part(text=json.dumps(initial, ensure_ascii=False))])
    ]
    try:
        for round_index in range(space_details.MAX_MODEL_CALLS):
            # A cancelled SDK call must not start another paid request even if it
            # suppresses cancellation and eventually returns a value.
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            tools = [declaration] if declaration and round_index == 0 else []
            trace["model_calls"] += 1
            response = await send(contents, tools)
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            candidates = response.candidates or []
            if len(candidates) != 1 or candidates[0].content is None:
                raise ValueError("missing space response")
            content = candidates[0].content
            calls = [p.function_call for p in content.parts or [] if p.function_call is not None]
            if calls:
                if not tools or len(calls) != 1 or calls[0].name != space_details.NAME:
                    raise ValueError("unavailable space tool")
                call = calls[0]
                result = space_details.lookup(seed, call.args)
                # Invalid arbitrary arguments are not copied into the tool result or trace.
                arguments = deepcopy(call.args) if result["status"] == "ok" else None
                trace["tool_calls"].append(
                    {"name": space_details.NAME, "arguments": arguments, "result": result}
                )
                # Preserve native call IDs and opaque thought signatures for continuation.
                # They stay in memory and are not stored as model reasoning.
                contents = [
                    *contents,
                    content,
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=call.name, id=call.id, response=result
                                )
                            )
                        ],
                    ),
                ]
                continue
            text = "".join(
                p.text for p in content.parts or [] if p.text is not None and not p.thought
            )
            if len(text.encode()) > 64_000:
                raise ValueError("space response exceeds budget")
            answer = SpaceAnswer.model_validate_json(text)
            refs = set(answer.evidence_ids)
            if (
                len(refs) != len(answer.evidence_ids)
                or not refs <= space_details.validate_trace(seed, trace)
                or bool(answer.text.strip()) != bool(refs)
            ):
                raise ValueError("space cited unread material")
            return ProseGeneration(answer.model_dump(mode="json"), deepcopy(trace))
        raise ValueError("space tool round limit")
    except (ValueError, KeyError, TypeError):
        return ProseGeneration(None, deepcopy(trace), "invalid_response")
    except Exception:  # noqa: BLE001 - raw provider errors never enter the receipt
        return ProseGeneration(None, deepcopy(trace), "provider_failed")
