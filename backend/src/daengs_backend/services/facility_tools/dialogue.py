"""Resume native Gemini tool turns using recorded steps and committed command results."""

import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from pydantic import ValidationError

from daengs_backend.services.facility_tools.catalog import SYSTEM_INSTRUCTION, tools_for
from daengs_place.place.commands.contract import CommandResult, FacilityState
from daengs_place.place.commands.view import context, tool_result
from daengs_place.place.providers.gemini import GeminiIntentProposerError


class CommandPort(Protocol):
    async def read(self) -> FacilityState: ...
    async def execute(
        self, command_id: str, name: str, arguments: dict, expected_revision: int
    ) -> CommandResult: ...


@dataclass
class ToolTurn:
    request_id: str
    query: str
    revision: int
    history: list = field(default_factory=list)
    pending_calls: list = field(default_factory=list)
    executions: list = field(default_factory=list)
    provider_calls: list = field(default_factory=list)
    response: str | None = None
    status: str = "pending"
    format_retry: bool = False
    awaiting_user: bool = False
    completed_call_ids: set = field(default_factory=set)


def answer_text(steps):
    parts = []
    for step in steps:
        if step.get("type") == "model_output":
            parts.extend(c["text"] for c in step.get("content", []) if c.get("type") == "text")
        elif step.get("type") == "text":
            parts.append(step.get("text", ""))
    return "".join(parts).strip()


def valid_answer(text):
    # Punctuation cannot reliably distinguish a sentence from a mascot interjection.
    # Enforce the display contract; sentence style remains the model's instruction.
    return bool(text) and len(text) <= 70 and not any(c in text for c in "\r\n\u2028\u2029")


class FacilityDialogue:
    def __init__(self, provider, *, max_calls=4, max_tools=6):
        self.provider, self.max_calls, self.max_tools = provider, max_calls, max_tools

    async def run(self, turn: ToolTurn, port: CommandPort, *, recent=()):
        if turn.status == "ready":
            return turn
        state = await port.read()
        if state.revision != turn.revision and not turn.pending_calls:
            turn.status = "conflict"
            return turn
        if not turn.history:
            content = json.dumps(
                {
                    "current_screen": context(state),
                    "recent_dialogue": list(recent)[-6:],
                    "user_query": turn.query,
                },
                ensure_ascii=False,
            )
            turn.history.append(
                {"type": "user_input", "content": [{"type": "text", "text": content}]}
            )
        # A failed response call can be resumed with the already executed function results.
        turn.status = "pending"
        while True:
            while turn.pending_calls:
                call = turn.pending_calls[0]
                if call.get("id") in turn.completed_call_ids:
                    turn.status = "provider_protocol_error"
                    return turn
                if len(turn.executions) >= self.max_tools:
                    turn.status = "limit_reached"
                    return turn
                command_id = f"{turn.request_id}:{len(turn.executions)}"
                try:
                    if turn.awaiting_user:
                        output = CommandResult(
                            status="failed",
                            state=await port.read(),
                            code="awaiting_user_confirmation",
                        )
                    else:
                        output = await port.execute(
                            command_id, call["name"], call.get("arguments", {}), turn.revision
                        )
                except (ValidationError, ValueError):
                    output = CommandResult(
                        status="failed", state=await port.read(), code="invalid_arguments"
                    )
                turn.revision = output.state.revision
                if output.status == "needs_confirmation":
                    turn.awaiting_user = True
                payload = tool_result(output)
                turn.executions.append(
                    {
                        "command_id": command_id,
                        "name": call["name"],
                        "arguments": call.get("arguments", {}),
                        "result": payload,
                    }
                )
                turn.history.append(
                    {
                        "type": "function_result",
                        "name": call["name"],
                        "call_id": call["id"],
                        "result": [
                            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                        ],
                        "is_error": output.status in {"failed", "conflict", "unsupported"},
                    }
                )
                turn.completed_call_ids.add(call["id"])
                turn.pending_calls.pop(0)
                if output.status == "conflict":
                    turn.status = "conflict"
                    return turn
            state = await port.read()
            if state.revision != turn.revision:
                turn.status = "conflict"
                return turn
            if len(turn.provider_calls) >= self.max_calls:
                turn.status = "limit_reached"
                return turn
            # A proposal created in this turn cannot manufacture its own user consent.
            tools = [] if turn.format_retry or turn.awaiting_user else tools_for(state)
            payload = {
                "system_instruction": SYSTEM_INSTRUCTION,
                "input": turn.history.copy(),
                "generation_config": {"temperature": 0.2, "max_output_tokens": 700},
            }
            if tools:
                payload["tools"] = tools
            # No tool_choice=any: thanks, questions and final answers need no fake tool.
            record = {"request": payload}
            turn.provider_calls.append(record)
            try:
                response = await self.provider._call(payload)
            except (GeminiIntentProposerError, TimeoutError) as error:
                record["error_type"] = type(error).__name__
                if isinstance(error.__cause__, httpx.HTTPStatusError):
                    record["http_status"] = error.__cause__.response.status_code
                turn.status = "provider_error"
                return turn
            record["response"] = response
            if (await port.read()).revision != turn.revision:
                turn.status = "conflict"
                return turn
            steps = response.get("steps", [])
            calls = [s for s in steps if s.get("type") == "function_call"]
            allowed = {t["name"] for t in tools}
            if any(
                not c.get("id")
                or c.get("name") not in allowed
                or not isinstance(c.get("arguments", {}), dict)
                for c in calls
            ):
                turn.status = "provider_protocol_error"
                return turn
            # Preserve thought signatures and tool call IDs exactly for stateless continuation.
            turn.history.extend(steps)
            if calls:
                if len({c["id"] for c in calls}) != len(calls):
                    turn.status = "provider_protocol_error"
                    return turn
                turn.pending_calls = calls
                continue
            text = answer_text(steps)
            if valid_answer(text):
                turn.response, turn.status = text, "ready"
                return turn
            if turn.format_retry:
                turn.status = "answer_format_failed"
                return turn
            turn.format_retry = True
            turn.history.append(
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "text",
                            "text": "실행 결과는 유지하고, 최종 안내만 줄바꿈 없이 70자 이내 한 문장으로 다시 써줘.",
                        }
                    ],
                }
            )
