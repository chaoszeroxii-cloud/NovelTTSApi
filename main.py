#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Novel TTS API — FastAPI
Endpoints:
  POST /generate          → batch MP3 file (รับทั้งตอน คืน MP3)
  POST /stream            → chunked HTTP stream (real-time audio)
  WebSocket /ws/stream    → WebSocket binary stream
  GET  /voices            → list available voices
  POST /preview           → preview สั้น (200 chars แรก)
  GET  /health            → health check
"""

import asyncio
import logging
import os
from typing import Dict, Optional

from fastapi import (
    FastAPI, HTTPException, WebSocket, WebSocketDisconnect,
    Depends, Header, Query
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

import tts_engine as engine

# ─── Setup ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.environ.get("API_KEY", "")  # ถ้า set จะ require key ทุก request

app = FastAPI(
    title="Novel TTS API",
    description="Edge TTS สำหรับนิยาย — batch + streaming",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth ─────────────────────────────────────────────────────────────────────

def verify_api_key(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ─── Request Models ───────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str = Field(..., description="ข้อความทั้งตอน")
    bf_lib: Dict[str, str] = Field(default={}, description="lib แทนที่คำ ก่อน process (ส่งแค่คำที่ใช้)")
    at_lib: Dict[str, str] = Field(default={}, description="lib แทนที่คำ หลัง process (ส่งแค่คำที่ใช้)")
    rate: str = Field(default="+35%", description="ความเร็วเสียง เช่น +35% หรือ -10%")
    voice_gender: str = Field(default="Female", description="Female หรือ Male")
    voice_name: Optional[str] = Field(default=None, description="ชื่อ voice เฉพาะ (ถ้าต้องการ lock)")
    lang: str = Field(default="th", description="ภาษา เช่น th, en")


class PreviewRequest(BaseModel):
    text: str
    bf_lib: Dict[str, str] = {}
    at_lib: Dict[str, str] = {}
    rate: str = "+35%"
    voice_gender: str = "Female"
    voice_name: Optional[str] = None
    lang: str = "th"
    preview_chars: int = Field(default=300, ge=50, le=1000)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/voices")
async def list_voices(
    lang: str = Query(default="th"),
    gender: str = Query(default=""),
    _auth=Depends(verify_api_key),
):
    """
    List available voices จาก edge-tts
    """
    try:
        voices = await engine.get_voices(lang=lang, gender=gender)
        return {
            "voices": [
                {
                    "name": v["Name"],
                    "gender": v.get("Gender"),
                    "locale": v.get("Locale"),
                    "friendly_name": v.get("FriendlyName", ""),
                }
                for v in voices
            ],
            "count": len(voices),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate(
    req: TTSRequest,
    _auth=Depends(verify_api_key),
):
    """
    Batch generation — รับข้อความทั้งตอน คืนไฟล์ MP3
    - แบ่ง chunk อัตโนมัติถ้าข้อความยาว
    - รวม chunk ด้วย ffmpeg
    - คืน Content-Type: audio/mpeg
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text ไม่ควรว่าง")

    try:
        audio_bytes, meta = await engine.generate_audio(
            text=req.text,
            bf_lib=req.bf_lib,
            at_lib=req.at_lib,
            rate=req.rate,
            voice_gender=req.voice_gender,
            voice_name=req.voice_name,
            lang=req.lang,
        )
        headers = {
            "X-Voice": meta["voice"],
            "X-Chunks": str(meta["chunks"]),
            "X-Chars": str(meta["processed_chars"]),
            "Content-Disposition": "attachment; filename=output.mp3",
        }
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers=headers,
        )
    except Exception as e:
        logger.error(f"/generate error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stream")
async def stream_audio(
    req: TTSRequest,
    _auth=Depends(verify_api_key),
):
    """
    HTTP Chunked Streaming — ส่ง MP3 bytes แบบ real-time
    ฝั่ง client รับ response แล้ว pipe เข้า audio player ได้เลย
    
    ตัวอย่าง JS:
        const resp = await fetch('/stream', { method: 'POST', body: JSON.stringify(req) })
        const reader = resp.body.getReader()
        // push chunks เข้า MediaSource API
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text ไม่ควรว่าง")

    async def audio_generator():
        try:
            async for chunk in engine.stream_audio_chunks(
                text=req.text,
                bf_lib=req.bf_lib,
                at_lib=req.at_lib,
                rate=req.rate,
                voice_gender=req.voice_gender,
                voice_name=req.voice_name,
                lang=req.lang,
            ):
                yield chunk
        except Exception as e:
            logger.error(f"/stream error: {e}", exc_info=True)

    return StreamingResponse(
        audio_generator(),
        media_type="audio/mpeg",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """
    WebSocket Streaming — เหมาะกับ desktop app / Electron
    
    Protocol:
      Client → Server: JSON config (TTSRequest fields)
      Server → Client: binary MP3 chunks (ต่อเนื่อง)
      Server → Client: text "END" เมื่อจบ
      Server → Client: text "ERROR: <msg>" ถ้าผิดพลาด
    
    ตัวอย่าง Python client:
        ws = await websockets.connect("ws://host/ws/stream")
        await ws.send(json.dumps({"text": "...", "bf_lib": {}, ...}))
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                audio_buffer.write(msg)
            elif msg == "END":
                break
    """
    await websocket.accept()
    try:
        # รับ config จาก client
        raw = await websocket.receive_text()
        import json
        data = json.loads(raw)
        req = TTSRequest(**data)

        # verify API key ถ้ามี (ส่งใน config)
        if API_KEY and data.get("api_key") != API_KEY:
            await websocket.send_text("ERROR: Invalid API key")
            await websocket.close()
            return

        if not req.text.strip():
            await websocket.send_text("ERROR: text ไม่ควรว่าง")
            await websocket.close()
            return

        # stream audio
        async for chunk in engine.stream_audio_chunks(
            text=req.text,
            bf_lib=req.bf_lib,
            at_lib=req.at_lib,
            rate=req.rate,
            voice_gender=req.voice_gender,
            voice_name=req.voice_name,
            lang=req.lang,
        ):
            await websocket.send_bytes(chunk)

        await websocket.send_text("END")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"/ws/stream error: {e}", exc_info=True)
        try:
            await websocket.send_text(f"ERROR: {e}")
        except Exception:
            pass


@app.post("/preview")
async def preview(
    req: PreviewRequest,
    _auth=Depends(verify_api_key),
):
    """
    Preview สั้น — ใช้แค่ N ตัวอักษรแรก เพื่อทดสอบ voice/rate
    คืน MP3 bytes เหมือน /generate
    """
    text_short = req.text[: req.preview_chars]
    try:
        audio_bytes, meta = await engine.generate_audio(
            text=text_short,
            bf_lib=req.bf_lib,
            at_lib=req.at_lib,
            rate=req.rate,
            voice_gender=req.voice_gender,
            voice_name=req.voice_name,
            lang=req.lang,
        )
        headers = {"X-Voice": meta["voice"], "X-Preview-Chars": str(req.preview_chars)}
        return Response(content=audio_bytes, media_type="audio/mpeg", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Dev server ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
