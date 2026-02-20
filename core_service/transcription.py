"""
Standalone Deepgram streaming transcription handler.
Sends transcription results directly to a webhook URL.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
)

from .config import settings

logger = logging.getLogger(__name__)


class DeepgramStreamingHandler:
    """
    Handles streaming transcription via Deepgram and delivers
    results to a webhook URL.
    """

    def __init__(
        self,
        *,
        bot_id: str,
        webhook_url: str,
        speaker_id: str,
        speaker_name: Optional[str] = None,
        language: str = "en",
        model: str = "nova-2",
        sample_rate: int = 16000,
        api_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        on_transcription_callback: Optional[Callable] = None,
    ):
        """
        Initialize the Deepgram streaming handler.

        Args:
            bot_id: Bot session ID for webhook payloads
            webhook_url: URL to send transcription results
            speaker_id: Unique identifier for the speaker
            speaker_name: Display name of the speaker
            language: Language code for transcription
            model: Deepgram model to use
            sample_rate: Audio sample rate
            api_key: Deepgram API key (defaults to config)
            metadata: Additional metadata for webhook payloads
            on_transcription_callback: Optional callback for transcription events
        """
        self.bot_id = bot_id
        self.webhook_url = webhook_url
        self.speaker_id = speaker_id
        self.speaker_name = speaker_name
        self.language = language
        self.model = model
        self.sample_rate = sample_rate
        self.metadata = metadata
        self.on_transcription_callback = on_transcription_callback

        self.api_key = api_key or settings.deepgram_api_key
        if not self.api_key:
            raise ValueError("Deepgram API key is required")

        self.last_send_time = time.time()
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Configure Deepgram client with keepalive
        config = DeepgramClientOptions(options={"keepalive": "true"})
        self.deepgram = DeepgramClient(self.api_key, config)
        self.dg_connection = self.deepgram.listen.websocket.v("1")

        self._setup_event_handlers()

    def _setup_event_handlers(self):
        """Set up Deepgram event handlers."""

        def on_message(instance, result, **kwargs):
            """Handle transcription results."""
            try:
                alternatives = result.channel.alternatives
                if not alternatives:
                    return

                transcript = alternatives[0].transcript
                if not transcript or len(transcript.strip()) == 0:
                    return

                is_final = result.is_final
                duration_ms = int(result.duration * 1000) if hasattr(result, "duration") else 0
                timestamp_ms = int(time.time() * 1000)

                logger.info(
                    f"Transcription from {self.speaker_name or self.speaker_id}: "
                    f"{transcript} (final={is_final})"
                )

                # Call the callback if provided
                if self.on_transcription_callback:
                    try:
                        self.on_transcription_callback(
                            bot_id=self.bot_id,
                            speaker_id=self.speaker_id,
                            speaker_name=self.speaker_name,
                            text=transcript,
                            timestamp_ms=timestamp_ms,
                            duration_ms=duration_ms,
                            is_final=is_final,
                            metadata=self.metadata,
                        )
                    except Exception as e:
                        logger.error(f"Error in transcription callback: {e}")

            except Exception as e:
                logger.error(f"Error processing Deepgram message: {e}")

        def on_error(instance, error, **kwargs):
            """Handle Deepgram errors."""
            logger.error(f"Deepgram streaming error for {self.speaker_id}: {error}")

        def on_close(instance, close, **kwargs):
            """Handle connection close."""
            logger.info(f"Deepgram connection closed for {self.speaker_id}")
            self._connected = False

        def on_open(instance, open_event, **kwargs):
            """Handle connection open."""
            logger.info(f"Deepgram connection opened for {self.speaker_id}")
            self._connected = True

        self.dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        self.dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        self.dg_connection.on(LiveTranscriptionEvents.Close, on_close)
        self.dg_connection.on(LiveTranscriptionEvents.Open, on_open)

    def start(self):
        """Start the Deepgram connection."""
        try:
            options = LiveOptions(
                model=self.model,
                smart_format=True,
                language=self.language,
                encoding="linear16",
                sample_rate=self.sample_rate,
                interim_results=True,
            )

            if not self.dg_connection.start(options):
                raise RuntimeError("Failed to start Deepgram connection")

            logger.info(
                f"Started Deepgram streaming for speaker {self.speaker_id} "
                f"(language={self.language}, model={self.model})"
            )

        except Exception as e:
            logger.error(f"Failed to start Deepgram connection: {e}")
            raise

    def send(self, audio_data: bytes):
        """
        Send audio data to Deepgram for transcription.

        Args:
            audio_data: Raw PCM audio bytes (16-bit, mono)
        """
        if not self._connected:
            logger.warning("Deepgram not connected, cannot send audio")
            return

        try:
            self.dg_connection.send(audio_data)
            self.last_send_time = time.time()
        except Exception as e:
            logger.error(f"Error sending audio to Deepgram: {e}")
            raise

    def finish(self):
        """Close the Deepgram connection."""
        try:
            if self._connected:
                self.dg_connection.finish()
                self._connected = False
                logger.info(f"Finished Deepgram streaming for speaker {self.speaker_id}")
        except Exception as e:
            logger.error(f"Error finishing Deepgram connection: {e}")

    @property
    def is_connected(self) -> bool:
        """Check if connected to Deepgram."""
        return self._connected


class TranscriptionManager:
    """
    Manages multiple streaming transcription sessions per speaker.
    """

    def __init__(
        self,
        *,
        bot_id: str,
        webhook_url: str,
        language: str = "en",
        model: str = "nova-2",
        sample_rate: int = 16000,
        metadata: Optional[Dict[str, Any]] = None,
        on_transcription_callback: Optional[Callable] = None,
    ):
        """
        Initialize the transcription manager.

        Args:
            bot_id: Bot session ID
            webhook_url: URL to send transcription results
            language: Language code for transcription
            model: Deepgram model to use
            sample_rate: Audio sample rate
            metadata: Additional metadata for webhook payloads
            on_transcription_callback: Callback for transcription events
        """
        self.bot_id = bot_id
        self.webhook_url = webhook_url
        self.language = language
        self.model = model
        self.sample_rate = sample_rate
        self.metadata = metadata
        self.on_transcription_callback = on_transcription_callback

        self._handlers: Dict[str, DeepgramStreamingHandler] = {}
        self._speaker_names: Dict[str, str] = {}

        # Silence detection timeout from config
        self.silence_timeout_seconds = settings.silence_timeout_seconds
        self._last_audio_time: Dict[str, float] = {}

    def get_or_create_handler(
        self,
        speaker_id: str,
        speaker_name: Optional[str] = None,
    ) -> DeepgramStreamingHandler:
        """
        Get or create a transcription handler for a speaker.

        Args:
            speaker_id: Unique identifier for the speaker
            speaker_name: Display name of the speaker

        Returns:
            DeepgramStreamingHandler instance
        """
        if speaker_name:
            self._speaker_names[speaker_id] = speaker_name

        if speaker_id in self._handlers:
            return self._handlers[speaker_id]

        handler = DeepgramStreamingHandler(
            bot_id=self.bot_id,
            webhook_url=self.webhook_url,
            speaker_id=speaker_id,
            speaker_name=self._speaker_names.get(speaker_id),
            language=self.language,
            model=self.model,
            sample_rate=self.sample_rate,
            metadata=self.metadata,
            on_transcription_callback=self.on_transcription_callback,
        )

        handler.start()
        self._handlers[speaker_id] = handler
        self._last_audio_time[speaker_id] = time.time()

        logger.info(f"Created transcription handler for speaker {speaker_id}")
        return handler

    def add_audio(
        self,
        speaker_id: str,
        audio_data: bytes,
        speaker_name: Optional[str] = None,
    ):
        """
        Add audio data for a speaker.

        Args:
            speaker_id: Unique identifier for the speaker
            audio_data: Raw PCM audio bytes
            speaker_name: Display name of the speaker
        """
        handler = self.get_or_create_handler(speaker_id, speaker_name)
        handler.send(audio_data)
        self._last_audio_time[speaker_id] = time.time()

    def cleanup_idle_handlers(self):
        """Remove handlers that have been idle for too long."""
        current_time = time.time()
        speakers_to_remove = []

        for speaker_id, last_time in self._last_audio_time.items():
            if current_time - last_time > self.silence_timeout_seconds:
                speakers_to_remove.append(speaker_id)

        for speaker_id in speakers_to_remove:
            if speaker_id in self._handlers:
                self._handlers[speaker_id].finish()
                del self._handlers[speaker_id]
            if speaker_id in self._last_audio_time:
                del self._last_audio_time[speaker_id]

            logger.info(f"Cleaned up idle handler for speaker {speaker_id}")

    def finish_all(self):
        """Close all transcription handlers."""
        for speaker_id, handler in list(self._handlers.items()):
            try:
                handler.finish()
            except Exception as e:
                logger.error(f"Error finishing handler for {speaker_id}: {e}")

        self._handlers.clear()
        self._last_audio_time.clear()
        logger.info("Finished all transcription handlers")
