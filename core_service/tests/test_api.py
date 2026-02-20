"""
Tests for the core transcription service API.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Set test environment variables before importing
os.environ["DEEPGRAM_API_KEY"] = "test_key"
os.environ["API_KEY"] = ""  # Disable auth for tests

from core_service.main import app
from core_service.schemas import BotState
from core_service.session_manager import SessionManager


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_session_manager():
    """Reset session manager between tests."""
    manager = SessionManager.get_instance()
    manager._sessions.clear()
    yield
    manager._sessions.clear()


class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_check(self, client):
        """Test health check returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "active_sessions" in data


class TestCreateBot:
    """Tests for the create bot endpoint."""

    def test_create_bot_invalid_url(self, client):
        """Test that non-Teams URLs are rejected."""
        response = client.post(
            "/bots",
            json={
                "meeting_url": "https://zoom.us/j/123456",
                "webhook_url": "https://example.com/webhook",
            },
        )
        assert response.status_code == 400
        assert "Invalid Teams meeting URL" in response.json()["detail"]

    @patch("core_service.main.run_bot_session")
    @patch("core_service.main.asyncio.create_task")
    def test_create_bot_valid_request(self, mock_create_task, mock_run, client):
        """Test creating a bot with valid request."""
        mock_run.return_value = None

        response = client.post(
            "/bots",
            json={
                "meeting_url": "https://teams.microsoft.com/l/meetup-join/abc123",
                "webhook_url": "https://example.com/webhook",
                "bot_name": "Test Bot",
                "language": "en",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "joining"
        assert data["meeting_url"] == "https://teams.microsoft.com/l/meetup-join/abc123"
        assert data["webhook_url"] == "https://example.com/webhook"
        assert data["id"].startswith("bot_")

    def test_create_bot_missing_required_fields(self, client):
        """Test that missing required fields return 422."""
        response = client.post(
            "/bots",
            json={
                "meeting_url": "https://teams.microsoft.com/l/meetup-join/abc123",
                # missing webhook_url
            },
        )
        assert response.status_code == 422


class TestGetBot:
    """Tests for the get bot status endpoint."""

    def test_get_bot_not_found(self, client):
        """Test getting non-existent bot returns 404."""
        response = client.get("/bots/bot_nonexistent123")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @patch("core_service.main.run_bot_session")
    @patch("core_service.main.asyncio.create_task")
    def test_get_bot_exists(self, mock_create_task, mock_run, client):
        """Test getting existing bot returns status."""
        # Create a bot first
        mock_run.return_value = None
        create_response = client.post(
            "/bots",
            json={
                "meeting_url": "https://teams.microsoft.com/l/meetup-join/abc123",
                "webhook_url": "https://example.com/webhook",
            },
        )
        bot_id = create_response.json()["id"]

        # Get the bot
        response = client.get(f"/bots/{bot_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == bot_id


class TestLeaveBot:
    """Tests for the leave meeting endpoint."""

    def test_leave_bot_not_found(self, client):
        """Test leaving non-existent bot returns 404."""
        response = client.post("/bots/bot_nonexistent123/leave")
        assert response.status_code == 404

    @patch("core_service.main.run_bot_session")
    @patch("core_service.main.asyncio.create_task")
    def test_leave_bot_success(self, mock_create_task, mock_run, client):
        """Test leaving meeting works."""
        mock_run.return_value = None

        # Create a bot first
        create_response = client.post(
            "/bots",
            json={
                "meeting_url": "https://teams.microsoft.com/l/meetup-join/abc123",
                "webhook_url": "https://example.com/webhook",
            },
        )
        bot_id = create_response.json()["id"]

        # Leave the meeting
        response = client.post(f"/bots/{bot_id}/leave")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] in ["leaving", "ended"]


class TestApiKeyAuth:
    """Tests for API key authentication."""

    def test_auth_disabled_when_no_key_configured(self, client):
        """Test that auth is disabled when API_KEY is not set."""
        # API_KEY is set to empty string in fixture
        response = client.get("/health")
        assert response.status_code == 200

    def test_auth_required_when_key_configured(self):
        """Test that auth is required when API_KEY is set."""
        from core_service import config

        # Temporarily set the API key in the settings object
        original_api_key = config.settings.api_key
        config.settings.api_key = "test_secret_key"

        try:
            # Need to create a new client to use updated settings
            client = TestClient(app)

            # Request without auth header should fail
            response = client.post(
                "/bots",
                json={
                    "meeting_url": "https://teams.microsoft.com/l/meetup-join/abc123",
                    "webhook_url": "https://example.com/webhook",
                },
            )
            assert response.status_code == 401

            # Request with wrong auth header should fail
            response = client.post(
                "/bots",
                json={
                    "meeting_url": "https://teams.microsoft.com/l/meetup-join/abc123",
                    "webhook_url": "https://example.com/webhook",
                },
                headers={"Authorization": "Token wrong_key"},
            )
            assert response.status_code == 401

            # Request with correct auth header should work (but may fail for other reasons)
            response = client.post(
                "/bots",
                json={
                    "meeting_url": "https://teams.microsoft.com/l/meetup-join/abc123",
                    "webhook_url": "https://example.com/webhook",
                },
                headers={"Authorization": "Token test_secret_key"},
            )
            # Should not be 401
            assert response.status_code != 401
        finally:
            # Clean up - restore original value
            config.settings.api_key = original_api_key
