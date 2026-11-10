"""
Tests for app/stt.py.   Run:  uv run python -m tests.test_stt

No network and no credits: the HTTP call is replaced by a FAKE. The ffmpeg tests are real
(they generate a short beep with ffmpeg itself) and are skipped if ffmpeg is not installed.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import requests

from app import stt
from app.stt import STTError, STTResult

KEY = "test-key"


# ---------------------------------------------------------------- fakes
class FakeResp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None or isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakePost:
    """Returns prepared responses in order and records every call."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


OK_BODY = {"success": True, "request_id": "r1", "transcript": " ਲੁਧਿਆਣਾ ਆਲੂ ਰੇਟ ", "model": "prisma-v3.0",
           "detected_language_code": "pa-IN", "processing_time": 0.4, "end_to_end_latency": 0.5}
OK = FakeResp(200, OK_BODY)
WAV = b"RIFF....fake"  # transcribe() never looks inside the bytes


def run(*responses, **kw):
    post = FakePost(*responses)
    sleeps = []
    result = stt.transcribe(WAV, api_key=KEY, post_fn=post, sleep_fn=sleeps.append, **kw)
    return result, post, sleeps


def expect_error(*responses, **kw):
    post = FakePost(*responses)
    sleeps = []
    try:
        stt.transcribe(WAV, api_key=KEY, post_fn=post, sleep_fn=sleeps.append, **kw)
    except STTError as exc:
        return exc, post, sleeps
    raise AssertionError("expected STTError")


# ---------------------------------------------------------------- request building
def test_every_config_sends_model_prisma_v3():
    for config in stt.CONFIGS.values():
        assert stt.build_form(config)["model"] == "prisma-v3.0", config.name


def test_plain_config_has_no_context_fields():
    form = stt.build_form(stt.CONFIGS["paen_codemix"])
    assert form == {"model": "prisma-v3.0", "language_code": "pa-IN,en-IN", "format": "codemix"}


def test_context_config_sends_topic_and_gurmukhi_keyterms_unescaped():
    form = stt.build_form(stt.CONFIGS["paen_codemix_ctx"])
    assert form["topic"] == stt.TOPIC
    assert "ਆਲੂ" in form["keyterms"]                      # readable Gurmukhi, not \\u0a06...
    terms = json.loads(form["keyterms"])
    assert 0 < len(terms) <= stt.MAX_KEYTERMS and "wheat" in terms and "ਕਣਕ" in terms


def test_request_url_headers_and_file_field():
    _, post, _ = run(OK, request_id="my-id")
    url, kw = post.calls[0]
    assert url == "https://api.vachana.ai/stt/v3"
    assert kw["headers"] == {"X-API-Key-ID": KEY, "X-API-Request-ID": "my-id"}
    name, content, mime = kw["files"]["audio_file"]
    assert content == WAV and mime == "audio/wav"


def test_missing_api_key_fails_before_any_network_call():
    import os
    saved = os.environ.pop(stt.API_KEY_ENV, None)
    try:
        post = FakePost()
        try:
            stt.transcribe(WAV, post_fn=post)
        except STTError as exc:
            assert exc.kind == "auth" and post.calls == []
            return
        raise AssertionError("expected STTError")
    finally:
        if saved is not None:
            os.environ[stt.API_KEY_ENV] = saved


# ---------------------------------------------------------------- reading responses
def test_ok_response_is_parsed_and_stripped():
    result, _, _ = run(OK)
    assert result.transcript == "ਲੁਧਿਆਣਾ ਆਲੂ ਰੇਟ"
    assert result.detected_language == "pa-IN" and result.truncated is False
    assert result.server_seconds == 0.4 and result.client_seconds >= 0


def test_empty_transcript_is_not_an_error():
    result, _, _ = run(FakeResp(200, {"success": True, "request_id": "r", "transcript": ""}))
    assert result.transcript == ""


def test_short_audio_response_without_model_or_timings_is_fine():
    result, _, _ = run(FakeResp(200, {"success": True, "request_id": "r", "transcript": ""}))
    assert result.server_seconds is None and result.detected_language is None


def test_truncated_flag_is_surfaced():
    result, _, _ = run(FakeResp(200, {**OK_BODY, "truncated": True}))
    assert result.truncated is True


def test_non_string_transcript_is_rejected():
    exc, _, _ = expect_error(FakeResp(200, {**OK_BODY, "transcript": {"a": 1}}))
    assert "transcript type" in str(exc)


# ---------------------------------------------------------------- the four error shapes
def test_envelope_error_bad_request_not_retried():
    body = {"success": False, "error": {"type": "INVALID_CONTEXT", "message": "topic too long"}}
    exc, post, _ = expect_error(FakeResp(400, body))
    assert (exc.kind, exc.error_type, exc.retryable) == ("bad_request", "INVALID_CONTEXT", False)
    assert len(post.calls) == 1


def test_envelope_error_audio_problem_is_bad_audio():
    body = {"success": False, "error": {"type": "MAX_AUDIO_DURATION_EXCEEDED", "message": "too long"}}
    exc, _, _ = expect_error(FakeResp(400, body))
    assert exc.kind == "bad_audio"


def test_gateway_error_invalid_key_is_auth_and_not_retried():
    body = {"detail": {"error_code": "INVALID_API_KEY", "message": "Unauthorized", "status_code": 401}}
    exc, post, _ = expect_error(FakeResp(401, body))
    assert (exc.kind, exc.error_type) == ("auth", "INVALID_API_KEY") and len(post.calls) == 1


def test_validation_error_422_list_shape():
    body = {"detail": [{"type": "missing", "loc": ["body", "language_code"], "msg": "Field required"}]}
    exc, _, _ = expect_error(FakeResp(422, body))
    assert exc.error_type == "VALIDATION" and "language_code" in str(exc)


def test_timeout_504_shape_is_timeout_kind():
    body = {"error": "Gateway Timeout", "code": "UPSTREAM_TIMEOUT", "message": "no response in 5000ms"}
    exc, _, _ = expect_error(FakeResp(504, body), FakeResp(504, body))
    assert (exc.kind, exc.error_type, exc.retryable) == ("timeout", "UPSTREAM_TIMEOUT", True)


def test_non_json_error_body_still_gives_a_clean_error():
    exc, _, _ = expect_error(FakeResp(502, None, text="<html>Bad gateway</html>"), FakeResp(502, None, text="x"))
    assert exc.kind == "server" and exc.http_status == 502


def test_402_is_credit_kind():
    exc, _, _ = expect_error(FakeResp(402, {"detail": {"error_code": "X", "message": "no credit"}}))
    assert exc.kind == "credit"


# ---------------------------------------------------------------- retry policy
RATE = {"detail": {"error_code": "RATE_LIMITED", "message": "Only 1 active request", "status_code": 429}}


def test_retries_once_on_429_then_succeeds():
    result, post, sleeps = run(FakeResp(429, RATE), OK)
    assert result.transcript and len(post.calls) == 2 and len(sleeps) == 1


def test_gives_up_after_retries_exhausted():
    exc, post, sleeps = expect_error(FakeResp(429, RATE), FakeResp(429, RATE), FakeResp(200, OK_BODY))
    assert exc.kind == "rate_limit" and len(post.calls) == 2   # 1 try + 1 retry, third response unused
    assert len(sleeps) == 1


def test_retries_zero_means_one_attempt():
    exc, post, _ = expect_error(FakeResp(429, RATE), retries=0)
    assert len(post.calls) == 1


def test_network_error_is_retried():
    result, post, _ = run(requests.ConnectionError("boom"), OK)
    assert result.transcript and len(post.calls) == 2


def test_network_error_twice_raises_network_kind():
    exc, _, _ = expect_error(requests.Timeout("slow"), requests.Timeout("slow"))
    assert exc.kind == "network"


# ---------------------------------------------------------------- helpers
def test_suffix_for_content_types():
    assert stt.suffix_for("audio/webm;codecs=opus") == ".webm"
    assert stt.suffix_for("audio/mp4") == ".m4a"
    assert stt.suffix_for("AUDIO/OGG") == ".ogg"
    assert stt.suffix_for(None) == ".bin" and stt.suffix_for("text/plain") == ".bin"


# ---------------------------------------------------------------- real ffmpeg (skipped if absent)
def make_beep(path: Path, seconds: float, codec_args):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", *codec_args, str(path)], check=True)


def beep_bytes(seconds: float, suffix: str, codec_args) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"beep{suffix}"
        make_beep(p, seconds, codec_args)
        return p.read_bytes()


def test_ffmpeg_converts_webm_opus_to_16k_mono_wav():
    if not shutil.which("ffmpeg"):
        print("   (skipped: ffmpeg not installed)")
        return
    webm = beep_bytes(1.0, ".webm", ["-c:a", "libopus"])
    wav = stt.to_wav_16k_mono(webm, ".webm")
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    assert 0.9 < stt.wav_duration_seconds(wav) < 1.2


def test_ffmpeg_rejects_garbage_and_empty_input():
    if not shutil.which("ffmpeg"):
        return
    for bad in (b"this is not audio at all" * 20, b""):
        try:
            stt.to_wav_16k_mono(bad, ".webm")
        except STTError as exc:
            assert exc.kind == "bad_audio"
        else:
            raise AssertionError("expected STTError")


def test_missing_ffmpeg_gives_setup_error():
    try:
        stt.to_wav_16k_mono(b"x", ".webm", ffmpeg="definitely-not-installed-ffmpeg")
    except STTError as exc:
        assert exc.kind == "setup"
    else:
        raise AssertionError("expected STTError")


def test_transcribe_audio_end_to_end_with_fake_http():
    if not shutil.which("ffmpeg"):
        return
    webm = beep_bytes(1.0, ".webm", ["-c:a", "libopus"])
    post = FakePost(OK)
    result = stt.transcribe_audio(webm, "audio/webm;codecs=opus", api_key=KEY, post_fn=post)
    assert isinstance(result, STTResult) and result.transcript and result.convert_seconds > 0
    sent = post.calls[0][1]["files"]["audio_file"][1]
    assert sent[:4] == b"RIFF"                      # what we sent is a WAV, not the WebM


def test_recording_over_30_seconds_is_rejected_without_calling_the_api():
    if not shutil.which("ffmpeg"):
        return
    long_webm = beep_bytes(31.0, ".webm", ["-c:a", "libopus"])
    post = FakePost()
    try:
        stt.transcribe_audio(long_webm, "audio/webm", api_key=KEY, post_fn=post)
    except STTError as exc:
        assert exc.kind == "bad_audio" and post.calls == []
    else:
        raise AssertionError("expected STTError")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")