"""
Tests for webhook delivery.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ["DEEPGRAM_API_KEY"] = "test_key"
os.environ["DEBUG"] = "true"  # Allow HTTP URLs in tests

from core_service.webhook_delivery import (
    _validate_webhook_url,
    deliver_webhook,
    send_bot_status_event,
    send_transcription_event,
)


class TestWebhookUrlValidation:
    """Tests for webhook URL validation."""

    def test_valid_https_url(self):
        """HTTPS URLs should be valid."""
        assert _validate_webhook_url("https://example.com/webhook") is True

    def test_valid_http_url_in_debug_mode(self):
        """HTTP URLs are allowed in debug mode."""
        # DEBUG is set to true in env
        assert _validate_webhook_url("http://example.com/webhook") is True

    def test_blocked_localhost(self):
        """Localhost URLs should be blocked."""
        assert _validate_webhook_url("https://localhost/webhook") is False
        assert _validate_webhook_url("https://127.0.0.1/webhook") is False

    def test_blocked_private_networks(self):
        """Private network URLs should be blocked."""
        assert _validate_webhook_url("https://192.168.1.1/webhook") is False
        assert _validate_webhook_url("https://10.0.0.1/webhook") is False

    def test_invalid_scheme(self):
        """Invalid schemes should be rejected."""
        assert _validate_webhook_url("ftp://example.com/file") is False
        assert _validate_webhook_url("file:///etc/passwd") is False

    def test_missing_hostname(self):
        """URLs without hostname should be rejected."""
        assert _validate_webhook_url("/path/only") is False


@pytest.mark.asyncio
class TestWebhookDelivery:
    """Tests for webhook delivery functions."""

    @patch("core_service.webhook_delivery.httpx.AsyncClient")
    async def test_deliver_webhook_success(self, mock_client_class):
        """Test successful webhook delivery."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await deliver_webhook(
            webhook_url="https://example.com/webhook",
            event_type="test",
            bot_id="bot_123",
            data={"key": "value"},
        )

        assert result is True
        mock_client.post.assert_called_once()

    @patch("core_service.webhook_delivery.httpx.AsyncClient")
    async def test_deliver_webhook_failure_retries(self, mock_client_class):
        """Test webhook delivery retries on failure."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await deliver_webhook(
            webhook_url="https://example.com/webhook",
            event_type="test",
            bot_id="bot_123",
            data={"key": "value"},
            retry_count=2,
        )

        assert result is False
        # Should have been called 3 times (1 initial + 2 retries)
        assert mock_client.post.call_count == 3

    @patch("core_service.webhook_delivery.deliver_webhook")
    async def test_send_transcription_event(self, mock_deliver):
        """Test sending transcription event."""
        mock_deliver.return_value = True

        result = await send_transcription_event(
            webhook_url="https://example.com/webhook",
            bot_id="bot_123",
            speaker_id="speaker_1",
            text="Hello world",
            timestamp_ms=1234567890000,
            duration_ms=1000,
            speaker_name="Test User",
            is_final=True,
        )

        assert result is True
        mock_deliver.assert_called_once()
        call_kwargs = mock_deliver.call_args[1]
        assert call_kwargs["event_type"] == "transcription"
        assert call_kwargs["data"]["text"] == "Hello world"
        assert call_kwargs["data"]["speaker_name"] == "Test User"

    @patch("core_service.webhook_delivery.deliver_webhook")
    async def test_send_bot_status_event(self, mock_deliver):
        """Test sending bot status event."""
        mock_deliver.return_value = True

        result = await send_bot_status_event(
            webhook_url="https://example.com/webhook",
            bot_id="bot_123",
            status="in_meeting",
            message="Bot joined successfully",
        )

        assert result is True
        mock_deliver.assert_called_once()
        call_kwargs = mock_deliver.call_args[1]
        assert call_kwargs["event_type"] == "bot_status"
        assert call_kwargs["data"]["status"] == "in_meeting"
        assert call_kwargs["data"]["message"] == "Bot joined successfully"
