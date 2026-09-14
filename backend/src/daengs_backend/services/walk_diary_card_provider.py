"""One bounded Gemini transport call; model SDK is loaded only when invoked."""

import json

from daengs_backend.services import walk_diary_card_policy as policy


async def generate_card_prose(stage, payload, schema):
    from google import genai
    from google.genai import types

    from daengs_backend.config import settings

    key = settings.gemini_api_key.get_secret_value().strip()
    if not key:
        raise ValueError("diary_writer_not_configured")
    async with genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=int(policy.TIMEOUT_SECONDS * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    ).aio as client:
        response = await client.models.generate_content(
            model=policy.MODEL,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=policy.PROMPTS[stage],
                temperature=0,
                candidate_count=1,
                max_output_tokens=2048 if stage == "title" else 512,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        return response.text
