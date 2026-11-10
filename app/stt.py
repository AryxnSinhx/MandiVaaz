"""
app/stt.py -- speech to text for Mandi Vaaz (Gnani Prisma v3.0, REST).

Two jobs, kept as separate functions so each can be tested alone:

  1. to_wav_16k_mono()  browser recording (WebM/Ogg/MP4...) --ffmpeg--> 16 kHz mono WAV
                        (Prisma does NOT accept WebM, which is what Chrome records)
  2. transcribe()       WAV bytes --HTTP POST--> transcript text

transcribe_audio() runs both. Everything the API documentation says (Nov 5, 2026) is
encoded here; the places where I am guessing are marked ASSUMPTION.

Needs:  ffmpeg on PATH (check:  ffmpeg -version)   and   requests  (uv add requests)
Key:    environment variable GNANI_API_KEY (the same name the official SDK reads)

Try it on a real recording (uses credits, about Rs 0.5 per minute of audio):
    uv run --env-file .env python -m app.stt path\\to\\clip.wav
    uv run --env-file .env python -m app.stt clip.wav --config paen_codemix
Run tests (no network, no credits):
    uv run python -m tests.test_stt
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

from app.templates import CROPS_PA, MANDIS_PA

# --------------------------------------------------------------------------
# Constants taken from the API documentation
# --------------------------------------------------------------------------
BASE_URL = "https://api.vachana.ai"
STT_PATH = "/stt/v3"
API_KEY_ENV = "GNANI_API_KEY"
MODEL = "prisma-v3.0"          # MUST be sent; if omitted the API silently uses v2.5
MAX_AUDIO_SECONDS = 30         # hard limit of the REST endpoint
MAX_KEYTERMS = 200             # documented limit for prisma-v3.0 keyterms
MAX_TOPIC_CHARS = 1600         # documented limit for topic

# Statuses worth retrying once: rate limit (one active request at a time), server
# trouble, gateway timeout. Everything else (bad key, bad audio, no credit) will fail again.
RETRYABLE_HTTP = {429, 500, 503, 504}


# --------------------------------------------------------------------------
# Which recogniser settings to use (the four configs you tested on 40 clips)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class STTConfig:
    name: str
    language_code: str   # "pa-IN" or a comma list such as "pa-IN,en-IN" (1 to 4 languages)
    format: str          # "verbatim" or "codemix" (codemix keeps English words in Latin script)
    context: bool = False  # send topic + keyterms?


CONFIGS: Dict[str, STTConfig] = {
    c.name: c for c in (
        STTConfig("pa_verbatim", "pa-IN", "verbatim"),
        STTConfig("pa_codemix", "pa-IN", "codemix"),
        STTConfig("paen_codemix", "pa-IN,en-IN", "codemix"),
        STTConfig("paen_codemix_ctx", "pa-IN,en-IN", "codemix", context=True),
    )
}
DEFAULT_CONFIG = CONFIGS["paen_codemix_ctx"]   # provisional (handoff): not final until fresh clips are tested
FALLBACK_CONFIG = CONFIGS["paen_codemix"]

# ASSUMPTION: this topic text and keyterm list are MY placeholder. Your 39/40 result for
# paen_codemix_ctx was measured with the list inside your stt_feasibility.py. Copy that
# exact list here if it differs, or the headline number no longer describes this code.
# All 9 crops are included on purpose, not just the 3 we can answer: if "wheat" is not
# in the list, the recogniser may force it into a listed word, and we would answer for
# the wrong crop instead of refusing honestly. (Hypothesis; your open item 5 tests it.)
TOPIC = "A farmer in Punjab asks the market rate (bhaav) of a crop in a mandi."
KEYTERMS: List[str] = [
    *CROPS_PA.values(), *MANDIS_PA.values(),   # Gurmukhi names
    *CROPS_PA.keys(), *MANDIS_PA.keys(),       # Roman names (potato, ludhiana, ...)
    "ਭਾਅ", "ਰੇਟ", "ਮੰਡੀ", "mandi", "rate",
]


# --------------------------------------------------------------------------
# Result and error types
# --------------------------------------------------------------------------
@dataclass
class STTResult:
    transcript: str                    # may be "" (silence): NOT an error
    request_id: str
    config: str
    detected_language: Optional[str] = None
    truncated: bool = False            # model cut the transcript short (needs shorter audio)
    server_seconds: Optional[float] = None       # API's own processing_time
    api_end_to_end_seconds: Optional[float] = None  # API's end_to_end_latency (includes upload)
    client_seconds: float = 0.0        # our measured round trip for the successful HTTP call
    convert_seconds: float = 0.0       # ffmpeg time (filled by transcribe_audio)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class STTError(Exception):
    """Anything that stops us getting a transcript. `kind` is a coarse category for main.py:
    setup | auth | credit | rate_limit | bad_audio | bad_request | timeout | server | network | unknown"""

    def __init__(self, message: str, *, kind: str = "unknown", http_status: Optional[int] = None,
                 error_type: Optional[str] = None, retryable: bool = False,
                 request_id: Optional[str] = None):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status
        self.error_type = error_type
        self.retryable = retryable
        self.request_id = request_id


# --------------------------------------------------------------------------
# Step 1: audio conversion
# --------------------------------------------------------------------------
_SUFFIX_BY_TYPE = {
    "audio/webm": ".webm", "video/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/flac": ".flac", "audio/aac": ".aac",
}


def suffix_for(content_type: Optional[str]) -> str:
    """'audio/webm;codecs=opus' -> '.webm'. Unknown -> '.bin' (ffmpeg sniffs the real format)."""
    base = (content_type or "").split(";")[0].strip().lower()
    return _SUFFIX_BY_TYPE.get(base, ".bin")


def to_wav_16k_mono(audio: bytes, suffix: str = ".bin", ffmpeg: str = "ffmpeg", timeout: float = 20.0) -> bytes:
    """Convert any audio ffmpeg understands into 16 kHz, mono, 16-bit PCM WAV bytes."""
    exe = shutil.which(ffmpeg)
    if exe is None:
        raise STTError("ffmpeg was not found on PATH. Install it and check `ffmpeg -version`.", kind="setup")
    if not audio:
        raise STTError("Empty audio upload.", kind="bad_audio")
    # Files, not pipes: ffmpeg cannot go back and fix the WAV header when writing to a pipe.
    with tempfile.TemporaryDirectory() as folder:
        src, dst = Path(folder) / f"in{suffix}", Path(folder) / "out.wav"
        src.write_bytes(audio)
        cmd = [exe, "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
               "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise STTError("ffmpeg took too long to convert the audio.", kind="bad_audio") from None
        if proc.returncode != 0 or not dst.exists():
            tail = proc.stderr.decode("utf-8", "replace")[-300:]
            raise STTError(f"ffmpeg could not read the audio: {tail}", kind="bad_audio")
        return dst.read_bytes()


def wav_duration_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# --------------------------------------------------------------------------
# Step 2: the HTTP call
# --------------------------------------------------------------------------
def build_form(config: STTConfig) -> Dict[str, str]:
    """The multipart text fields (everything except the audio file itself)."""
    form = {"model": MODEL, "language_code": config.language_code, "format": config.format}
    if config.context:
        if len(KEYTERMS) > MAX_KEYTERMS:
            raise ValueError(f"{len(KEYTERMS)} keyterms; the API allows {MAX_KEYTERMS}")
        if len(TOPIC) > MAX_TOPIC_CHARS:
            raise ValueError("topic too long")
        form["topic"] = TOPIC
        form["keyterms"] = json.dumps(KEYTERMS, ensure_ascii=False)  # keep Gurmukhi readable
    return form


def _error_from_response(resp, request_id: str) -> STTError:
    """The API uses FOUR different error shapes; this reads all of them."""
    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = None
    etype, message = "UNKNOWN", (getattr(resp, "text", "") or "")[:200]
    if isinstance(body, dict):
        if isinstance(body.get("error"), dict):                 # {"success":false,"error":{type,message}}
            etype = body["error"].get("type", etype)
            message = body["error"].get("message", message)
        elif isinstance(body.get("detail"), dict):              # auth / rate limit: {"detail":{error_code,...}}
            etype = body["detail"].get("error_code", etype)
            message = body["detail"].get("message", message)
        elif isinstance(body.get("detail"), list):              # 422 validation: {"detail":[...]}
            etype, message = "VALIDATION", str(body["detail"])[:200]
        elif body.get("code"):                                  # 504: {"error","code","message"}
            etype, message = str(body["code"]), body.get("message", message)

    audio_problems = {"EMPTY_AUDIO", "UNSUPPORTED_AUDIO_FORMAT", "AUDIO_CONVERSION_ERROR", "MAX_AUDIO_DURATION_EXCEEDED"}
    if status in (401, 403):
        kind = "auth"
    elif status == 402:
        kind = "credit"          # ASSUMPTION: docs list 402 as "insufficient credit" but mark it not verified
    elif status == 429:
        kind = "rate_limit"
    elif status == 504:
        kind = "timeout"
    elif status >= 500:
        kind = "server"
    elif etype in audio_problems:
        kind = "bad_audio"
    else:
        kind = "bad_request"
    return STTError(f"HTTP {status} {etype}: {message}", kind=kind, http_status=status, error_type=etype,
                    retryable=status in RETRYABLE_HTTP, request_id=request_id)


# The docs say some accounts allow ONE active request at a time (a second one gets 429).
# One lock per server process makes our own requests queue up instead of colliding.
_ONE_AT_A_TIME = threading.Lock()


def transcribe(
    wav_bytes: bytes,
    *,
    config: STTConfig = DEFAULT_CONFIG,
    api_key: Optional[str] = None,
    request_id: Optional[str] = None,
    retries: int = 1,
    timeout: float = 20.0,
    base_url: str = BASE_URL,
    post_fn: Optional[Callable] = None,       # injected in tests
    sleep_fn: Callable[[float], None] = time.sleep,
) -> STTResult:
    """POST one WAV to Prisma. Retries once on 429/5xx/504/network trouble, then raises STTError."""
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        raise STTError(f"No API key. Set the {API_KEY_ENV} environment variable (check your .env).", kind="auth")
    rid = request_id or str(uuid.uuid4())
    post = post_fn or requests.post
    url = base_url.rstrip("/") + STT_PATH
    headers = {"X-API-Key-ID": key, "X-API-Request-ID": rid}
    form = build_form(config)

    attempt = 0
    while True:
        try:
            with _ONE_AT_A_TIME:
                t0 = time.perf_counter()
                resp = post(url, headers=headers, data=form, timeout=timeout,
                            files={"audio_file": ("clip.wav", wav_bytes, "audio/wav")})
                elapsed = time.perf_counter() - t0
            if resp.status_code == 200:
                return _result_from_ok(resp, rid, config, elapsed)
            error = _error_from_response(resp, rid)
        except requests.RequestException as exc:  # timeout, DNS, connection reset...
            error = STTError(f"Network problem: {type(exc).__name__}: {exc}", kind="network",
                             retryable=True, request_id=rid)
        if not error.retryable or attempt >= retries:
            raise error
        attempt += 1
        sleep_fn(0.5 * attempt)


def _result_from_ok(resp, rid: str, config: STTConfig, elapsed: float) -> STTResult:
    try:
        body = resp.json()
    except ValueError:
        raise STTError("HTTP 200 but the body was not JSON.", kind="unknown", http_status=200, request_id=rid) from None
    transcript = body.get("transcript", "")
    if transcript is None:
        transcript = ""
    if not isinstance(transcript, str):  # docs: always a string on prisma-v3.0
        raise STTError(f"Unexpected transcript type: {type(transcript).__name__}", kind="unknown",
                       http_status=200, request_id=rid)
    return STTResult(
        transcript=transcript.strip(),
        request_id=body.get("request_id", rid),
        config=config.name,
        detected_language=body.get("detected_language_code"),
        truncated=bool(body.get("truncated", False)),
        server_seconds=body.get("processing_time"),
        api_end_to_end_seconds=body.get("end_to_end_latency"),
        client_seconds=elapsed,
    )


# --------------------------------------------------------------------------
# Both steps together: this is what main.py will call
# --------------------------------------------------------------------------
def transcribe_audio(
    raw_audio: bytes,
    content_type: Optional[str] = None,
    **kwargs,
) -> STTResult:
    """Browser recording bytes -> transcript. kwargs go to transcribe() (config=, request_id=, ...)."""
    t0 = time.perf_counter()
    wav = to_wav_16k_mono(raw_audio, suffix_for(content_type))
    convert_seconds = time.perf_counter() - t0
    seconds = wav_duration_seconds(wav)
    if seconds > MAX_AUDIO_SECONDS:
        raise STTError(f"Recording is {seconds:.1f} s; the limit is {MAX_AUDIO_SECONDS} s.", kind="bad_audio")
    result = transcribe(wav, **kwargs)
    result.convert_seconds = convert_seconds
    return result


# --------------------------------------------------------------------------
# Command line: transcribe one real file and show how the parser reads it
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Transcribe one audio file with Prisma v3.0")
    ap.add_argument("path")
    ap.add_argument("--config", default=DEFAULT_CONFIG.name, choices=sorted(CONFIGS))
    args = ap.parse_args()

    path = Path(args.path)
    res = transcribe_audio(path.read_bytes(), config=CONFIGS[args.config])
    print(f"config      : {res.config}")
    print(f"transcript  : {res.transcript!r}")
    print(f"language    : {res.detected_language}   truncated: {res.truncated}   request_id: {res.request_id}")
    print(f"latency (s) : convert {res.convert_seconds:.2f} | http {res.client_seconds:.2f} | "
          f"server {res.server_seconds} | api end-to-end {res.api_end_to_end_seconds}")
    from app.parser import parse
    p = parse(res.transcript)
    print(f"parser      : crop={p['crop']} mandis={p['mandis']} day={p['day']} status={p['status']}")