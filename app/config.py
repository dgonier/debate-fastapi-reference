"""App configuration loaded from environment variables."""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    debate_agent_name: str = "human-debate-agent"
    warmup_url: str = ""

    # Modal authentication (used for warmup / authenticated Modal endpoints)
    modal_api_key: Optional[str] = None
    modal_key: Optional[str] = Field(default=None, validation_alias="Modal-Key")
    modal_secret: Optional[str] = Field(default=None, validation_alias="Modal-Secret")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
