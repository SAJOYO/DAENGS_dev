"""One bounded Gemini writing attempt, with original cards retained on optional failure."""

import asyncio
import json

from daengs_backend.config import settings
from daengs_walk.diary_input import digest
from daengs_walk.diary_output import assemble_diary
from daengs_walk.diary_writing import (
    POLICY_VERSION,
    PROMPT,
    accept_writing,
    prepare_writing,
    response_schema,
)

MODEL = "gemini-3.1-flash-lite"
TIMEOUT_SECONDS = 15
MAX_INPUT_BYTES = 32_000
MAX_WRITABLE_SCENES = 12
MAX_OUTPUT_TOKENS = 8192
MAX_RESPONSE_BYTES = 64_000


def writing_version():
    return {
        "prompt_hash": digest(PROMPT),
        "policy": POLICY_VERSION,
        "model": MODEL,
        "timeout_s": TIMEOUT_SECONDS,
        "input_bytes": MAX_INPUT_BYTES,
        "scene_limit": MAX_WRITABLE_SCENES,
        "output_tokens": MAX_OUTPUT_TOKENS,
        "response_bytes": MAX_RESPONSE_BYTES,
    }


async def generate_background(payload, schema):
    from google import genai
    from google.genai import types

    key = settings.gemini_api_key.get_secret_value().strip()
    if not key:
        raise ValueError("diary_writer_not_configured")
    async with genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=TIMEOUT_SECONDS * 1000, retry_options=types.HttpRetryOptions(attempts=1)
        ),
    ).aio as client:
        response = await client.models.generate_content(
            model=MODEL,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=PROMPT,
                temperature=0.0,
                candidate_count=1,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        return response.text


async def write_diary(source, prepared, generate=generate_background):
    request = prepare_writing(source, prepared)
    if not request.payload["scenes"]:
        return assemble_diary(source, prepared.plan, None)
    if (
        len(request.payload["scenes"]) > MAX_WRITABLE_SCENES
        or len(json.dumps(request.payload, ensure_ascii=False).encode()) > MAX_INPUT_BYTES
    ):
        return assemble_diary(source, prepared.plan, None, failure_code="budget_exceeded")
    try:
        raw = await asyncio.wait_for(
            generate(request.payload, response_schema(request)), timeout=TIMEOUT_SECONDS
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - no provider errors or source data in responses/logs
        return assemble_diary(source, prepared.plan, None, failure_code="provider_failed")
    try:
        encoded = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        if len(encoded.encode()) > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeds budget")
        return accept_writing(source, prepared.plan, request, raw)
    except (ValueError, TypeError, KeyError):
        return assemble_diary(source, prepared.plan, None, failure_code="invalid_response")
