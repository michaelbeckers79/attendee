"""
Tests for session manager.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ["DEEPGRAM_API_KEY"] = "test_key"

from core_service.schemas import BotState
from core_service.session_manager import BotSession, SessionManager


@pytest.fixture(autouse=True)
def reset_session_manager():
    """Reset session manager singleton between tests."""
    manager = SessionManager.get_instance()
    manager._sessions.clear()
    yield
    manager._sessions.clear()


class TestBotSession:
    """Tests for BotSession class."""

    def test_session_creation(self):
        """Test creating a bot session."""
        session = BotSession(
            session_id="bot_test123",
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
            bot_name="Test Bot",
            language="en",
            metadata={"key": "value"},
        )

        assert session.id == "bot_test123"
        assert session.state == BotState.PENDING
        assert session.meeting_url == "https://teams.microsoft.com/l/meetup-join/abc"
        assert session.webhook_url == "https://example.com/webhook"
        assert session.bot_name == "Test Bot"
        assert session.metadata == {"key": "value"}

    @patch("core_service.session_manager.send_bot_status_event")
    @pytest.mark.asyncio
    async def test_set_state(self, mock_send):
        """Test setting session state."""
        mock_send.return_value = True

        session = BotSession(
            session_id="bot_test123",
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
            bot_name="Test Bot",
        )

        await session.set_state(BotState.IN_MEETING)

        assert session.state == BotState.IN_MEETING
        mock_send.assert_called_once()

    @patch("core_service.session_manager.send_bot_status_event")
    @pytest.mark.asyncio
    async def test_set_state_ended_sets_ended_at(self, mock_send):
        """Test that setting ENDED state sets ended_at."""
        mock_send.return_value = True

        session = BotSession(
            session_id="bot_test123",
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
            bot_name="Test Bot",
        )

        assert session.ended_at is None
        await session.set_state(BotState.ENDED)

        assert session.state == BotState.ENDED
        assert session.ended_at is not None

    def test_to_response(self):
        """Test converting session to response model."""
        session = BotSession(
            session_id="bot_test123",
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
            bot_name="Test Bot",
        )

        response = session.to_response()

        assert response.id == "bot_test123"
        assert response.state == BotState.PENDING
        assert response.meeting_url == "https://teams.microsoft.com/l/meetup-join/abc"

    def test_update_participant(self):
        """Test updating participant info."""
        session = BotSession(
            session_id="bot_test123",
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
            bot_name="Test Bot",
        )

        session.update_participant("p1", "John Doe", is_host=True)

        participant = session.get_participant("p1")
        assert participant is not None
        assert participant["participant_full_name"] == "John Doe"
        assert participant["is_host"] is True


class TestSessionManager:
    """Tests for SessionManager class."""

    def test_singleton(self):
        """Test that SessionManager is a singleton."""
        manager1 = SessionManager.get_instance()
        manager2 = SessionManager.get_instance()
        assert manager1 is manager2

    def test_create_session(self):
        """Test creating a session."""
        manager = SessionManager.get_instance()

        session = manager.create_session(
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
            bot_name="Test Bot",
            language="en",
        )

        assert session.id.startswith("bot_")
        assert session.meeting_url == "https://teams.microsoft.com/l/meetup-join/abc"

    def test_get_session(self):
        """Test getting a session by ID."""
        manager = SessionManager.get_instance()

        session = manager.create_session(
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
        )

        retrieved = manager.get_session(session.id)
        assert retrieved is session

    def test_get_session_not_found(self):
        """Test getting non-existent session returns None."""
        manager = SessionManager.get_instance()
        assert manager.get_session("bot_nonexistent") is None

    @patch("core_service.session_manager.send_bot_status_event")
    @pytest.mark.asyncio
    async def test_end_session(self, mock_send):
        """Test ending a session."""
        mock_send.return_value = True
        manager = SessionManager.get_instance()

        session = manager.create_session(
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
        )

        ended = await manager.end_session(session.id)
        assert ended is session
        assert session.state == BotState.ENDED

    def test_remove_session(self):
        """Test removing a session from memory."""
        manager = SessionManager.get_instance()

        session = manager.create_session(
            meeting_url="https://teams.microsoft.com/l/meetup-join/abc",
            webhook_url="https://example.com/webhook",
        )

        session_id = session.id
        manager.remove_session(session_id)

        assert manager.get_session(session_id) is None
