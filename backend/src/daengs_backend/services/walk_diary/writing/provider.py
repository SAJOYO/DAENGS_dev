"""Bounded Gemini writing; space may make one invocation-local detail lookup."""

import json

from daengs_backend.services.walk_diary.writing import policy


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
        if stage == "space":
            from daengs_backend.services.walk_diary import space_details
            from daengs_backend.services.walk_diary.writing.space_dialogue import write_space

            async def send(contents, declarations):
                return await client.models.generate_content(
                    model=policy.MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=policy.PROMPTS[stage] + space_details.INSTRUCTION,
                        temperature=0,
                        candidate_count=1,
                        max_output_tokens=512,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                        **(
                            {
                                "tools": [types.Tool(function_declarations=declarations)],
                                "tool_config": types.ToolConfig(
                                    function_calling_config=types.FunctionCallingConfig(mode="AUTO")
                                ),
                            }
                            if declarations
                            else {
                                "response_mime_type": "application/json",
                                "response_json_schema": schema,
                            }
                        ),
                    ),
                )

            return await write_space(payload, send)
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
