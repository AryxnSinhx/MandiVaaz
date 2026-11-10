"""app/pipeline.py - Unit 5a: one voice turn, from audio bytes to reply + mp3.

    audio bytes --STT--> transcript --agent--> reply text --TTS--> mp3 bytes

Nothing here imports FastAPI or the Gnani modules. The three stages are passed
in as functions (dependency injection), so the tests run with fakes.

Failure policy: a stage may fail (bad key, timeout, ffmpeg problem...). The
pipeline never crashes the request. It records which stage failed, says so in
`failed_stages`, and degrades:
    STT or agent fails -> Punjabi "error" sentence, spoken if TTS still works
    TTS fails          -> text reply only, audio is None (the page still shows text)
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("mandivaaz.pipeline")

SESSION_TTL_S = 15 * 60       # forget a session after 15 minutes of silence
SESSION_MAX = 500             # hard cap so memory cannot grow without limit
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# --------------------------------------------------------------------------
# Sessions: one agent State per browser tab (open item 11: state used to live
# forever; now it expires).
# --------------------------------------------------------------------------
def clean_session_id(raw: Optional[str]) -> Optional[str]:
    """Return raw if it is a safe-looking id, else None (caller makes a new one)."""
    if raw and _SESSION_ID_RE.match(raw):
        return raw
    return None


class SessionStore:
    def __init__(self, ttl: float = SESSION_TTL_S, max_items: int = SESSION_MAX,
                 clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl
        self._max = max_items
        self._clock = clock
        self._data: Dict[str, tuple] = {}     # id -> (state, last_seen)
        self._lock = threading.Lock()

    def get(self, sid: str) -> Any:
        with self._lock:
            item = self._data.get(sid)
            if item is None:
                return None
            state, seen = item
            if self._clock() - seen > self._ttl:
                del self._data[sid]
                return None
            return state

    def put(self, sid: str, state: Any) -> None:
        with self._lock:
            now = self._clock()
            self._data[sid] = (state, now)
            if len(self._data) > self._max:
                self._sweep(now)

    def drop(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)

    def _sweep(self, now: float) -> None:
        # expired first, then oldest, until we are back under the cap
        for k in [k for k, (_, seen) in self._data.items() if now - seen > self._ttl]:
            del self._data[k]
        while len(self._data) > self._max:
            oldest = min(self._data, key=lambda k: self._data[k][1])
            del self._data[oldest]

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# --------------------------------------------------------------------------
# One turn
# --------------------------------------------------------------------------
@dataclass
class TurnResult:
    transcript: str
    reply_text: str
    status: str
    new_state: Any
    audio: Optional[bytes] = None
    audio_mime: Optional[str] = None
    request_id: Optional[str] = None
    timings: Dict[str, Optional[float]] = field(default_factory=dict)
    failed_stages: List[Dict[str, str]] = field(default_factory=list)


def _as_text(rendered: Any) -> str:
    """reply.render() should give a string; accept a list of sentences too."""
    if isinstance(rendered, (list, tuple)):
        return " ".join(str(x) for x in rendered if x)
    return str(rendered or "")


def _status_name(status: Any) -> str:
    return str(getattr(status, "value", status))


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 3)


def run_turn(
    raw_audio: bytes,
    content_type: str,
    state: Any,
    *,
    stt_fn: Callable[[bytes, str], Any],
    step_fn: Callable[[str, Any], Any],
    tts_fn: Callable[[str], Any],
    error_text_fn: Callable[[], str],
    gate: Optional[Any] = None,
    to_words: Optional[Callable[[int], str]] = None,
    clock: Callable[[], float] = time.perf_counter,
) -> TurnResult:
    """Run STT -> agent -> TTS once. Never raises for a stage failure."""
    gate = gate or contextlib.nullcontext()
    t_all = clock()
    failed: List[Dict[str, str]] = []
    timings: Dict[str, Optional[float]] = {}

    def fail(stage: str, exc: Exception) -> None:
        kind = str(getattr(exc, "kind", type(exc).__name__))
        failed.append({"stage": stage, "kind": kind})
        # full detail goes to the server log only, never to the browser
        log.warning("stage %s failed: %s", stage, exc)

    def safe_error_text() -> str:
        try:
            return error_text_fn()
        except Exception as exc:  # noqa: BLE001 - last line of defence
            log.error("error_text_fn failed: %s", exc)
            return ""

    # ---- 1. speech to text ------------------------------------------------
    transcript, request_id = "", None
    stt_res = None
    t = clock()
    try:
        with gate:
            stt_res = stt_fn(raw_audio, content_type)
    except Exception as exc:  # noqa: BLE001 - request boundary
        fail("stt", exc)
    timings["stt_wall"] = _r(clock() - t)
    if stt_res is not None:
        transcript = getattr(stt_res, "transcript", "") or ""
        request_id = getattr(stt_res, "request_id", None)
        timings["stt_convert"] = _r(getattr(stt_res, "convert_seconds", None))
        timings["stt_server"] = _r(getattr(stt_res, "server_seconds", None))

    # ---- 2. agent decides -------------------------------------------------
    reply_text, status, new_state = "", "error", state
    if stt_res is not None:
        t = clock()
        try:
            reply = step_fn(transcript, state)
            rendered = reply.render(to_words=to_words) if to_words is not None else reply.render()
            reply_text = _as_text(rendered)
            status = _status_name(reply.status)
            new_state = reply.state
        except Exception as exc:  # noqa: BLE001
            fail("agent", exc)
            reply_text, status, new_state = "", "error", state
        timings["agent"] = _r(clock() - t)
    if status == "error" and not reply_text:
        reply_text = safe_error_text()

    # ---- 3. text to speech ------------------------------------------------
    audio, audio_mime = None, None
    if reply_text:
        t = clock()
        try:
            with gate:
                tts_res = tts_fn(reply_text)
            audio = tts_res.audio
            audio_mime = getattr(tts_res, "content_type", None) or "audio/mpeg"
            timings["tts_http"] = _r(getattr(tts_res, "http_seconds", None))
        except Exception as exc:  # noqa: BLE001
            fail("tts", exc)
        timings["tts_wall"] = _r(clock() - t)

    timings["total"] = _r(clock() - t_all)
    return TurnResult(
        transcript=transcript, reply_text=reply_text, status=status,
        new_state=new_state, audio=audio, audio_mime=audio_mime,
        request_id=request_id, timings=timings, failed_stages=failed,
    )
