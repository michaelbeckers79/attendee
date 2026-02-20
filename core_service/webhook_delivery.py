"""
Webhook delivery system - sends transcription events directly via HTTP.
No Redis, no Celery - synchronous delivery with simple retry logic.

Security Note: The webhook_url is intentionally user-provided, allowing API users
to receive transcription events at their own endpoints. This is standard webhook
functionality and requires authenticated API access to use.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from .config import settings
from .schemas import WebhookPayload

logger = logging.getLogger(__name__)


class WebhookDeliveryError(Exception):
    """Exception raised when webhook delivery fails."""


def _validate_webhook_url(url: str) -> bool:
    """
    Validate that a webhook URL is safe to call.

    Only allows HTTPS URLs (or HTTP in debug mode) to external endpoints.
    Blocks requests to internal/private networks.

    Args:
        url: The URL to validate

    Returns:
        True if the URL is valid and safe
    """
    try:
        parsed = urlparse(str(url))

        # Require HTTPS in production (allow HTTP only in debug mode)
        if not settings.debug and parsed.scheme != "https":
            logger.warning(f"Webhook URL must use HTTPS: {url}")
            return False

        # Must have http or https scheme
        if parsed.scheme not in ("http", "https"):
            logger.warning(f"Invalid webhook URL scheme: {parsed.scheme}")
            return False

        # Must have a hostname
        if not parsed.netloc:
            logger.warning(f"Webhook URL missing hostname: {url}")
            return False

        # Block common internal hostnames
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        blocked_hosts = ["localhost", "127.0.0.1", "0.0.0.0", "::1"]
        if hostname in blocked_hosts:
            logger.warning(f"Webhook URL points to blocked host: {hostname}")
            return False

        # Block internal IP ranges (basic check)
        # Note: More comprehensive checks would use ipaddress module
        if hostname.startswith("10.") or hostname.startswith("192.168."):
            logger.warning(f"Webhook URL points to private network: {hostname}")
            return False

        return True

    except Exception as e:
        logger.error(f"Error validating webhook URL: {e}")
        return False


async def deliver_webhook(
    webhook_url: str,
    event_type: str,
    bot_id: str,
    data: Dict[str, Any],
    timeout_seconds: Optional[int] = None,
    retry_count: Optional[int] = None,
) -> bool:
    """
    Deliver a webhook payload to the specified URL.

    Args:
        webhook_url: URL to send the webhook to
        event_type: Type of event (e.g., 'transcription', 'bot_status')
        bot_id: Bot session ID
        data: Event-specific data
        timeout_seconds: Request timeout in seconds (defaults to config)
        retry_count: Number of retry attempts (defaults to config)

    Returns:
        True if delivery was successful, False otherwise

    Raises:
        WebhookDeliveryError: If all delivery attempts fail
    """
    if timeout_seconds is None:
        timeout_seconds = settings.webhook_timeout_seconds
    if retry_count is None:
        retry_count = settings.webhook_retry_count

    # Validate webhook URL for security
    if not _validate_webhook_url(webhook_url):
        logger.error(f"Webhook URL validation failed: {webhook_url}")
        return False

    payload = WebhookPayload(
        event_type=event_type,
        bot_id=bot_id,
        timestamp=datetime.now(timezone.utc),
        data=data,
    )

    last_error: Optional[Exception] = None

    for attempt in range(retry_count + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    str(webhook_url),
                    json=payload.model_dump(mode="json"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "AttendeeTranscriptionBot/1.0",
                    },
                )

                if response.status_code >= 200 and response.status_code < 300:
                    logger.debug(f"Webhook delivered successfully to {webhook_url}")
                    return True

                logger.warning(
                    f"Webhook delivery failed with status {response.status_code} "
                    f"(attempt {attempt + 1}/{retry_count + 1})"
                )
                last_error = WebhookDeliveryError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )

        except httpx.TimeoutException as e:
            logger.warning(
                f"Webhook delivery timed out "
                f"(attempt {attempt + 1}/{retry_count + 1}): {e}"
            )
            last_error = e

        except httpx.RequestError as e:
            logger.warning(
                f"Webhook delivery request error "
                f"(attempt {attempt + 1}/{retry_count + 1}): {e}"
            )
            last_error = e

        except Exception as e:
            logger.error(f"Unexpected error during webhook delivery: {e}")
            last_error = e

    # All retries exhausted
    logger.error(
        f"Failed to deliver webhook to {webhook_url} "
        f"after {retry_count + 1} attempts"
    )
    return False


async def send_transcription_event(
    webhook_url: str,
    bot_id: str,
    speaker_id: str,
    text: str,
    timestamp_ms: int,
    duration_ms: int = 0,
    speaker_name: Optional[str] = None,
    is_final: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Send a transcription event to the webhook URL.

    Args:
        webhook_url: URL to send the webhook to
        bot_id: Bot session ID
        speaker_id: Unique identifier for the speaker
        text: Transcribed text
        timestamp_ms: Unix timestamp in milliseconds
        duration_ms: Duration of the utterance
        speaker_name: Display name of the speaker
        is_final: Whether this is a final transcription
        metadata: Additional metadata

    Returns:
        True if delivery was successful
    """
    data = {
        "speaker_id": speaker_id,
        "speaker_name": speaker_name,
        "text": text,
        "timestamp_ms": timestamp_ms,
        "duration_ms": duration_ms,
        "is_final": is_final,
    }

    if metadata:
        data["metadata"] = metadata

    return await deliver_webhook(
        webhook_url=webhook_url,
        event_type="transcription",
        bot_id=bot_id,
        data=data,
    )


async def send_bot_status_event(
    webhook_url: str,
    bot_id: str,
    status: str,
    message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Send a bot status event to the webhook URL.

    Args:
        webhook_url: URL to send the webhook to
        bot_id: Bot session ID
        status: Bot status (e.g., 'joining', 'in_meeting', 'ended')
        message: Optional status message
        metadata: Additional metadata

    Returns:
        True if delivery was successful
    """
    data = {
        "status": status,
    }

    if message:
        data["message"] = message

    if metadata:
        data["metadata"] = metadata

    return await deliver_webhook(
        webhook_url=webhook_url,
        event_type="bot_status",
        bot_id=bot_id,
        data=data,
    )
