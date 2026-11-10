"""app/tts.py - Unit 4: Timbre v3.0 text-to-speech over raw REST.

One public function, synthesize(), turns a Punjabi reply string into mp3 bytes.

Why raw REST and not the gnani-vachana SDK: the SDK still defaults to
timbre-v2.0 (no longer served) and we want exact control of the `model` field.

Everything that touches the outside world is injectable (post, sleep, api_key)
so the tests can run with fakes, the same method as test_stt.py.

CLI (run from the project root):
    uv run --env-file .env python -m app.tts "ਸਤ ਸ੍ਰੀ ਅਕਾਲ" --out hello.mp3
    uv run --env-file .env python -m app.tts --digits-test
"""
from __future__ import annotations

import os
import random
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import requests

# --------------------------------------------------------------------------
# Constants. Source: docs.gnani.ai/api/TTS/tts-inference.md (read Nov 7, 2026)
# --------------------------------------------------------------------------
TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
API_KEY_ENV = "GNANI_API_KEY"

DEFAULT_MODEL = "timbre-v3.0"
DEFAULT_VOICE = "Shivani"      # builder's choice: voice is not important for now
DEFAULT_LANGUAGE = "pa-IN"     # we always send Gurmukhi text, so say Punjabi explicitly

# The 13 timbre-v3.0 voices listed in the docs; each speaks all 10 languages.
V30_VOICES = frozenset({
    "Avinash", "Shivani", "Lakshmi", "Kalpana", "Shubham", "Prakash",
    "Devansh", "Yashraj", "Chetana", "Vasudha", "Riyaa", "Tarika", "Harshad",
})

SPEED_MIN, SPEED_MAX = 0.85, 1.15   # documented range (the docs say it may vary by model)

# OUR OWN safety limit, not an API limit. The docs only say "text that is too
# long" gives a 500 and do not state a number. Price replies are ~100 chars.
MAX_TEXT_CHARS = 600

# Browser-friendly output. sample_rate 24000 is in the documented supported
# list; 48000 is the API default. Change here if the audio sounds thin.
AUDIO_CONFIG = {
    "sample_rate": 24000,
    "num_channels": 1,
    "sample_width": 2,
    "container": "mp3",
    "bitrate": "128k",
}

CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 30
MAX_ATTEMPTS = 3          # 1 try + 2 retries
BACKOFF_BASE_S = 0.5      # waits 0.5 s, then 1.0 s (plus a little jitter)


# --------------------------------------------------------------------------
# Result and error types (shapes mirror the STT module as described in the
# handoff: a result dataclass, and one error class carrying a `kind`).
# --------------------------------------------------------------------------
class TTSError(Exception):
    """Anything that stops us getting audio. `kind` says what to do next."""

    KINDS = ("setup", "auth", "credit", "rate_limit", "bad_request",
             "timeout", "server", "network", "bad_response", "unknown")

    def __init__(self, kind: str, message: str, *, status: Optional[int] = None,
                 request_id: Optional[str] = None, attempts: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.request_id = request_id
        self.attempts = attempts

    def __str__(self) -> str:  # include the kind so logs are self-explanatory
        return "[%s] %s" % (self.kind, super().__str__())


@dataclass
class TTSResult:
    audio: bytes
    content_type: str
    request_id: str
    model: str
    voice: str
    language: str
    speed: float
    chars: int
    attempts: int
    http_seconds: float      # the one successful HTTP call
    client_seconds: float    # everything, including retries and waits

    def save(self, path: str) -> str:
        with open(path, "wb") as f:
            f.write(self.audio)
        return path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def _default_post(*args: Any, **kwargs: Any) -> Any:
    """Shared Session = connection reuse (skips repeat TLS handshakes)."""
    global _session
    with _session_lock:
        if _session is None:
            _session = requests.Session()
        sess = _session
    return sess.post(*args, **kwargs)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _error_message(resp: Any) -> str:
    """The API returns several different error JSON shapes. Dig out the message.

    flat:     {"success": false, "message": "...", "status_code": 400}
    gateway:  {"detail": {"error_code": "RATE_LIMITED", "message": "..."}}
    standard: {"success": false, "error": {"type": "...", "message": "..."}}
    422:      {"detail": [{"msg": "...", "loc": [...]}, ...]}
    """
    try:
        body = resp.json()
    except Exception:
        snippet = (getattr(resp, "text", "") or "").strip().replace("\n", " ")
        return snippet[:200] or "no error body"
    if not isinstance(body, dict):
        return str(body)[:200]
    if isinstance(body.get("message"), str):
        return body["message"][:300]
    err = body.get("error")
    if isinstance(err, dict) and err.get("message"):
        code = err.get("type")
        return ("%s: %s" % (code, err["message"]) if code else str(err["message"]))[:300]
    detail = body.get("detail")
    if isinstance(detail, dict) and detail.get("message"):
        code = detail.get("error_code")
        return ("%s: %s" % (code, detail["message"]) if code else str(detail["message"]))[:300]
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict) and first.get("msg"):
            return str(first["msg"])[:300]
    return str(body)[:200]


def _kind_for_status(status: int) -> str:
    if status == 401 or status == 403:
        return "auth"
    if status == 402:
        return "credit"            # docs mark 402 as "not verified"
    if status == 429:
        return "rate_limit"
    if status in (400, 404, 405, 413, 415, 422):
        return "bad_request"
    if status >= 500:
        return "server"
    return "unknown"


RETRYABLE = frozenset({"rate_limit", "server", "timeout", "network"})


def _validate(text: str, voice: str, model: str, language: str, speed: Any,
              strict_voice: bool) -> None:
    """Cheap local checks so obvious mistakes never cost a network call or credit."""
    if not text:
        raise TTSError("bad_request", "empty text (local check)")
    if len(text) > MAX_TEXT_CHARS:
        raise TTSError("bad_request", "text is %d chars, over our %d limit (local check)"
                       % (len(text), MAX_TEXT_CHARS))
    if not voice:
        raise TTSError("bad_request", "voice is required (local check)")
    if strict_voice and model == "timbre-v3.0" and voice not in V30_VOICES:
        raise TTSError("bad_request", "unknown timbre-v3.0 voice %r (local check)" % voice)
    if not language:
        raise TTSError("bad_request", "language is required (local check)")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise TTSError("bad_request", "speed must be a number (local check)")
    if not (SPEED_MIN <= float(speed) <= SPEED_MAX):
        raise TTSError("bad_request", "speed %s outside %s-%s (local check)"
                       % (speed, SPEED_MIN, SPEED_MAX))


# --------------------------------------------------------------------------
# The public function
# --------------------------------------------------------------------------
def synthesize(
    text: str,
    *,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
    speed: float = 1.0,
    api_key: Optional[str] = None,
    request_id: Optional[str] = None,
    audio_config: Optional[Dict[str, Any]] = None,
    strict_voice: bool = True,
    max_attempts: int = MAX_ATTEMPTS,
    post: Optional[Callable[..., Any]] = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: bool = True,
) -> TTSResult:
    """Speak `text`; return mp3 bytes plus timing. Raises TTSError on failure."""
    t_start = time.perf_counter()
    text = _clean_text(text)
    request_id = request_id or str(uuid.uuid4())

    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
    if not key:
        raise TTSError("setup", "%s is not set (run with --env-file .env)" % API_KEY_ENV,
                       request_id=request_id)
    _validate(text, voice, model, language, speed, strict_voice)

    payload = {
        "text": text,
        "voice": voice,
        "model": model,
        "language": language,
        "speed": float(speed),
        "audio_config": dict(audio_config or AUDIO_CONFIG),
    }
    headers = {
        "Content-Type": "application/json",
        "X-API-Key-ID": key,
        # Not in the REST header table; the SDK page says the SDK sends it. Harmless.
        "X-API-Request-ID": request_id,
    }
    do_post = post or _default_post

    last_err: Optional[TTSError] = None
    for attempt in range(1, max(1, max_attempts) + 1):
        if attempt > 1:
            delay = BACKOFF_BASE_S * (2 ** (attempt - 2))
            if jitter:
                delay += random.uniform(0, 0.25 * delay)
            sleep(delay)
        t_http = time.perf_counter()
        try:
            resp = do_post(TTS_URL, json=payload, headers=headers,
                           timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
        except requests.exceptions.Timeout:
            last_err = TTSError("timeout", "no response within %ss" % READ_TIMEOUT_S,
                                request_id=request_id, attempts=attempt)
        except requests.exceptions.RequestException as exc:
            # str(exc) can contain the URL but never our headers, so no key leak.
            last_err = TTSError("network", "%s: %s" % (type(exc).__name__, str(exc)[:150]),
                                request_id=request_id, attempts=attempt)
        else:
            http_seconds = time.perf_counter() - t_http
            status = resp.status_code
            if status == 200:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                body = resp.content or b""
                if not body or "json" in ctype or "text" in ctype:
                    # A 200 that is not audio: not worth retrying, tell the caller.
                    raise TTSError("bad_response",
                                   "200 OK but body is not audio (type=%r, %d bytes): %s"
                                   % (ctype, len(body), _error_message(resp)),
                                   status=200, request_id=request_id, attempts=attempt)
                return TTSResult(
                    audio=body, content_type=ctype or "audio/mpeg", request_id=request_id,
                    model=model, voice=voice, language=language, speed=float(speed),
                    chars=len(text), attempts=attempt, http_seconds=http_seconds,
                    client_seconds=time.perf_counter() - t_start)
            kind = _kind_for_status(status)
            last_err = TTSError(kind, "HTTP %d: %s" % (status, _error_message(resp)),
                                status=status, request_id=request_id, attempts=attempt)

        if last_err.kind not in RETRYABLE:
            raise last_err          # retrying a bad key or bad request only wastes time

    assert last_err is not None
    raise last_err                  # out of attempts


# --------------------------------------------------------------------------
# CLI: speak a sentence, or run the digits-vs-words listening test (open item 1)
# --------------------------------------------------------------------------
def run_digits_test(price: int = 2425, *, synth: Callable[..., TTSResult] = synthesize,
                    to_words: Optional[Callable[[int], str]] = None,
                    out_dir: str = ".") -> Dict[str, str]:
    """Make two mp3s of the same sentence: raw digits vs our Punjabi words.

    Listen to both. If digits_raw.mp3 says the price correctly in Punjabi,
    Timbre normalises digits itself and we can switch to_words off.
    """
    if to_words is None:
        from app.punjabi_numbers import price_to_words as to_words  # noqa: F811
    head = "ਲੁਧਿਆਣਾ ਵਿੱਚ ਆਲੂ ਦਾ ਭਾਅ "
    tail = " ਪ੍ਰਤੀ ਕੁਇੰਟਲ ਹੈ।"
    cases = {
        "digits_raw.mp3": head + "%d ਰੁਪਏ" % price + tail,
        "digits_words.mp3": head + to_words(price) + tail,
    }
    written = {}
    for fname, sentence in cases.items():
        res = synth(sentence)
        path = os.path.join(out_dir, fname)
        res.save(path)
        written[path] = sentence
        print("%-18s %6d bytes  %.2fs  %s" % (fname, len(res.audio), res.http_seconds, sentence))
    return written


def main(argv: Optional[list] = None) -> int:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Gurmukhi on the Windows console
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Timbre v3.0 TTS smoke test")
    ap.add_argument("text", nargs="?", help="sentence to speak")
    ap.add_argument("--out", default="tts_out.mp3")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--digits-test", action="store_true",
                    help="make digits_raw.mp3 and digits_words.mp3 for the listening test")
    ap.add_argument("--price", type=int, default=2425)
    args = ap.parse_args(argv)
    try:
        if args.digits_test:
            run_digits_test(args.price)
            return 0
        if not args.text:
            ap.error("give some text, or use --digits-test")
        res = synthesize(args.text, voice=args.voice, speed=args.speed)
        res.save(args.out)
        print("saved %s: %d bytes, %s, http %.2fs, total %.2fs, attempts %d, request_id %s"
              % (args.out, len(res.audio), res.content_type, res.http_seconds,
                 res.client_seconds, res.attempts, res.request_id))
        return 0
    except TTSError as exc:
        print("TTS failed:", exc, "(request_id=%s)" % exc.request_id)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())