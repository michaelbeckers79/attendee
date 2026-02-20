"""
Core FastAPI service for Teams meeting transcription.

This service provides a simple API to:
1. Create a bot that joins a Teams meeting
2. Stream real-time transcription to a webhook URL
3. End the bot session when the meeting ends

No database, no Redis - pure stateless operation with environment config.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .schemas import (
    BotResponse,
    BotState,
    CreateBotRequest,
    ErrorResponse,
    LeaveMeetingRequest,
)
from .session_manager import BotSession, SessionManager

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting core transcription service...")
    yield
    logger.info("Shutting down core transcription service...")

    # Cleanup all active sessions
    session_manager = SessionManager.get_instance()
    for session_id in list(session_manager.get_all_sessions().keys()):
        await session_manager.end_session(session_id)


# Create FastAPI app
app = FastAPI(
    title="Teams Meeting Transcription Service",
    description=(
        "A lightweight FastAPI service for real-time transcription of "
        "Microsoft Teams meetings. Sends transcription results directly "
        "to a webhook URL without database or Redis dependencies."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_api_key(authorization: Optional[str] = Header(None)) -> bool:
    """
    Verify API key from Authorization header.

    If no API key is configured, authentication is disabled.
    """
    if not settings.api_key:
        return True

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    # Support "Token <key>" and "Bearer <key>" formats
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() not in ("token", "bearer"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
        )

    if parts[1] != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return True


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    session_manager = SessionManager.get_instance()
    return {
        "status": "healthy",
        "active_sessions": session_manager.get_active_session_count(),
    }


@app.post(
    "/bots",
    response_model=BotResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Create a transcription bot",
    description="Create a bot that joins a Teams meeting and streams transcription to a webhook URL.",
)
async def create_bot(
    request: CreateBotRequest,
    _: bool = Depends(verify_api_key),
) -> BotResponse:
    """
    Create a new transcription bot for a Teams meeting.

    The bot will:
    1. Join the specified Teams meeting
    2. Capture audio from participants
    3. Stream real-time transcription to the webhook URL
    4. Automatically end when the meeting ends
    """
    # Validate Deepgram API key is configured
    if not settings.deepgram_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deepgram API key not configured",
        )

    # Validate Teams meeting URL
    if "teams.microsoft.com" not in request.meeting_url.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Teams meeting URL",
        )

    try:
        session_manager = SessionManager.get_instance()

        # Create the bot session
        session = session_manager.create_session(
            meeting_url=request.meeting_url,
            webhook_url=str(request.webhook_url),
            bot_name=request.bot_name,
            language=request.language or "en",
            metadata=request.metadata,
        )

        # Start the bot in a background task
        asyncio.create_task(run_bot_session(session))

        # Update state to joining
        await session.set_state(BotState.JOINING)

        return session.to_response()

    except Exception as e:
        logger.exception(f"Failed to create bot: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create bot: {str(e)}",
        )


@app.get(
    "/bots/{bot_id}",
    response_model=BotResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Get bot status",
    description="Get the current status of a transcription bot.",
)
async def get_bot(
    bot_id: str,
    _: bool = Depends(verify_api_key),
) -> BotResponse:
    """Get the status of an existing bot session."""
    session_manager = SessionManager.get_instance()
    session = session_manager.get_session(bot_id)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot {bot_id} not found",
        )

    return session.to_response()


@app.post(
    "/bots/{bot_id}/leave",
    response_model=BotResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Make bot leave meeting",
    description="Request the bot to leave the meeting and end the session.",
)
async def leave_meeting(
    bot_id: str,
    request: LeaveMeetingRequest = None,
    _: bool = Depends(verify_api_key),
) -> BotResponse:
    """Request the bot to leave the meeting."""
    session_manager = SessionManager.get_instance()
    session = session_manager.get_session(bot_id)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot {bot_id} not found",
        )

    if session.state in (BotState.ENDED, BotState.FAILED):
        # Already ended
        return session.to_response()

    # Update state to leaving
    await session.set_state(BotState.LEAVING)

    # End the session
    await session_manager.end_session(bot_id)

    return session.to_response()


async def run_bot_session(session: BotSession):
    """
    Run the bot session in the background.

    This handles:
    1. Joining the meeting
    2. Capturing audio and streaming transcription
    3. Handling meeting end
    """
    try:
        # Import here to avoid issues if selenium not installed
        from .teams_adapter import TeamsTranscriptionAdapter, SELENIUM_AVAILABLE

        if not SELENIUM_AVAILABLE:
            await session.set_state(
                BotState.FAILED,
                "Selenium dependencies not available",
            )
            return

        # Create the adapter
        adapter = TeamsTranscriptionAdapter(
            display_name=session.bot_name,
            meeting_url=session.meeting_url,
            add_audio_chunk_callback=lambda speaker_id, audio, name: session.add_audio_chunk(
                speaker_id, audio, name
            ),
            on_meeting_ended_callback=lambda: asyncio.create_task(
                on_meeting_ended(session)
            ),
            on_participant_joined_callback=lambda pid, name: session.update_participant(
                pid, name
            ),
            on_participant_left_callback=None,
        )

        # Join the meeting
        success = await adapter.join_meeting()

        if not success:
            await session.set_state(BotState.FAILED, "Failed to join meeting")
            return

        # Update state to in_meeting
        await session.set_state(BotState.IN_MEETING)

        # Keep running until session ends
        while session.state == BotState.IN_MEETING:
            await asyncio.sleep(1)

            # Cleanup idle transcription handlers periodically
            if session._transcription_manager:
                session._transcription_manager.cleanup_idle_handlers()

        # Leave the meeting
        await adapter.leave_meeting()

    except Exception as e:
        logger.exception(f"Bot session error: {e}")
        await session.set_state(BotState.FAILED, str(e))


async def on_meeting_ended(session: BotSession):
    """Handle meeting ended event."""
    if session.state != BotState.IN_MEETING:
        return

    logger.info(f"Meeting ended for bot {session.id}")
    await session.cleanup()
    await session.set_state(BotState.ENDED, "Meeting ended")


def main():
    """Run the FastAPI application."""
    import uvicorn

    uvicorn.run(
        "core_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
