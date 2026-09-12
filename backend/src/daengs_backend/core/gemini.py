"""Shared Gemini client construction; offline callers need no database/app settings."""

from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeminiClientSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    api_key: SecretStr = Field(default=SecretStr(""), validation_alias="GEMINI_API_KEY")
    timeout_ms: int = Field(default=30_000, gt=0, validation_alias="GEMINI_TIMEOUT_MS")


def create_client(*, api_key: str, timeout_ms: int) -> Any:
    from google import genai
    from google.genai import types

    if not api_key.strip():
        raise ValueError("GEMINI_API_KEY is required")
    return genai.Client(api_key=api_key.strip(), http_options=types.HttpOptions(timeout=timeout_ms))
