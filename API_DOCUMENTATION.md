# Novel TTS API - Documentation

**Version:** 1.0.0  
**Base URL:** `http://localhost:8000` (development)  
**Authentication:** Optional API Key via `X-API-Key` header

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Endpoints](#endpoints)
   - [GET /health](#get-health)
   - [GET /voices](#get-voices)
   - [POST /generate](#post-generate)
   - [POST /generate-multi](#post-generate-multi)
   - [POST /preview](#post-preview)
   - [POST /stream](#post-stream)
   - [WebSocket /ws/stream](#websocket-wsstream)
4. [Request Models](#request-models)
5. [Tone Configuration](#tone-configuration)
6. [Error Handling](#error-handling)
7. [Examples](#examples)

---

## Overview

Novel TTS API is a FastAPI-based service that provides text-to-speech functionality using Microsoft Edge TTS. It supports:

- **Batch generation** - Convert entire chapters to MP3
- **Multi-line generation** - Different tones per line (angry, sad, whisper, etc.)
- **Streaming** - Real-time audio streaming via HTTP chunked transfer or WebSocket
- **Glossary support** - Replace words before/after processing (bf_lib/at_lib)
- **Thai number conversion** - Automatically converts numbers to Thai text

---

## Authentication

If `API_KEY` environment variable is set, all endpoints require authentication via the `X-API-Key` header:

```bash
export API_KEY="your-secret-key"
```

**Request with API key:**
```bash
curl -X POST http://localhost:8000/generate \
  -H "X-API-Key: your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"text": "สวัสดี"}'
```

If `API_KEY` is not set, authentication is disabled.

---

## Endpoints

### GET /health

Health check endpoint to verify the API is running.

**Response:**
```json
{
  "status": "ok"
}
```

**Example:**
```bash
curl http://localhost:8000/health
```

---

### GET /voices

List available voices from Edge TTS.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lang` | string | "th" | Language code (th, en, etc.) |
| `gender` | string | "" | Filter by gender ("Male", "Female", or empty for all) |

**Response:**
```json
{
  "voices": [
    {
      "name": "th-TH-PremNeural",
      "gender": "Male",
      "locale": "th-TH",
      "friendly_name": "Prem (Male, Thai)"
    }
  ],
  "count": 1
}
```

**Example:**
```bash
curl "http://localhost:8000/voices?lang=th&gender=Female"
```

---

### POST /generate

Generate MP3 audio from text. Supports tone configuration via `pitch_hz`, `rate_pct`, and `volume_pct`.

**Request Body (TTSRequest):**
```json
{
  "text": "สวัสดีครับ สบายดีไหม?",
  "bf_lib": {"ครับ": "ครับ|"},
  "at_lib": {"สบายดี": "สบายดี"},
  "rate": "+35%",
  "rate_pct": null,
  "pitch_hz": null,
  "volume_pct": null,
  "voice_gender": "Female",
  "voice_name": null,
  "lang": "th"
}
```

**Response Headers:**
| Header | Description |
|--------|-------------|
| `X-Voice` | Voice used for generation |
| `X-Chunks` | Number of chunks processed |
| `X-Chars` | Total characters processed |
| `Content-Type` | `audio/mpeg` |

**Response Body:** Binary MP3 data.

**Example:**
```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "สวัสดีครับ",
    "voice_gender": "Female",
    "rate": "+35%"
  }' \
  --output output.mp3
```

---

### POST /generate-multi

Generate MP3 with different tones per line. Each line can have its own tone configuration.

**Request Body (MultiLineRequest):**
```json
{
  "lines": [
    {
      "text": "ไปที่นั่นซะ!",
      "tone": {
        "tone_name": "angry",
        "pitch_hz": "+20Hz",
        "rate_pct": "+15%",
        "volume_pct": "+10%"
      },
      "voice_gender": "Female",
      "voice_name": null
    },
    {
      "text": "วันนี้ฉันเศร้าใจ",
      "tone": {
        "tone_name": "sad",
        "pitch_hz": "-10Hz",
        "rate_pct": "-20%",
        "volume_pct": "-10%"
      },
      "voice_gender": "Female",
      "voice_name": null
    }
  ],
  "bf_lib": {},
  "at_lib": {},
  "lang": "th"
}
```

**Response Headers:**
| Header | Description |
|--------|-------------|
| `X-Lines` | Number of lines processed |
| `X-Audio-Parts` | Number of audio parts generated |
| `X-Chars` | Total characters processed |

**Response Body:** Binary MP3 data (merged from all lines).

**Example:**
```bash
curl -X POST http://localhost:8000/generate-multi \
  -H "Content-Type: application/json" \
  -d '{
    "lines": [
      {
        "text": "โกรธมาก!",
        "tone": {
          "tone_name": "angry",
          "pitch_hz": "+20Hz",
          "rate_pct": "+15%",
          "volume_pct": "0%"
        },
        "voice_gender": "Female"
      }
    ],
    "lang": "th"
  }' \
  --output multi-output.mp3
```

---

### POST /preview

Generate a short preview (first N characters) for testing voice/tone settings.

**Request Body (PreviewRequest):**
```json
{
  "text": "ข้อความยาวมากๆ ที่ต้องการแค่ทดสอบเสียง...",
  "preview_chars": 300,
  "voice_gender": "Female",
  "rate": "+35%",
  "pitch_hz": "+0Hz",
  "volume_pct": "0%"
}
```

**Response:** MP3 audio of the first `preview_chars` characters.

**Example:**
```bash
curl -X POST http://localhost:8000/preview \
  -H "Content-Type: application/json" \
  -d '{
    "text": "ทดสอบเสียงพูดภาษาไทย",
    "preview_chars": 100
  }' \
  --output preview.mp3
```

---

### POST /stream

Stream MP3 audio via HTTP chunked transfer encoding.

**Request Body:** Same as `/generate` (TTSRequest).

**Response:** Chunked `audio/mpeg` stream.

**JavaScript Example:**
```javascript
const response = await fetch('http://localhost:8000/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    text: 'สวัสดี',
    voice_gender: 'Female'
  })
});

const reader = response.body.getReader();
const audioChunks = [];

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  audioChunks.push(value);
}

const audioBlob = new Blob(audioChunks, { type: 'audio/mpeg' });
const audioUrl = URL.createObjectURL(audioBlob);
```

---

### WebSocket /ws/stream

Stream MP3 audio via WebSocket for real-time playback.

**Protocol:**
1. Client sends JSON config (TTSRequest fields)
2. Server streams binary MP3 chunks
3. Server sends text `"END"` when complete
4. Server sends text `"ERROR: <message>"` on error

**Python Client Example:**
```python
import websockets
import json

async def stream_audio():
    ws = await websockets.connect("ws://localhost:8000/ws/stream")
    
    # Send config
    await ws.send(json.dumps({
        "text": "สวัสดีครับ",
        "voice_gender": "Female",
        "rate": "+35%"
    }))
    
    # Receive audio chunks
    audio_buffer = b""
    async for msg in ws:
        if isinstance(msg, bytes):
            audio_buffer += msg
        elif msg == "END":
            break
        elif msg.startswith("ERROR:"):
            print(f"Error: {msg}")
            break
    
    # Save to file
    with open("output.mp3", "wb") as f:
        f.write(audio_buffer)
```

---

## Request Models

### TTSRequest (Base for /generate, /stream, /preview)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | **required** | Text to synthesize |
| `bf_lib` | Dict[str, str] | `{}` | Pre-processing glossary (word → replacement) |
| `at_lib` | Dict[str, str] | `{}` | Post-processing glossary |
| `rate` | string | `"+35%"` | Speech rate (e.g., `+35%`, `-10%`) |
| `rate_pct` | string | `null` | Override rate (percentage format) |
| `pitch_hz` | string | `null` | Pitch adjustment (e.g., `+0Hz`, `-10Hz`) |
| `volume_pct` | string | `null` | Volume adjustment (e.g., `0`, `+30%`) |
| `voice_gender` | string | `"Female"` | `"Female"` or `"Male"` |
| `voice_name` | string | `null` | Specific voice name (e.g., `th-TH-PremNeural`) |
| `lang` | string | `"th"` | Language code |

### ToneConfig

| Field | Type | Description |
|-------|------|-------------|
| `tone_name` | string | Tone identifier (normal, angry, whisper, sad, excited, fearful, serious, cold) |
| `pitch_hz` | string | Pitch adjustment in Hz (e.g., `+20Hz`, `-10Hz`) |
| `rate_pct` | string | Rate adjustment in percentage (e.g., `+15%`, `-20%`) |
| `volume_pct` | string | Volume adjustment (e.g., `0`, `+30%`, `-50%`) |

### LineAudio

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Text for this line |
| `tone` | ToneConfig | Tone configuration for this line |
| `voice_gender` | string | `"Female"` or `"Male"` |
| `voice_name` | string | Optional specific voice |

### MultiLineRequest

| Field | Type | Description |
|-------|------|-------------|
| `lines` | List[LineAudio] | Array of lines with tone configs |
| `bf_lib` | Dict[str, str] | Global pre-processing glossary |
| `at_lib` | Dict[str, str] | Global post-processing glossary |
| `lang` | string | Language code (default: `"th"`) |

---

## Tone Configuration

### Built-in Tone Presets

| Tone Name | pitch_hz | rate_pct | volume_pct | Description |
|-----------|----------|----------|-------------|-------------|
| `normal` | `+0Hz` | `+0%` | `0` | Standard narration |
| `angry` | `+20Hz` | `+15%` | `+10%` | Loud, fast, higher pitch |
| `whisper` | `-30Hz` | `-30%` | `-50%` | Quiet, slow, lower pitch |
| `sad` | `-10Hz` | `-20%` | `-10%` | Slow, slightly lower pitch |
| `excited` | `+15Hz` | `+25%` | `+20%` | Fast, higher pitch, loud |
| `fearful` | `-5Hz` | `+5%` | `-20%` | Slightly higher, quiet |
| `serious` | `-5Hz` | `-5%` | `+5%` | Slightly slower, deeper |
| `cold` | `-15Hz` | `-10%` | `-30%` | Slow, deep, quiet |

### Custom Tone Example

```json
{
  "tone_name": "custom_happy",
  "pitch_hz": "+10Hz",
  "rate_pct": "+20%",
  "volume_pct": "+15%"
}
```

---

## Error Handling

### HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request (empty text, invalid parameters) |
| 401 | Unauthorized (invalid/missing API key) |
| 500 | Internal Server Error |

### Error Response Format

```json
{
  "detail": "Error message here"
}
```

### Common Errors

**Empty text:**
```json
{
  "detail": "text ไม่ควรว่าง"
}
```

**Invalid API key:**
```json
{
  "detail": "Invalid API key"
}
```

**WebSocket error:**
```
ERROR: <error message>
```

---

## Examples

### Complete Workflow: Generate Multi-Line Audio

```bash
# 1. Check API health
curl http://localhost:8000/health

# 2. List available voices
curl "http://localhost:8000/voices?lang=th"

# 3. Generate multi-line audio with different tones
curl -X POST http://localhost:8000/generate-multi \
  -H "Content-Type: application/json" \
  -d '{
    "lines": [
      {
        "text": "ตื่นเช้ามาเจอเรื่องบ้าๆ!",
        "tone": {
          "tone_name": "angry",
          "pitch_hz": "+20Hz",
          "rate_pct": "+15%",
          "volume_pct": "+10%"
        },
        "voice_gender": "Female"
      },
      {
        "text": "ทำไมถึงเป็นแบบนี้...",
        "tone": {
          "tone_name": "sad",
          "pitch_hz": "-10Hz",
          "rate_pct": "-20%",
          "volume_pct": "-10%"
        },
        "voice_gender": "Female"
      },
      {
        "text": "แต่เดี๋ยวก็ผ่านไป",
        "tone": {
          "tone_name": "normal",
          "pitch_hz": "+0Hz",
          "rate_pct": "+0%",
          "volume_pct": "0%"
        },
        "voice_gender": "Female"
      }
    ],
    "bf_lib": {"บ้าๆ": "บ้าบอ|"},
    "lang": "th"
  }' \
  --output chapter1.mp3

# 4. Play the result
# (Windows)
start chapter1.mp3
# (macOS)
open chapter1.mp3
# (Linux)
xdg-open chapter1.mp3
```

### Python Client Example

```python
import requests
import json

API_URL = "http://localhost:8000"

# Generate single text
def generate_audio(text, voice_gender="Female"):
    response = requests.post(
        f"{API_URL}/generate",
        json={
            "text": text,
            "voice_gender": voice_gender,
            "rate": "+35%"
        }
    )
    response.raise_for_status()
    
    with open("output.mp3", "wb") as f:
        f.write(response.content)
    
    print(f"Generated: {response.headers.get('X-Chars')} chars")

# Generate multi-line with tones
def generate_multi_line(lines):
    response = requests.post(
        f"{API_URL}/generate-multi",
        json={
            "lines": lines,
            "lang": "th"
        }
    )
    response.raise_for_status()
    
    with open("multi-output.mp3", "wb") as f:
        f.write(response.content)
    
    print(f"Generated: {response.headers.get('X-Lines')} lines")

# Usage
generate_audio("สวัสดีครับ นี่คือการทดสอบ")
```

---

## Running the Server

```bash
# Navigate to the API directory
cd d:\Web App Project\translator\novel-tts-api

# Install dependencies
pip install -r requirements.txt

# Run with auto-reload (development)
python main.py

# Run with uvicorn (production)
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

# With API key
export API_KEY="your-secret-key"
python main.py
```

**Default:** Server runs on `http://localhost:8000`

---

## Rate Limiting & Performance

- **Max characters per chunk:** 4,000 (automatic splitting for long text)
- **Supported audio format:** MP3 (MPEG)
- **Thai number conversion:** Automatic (e.g., "123" → "หนึ่งร้อยยี่สิบสาม")
- **Glossary processing:** Applied before (bf_lib) and after (at_lib) text processing

---

## Deployment

### Docker (Optional)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
docker build -t novel-tts-api .
docker run -p 8000:8000 -e API_KEY="your-key" novel-tts-api
```

### Railway (Recommended)

Use the included `railway.toml` for one-click deployment to Railway.

---

**For issues or questions, please refer to the main project repository.**
