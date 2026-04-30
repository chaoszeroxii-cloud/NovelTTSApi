#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Novel TTS API — FastAPI
Endpoints:
  POST /generate          → batch MP3 file (รับทั้งตอน + tone config คืน MP3)
  POST /stream            → chunked HTTP stream (real-time audio + tone)
  WebSocket /ws/stream    → WebSocket binary stream
  GET  /voices            → list available voices
  POST /preview           → preview สั้น (tone config support)
  GET  /health            → health check
"""

import asyncio
import logging
import os
from typing import Dict, Optional, List

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
    rate_pct: Optional[str] = Field(default=None, description="rate override (percentage)")
    pitch_hz: Optional[str] = Field(default=None, description="pitch adjustment เช่น +0Hz, -10Hz")
    volume_pct: Optional[str] = Field(default=None, description="volume adjustment เช่น 0, +30%, -50%")
    voice_gender: str = Field(default="Female", description="Female หรือ Male")
    voice_name: Optional[str] = Field(default=None, description="ชื่อ voice เฉพาะ (ถ้าต้องการ lock)")
    lang: str = Field(default="th", description="ภาษา เช่น th, en")


class PreviewRequest(BaseModel):
    text: str
    bf_lib: Dict[str, str] = {}
    at_lib: Dict[str, str] = {}
    rate: str = "+35%"
    rate_pct: Optional[str] = Field(default=None, description="rate override (percentage)")
    pitch_hz: Optional[str] = Field(default=None, description="pitch adjustment")
    volume_pct: Optional[str] = Field(default=None, description="volume adjustment")
    voice_gender: str = "Female"
    voice_name: Optional[str] = None
    lang: str = "th"
    preview_chars: int = Field(default=300, ge=50, le=1000)


# ─── Multi-line TTS Models ───────────────────────────────────────────────────

class ToneConfig(BaseModel):
    """แต่ละ tone มีค่า pitch, rate, volume"""
    tone_name: str = Field(..., description="normal, angry, whisper, sad, excited, fearful, serious, cold")
    pitch_hz: str = Field(..., description="เช่น +0Hz, -10Hz (Hz format)")
    rate_pct: str = Field(..., description="เช่น +0%, +15% (percentage)")
    volume_pct: str = Field(..., description="เช่น 0, +30%, -50% (percentage)")


class LineAudio(BaseModel):
    """แต่ละบรรทัด/ส่วนข้อความ"""
    text: str = Field(..., description="ข้อความของบรรทัด")
    tone: ToneConfig = Field(..., description="Tone config สำหรับบรรทัดนี้")
    voice_gender: str = Field(default="Female", description="Female หรือ Male")
    voice_name: Optional[str] = Field(default=None, description="ชื่อ voice เฉพาะ (ถ้าต้องการ lock)")


class MultiLineRequest(BaseModel):
    """ส่ง array บรรทัด + glossary global"""
    lines: List[LineAudio] = Field(..., description="List of lines with tone config")
    bf_lib: Dict[str, str] = Field(default={}, description="lib แทนที่คำ ก่อน process (ส่งทั้งหมด)")
    at_lib: Dict[str, str] = Field(default={}, description="lib แทนที่คำ หลัง process (ส่งทั้งหมด)")
    lang: str = Field(default="th", description="ภาษา")


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
    - รองรับ tone config (pitch_hz, rate_pct, volume_pct)
    - คืน Content-Type: audio/mpeg
    """
    logger.info(f"POST /generate - Request: text_len={len(req.text)}, lang={req.lang}, voice_gender={req.voice_gender}, voice_name={req.voice_name}, rate={req.rate}, pitch_hz={req.pitch_hz}, rate_pct={req.rate_pct}, volume_pct={req.volume_pct}, bf_lib_keys={list(req.bf_lib.keys())}, at_lib_keys={list(req.at_lib.keys())}")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text ไม่ควรว่าง")

    try:
        # Use rate_pct if provided, otherwise use rate
        final_rate = req.rate_pct or req.rate
        
        audio_bytes, meta = await engine.generate_audio(
            text=req.text,
            bf_lib=req.bf_lib,
            at_lib=req.at_lib,
            rate=final_rate,
            pitch_hz=req.pitch_hz,
            volume_pct=req.volume_pct,
            voice_gender=req.voice_gender,
            voice_name=req.voice_name,
            lang=req.lang
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
    รองรับ tone config (pitch_hz, rate_pct, volume_pct)
    
    ตัวอย่าง JS:
        const resp = await fetch('/stream', { method: 'POST', body: JSON.stringify(req) })
        const reader = resp.body.getReader()
        // push chunks เข้า MediaSource API
    """
    logger.info(f"POST /stream - Request: text_len={len(req.text)}, lang={req.lang}, voice_gender={req.voice_gender}, voice_name={req.voice_name}, rate={req.rate}, pitch_hz={req.pitch_hz}, rate_pct={req.rate_pct}, volume_pct={req.volume_pct}, bf_lib_keys={list(req.bf_lib.keys())}, at_lib_keys={list(req.at_lib.keys())}")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text ไม่ควรว่าง")

    async def audio_generator():
        try:
            final_rate = req.rate_pct or req.rate
            async for chunk in engine.stream_audio_chunks(
                text=req.text,
                bf_lib=req.bf_lib,
                at_lib=req.at_lib,
                rate=final_rate,
                pitch_hz=req.pitch_hz,
                volume_pct=req.volume_pct,
                voice_gender=req.voice_gender,
                voice_name=req.voice_name,
                lang=req.lang
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
        logger.info(f"WebSocket /ws/stream - Request: text_len={len(req.text)}, lang={req.lang}, voice_gender={req.voice_gender}, voice_name={req.voice_name}, rate={req.rate}, pitch_hz={req.pitch_hz}, rate_pct={req.rate_pct}, volume_pct={req.volume_pct}, bf_lib_keys={list(req.bf_lib.keys())}, at_lib_keys={list(req.at_lib.keys())}")

        # verify API key ถ้ามี (ส่งใน config)
        if API_KEY and data.get("api_key") != API_KEY:
            await websocket.send_text("ERROR: Invalid API key")
            await websocket.close()
            return

        if not req.text.strip():
            await websocket.send_text("ERROR: text ไม่ควรว่าง")
            await websocket.close()
            return

        processed = engine.preprocess_text(req.text, req.bf_lib, req.at_lib)
        chunks = engine.split_text_by_chars(processed)
        voice = await engine.pick_voice(req.lang, req.voice_gender, req.voice_name)
        final_rate = req.rate_pct or req.rate
        final_pitch = req.pitch_hz or "+0Hz"
        final_volume = req.volume_pct or "+0%"
        total_chunks = len(chunks)

        for idx, chunk_text in enumerate(chunks):
            await websocket.send_json({
                "type": "progress",
                "phase": "starting",
                "current": idx + 1,
                "total": total_chunks,
                "percent": int((idx / total_chunks) * 100) if total_chunks > 0 else 0,
            })
            async for chunk in engine.synthesize_stream(
                chunk_text,
                voice,
                final_rate,
                final_pitch,
                final_volume
            ):
                await websocket.send_bytes(chunk)
            await websocket.send_json({
                "type": "progress",
                "phase": "completed",
                "current": idx + 1,
                "total": total_chunks,
                "percent": int(((idx + 1) / total_chunks) * 100) if total_chunks > 0 else 100,
            })

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
    Preview สั้น — ใช้แค่ N ตัวอักษรแรก เพื่อทดสอบ voice/rate/tone
    คืน MP3 bytes เหมือน /generate
    """
    logger.info(f"POST /preview - Request: text_len={len(req.text)}, preview_chars={req.preview_chars}, lang={req.lang}, voice_gender={req.voice_gender}, voice_name={req.voice_name}, rate={req.rate}, pitch_hz={req.pitch_hz}, rate_pct={req.rate_pct}, volume_pct={req.volume_pct}, bf_lib_keys={list(req.bf_lib.keys())}, at_lib_keys={list(req.at_lib.keys())}")
    text_short = req.text[: req.preview_chars]
    try:
        final_rate = req.rate_pct or req.rate
        audio_bytes, meta = await engine.generate_audio(
            text=text_short,
            bf_lib=req.bf_lib,
            at_lib=req.at_lib,
            rate=final_rate,
            pitch_hz=req.pitch_hz,
            volume_pct=req.volume_pct,
            voice_gender=req.voice_gender,
            voice_name=req.voice_name,
            lang=req.lang
        )
        headers = {"X-Voice": meta["voice"], "X-Preview-Chars": str(req.preview_chars)}
        return Response(content=audio_bytes, media_type="audio/mpeg", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-multi")
async def generate_multi(
    req: MultiLineRequest,
    _auth=Depends(verify_api_key),
):
    """
    Multi-line generation — แต่ละบรรทัดมี tone config + voice + pitch/rate/volume
    - รับ array lines ที่มี text + tone (pitch, rate, volume)
    - ส่ง glossary (bf_lib, at_lib) ที่จะใช้ทั้งหมด
    - Generate MP3 แยกต่อบรรทัด แล้ว merge
    - คืน MP3 ไฟล์เดียว
    """
    logger.info(f"POST /generate-multi - Request: lines={len(req.lines)}, lang={req.lang}, bf_lib_keys={list(req.bf_lib.keys())}, at_lib_keys={list(req.at_lib.keys())}")
    
    if not req.lines:
        raise HTTPException(status_code=400, detail="lines ต้องไม่ว่าง")

    try:
        # Build line configs พร้อม voice (pick voice สำหรับแต่ละบรรทัด)
        line_configs = []
        for i, line in enumerate(req.lines):
            if not line.text.strip():
                logger.warning(f"  line {i+1}: ข้อความว่าง — skip")
                continue

            # Pick voice สำหรับบรรทัดนี้
            voice = await engine.pick_voice(
                lang=req.lang,
                gender=line.voice_gender,
                voice_name=line.voice_name
            )

            line_configs.append({
                "text": line.text,
                "voice": voice,
                "pitch_hz": line.tone.pitch_hz,
                "rate_pct": line.tone.rate_pct,
                "volume_pct": line.tone.volume_pct,
            })

        if not line_configs:
            raise HTTPException(status_code=400, detail="ไม่มีบรรทัดที่ถูกต้อง")

        # Generate multi-line audio
        audio_bytes, meta = await engine.generate_audio_for_lines(
            lines=line_configs,
            bf_lib=req.bf_lib,
            at_lib=req.at_lib,
        )

        headers = {
            "X-Lines": str(meta["lines"]),
            "X-Audio-Parts": str(meta["audio_parts"]),
            "X-Chars": str(meta["total_chars"]),
            "Content-Disposition": "attachment; filename=output-multi.mp3",
        }
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers=headers,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"/generate-multi error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── Dev server ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
