"""App configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    debate_agent_name: str = "human-debate-agent"
    warmup_url: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
