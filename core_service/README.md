# Core Transcription Service

A lightweight FastAPI service for real-time transcription of Microsoft Teams meetings.

## Features

- **Real-time Transcription**: Streams transcription results directly to your webhook URL
- **No Database**: All configuration via environment variables
- **No Redis**: Synchronous webhook delivery with simple retry logic
- **Simple API**: Just 3 endpoints - create bot, get status, leave meeting
- **Deepgram Integration**: Uses Deepgram's streaming API for high-quality transcription

## Quick Start

### Environment Variables

Create a `.env` file with:

```env
# Required
DEEPGRAM_API_KEY=your_deepgram_api_key

# Optional
DEEPGRAM_MODEL=nova-2
DEEPGRAM_LANGUAGE=en
DEFAULT_BOT_NAME=Transcription Bot
API_KEY=your_secret_api_key
DEBUG=false
```

### Running with Docker

```bash
cd core_service
docker compose -f service-compose.yaml up --build
```

### Running Locally

```bash
# Install dependencies
pip install -r core_service/requirements.txt

# Run the service
python -m core_service.main
```

## API Endpoints

### Create Bot

```bash
POST /bots
```

Request:
```json
{
  "meeting_url": "https://teams.microsoft.com/l/meetup-join/...",
  "webhook_url": "https://your-server.com/webhook",
  "bot_name": "My Bot",
  "language": "en"
}
```

Response:
```json
{
  "id": "bot_abc123def456",
  "state": "joining",
  "meeting_url": "https://teams.microsoft.com/...",
  "webhook_url": "https://your-server.com/webhook",
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Get Bot Status

```bash
GET /bots/{bot_id}
```

### Leave Meeting

```bash
POST /bots/{bot_id}/leave
```

## Webhook Events

### Transcription Event

```json
{
  "event_type": "transcription",
  "bot_id": "bot_abc123def456",
  "timestamp": "2024-01-15T10:35:00Z",
  "data": {
    "speaker_id": "participant_123",
    "speaker_name": "John Doe",
    "text": "Hello, this is a test.",
    "timestamp_ms": 1705315700000,
    "duration_ms": 2500,
    "is_final": true
  }
}
```

### Bot Status Event

```json
{
  "event_type": "bot_status",
  "bot_id": "bot_abc123def456",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "status": "in_meeting",
    "message": null
  }
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Service                       │
├─────────────────────────────────────────────────────────┤
│  POST /bots                                              │
│    │                                                     │
│    ▼                                                     │
│  SessionManager (in-memory)                              │
│    │                                                     │
│    ▼                                                     │
│  BotSession                                              │
│    ├── TeamsTranscriptionAdapter                         │
│    │     └── Chrome/Selenium → Teams Meeting             │
│    │                                                     │
│    └── TranscriptionManager                              │
│          └── DeepgramStreamingHandler → Deepgram API     │
│                │                                         │
│                ▼                                         │
│           Webhook Delivery → Your Server                 │
└─────────────────────────────────────────────────────────┘
```

## Configuration Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `DEEPGRAM_API_KEY` | Your Deepgram API key | Required |
| `DEEPGRAM_MODEL` | Deepgram model to use | `nova-2` |
| `DEEPGRAM_LANGUAGE` | Transcription language | `en` |
| `DEEPGRAM_SAMPLE_RATE` | Audio sample rate | `16000` |
| `DEFAULT_BOT_NAME` | Default bot display name | `Transcription Bot` |
| `API_KEY` | Optional API key for auth | None |
| `HOST` | Server host | `0.0.0.0` |
| `PORT` | Server port | `8000` |
| `DEBUG` | Enable debug mode | `false` |
| `WEBHOOK_TIMEOUT_SECONDS` | Webhook request timeout | `30` |
| `WEBHOOK_RETRY_COUNT` | Webhook retry attempts | `3` |
| `DISPLAY_WIDTH` | Virtual display width | `1920` |
| `DISPLAY_HEIGHT` | Virtual display height | `1080` |

## Differences from Main Attendee Service

| Feature | Main Service | Core Service |
|---------|--------------|--------------|
| Framework | Django | FastAPI |
| Database | PostgreSQL | None |
| Queue | Redis/Celery | None |
| Recording | Yes | No |
| Transcription | Yes | Yes |
| Platforms | Zoom, Teams, Meet | Teams only |
| API | Full REST API | 3 endpoints |

## Development

### Running Tests

```bash
# Install dev dependencies
pip install pytest pytest-asyncio

# Run tests
pytest core_service/tests/
```

### Code Style

```bash
# Format code
ruff format core_service/

# Lint
ruff check core_service/
```

## License

See the main repository LICENSE file.
