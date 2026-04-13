"""
client_examples.py — ตัวอย่างการเรียก API จาก Python (desktop app / text editor)

ฟีเจอร์ smart lib filtering:
  ก่อนส่ง request ให้ filter เฉพาะ key ที่ปรากฎในข้อความ
  เพื่อลด payload (lib อาจใหญ่มาก)
"""

import asyncio
import io
import json
import re
import wave
from pathlib import Path
from typing import Dict, Optional

import httpx
import websockets


API_BASE = "http://localhost:8000"
API_KEY = "your-secret-key"  # ตั้งให้ตรงกับ server

HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


# ─── Smart lib filtering ─────────────────────────────────────────────────────

def filter_lib_for_text(text: str, lib: Dict[str, str]) -> Dict[str, str]:
    """
    ส่งแค่ key ที่ปรากฎในข้อความจริง → ลด payload
    เรียกบนฝั่ง client ก่อนส่ง request
    """
    if not lib:
        return {}
    return {k: v for k, v in lib.items() if k in text}


# ─── 1. Batch generate — คืนไฟล์ MP3 ────────────────────────────────────────

async def generate_chapter_audio(
    text: str,
    bf_lib: Dict[str, str],
    at_lib: Dict[str, str],
    output_path: Optional[Path] = None,
    rate: str = "+35%",
    voice_gender: str = "Female",
) -> bytes:
    """
    เรียก POST /generate
    คืน bytes ของไฟล์ MP3
    ถ้าระบุ output_path จะบันทึกไฟล์ด้วย
    """
    payload = {
        "text": text,
        "bf_lib": filter_lib_for_text(text, bf_lib),
        "at_lib": filter_lib_for_text(text, at_lib),
        "rate": rate,
        "voice_gender": voice_gender,
    }

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{API_BASE}/generate",
            headers=HEADERS,
            json=payload,
        )
        resp.raise_for_status()
        audio = resp.content

    if output_path:
        output_path.write_bytes(audio)
        print(f"Saved: {output_path} ({len(audio):,} bytes)")
        print(f"Voice: {resp.headers.get('X-Voice')}")
        print(f"Chunks: {resp.headers.get('X-Chunks')}")

    return audio


# ─── 2a. HTTP Streaming — เล่นแบบ real-time ─────────────────────────────────

async def stream_and_play_http(
    text: str,
    bf_lib: Dict[str, str],
    at_lib: Dict[str, str],
    rate: str = "+35%",
    voice_gender: str = "Female",
    on_chunk=None,  # callback(bytes) สำหรับ pipe เข้า audio player
):
    """
    เรียก POST /stream
    รับ MP3 bytes แบบ chunked streaming
    ใช้ on_chunk callback เพื่อส่งต่อไปยัง audio player
    
    ตัวอย่าง on_chunk:
        def on_chunk(data: bytes):
            pygame_sound_buffer.write(data)  # หรือ pipe เข้า subprocess ffplay
    """
    payload = {
        "text": text,
        "bf_lib": filter_lib_for_text(text, bf_lib),
        "at_lib": filter_lib_for_text(text, at_lib),
        "rate": rate,
        "voice_gender": voice_gender,
    }

    audio_buf = io.BytesIO()
    async with httpx.AsyncClient(timeout=600) as client:
        async with client.stream(
            "POST", f"{API_BASE}/stream", headers=HEADERS, json=payload
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                audio_buf.write(chunk)
                if on_chunk:
                    on_chunk(chunk)

    return audio_buf.getvalue()


# ─── 2b. WebSocket Streaming — เหมาะกับ desktop app ─────────────────────────

async def stream_via_websocket(
    text: str,
    bf_lib: Dict[str, str],
    at_lib: Dict[str, str],
    rate: str = "+35%",
    voice_gender: str = "Female",
    on_chunk=None,  # callback(bytes)
    on_done=None,   # callback() เมื่อจบ
) -> bytes:
    """
    เรียก WebSocket /ws/stream
    ได้รับ binary MP3 chunks แบบ real-time
    
    เหมาะกับ:
    - Electron app (ใช้ Node.js WebSocket)
    - Desktop Python app (tkinter, PySide, PyQt)
    - ทุกที่ที่ต้องการ low-latency playback
    """
    ws_url = API_BASE.replace("http://", "ws://").replace("https://", "wss://")
    ws_url += "/ws/stream"

    payload = {
        "text": text,
        "bf_lib": filter_lib_for_text(text, bf_lib),
        "at_lib": filter_lib_for_text(text, at_lib),
        "rate": rate,
        "voice_gender": voice_gender,
        "api_key": API_KEY,
    }

    audio_buf = io.BytesIO()
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps(payload))

        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                audio_buf.write(msg)
                if on_chunk:
                    on_chunk(msg)
            elif isinstance(msg, str):
                if msg == "END":
                    if on_done:
                        on_done()
                    break
                elif msg.startswith("ERROR:"):
                    raise RuntimeError(msg)

    return audio_buf.getvalue()


# ─── 3. Preview ──────────────────────────────────────────────────────────────

async def preview_voice(
    text: str,
    rate: str = "+35%",
    voice_name: Optional[str] = None,
    preview_chars: int = 300,
) -> bytes:
    """ทดสอบ voice/rate โดยใช้แค่ข้อความสั้น"""
    payload = {
        "text": text,
        "rate": rate,
        "voice_name": voice_name,
        "preview_chars": preview_chars,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{API_BASE}/preview", headers=HEADERS, json=payload)
        resp.raise_for_status()
        print(f"Preview voice: {resp.headers.get('X-Voice')}")
        return resp.content


# ─── 4. List voices ──────────────────────────────────────────────────────────

async def list_voices(lang: str = "th") -> list:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{API_BASE}/voices?lang={lang}", headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        for v in data["voices"]:
            print(f"  {v['name']} — {v['gender']} ({v['locale']})")
        return data["voices"]


# ─── Example usage ───────────────────────────────────────────────────────────

BF_LIB = {
    "魔法师": "นักเวทย์",
    "剣聖": "ดาบศักดิ์สิทธิ์",
    # ... ทั้งหมด (client จะ filter ก่อนส่ง)
}
AT_LIB = {
    "หนึ่ง|ร้อย|": "ร้อย|",
    # ...
}

async def demo():
    text = """
    เรื่องราวของวีรบุรุษหมายเลข 1 ที่ออกเดินทางเพื่อปกป้องโลก
    พลังของเขามีค่าถึง 99999 แต้ม และเขาสามารถเอาชนะศัตรูได้ทุกคน
    """

    # 1. Batch — ได้ MP3 file
    audio = await generate_chapter_audio(
        text=text,
        bf_lib=BF_LIB,
        at_lib=AT_LIB,
        output_path=Path("output.mp3"),
    )

    # 2. Real-time stream via WebSocket (เล่นขณะโหลด)
    def play_chunk(data: bytes):
        print(f"  received {len(data)} bytes")
        # pipe to audio player here

    await stream_via_websocket(
        text=text,
        bf_lib=BF_LIB,
        at_lib=AT_LIB,
        on_chunk=play_chunk,
        on_done=lambda: print("Stream done!"),
    )


if __name__ == "__main__":
    asyncio.run(demo())
