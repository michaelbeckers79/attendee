"""
Bot session manager - manages active bot sessions in memory.
No database storage - sessions exist only while the bot is running.
"""

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .config import settings
from .schemas import BotResponse, BotState
from .transcription import TranscriptionManager
from .webhook_delivery import send_bot_status_event, send_transcription_event

logger = logging.getLogger(__name__)


class BotSession:
    """
    Represents an active bot session in a meeting.
    Manages transcription and webhook delivery for a single meeting.
    """

    def __init__(
        self,
        *,
        session_id: str,
        meeting_url: str,
        webhook_url: str,
        bot_name: str,
        language: str = "en",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize a bot session.

        Args:
            session_id: Unique session identifier
            meeting_url: Teams meeting URL
            webhook_url: URL for transcription webhooks
            bot_name: Display name for the bot
            language: Transcription language code
            metadata: Additional metadata for webhooks
        """
        self.id = session_id
        self.meeting_url = meeting_url
        self.webhook_url = webhook_url
        self.bot_name = bot_name
        self.language = language
        self.metadata = metadata

        self.state = BotState.PENDING
        self.created_at = datetime.now(timezone.utc)
        self.ended_at: Optional[datetime] = None
        self.error_message: Optional[str] = None

        self._transcription_manager: Optional[TranscriptionManager] = None
        self._bot_adapter = None
        self._run_thread: Optional[threading.Thread] = None
        self._stop_requested = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Participants tracking
        self._participants: Dict[str, Dict[str, Any]] = {}

    def _on_transcription(
        self,
        bot_id: str,
        speaker_id: str,
        speaker_name: Optional[str],
        text: str,
        timestamp_ms: int,
        duration_ms: int,
        is_final: bool,
        metadata: Optional[Dict[str, Any]],
    ):
        """
        Handle transcription events and deliver to webhook.

        This is called synchronously from the Deepgram handler,
        so we need to schedule the async webhook delivery.
        """
        # Combine session metadata with event metadata
        combined_metadata = {**(self.metadata or {}), **(metadata or {})}

        # Schedule webhook delivery in the event loop
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                send_transcription_event(
                    webhook_url=self.webhook_url,
                    bot_id=self.id,
                    speaker_id=speaker_id,
                    speaker_name=speaker_name,
                    text=text,
                    timestamp_ms=timestamp_ms,
                    duration_ms=duration_ms,
                    is_final=is_final,
                    metadata=combined_metadata if combined_metadata else None,
                ),
                self._loop,
            )
        else:
            # Fallback to synchronous delivery
            asyncio.run(
                send_transcription_event(
                    webhook_url=self.webhook_url,
                    bot_id=self.id,
                    speaker_id=speaker_id,
                    speaker_name=speaker_name,
                    text=text,
                    timestamp_ms=timestamp_ms,
                    duration_ms=duration_ms,
                    is_final=is_final,
                    metadata=combined_metadata if combined_metadata else None,
                )
            )

    def _create_transcription_manager(self) -> TranscriptionManager:
        """Create the transcription manager for this session."""
        return TranscriptionManager(
            bot_id=self.id,
            webhook_url=self.webhook_url,
            language=self.language,
            model=settings.deepgram_model,
            sample_rate=settings.deepgram_sample_rate,
            metadata=self.metadata,
            on_transcription_callback=self._on_transcription,
        )

    def add_audio_chunk(
        self,
        speaker_id: str,
        audio_data: bytes,
        speaker_name: Optional[str] = None,
    ):
        """
        Add an audio chunk for transcription.

        Args:
            speaker_id: Unique speaker identifier
            audio_data: Raw PCM audio bytes
            speaker_name: Display name of the speaker
        """
        if not self._transcription_manager:
            self._transcription_manager = self._create_transcription_manager()

        self._transcription_manager.add_audio(
            speaker_id=speaker_id,
            audio_data=audio_data,
            speaker_name=speaker_name,
        )

    def update_participant(
        self,
        participant_id: str,
        participant_name: Optional[str] = None,
        **kwargs,
    ):
        """
        Update participant information.

        Args:
            participant_id: Unique participant identifier
            participant_name: Display name of the participant
            **kwargs: Additional participant data
        """
        if participant_id not in self._participants:
            self._participants[participant_id] = {}

        if participant_name:
            self._participants[participant_id]["name"] = participant_name

        self._participants[participant_id].update(kwargs)

    def get_participant(self, participant_id: str) -> Optional[Dict[str, Any]]:
        """Get participant information."""
        if participant_id in self._participants:
            return {
                "participant_uuid": participant_id,
                "participant_full_name": self._participants[participant_id].get("name"),
                **self._participants[participant_id],
            }
        return None

    async def set_state(self, state: BotState, message: Optional[str] = None):
        """
        Update the session state and notify via webhook.

        Args:
            state: New bot state
            message: Optional status message
        """
        old_state = self.state
        self.state = state

        if state in (BotState.ENDED, BotState.FAILED):
            self.ended_at = datetime.now(timezone.utc)

        if state == BotState.FAILED:
            self.error_message = message

        logger.info(f"Bot {self.id} state changed: {old_state} -> {state}")

        # Send status webhook
        await send_bot_status_event(
            webhook_url=self.webhook_url,
            bot_id=self.id,
            status=state.value,
            message=message,
            metadata=self.metadata,
        )

    async def cleanup(self):
        """Clean up resources when session ends."""
        if self._transcription_manager:
            self._transcription_manager.finish_all()
            self._transcription_manager = None

        self._stop_requested = True

    def to_response(self) -> BotResponse:
        """Convert to API response model."""
        return BotResponse(
            id=self.id,
            state=self.state,
            meeting_url=self.meeting_url,
            webhook_url=self.webhook_url,
            created_at=self.created_at,
            ended_at=self.ended_at,
        )


class SessionManager:
    """
    Manages all active bot sessions.
    Thread-safe singleton for session lifecycle management.
    """

    _instance: Optional["SessionManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._sessions: Dict[str, BotSession] = {}
        self._sessions_lock = threading.Lock()
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "SessionManager":
        """Get the singleton instance."""
        return cls()

    def create_session(
        self,
        meeting_url: str,
        webhook_url: str,
        bot_name: Optional[str] = None,
        language: str = "en",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BotSession:
        """
        Create a new bot session.

        Args:
            meeting_url: Teams meeting URL
            webhook_url: URL for transcription webhooks
            bot_name: Display name for the bot
            language: Transcription language code
            metadata: Additional metadata

        Returns:
            Created BotSession instance
        """
        session_id = f"bot_{uuid.uuid4().hex[:16]}"

        session = BotSession(
            session_id=session_id,
            meeting_url=meeting_url,
            webhook_url=webhook_url,
            bot_name=bot_name or settings.default_bot_name,
            language=language,
            metadata=metadata,
        )

        with self._sessions_lock:
            self._sessions[session_id] = session

        logger.info(f"Created bot session {session_id} for meeting {meeting_url}")
        return session

    def get_session(self, session_id: str) -> Optional[BotSession]:
        """Get a session by ID."""
        with self._sessions_lock:
            return self._sessions.get(session_id)

    async def end_session(self, session_id: str) -> Optional[BotSession]:
        """
        End a bot session.

        Args:
            session_id: Session ID to end

        Returns:
            The ended session, or None if not found
        """
        with self._sessions_lock:
            session = self._sessions.get(session_id)

        if not session:
            return None

        await session.cleanup()
        await session.set_state(BotState.ENDED, "Session ended by request")

        # Keep session in memory briefly for status queries
        # In production, you might want a TTL cleanup mechanism

        return session

    def remove_session(self, session_id: str):
        """Remove a session from memory."""
        with self._sessions_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"Removed session {session_id} from memory")

    def get_all_sessions(self) -> Dict[str, BotSession]:
        """Get all active sessions."""
        with self._sessions_lock:
            return dict(self._sessions)

    def get_active_session_count(self) -> int:
        """Get count of active sessions."""
        with self._sessions_lock:
            return len(
                [s for s in self._sessions.values() if s.state == BotState.IN_MEETING]
            )
