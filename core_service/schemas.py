"""
Pydantic models/schemas for API requests and responses.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class BotState(str, Enum):
    """Bot lifecycle states."""

    PENDING = "pending"
    JOINING = "joining"
    IN_MEETING = "in_meeting"
    LEAVING = "leaving"
    ENDED = "ended"
    FAILED = "failed"


class TranscriptionProvider(str, Enum):
    """Supported transcription providers."""

    DEEPGRAM = "deepgram"


class CreateBotRequest(BaseModel):
    """Request to create a transcription bot for a Teams meeting."""

    meeting_url: str = Field(
        ...,
        description="Microsoft Teams meeting URL",
        examples=["https://teams.microsoft.com/l/meetup-join/..."],
    )
    webhook_url: HttpUrl = Field(
        ...,
        description="URL where realtime transcription results will be sent",
    )
    bot_name: Optional[str] = Field(
        default=None,
        description="Display name for the bot in the meeting",
    )
    transcription_provider: TranscriptionProvider = Field(
        default=TranscriptionProvider.DEEPGRAM,
        description="Transcription provider to use",
    )
    language: Optional[str] = Field(
        default="en",
        description="Language code for transcription (e.g., 'en', 'es', 'fr')",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata to include with webhook payloads",
    )


class BotResponse(BaseModel):
    """Response containing bot status."""

    id: str = Field(
        description="Unique identifier for the bot session",
    )
    state: BotState = Field(
        description="Current state of the bot",
    )
    meeting_url: str = Field(
        description="Meeting URL the bot is joining",
    )
    webhook_url: str = Field(
        description="URL where transcription results are sent",
    )
    created_at: datetime = Field(
        description="When the bot session was created",
    )
    ended_at: Optional[datetime] = Field(
        default=None,
        description="When the bot session ended",
    )


class TranscriptionEvent(BaseModel):
    """A single transcription event."""

    bot_id: str = Field(
        description="Bot session ID",
    )
    speaker_id: str = Field(
        description="Unique identifier for the speaker",
    )
    speaker_name: Optional[str] = Field(
        default=None,
        description="Display name of the speaker",
    )
    text: str = Field(
        description="Transcribed text",
    )
    timestamp_ms: int = Field(
        description="Unix timestamp in milliseconds when speech started",
    )
    duration_ms: int = Field(
        default=0,
        description="Duration of the utterance in milliseconds",
    )
    is_final: bool = Field(
        default=True,
        description="Whether this is a final or interim transcription",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional metadata from the request",
    )


class WebhookPayload(BaseModel):
    """Payload sent to webhook URL."""

    event_type: str = Field(
        description="Type of event (e.g., 'transcription', 'bot_status')",
    )
    bot_id: str = Field(
        description="Bot session ID",
    )
    timestamp: datetime = Field(
        description="When this event occurred",
    )
    data: Dict[str, Any] = Field(
        description="Event-specific data",
    )


class LeaveMeetingRequest(BaseModel):
    """Request for a bot to leave a meeting."""

    pass  # No additional fields needed


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str = Field(
        description="Error message",
    )
    detail: Optional[str] = Field(
        default=None,
        description="Additional error details",
    )
