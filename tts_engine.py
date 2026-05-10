#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS Engine — ใช้ logic จาก TTS_Edge.py ปรับให้เป็น library สำหรับ API
"""

import asyncio
import io
import logging
import os
import random
import re
import shutil
import subprocess
import tempfile
from typing import AsyncIterator, Dict, List, Optional, Tuple

import edge_tts
from edge_tts import VoicesManager

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────
MAX_CHARS_PER_CHUNK = 4000
ALLOWED_SYMBOLS = {".", "-", "*", "+", ":", "/", "x"}

THAI_DIGITS: Dict[str, str] = {
    "0": "สูน|", "1": "หนึ่ง|", "2": "สอง|", "3": "สาม|",
    "4": "สี่|",  "5": "ห้า|",  "6": "หก|",  "7": "เจ็ด|",
    "8": "แปด|", "9": "เก้า|",
}
THAI_POS = {
    0: "", 1: "สิบ|", 2: "ร้อย|", 3: "พัน|",
    4: "หมื่น|", 5: "แสน|", 6: "ล้าน|",
    12: "ล้าน|ล้าน|", 18: "ล้าน|ล้าน|ล้าน|", 24: "ล้าน|ล้าน|ล้าน|ล้าน|",
}


# ─── Text Processing ─────────────────────────────────────────────────────────

def replace_with_lib(text: str, lib: Dict[str, str]) -> str:
    """แทนที่คำด้วย lib (exact match, compiled regex)
    Sort keys by length descending so longer words match before
    shorter ones that are substrings of them (e.g. 'แทรกซึม' before 'แทรก')."""
    if not lib:
        return text
    sorted_keys = sorted(lib.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in sorted_keys))
    return pattern.sub(lambda m: lib[m.group(0)], text)


def normalize_text(text: str) -> str:
    return (
        text.replace(",", "")
            .replace(" -", "-")
            .replace("(− +)", "|บวก|ลบ|")
            .replace("(−)", "|ลบ|")
            .replace("∞", "|ไม่จำกัด|")
            .replace("-", "|ลบ|")
            .replace("−", "|ลบ|")
    )


def is_valid_number_str(s: str) -> bool:
    if not s:
        return False
    special_rules = {".": 2, "*": 2, "x": 2, "+": 2, "/": 2, "-": 2, ":": 99}
    if any((c + c) in s for c in special_rules):
        return False
    if any(c in s for c in special_rules):
        return all(s.count(c) <= n for c, n in special_rules.items())
    return True


def number_to_thai_text(num_str: str) -> str:
    if not num_str:
        return ""
    if len(num_str) == 1 and not num_str.isdigit():
        return num_str
    if num_str[0] in {".", ":"}:
        return number_to_thai_text(num_str[1:])
    if num_str[-1:] and num_str[-1] in {".", "-", "x", ":"}:
        return number_to_thai_text(num_str[:-1])
    if num_str[0] in {"-", "+", "*"}:
        prefix = "ลบ" if num_str[0] == "-" else "บวก"
        return prefix + number_to_thai_text(num_str[1:])
    for sym, word in {"x": "คูณ", "*": "บวก", "+": "บวก", "-": "ถึง", "−": "ถึง", ":": "ต่อ", "/": "ต่อ"}.items():
        if sym in num_str:
            parts = num_str.split(sym)
            return word.join(number_to_thai_text(p) for p in parts)
    if num_str.count(".") >= 2:
        parts = num_str.split(".")
        return "จุด".join(number_to_thai_text(p) for p in parts)
    if "." in num_str:
        integer_part, decimal_part = num_str.split(".", 1)
    else:
        integer_part, decimal_part = num_str, None
    if integer_part == "0":
        thai_text = THAI_DIGITS["0"]
    else:
        thai_text = ""
        integer_part = integer_part[::-1].replace(",", "")
        for i, digit in enumerate(integer_part):
            if i % 6 == 0 and i != 0 and digit != "0":
                thai_text = THAI_POS[i] + thai_text
            if digit != "0":
                thai_text = THAI_DIGITS[digit] + THAI_POS[i % 6] + thai_text
    if decimal_part:
        thai_text += "จุด|"
        for d in decimal_part:
            thai_text += THAI_DIGITS.get(d, d)
    thai_text = (
        thai_text.replace("หนึ่ง|สิบ|", "สิบ|")
                 .replace("สอง|สิบ|", "ยี่|สิบ|")
                 .replace("สิบ|หนึ่ง|", "สิบ|เอ็ด|")
    )
    return thai_text


def convert_numbers_in_text(text: str) -> str:
    output = []
    number_buf = ""

    def flush_number():
        nonlocal number_buf
        if is_valid_number_str(number_buf):
            output.append(number_to_thai_text(number_buf))
        else:
            output.append(number_buf)
        number_buf = ""

    for word in text.split():
        for ch in word:
            if ch.isdigit() or ch in ALLOWED_SYMBOLS:
                number_buf += ch
            else:
                if number_buf:
                    flush_number()
                output.append(ch)
        if number_buf:
            flush_number()
        output.append("\n" if len(word) >= 14 or word.endswith(".") else " ")

    return "".join(output)


def preprocess_text(
    text: str,
    bf_lib: Dict[str, str],
    at_lib: Dict[str, str],
    append_end: bool = True,
) -> str:
    """Pipeline เต็ม: bf_lib → normalize → convert numbers → at_lib"""
    text = replace_with_lib(text, bf_lib)
    text = normalize_text(text)
    text = convert_numbers_in_text(text)
    text = replace_with_lib(text, at_lib)
    if append_end:
        text += "\nจบตอน"
    return text


def split_text_by_chars(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        cut = end
        for i in range(end, start, -1):
            if text[i - 1] in {" ", "\n", ".", "…", "?", "!"}:
                cut = i
                break
        chunks.append(text[start:cut].strip())
        start = cut
    return [c for c in chunks if c]


# ─── Voice Selection ──────────────────────────────────────────────────────────

_voices_cache: Optional[List[dict]] = None

async def get_voices(lang: str = "th", gender: str = "Female") -> List[dict]:
    global _voices_cache
    if _voices_cache is None:
        manager = await VoicesManager.create()
        _voices_cache = manager.voices

    # filter by lang
    candidates = [v for v in _voices_cache if v.get("Locale", "").startswith(lang)]
    if gender:
        filtered = [v for v in candidates if v.get("Gender") == gender]
        if filtered:
            candidates = filtered
    return candidates


async def pick_voice(lang: str = "th", gender: str = "Female", voice_name: Optional[str] = None) -> str:
    if voice_name:
        return voice_name
    # Normalize gender to capital case for edge-tts
    normalized_gender = gender.capitalize() if gender else "Female"
    candidates = await get_voices(lang, normalized_gender)
    if not candidates:
        raise RuntimeError(f"ไม่พบเสียง lang={lang} gender={normalized_gender}")
    return random.choice(candidates)["Name"]


# ─── Synthesis ───────────────────────────────────────────────────────────────

async def synthesize_to_bytes(
    text: str,
    voice: str,
    rate: str = "+35%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    max_retries: int = 5,
) -> bytes:
    """แปลงข้อความ → bytes (MP3) ผ่าน edge-tts (รองรับ pitch, volume)"""
    for attempt in range(1, max_retries + 1):
        try:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=rate,
                pitch=pitch,
                volume=volume
            )
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()
        except Exception as e:
            if attempt == max_retries:
                raise
            wait = 2 ** attempt
            logger.warning(f"TTS retry {attempt}/{max_retries}: {e} — wait {wait}s")
            await asyncio.sleep(wait)
    return b""


async def synthesize_stream(
    text: str,
    voice: str,
    rate: str = "+35%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> AsyncIterator[bytes]:
    """Stream audio chunks จาก edge-tts แบบ real-time พร้อม tone config"""
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate,
        pitch=pitch,
        volume=volume
    )
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            yield chunk["data"]


async def synthesize_stream_events(
    text: str,
    voice: str,
    rate: str = "+35%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> AsyncIterator[dict]:
    """Stream raw edge-tts events for richer progress updates."""
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate,
        pitch=pitch,
        volume=volume,
    )
    async for chunk in communicate.stream():
        yield chunk


# ─── Concat with ffmpeg ───────────────────────────────────────────────────────

def concat_mp3_bytes(audio_parts: List[bytes]) -> bytes:
    """รวม MP3 หลายชิ้นด้วย ffmpeg (ถ้าไม่มี ffmpeg ใช้ byte concat แทน)"""
    if len(audio_parts) == 1:
        return audio_parts[0]

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.warning("ไม่พบ ffmpeg — ใช้ byte concat แทน (อาจมี glitch เล็กน้อย)")
        return b"".join(audio_parts)

    with tempfile.TemporaryDirectory() as tmpdir:
        part_paths = []
        for i, data in enumerate(audio_parts):
            p = os.path.join(tmpdir, f"part{i:03d}.mp3")
            with open(p, "wb") as f:
                f.write(data)
            part_paths.append(p)

        concat_list = os.path.join(tmpdir, "concat.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in part_paths:
                f.write(f"file '{p}'\n")

        out_path = os.path.join(tmpdir, "output.mp3")
        subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-loglevel", "error",
             "-safe", "0", "-f", "concat", "-i", concat_list,
             "-c:a", "copy", "-y", out_path],
            check=True,
        )
        with open(out_path, "rb") as f:
            return f.read()


# ─── High-level API functions ─────────────────────────────────────────────────

async def generate_audio(
    text: str,
    bf_lib: Dict[str, str] = {},
    at_lib: Dict[str, str] = {},
    rate: str = "+35%",
    pitch_hz: Optional[str] = None,
    volume_pct: Optional[str] = None,
    voice_gender: str = "Female",
    voice_name: Optional[str] = None,
    lang: str = "th",
) -> Tuple[bytes, dict]:
    """
    Batch generation: รับข้อความทั้งตอน → คืน MP3 bytes พร้อม tone config
    Returns: (audio_bytes, metadata)
    """
    processed = preprocess_text(text, bf_lib, at_lib, append_end=False)
    chunks = split_text_by_chars(processed)
    voice = await pick_voice(lang, voice_gender, voice_name)

    # Default tone values if not provided
    final_pitch = pitch_hz or "+0Hz"
    final_volume = volume_pct or "+0%"

    logger.info(f"Generating {len(chunks)} chunk(s) with voice={voice}, rate={rate}, pitch={final_pitch}, volume={final_volume}")

    audio_parts = []
    for i, chunk in enumerate(chunks):
        logger.info(f"  chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
        data = await synthesize_to_bytes(chunk, voice, rate, final_pitch, final_volume)
        audio_parts.append(data)

    final_audio = concat_mp3_bytes(audio_parts)
    metadata = {
        "voice": voice,
        "chunks": len(chunks),
        "processed_chars": len(processed),
        "original_chars": len(text),
    }
    return final_audio, metadata


async def stream_audio_chunks(
    text: str,
    bf_lib: Dict[str, str] = {},
    at_lib: Dict[str, str] = {},
    rate: str = "+35%",
    pitch_hz: Optional[str] = None,
    volume_pct: Optional[str] = None,
    voice_gender: str = "Female",
    voice_name: Optional[str] = None,
    lang: str = "th",
) -> AsyncIterator[bytes]:
    """
    Streaming generation: yield MP3 bytes ทันทีที่ได้จาก edge-tts พร้อม tone config
    เหมาะกับ real-time playback
    """
    processed = preprocess_text(text, bf_lib, at_lib)
    chunks = split_text_by_chars(processed)
    voice = await pick_voice(lang, voice_gender, voice_name)

    # Default tone values if not provided
    final_pitch = pitch_hz or "+0Hz"
    final_volume = volume_pct or "+0%"

    logger.info(f"Streaming {len(chunks)} chunk(s) with voice={voice}, rate={rate}, pitch={final_pitch}, volume={final_volume}")

    for chunk in chunks:
        async for audio_chunk in synthesize_stream(chunk, voice, rate, final_pitch, final_volume):
            yield audio_chunk


# ─── Multi-line Generation ───────────────────────────────────────────────────

async def generate_audio_for_lines(
    lines: List[dict],  # [{"text": "...", "pitch_hz": "+0Hz", "rate_pct": "+0%", "volume_pct": "+0%", "voice": "..."}, ...]
    bf_lib: Dict[str, str] = {},
    at_lib: Dict[str, str] = {},
) -> Tuple[bytes, dict]:
    """
    Per-line generation: แต่ละบรรทัดสร้าง MP3 แยก แล้ว merge
    
    Args:
        lines: List of dicts with keys: text, pitch_hz, rate_pct, volume_pct, voice
        bf_lib: Dictionary for before-processing replacements
        at_lib: Dictionary for after-processing replacements
    
    Returns: (audio_bytes, metadata)
    """
    if not lines:
        raise ValueError("lines ต้องไม่ว่าง")

    logger.info(f"Generating {len(lines)} line(s)")

    audio_parts = []
    total_chars = 0

    for i, line_cfg in enumerate(lines):
        text = line_cfg.get("text", "").strip()
        if not text:
            logger.warning(f"  line {i+1}: ข้อความว่าง — skip")
            continue

        # Process text ด้วย glossary
        processed = preprocess_text(text, bf_lib, at_lib, append_end=False)
        total_chars += len(processed)

        pitch_hz = line_cfg.get("pitch_hz", "+0Hz")
        rate_pct = line_cfg.get("rate_pct", "+0%")
        volume_pct = line_cfg.get("volume_pct", "+0%")
        voice = line_cfg.get("voice", "")

        if not voice:
            raise ValueError(f"line {i+1}: voice ต้องระบุ")

        logger.info(f"  line {i+1}/{len(lines)}: voice={voice}, pitch={pitch_hz}, rate={rate_pct}, volume={volume_pct}")

        # Generate MP3 สำหรับบรรทัดนี้
        data = await synthesize_to_bytes(
            processed, voice,
            rate=rate_pct,
            pitch=pitch_hz,
            volume=volume_pct
        )
        if data:
            audio_parts.append(data)

    if not audio_parts:
        raise RuntimeError("ไม่มี audio parts สร้างสำเร็จ")

    # Merge all parts
    final_audio = concat_mp3_bytes(audio_parts)
    metadata = {
        "lines": len(lines),
        "audio_parts": len(audio_parts),
        "total_chars": total_chars,
    }
    return final_audio, metadata
