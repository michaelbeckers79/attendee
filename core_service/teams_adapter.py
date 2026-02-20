"""
Simplified Teams bot adapter for transcription only.
No recording, no video - just audio capture and transcription.
"""

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .config import settings

logger = logging.getLogger(__name__)

# Dynamically check for Selenium dependencies
try:
    from pyvirtualdisplay import Display
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from websockets.sync.server import serve

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logger.warning("Selenium dependencies not available. Bot adapter will not work.")


class TeamsTranscriptionAdapter:
    """
    A simplified Teams adapter focused on transcription only.
    Uses Selenium to join meetings and capture audio via WebSocket.
    """

    def __init__(
        self,
        *,
        display_name: str,
        meeting_url: str,
        add_audio_chunk_callback: Callable[[str, bytes, Optional[str]], None],
        on_meeting_ended_callback: Callable[[], None],
        on_participant_joined_callback: Optional[Callable[[str, str], None]] = None,
        on_participant_left_callback: Optional[Callable[[str, str], None]] = None,
    ):
        """
        Initialize the Teams adapter.

        Args:
            display_name: Bot display name in the meeting
            meeting_url: Teams meeting URL to join
            add_audio_chunk_callback: Called with (speaker_id, audio_bytes, speaker_name)
            on_meeting_ended_callback: Called when meeting ends
            on_participant_joined_callback: Called with (participant_id, participant_name)
            on_participant_left_callback: Called with (participant_id, participant_name)
        """
        if not SELENIUM_AVAILABLE:
            raise RuntimeError(
                "Selenium dependencies not available. "
                "Install with: pip install selenium pyvirtualdisplay websockets"
            )

        self.display_name = display_name
        self.meeting_url = meeting_url
        self.add_audio_chunk_callback = add_audio_chunk_callback
        self.on_meeting_ended_callback = on_meeting_ended_callback
        self.on_participant_joined_callback = on_participant_joined_callback
        self.on_participant_left_callback = on_participant_left_callback

        self.driver: Optional[webdriver.Chrome] = None
        self.display: Optional[Display] = None
        self.websocket_server = None
        self.websocket_thread: Optional[threading.Thread] = None

        self._running = False
        self._joined = False
        self._participants: Dict[str, Dict[str, Any]] = {}

        # WebSocket port for receiving audio from browser
        self.websocket_port = 8097

    def _setup_display(self):
        """Set up virtual display for headless operation."""
        try:
            self.display = Display(
                visible=False,
                size=(settings.display_width, settings.display_height),
            )
            self.display.start()
            logger.info(
                f"Started virtual display {settings.display_width}x{settings.display_height}"
            )
        except Exception as e:
            logger.warning(f"Could not start virtual display: {e}")

    def _setup_chrome(self):
        """Set up Chrome WebDriver with necessary options."""
        options = webdriver.ChromeOptions()

        # Essential options for Teams
        options.add_argument("--use-fake-ui-for-media-stream")  # Auto-allow mic/camera
        options.add_argument("--use-fake-device-for-media-stream")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument(
            f"--window-size={settings.display_width},{settings.display_height}"
        )

        # Disable various Chrome features that aren't needed
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-translate")

        # Audio settings
        options.add_argument("--autoplay-policy=no-user-gesture-required")

        # Set user agent
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Create service
        service_kwargs = {}
        if settings.chrome_driver_path:
            service_kwargs["executable_path"] = settings.chrome_driver_path

        service = Service(**service_kwargs)

        self.driver = webdriver.Chrome(service=service, options=options)
        logger.info("Chrome WebDriver initialized")

    def _handle_websocket_message(self, message: bytes):
        """Process incoming WebSocket message from browser."""
        try:
            if len(message) < 5:
                return

            # Parse message type (first byte)
            msg_type = message[0]

            if msg_type == 1:  # Audio data
                self._handle_audio_message(message)
            elif msg_type == 2:  # Participant update
                self._handle_participant_message(message)
            elif msg_type == 3:  # Meeting ended
                self._handle_meeting_ended()
            elif msg_type == 4:  # Caption/transcription from platform
                self._handle_caption_message(message)

        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")

    def _handle_audio_message(self, message: bytes):
        """Handle incoming audio data."""
        try:
            # Format: [type][speaker_id_len][speaker_id][audio_data]
            speaker_id_len = message[1]
            speaker_id = message[2 : 2 + speaker_id_len].decode("utf-8")
            audio_data = message[2 + speaker_id_len :]

            speaker_name = self._participants.get(speaker_id, {}).get("name")

            self.add_audio_chunk_callback(speaker_id, audio_data, speaker_name)

        except Exception as e:
            logger.error(f"Error handling audio message: {e}")

    def _handle_participant_message(self, message: bytes):
        """Handle participant join/leave events."""
        try:
            # Format: [type][json_data]
            data = json.loads(message[1:].decode("utf-8"))
            participant_id = data.get("id")
            participant_name = data.get("name", "Unknown")
            event_type = data.get("event")  # "join" or "leave"

            if event_type == "join":
                self._participants[participant_id] = {"name": participant_name}
                if self.on_participant_joined_callback:
                    self.on_participant_joined_callback(participant_id, participant_name)
                logger.info(f"Participant joined: {participant_name}")

            elif event_type == "leave":
                if participant_id in self._participants:
                    del self._participants[participant_id]
                if self.on_participant_left_callback:
                    self.on_participant_left_callback(participant_id, participant_name)
                logger.info(f"Participant left: {participant_name}")

        except Exception as e:
            logger.error(f"Error handling participant message: {e}")

    def _handle_meeting_ended(self):
        """Handle meeting ended event."""
        logger.info("Meeting ended signal received")
        self._running = False
        self.on_meeting_ended_callback()

    def _handle_caption_message(self, message: bytes):
        """Handle platform closed captions."""
        # Platform captions could be used as a fallback
        # For now, we rely on Deepgram for transcription
        pass

    def _websocket_handler(self, websocket):
        """Handle WebSocket connections from browser."""
        logger.info("WebSocket client connected")
        try:
            for message in websocket:
                if not self._running:
                    break
                if isinstance(message, bytes):
                    self._handle_websocket_message(message)
        except Exception as e:
            logger.error(f"WebSocket handler error: {e}")
        finally:
            logger.info("WebSocket client disconnected")

    def _start_websocket_server(self):
        """Start the WebSocket server for browser communication."""
        def run_server():
            try:
                with serve(
                    self._websocket_handler,
                    "localhost",
                    self.websocket_port,
                ) as server:
                    self.websocket_server = server
                    logger.info(f"WebSocket server started on port {self.websocket_port}")
                    # Use poll_interval to periodically check if we should stop
                    while self._running:
                        server.serve_forever(poll_interval=0.1)
                        break  # serve_forever returns when shutdown() is called
            except Exception as e:
                logger.error(f"WebSocket server error: {e}")

        self.websocket_thread = threading.Thread(target=run_server, daemon=True)
        self.websocket_thread.start()

    def _inject_audio_capture_script(self):
        """
        Inject JavaScript to capture audio and send via WebSocket.

        NOTE: This is a minimal stub implementation. The full implementation
        requires the complete JavaScript payload from teams_chromedriver_payload.js
        in the main bots module to properly capture per-participant audio streams.

        For production use, consider:
        1. Copying teams_chromedriver_payload.js to this service
        2. Or reusing the WebBotAdapter from the main bots module
        """
        # Basic WebSocket connection for the adapter
        # The actual audio capture requires more sophisticated JS injection
        script = """
        (function() {
            // Connect to WebSocket server
            const ws = new WebSocket('ws://localhost:""" + str(self.websocket_port) + """');

            ws.onopen = () => {
                console.log('Audio capture WebSocket connected');
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };

            // Store reference for cleanup
            window.audioCaptureWs = ws;

            // Note: Full audio capture implementation requires additional code
            // to intercept WebRTC audio streams and send to the WebSocket
        })();
        """
        self.driver.execute_script(script)

    async def join_meeting(self) -> bool:
        """
        Join the Teams meeting.

        Returns:
            True if successfully joined, False otherwise
        """
        try:
            self._running = True

            # Setup display and browser
            self._setup_display()
            self._setup_chrome()

            # Start WebSocket server for audio capture
            self._start_websocket_server()

            # Navigate to meeting URL
            logger.info(f"Navigating to meeting: {self.meeting_url}")
            self.driver.get(self.meeting_url)

            # Wait for page to load
            await asyncio.sleep(3)

            # Click "Continue on this browser" if present
            try:
                continue_btn = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(text(), 'Continue on this browser')]")
                    )
                )
                continue_btn.click()
                await asyncio.sleep(2)
            except Exception:
                logger.debug("'Continue on this browser' button not found")

            # Enter display name
            try:
                name_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "username"))
                )
                name_input.clear()
                name_input.send_keys(self.display_name)
                await asyncio.sleep(1)
            except Exception:
                logger.debug("Name input not found")

            # Turn off camera and mic before joining
            try:
                # Find and click camera toggle
                camera_btn = self.driver.find_element(
                    By.CSS_SELECTOR, "[data-tid='toggle-video']"
                )
                if "on" in camera_btn.get_attribute("aria-label").lower():
                    camera_btn.click()
                    await asyncio.sleep(0.5)

                # Find and click mic toggle
                mic_btn = self.driver.find_element(
                    By.CSS_SELECTOR, "[data-tid='toggle-mute']"
                )
                if "unmute" not in mic_btn.get_attribute("aria-label").lower():
                    mic_btn.click()
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.debug(f"Could not toggle camera/mic: {e}")

            # Click join button
            try:
                join_btn = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, "[data-tid='prejoin-join-button']")
                    )
                )
                join_btn.click()
                logger.info("Clicked join button")
            except Exception:
                # Try alternative join button selectors
                try:
                    join_btn = self.driver.find_element(
                        By.XPATH, "//button[contains(text(), 'Join now')]"
                    )
                    join_btn.click()
                except Exception:
                    logger.error("Could not find join button")
                    return False

            # Wait for meeting to load
            await asyncio.sleep(5)

            # Inject audio capture script
            self._inject_audio_capture_script()

            self._joined = True
            logger.info("Successfully joined Teams meeting")
            return True

        except Exception as e:
            logger.error(f"Failed to join meeting: {e}")
            return False

    async def leave_meeting(self):
        """Leave the current meeting."""
        try:
            self._running = False

            if self.driver:
                # Try to click leave button
                try:
                    leave_btn = self.driver.find_element(
                        By.CSS_SELECTOR, "[data-tid='hangup-button']"
                    )
                    leave_btn.click()
                    await asyncio.sleep(2)
                except Exception:
                    pass

            await self.cleanup()
            logger.info("Left Teams meeting")

        except Exception as e:
            logger.error(f"Error leaving meeting: {e}")

    async def cleanup(self):
        """Clean up all resources."""
        self._running = False

        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

        if self.display:
            try:
                self.display.stop()
            except Exception:
                pass
            self.display = None

        if self.websocket_server:
            try:
                self.websocket_server.shutdown()
            except Exception:
                pass
            self.websocket_server = None

        logger.info("Cleaned up adapter resources")

    @property
    def is_joined(self) -> bool:
        """Check if currently in a meeting."""
        return self._joined and self._running
