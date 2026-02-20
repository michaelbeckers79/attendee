"""
Configuration via environment variables.
No database, no Redis - all config from environment.
"""

import os
from typing import Optional

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings from environment variables."""

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Deepgram API configuration
    deepgram_api_key: str = os.getenv("DEEPGRAM_API_KEY", "")

    # Deepgram streaming options
    deepgram_model: str = os.getenv("DEEPGRAM_MODEL", "nova-2")
    deepgram_language: str = os.getenv("DEEPGRAM_LANGUAGE", "en")
    deepgram_sample_rate: int = int(os.getenv("DEEPGRAM_SAMPLE_RATE", "16000"))

    # Chrome/Selenium options for Teams
    chrome_driver_path: Optional[str] = os.getenv("CHROME_DRIVER_PATH", None)
    display_width: int = int(os.getenv("DISPLAY_WIDTH", "1920"))
    display_height: int = int(os.getenv("DISPLAY_HEIGHT", "1080"))

    # Bot configuration
    default_bot_name: str = os.getenv("DEFAULT_BOT_NAME", "Transcription Bot")

    # Server configuration
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # Webhook delivery
    webhook_timeout_seconds: int = int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "30"))
    webhook_retry_count: int = int(os.getenv("WEBHOOK_RETRY_COUNT", "3"))

    # API security
    api_key: Optional[str] = os.getenv("API_KEY", None)


# Global settings instance
settings = Settings()
