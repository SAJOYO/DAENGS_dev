from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """The Journey subset of DAENGS_geo settings at c5f0d5f."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="DAENGS_", extra="ignore")

    walk_route_provider: Literal["tmap", "fake", "none"] = "fake"
    car_route_provider: Literal["fake", "none"] = "fake"
    transit_route_provider: Literal["fake", "none"] = "fake"
    usage_policy: Literal["deny-all", "dev"] = "deny-all"
    tmap_app_key: str = ""


settings = Settings()
