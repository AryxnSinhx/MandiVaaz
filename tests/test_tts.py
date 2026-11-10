"""tests/test_tts.py - run with:  uv run python -m tests.test_tts

No network, no API key: every HTTP call is a fake passed in as `post`.
"""
import os
import tempfile

import requests

from app import tts
from app.tts import TTSError, TTSResult, synthesize

KEY = "secret-key-123"
MP3 = b"ID3\x04\x00\x00" + b"\x00" * 200   # not real audio, just non-empty bytes


class FakeResp:
    def __init__(self, status=200, content=b"", ctype="audio/mpeg", json_body=None, text=""):
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": ctype}
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakePost:
    """Plays back a script of responses/exceptions, one per call, and records calls."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def ok():
    return FakeResp(200, MP3)


def run(post, **kw):
    sleeps = []
    kw.setdefault("api_key", KEY)
    res = synthesize("ਸਤ ਸ੍ਰੀ ਅਕਾਲ", post=post, sleep=sleeps.append, jitter=False, **kw)
    return res, sleeps


def expect_error(kind, post=None, text="ਸਤ ਸ੍ਰੀ ਅਕਾਲ", **kw):
    post = post or FakePost(ok())
    kw.setdefault("api_key", KEY)
    try:
        synthesize(text, post=post, sleep=lambda s: None, jitter=False, **kw)
    except TTSError as exc:
        assert exc.kind == kind, "wanted %s got %s (%s)" % (kind, exc.kind, exc)
        assert KEY not in str(exc), "API key leaked into the error text"
        return exc, post
    raise AssertionError("expected TTSError(%s), nothing raised" % kind)


# ---------------------------------------------------------------- happy path
def test_payload_and_headers():
    post = FakePost(ok())
    res, _ = run(post)
    call = post.calls[0]
    assert call["url"] == "https://api.vachana.ai/api/v1/tts/inference"
    body = call["json"]
    assert body["model"] == "timbre-v3.0" and body["voice"] == "Shivani"
    assert body["language"] == "pa-IN" and body["speed"] == 1.0
    assert body["audio_config"]["container"] == "mp3"
    assert call["headers"]["X-API-Key-ID"] == KEY
    assert call["headers"]["X-API-Request-ID"] == res.request_id
    assert call["timeout"] == (5, 30)


def test_success_result_fields():
    res, sleeps = run(FakePost(ok()))
    assert isinstance(res, TTSResult)
    assert res.audio == MP3 and res.content_type == "audio/mpeg"
    assert res.attempts == 1 and sleeps == []
    assert res.chars == len("ਸਤ ਸ੍ਰੀ ਅਕਾਲ")
    assert res.client_seconds >= res.http_seconds >= 0


def test_request_id_given_and_generated():
    res, _ = run(FakePost(ok()), request_id="order-1")
    assert res.request_id == "order-1"
    res2, _ = run(FakePost(ok()))
    assert len(res2.request_id) == 36        # uuid4 text form


def test_text_whitespace_is_cleaned():
    post = FakePost(ok())
    synthesize("  ਸਤ \n ਸ੍ਰੀ   ਅਕਾਲ ", api_key=KEY, post=post, sleep=lambda s: None)
    assert post.calls[0]["json"]["text"] == "ਸਤ ਸ੍ਰੀ ਅਕਾਲ"


def test_save_writes_file():
    res, _ = run(FakePost(ok()))
    with tempfile.TemporaryDirectory() as d:
        p = res.save(os.path.join(d, "a.mp3"))
        assert open(p, "rb").read() == MP3


# ------------------------------------------------------- local validation
def test_missing_key_is_setup_error_and_no_call():
    old = os.environ.pop("GNANI_API_KEY", None)
    try:
        post = FakePost(ok())
        try:
            synthesize("x", post=post)
            raise AssertionError("should have raised")
        except TTSError as exc:
            assert exc.kind == "setup"
        assert post.calls == []
    finally:
        if old is not None:
            os.environ["GNANI_API_KEY"] = old


def test_empty_text_rejected_without_call():
    _, post = expect_error("bad_request", text="   \n ")
    assert post.calls == []


def test_text_too_long_rejected():
    _, post = expect_error("bad_request", text="ਅ" * (tts.MAX_TEXT_CHARS + 1))
    assert post.calls == []
    run(FakePost(ok()))  # sanity: normal text still works


def test_unknown_voice_strict_and_relaxed():
    _, post = expect_error("bad_request", voice="Nalini")   # a v2.5 voice
    assert post.calls == []
    res, _ = run(FakePost(ok()), voice="Nalini", strict_voice=False)
    assert res.voice == "Nalini"


def test_speed_bounds():
    for bad in (0.84, 1.16, "fast", True, None):
        expect_error("bad_request", speed=bad)
    for good in (0.85, 1.0, 1.15, 1):
        run(FakePost(ok()), speed=good)


# --------------------------------------------------------- error mapping
def test_401_gateway_shape_is_auth_no_retry():
    body = {"detail": {"error_code": "INVALID_API_KEY", "message": "Unauthorized", "status_code": 401}}
    exc, post = expect_error("auth", FakePost(FakeResp(401, json_body=body, ctype="application/json")))
    assert len(post.calls) == 1
    assert "INVALID_API_KEY" in str(exc) and exc.status == 401


def test_402_is_credit_no_retry():
    exc, post = expect_error("credit", FakePost(FakeResp(402, json_body={"message": "no credit"})))
    assert len(post.calls) == 1


def test_403_standard_shape_is_auth():
    body = {"success": False, "error": {"type": "FORBIDDEN", "message": "check credits"}}
    exc, _ = expect_error("auth", FakePost(FakeResp(403, json_body=body)))
    assert "FORBIDDEN" in str(exc)


def test_400_flat_shape_is_bad_request_no_retry():
    body = {"success": False, "message": "Model 'x' is not supported", "status_code": 400}
    exc, post = expect_error("bad_request", FakePost(FakeResp(400, json_body=body)))
    assert len(post.calls) == 1 and "not supported" in str(exc)


def test_422_detail_list_message():
    body = {"detail": [{"msg": "Field required", "loc": ["body", "text"]}]}
    exc, _ = expect_error("bad_request", FakePost(FakeResp(422, json_body=body)))
    assert "Field required" in str(exc)


def test_non_json_error_body_is_truncated():
    html = "<html>" + "bad gateway " * 100 + "</html>"
    exc, post = expect_error("server", FakePost(FakeResp(502, text=html)))
    assert len(str(exc)) < 300 and len(post.calls) == tts.MAX_ATTEMPTS


# ------------------------------------------------------------- retries
def test_429_then_success():
    busy = FakeResp(429, json_body={"detail": {"error_code": "RATE_LIMITED", "message": "wait"}})
    post = FakePost(busy, ok())
    res, sleeps = run(post)
    assert res.attempts == 2 and len(post.calls) == 2 and sleeps == [0.5]


def test_429_forever_gives_rate_limit_after_3_tries():
    busy = FakeResp(429, json_body={"detail": {"error_code": "RATE_LIMITED", "message": "wait"}})
    exc, post = expect_error("rate_limit", FakePost(busy))
    assert len(post.calls) == 3 and exc.attempts == 3


def test_backoff_sequence():
    post = FakePost(FakeResp(500), FakeResp(500), ok())
    res, sleeps = run(post)
    assert res.attempts == 3 and sleeps == [0.5, 1.0]


def test_500_forever_is_server_error():
    exc, post = expect_error("server", FakePost(FakeResp(500, json_body={"message": "down"})))
    assert len(post.calls) == 3


def test_timeout_retried_then_kind_timeout():
    exc, post = expect_error("timeout", FakePost(requests.exceptions.ReadTimeout("slow")))
    assert len(post.calls) == 3


def test_connection_error_is_network():
    exc, _ = expect_error("network", FakePost(requests.exceptions.ConnectionError("dns")))
    assert "ConnectionError" in str(exc)


def test_timeout_then_success():
    res, sleeps = run(FakePost(requests.exceptions.ConnectTimeout("t"), ok()))
    assert res.attempts == 2


def test_max_attempts_one_means_no_retry():
    post = FakePost(FakeResp(500))
    expect_error("server", post, max_attempts=1)
    assert len(post.calls) == 1


# ------------------------------------------------- 200 but not audio
def test_200_json_is_bad_response_no_retry():
    r = FakeResp(200, b'{"success": false}', ctype="application/json", json_body={"message": "odd"})
    exc, post = expect_error("bad_response", FakePost(r))
    assert len(post.calls) == 1


def test_200_empty_body_is_bad_response():
    expect_error("bad_response", FakePost(FakeResp(200, b"")))


# --------------------------------------------------- error text helper
def test_error_message_shapes():
    f = tts._error_message
    assert f(FakeResp(json_body={"message": "flat"})) == "flat"
    assert f(FakeResp(json_body={"error": {"type": "T", "message": "std"}})) == "T: std"
    assert f(FakeResp(json_body={"detail": {"error_code": "E", "message": "gw"}})) == "E: gw"
    assert f(FakeResp(json_body={"detail": [{"msg": "m"}]})) == "m"
    assert f(FakeResp(text="plain")) == "plain"
    assert f(FakeResp()) == "no error body"


# ------------------------------------------------------- digits test CLI
def test_digits_test_writes_two_files_with_injected_words():
    seen = []

    def fake_synth(sentence, **kw):
        seen.append(sentence)
        return TTSResult(MP3, "audio/mpeg", "id", "m", "v", "pa-IN", 1.0, len(sentence), 1, 0.1, 0.1)

    with tempfile.TemporaryDirectory() as d:
        written = tts.run_digits_test(2425, synth=fake_synth, to_words=lambda n: "WORDS%d" % n,
                                      out_dir=d)
        assert sorted(os.path.basename(p) for p in written) == ["digits_raw.mp3", "digits_words.mp3"]
        assert all(os.path.exists(p) for p in written)
    assert "2425" in seen[0] and "WORDS2425" not in seen[0]
    assert "WORDS2425" in seen[1]
    assert not any(ch.isdigit() for ch in seen[1].replace("WORDS2425", ""))  # no raw digits left


# ------------------------------------------------------------ runner
if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL", name, "->", repr(exc))
    print("\n%d tests, %d failed" % (len(tests), failed))
    raise SystemExit(1 if failed else 0)