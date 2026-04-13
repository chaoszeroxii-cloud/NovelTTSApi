# Novel TTS API

Edge TTS สำหรับนิยาย — deploy บน cloud แล้วเรียกจาก text editor

## Endpoints

| Method | Path | ใช้ทำอะไร |
|--------|------|----------|
| GET | `/health` | Health check |
| GET | `/voices` | List voices (filter by lang, gender) |
| POST | `/generate` | **Batch** — รับข้อความทั้งตอน คืน MP3 file |
| POST | `/stream` | **HTTP Stream** — stream MP3 bytes แบบ real-time |
| WS | `/ws/stream` | **WebSocket Stream** — เหมาะกับ desktop app |
| POST | `/preview` | Preview เสียงด้วยข้อความสั้น |

---

## Deploy

### Railway (แนะนำ — ง่ายสุด)

```bash
# 1. สร้าง project บน railway.app
# 2. link กับ GitHub repo
# 3. set environment variables:

API_KEY=your-secret-key   # optional แต่แนะนำ

# Railway จะ build Dockerfile อัตโนมัติ
```

### Render

```bash
# Build Command: pip install -r requirements.txt
# Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
# Environment: API_KEY=your-secret-key
```

### Google Cloud Run

```bash
gcloud builds submit --tag gcr.io/PROJECT/novel-tts
gcloud run deploy novel-tts \
  --image gcr.io/PROJECT/novel-tts \
  --platform managed \
  --region asia-southeast1 \
  --set-env-vars API_KEY=your-key \
  --allow-unauthenticated \
  --memory 512Mi
```

### Local dev

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# เปิด http://localhost:8000/docs สำหรับ Swagger UI
```

---

## API Request Format

### POST /generate

```json
{
  "text": "ข้อความทั้งตอน...",
  "bf_lib": { "魔法師": "นักเวทย์" },
  "at_lib": { "หนึ่ง|ร้อย|": "ร้อย|" },
  "rate": "+35%",
  "voice_gender": "Female",
  "voice_name": null,
  "lang": "th"
}
```

Response: `audio/mpeg` binary  
Headers: `X-Voice`, `X-Chunks`, `X-Chars`

### POST /stream (HTTP Chunked)

Same body → chunked `audio/mpeg` stream

### WS /ws/stream

1. Client เชื่อมต่อ WebSocket
2. ส่ง JSON config (เหมือน /generate + `"api_key"`)
3. รับ binary MP3 chunks จนกว่าจะได้ `"END"` text

---

## Smart Lib Filtering (ฝั่ง client)

```python
def filter_lib_for_text(text: str, full_lib: dict) -> dict:
    """ส่งแค่ key ที่ปรากฎในข้อความ ลด payload"""
    return {k: v for k, v in full_lib.items() if k in text}
```

ส่ง `filter_lib_for_text(chapter_text, full_bf_lib)` แทน full lib — ทำให้ request เล็กลงมาก

---

## Flow ใน Text Editor

```
user แปลข้อความ
        ↓
กด "ฟังเสียง" / "บันทึกเสียง"
        ↓
client filter bf_lib + at_lib เฉพาะคำในบท
        ↓
POST /generate (batch) หรือ WS /ws/stream (real-time)
        ↓
รับ MP3 → เล่น หรือ บันทึกไฟล์
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | (ว่าง = ไม่ require) | Secret key สำหรับ auth |
| `PORT` | 8000 | Port (Railway inject อัตโนมัติ) |
"# NovelTTSApi" 
