"""app/main.py - Unit 5b: the FastAPI server.

Run from the project root:
    uv run --env-file .env uvicorn app.main:app --reload

Endpoints
    POST /ask     multipart: audio (file) + session_id (text)  -> JSON
    POST /reset   form: session_id                             -> JSON
    GET  /health
    GET  /        serves web/ (the mic page, built in Unit 6) if the folder exists

The API keys live only in this process (GNANI_API_KEY from .env); the browser
never sees them.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from app.pipeline import SessionStore, clean_session_id, run_turn

log = logging.getLogger("mandivaaz.main")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024        # 10 s of browser opus is ~100 KB; 5 MB is generous
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# --------------------------------------------------------------------------
# The real stages. Imported lazily so tests (and this file's import) do not
# need your other modules or an API key.
# --------------------------------------------------------------------------
def real_stt(raw: bytes, content_type: str) -> Any:
    from app import stt
    return stt.transcribe_audio(raw, content_type)


def real_step(text: str, state: Any) -> Any:
    from app import agent, lookup, parser
    return agent.step(text, state, parse_fn=parser.parse, price_fn=lookup.get_price)


def real_tts(text: str) -> Any:
    from app import tts
    return tts.synthesize(text)


def real_error_text() -> str:
    from app import templates
    return templates.render(templates.Status.error)


def real_to_words(n: int) -> str:
    """Digits -> Punjabi words ('200' -> 'ਦੋ ਸੌ'). Chosen by listening on Nov 7.
    Imported lazily so this file loads without your other modules.
    NOT price_to_words: that one adds its own word for rupees, and the
    templates already say it, giving 'ਰੁਪਏ ਰੁਪਏ'."""
    from app.punjabi_numbers import number_to_words
    return number_to_words(n)


def _base_mime(content_type: Optional[str]) -> str:
    """'audio/webm;codecs=opus' -> 'audio/webm' (browsers add parameters)."""
    return (content_type or "application/octet-stream").split(";")[0].strip().lower()


def create_app(
    *,
    stt_fn: Callable = real_stt,
    step_fn: Callable = real_step,
    tts_fn: Callable = real_tts,
    error_text_fn: Callable = real_error_text,
    to_words: Optional[Callable] = real_to_words,   # None = send digits as they are
    store: Optional[SessionStore] = None,
    max_concurrent_gnani: Optional[int] = None,
    web_dir: Optional[Path] = WEB_DIR,
) -> FastAPI:
    app = FastAPI(title="Mandi Vaaz")
    store = store if store is not None else SessionStore()

    # Some Gnani accounts allow only ONE active request (HTTP 429 otherwise).
    # This gate makes concurrent browser tabs queue instead of colliding.
    # Unverified for your account: raise GNANI_MAX_CONCURRENT if it allows more.
    slots = max_concurrent_gnani or int(os.environ.get("GNANI_MAX_CONCURRENT", "1"))
    gate = threading.BoundedSemaphore(max(1, slots))

    # NOTE: plain `def` (not `async def`) on purpose. The Gnani calls block for
    # seconds; FastAPI runs plain-def endpoints in a thread pool, so one slow
    # request does not freeze the server.
    @app.post("/ask")
    def ask(audio: UploadFile = File(...), session_id: str = Form("")) -> dict:
        sid = clean_session_id(session_id) or uuid.uuid4().hex
        raw = audio.file.read(MAX_UPLOAD_BYTES + 1)
        if not raw:
            raise HTTPException(status_code=400, detail="empty audio upload")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="audio too large")

        result = run_turn(
            raw, _base_mime(audio.content_type), store.get(sid),
            stt_fn=stt_fn, step_fn=step_fn, tts_fn=tts_fn,
            error_text_fn=error_text_fn, gate=gate, to_words=to_words,
        )
        # Only a successful agent turn moves the conversation forward.
        if not any(f["stage"] in ("stt", "agent") for f in result.failed_stages):
            store.put(sid, result.new_state)

        log.info("turn sid=%s status=%s failed=%s total=%ss", sid[:8], result.status,
                 [f["stage"] for f in result.failed_stages], result.timings.get("total"))
        return {
            "session_id": sid,
            "request_id": result.request_id,
            "transcript": result.transcript,
            "reply_text": result.reply_text,
            "status": result.status,
            "audio_mime": result.audio_mime,
            "audio_b64": base64.b64encode(result.audio).decode("ascii") if result.audio else None,
            "failed_stages": result.failed_stages,
            "timings": result.timings,
        }

    @app.post("/reset")
    def reset(session_id: str = Form("")) -> dict:
        sid = clean_session_id(session_id)
        if sid:
            store.drop(sid)
        return {"ok": True}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "web": bool(web_dir and Path(web_dir).is_dir()),
                "sessions": len(store)}

    # Mount the static page LAST so it cannot shadow the API routes above.
    if web_dir and Path(web_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


app = create_app()
