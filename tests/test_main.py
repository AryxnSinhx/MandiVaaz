"""tests/test_main.py - run with:  uv run python -m tests.test_main

Needs (once):  uv add fastapi uvicorn python-multipart   and   uv add --dev httpx
No network, no API key, no ffmpeg: every stage is a fake.
"""
import base64
import os
import tempfile
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main as main_mod
from app.main import create_app
from app.pipeline import SessionStore, clean_session_id, run_turn

MP3 = b"ID3-fake-mp3-bytes"


# ------------------------------------------------------------------ fakes
class FakeStatus:
    def __init__(self, value):
        self.value = value


class FakeReply:
    def __init__(self, text, status="answer", state=None):
        self._text, self.status, self.state = text, FakeStatus(status), state

    def render(self, to_words=None):
        return self._text


class StageError(Exception):
    def __init__(self, kind, msg="boom"):
        super().__init__(msg)
        self.kind = kind


class Stages:
    """Fake stt/agent/tts that record what they were given."""

    def __init__(self, transcript="ludhiana potato", fail_stt=None, fail_agent=False,
                 fail_tts=False, reply_text="REPLY", status="answer"):
        self.transcript, self.fail_stt, self.fail_agent = transcript, fail_stt, fail_agent
        self.fail_tts, self.reply_text, self.status = fail_tts, reply_text, status
        self.stt_calls, self.step_calls, self.tts_calls = [], [], []
        self.turns = 0

    def stt(self, raw, content_type):
        self.stt_calls.append((raw, content_type))
        if self.fail_stt:
            raise StageError(self.fail_stt, "secret detail")
        return SimpleNamespace(transcript=self.transcript, request_id="req-1",
                               convert_seconds=0.11, server_seconds=0.22)

    def step(self, text, state):
        self.step_calls.append((text, state))
        if self.fail_agent:
            raise RuntimeError("agent exploded")
        self.turns += 1
        return FakeReply(self.reply_text, self.status, state={"turn": self.turns})

    def tts(self, text):
        self.tts_calls.append(text)
        if self.fail_tts:
            raise StageError("timeout")
        return SimpleNamespace(audio=MP3, content_type="audio/mpeg", http_seconds=0.33)

    def kw(self, **extra):
        d = dict(stt_fn=self.stt, step_fn=self.step, tts_fn=self.tts,
                 error_text_fn=lambda: "ERRTEXT")
        d.update(extra)
        return d


def turn(stages, state=None, **extra):
    return run_turn(b"audio", "audio/webm", state, **stages.kw(**extra))


# --------------------------------------------------------------- pipeline
def test_happy_path():
    s = Stages()
    r = turn(s)
    assert r.transcript == "ludhiana potato" and r.reply_text == "REPLY"
    assert r.status == "answer" and r.audio == MP3 and r.audio_mime == "audio/mpeg"
    assert r.new_state == {"turn": 1} and r.failed_stages == [] and r.request_id == "req-1"
    assert s.tts_calls == ["REPLY"]
    for k in ("stt_wall", "stt_convert", "stt_server", "agent", "tts_http", "tts_wall", "total"):
        assert k in r.timings, k


def test_state_is_passed_to_agent():
    s = Stages()
    turn(s, state={"turn": 7})
    assert s.step_calls == [("ludhiana potato", {"turn": 7})]


def test_stt_failure_degrades_to_spoken_error():
    s = Stages(fail_stt="auth")
    r = turn(s, state={"keep": 1})
    assert r.status == "error" and r.reply_text == "ERRTEXT"
    assert r.audio == MP3                                   # error sentence is still spoken
    assert r.failed_stages == [{"stage": "stt", "kind": "auth"}]
    assert r.new_state == {"keep": 1} and s.step_calls == []
    assert "secret detail" not in repr(r.failed_stages)     # detail stays in the server log


def test_agent_failure_degrades_to_spoken_error():
    s = Stages(fail_agent=True)
    r = turn(s, state={"keep": 1})
    assert r.status == "error" and r.reply_text == "ERRTEXT"
    assert r.failed_stages == [{"stage": "agent", "kind": "RuntimeError"}]
    assert r.new_state == {"keep": 1} and r.transcript == "ludhiana potato"


def test_tts_failure_keeps_text_drops_audio():
    r = turn(Stages(fail_tts=True))
    assert r.reply_text == "REPLY" and r.audio is None and r.audio_mime is None
    assert r.failed_stages == [{"stage": "tts", "kind": "timeout"}]
    assert r.new_state == {"turn": 1}                       # the agent turn still counted


def test_error_text_failure_means_no_tts_call():
    s = Stages(fail_stt="network")

    def bad():
        raise ValueError("no templates")

    r = turn(s, error_text_fn=bad)
    assert r.reply_text == "" and r.audio is None and s.tts_calls == []


def test_render_may_return_list():
    s = Stages(reply_text=["ਇੱਕ", "ਦੋ", ""])
    assert turn(s).reply_text == "ਇੱਕ ਦੋ"


def test_empty_transcript_still_goes_to_agent():
    s = Stages(transcript="")
    turn(s)
    assert s.step_calls == [("", None)]    # the agent turns it into 'not understood'


def test_gate_is_used_for_stt_and_tts_only():
    events = []

    class Gate:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *a):
            events.append("exit")

    s = Stages()
    turn(s, gate=Gate())
    assert events == ["enter", "exit", "enter", "exit"]


# --------------------------------------------------------- session store
def test_session_ttl_and_drop():
    now = [100.0]
    st = SessionStore(ttl=10, clock=lambda: now[0])
    st.put("abc12345", "S1")
    assert st.get("abc12345") == "S1"
    now[0] += 9
    assert st.get("abc12345") == "S1"
    now[0] += 11                                           # >10 s since last put
    assert st.get("abc12345") is None and len(st) == 0
    st.put("abc12345", "S2")
    st.drop("abc12345")
    assert st.get("abc12345") is None


def test_session_cap_evicts_oldest():
    now = [0.0]
    st = SessionStore(ttl=1000, max_items=3, clock=lambda: now[0])
    for i in range(4):
        now[0] += 1
        st.put("sess%04d" % i, i)
    assert len(st) == 3 and st.get("sess0000") is None and st.get("sess0003") == 3


def test_clean_session_id():
    assert clean_session_id("abcdefgh12") == "abcdefgh12"
    for bad in ("", None, "short", "../../etc/passwd", "a b c d e f g h", "x" * 65):
        assert clean_session_id(bad) is None, bad


# ------------------------------------------------------------------- HTTP
def client_for(stages, **kw):
    return TestClient(create_app(web_dir=None, **stages.kw(**kw)))


def post(client, sid="session-0001", body=b"abc", ctype="audio/webm;codecs=opus", data=True):
    form = {"session_id": sid} if data else {}
    return client.post("/ask", files={"audio": ("clip.webm", body, ctype)}, data=form)


def test_ask_returns_json_with_base64_audio():
    s = Stages()
    c = client_for(s)
    r = post(c)
    assert r.status_code == 200
    j = r.json()
    assert j["transcript"] == "ludhiana potato" and j["reply_text"] == "REPLY"
    assert j["status"] == "answer" and j["session_id"] == "session-0001"
    assert base64.b64decode(j["audio_b64"]) == MP3 and j["audio_mime"] == "audio/mpeg"
    assert j["failed_stages"] == [] and "total" in j["timings"]


def test_content_type_parameters_are_stripped():
    s = Stages()
    post(client_for(s))
    assert s.stt_calls[0] == (b"abc", "audio/webm")


def test_conversation_state_follows_session_id():
    s = Stages()
    c = client_for(s)
    post(c, sid="session-AAAA")
    post(c, sid="session-AAAA")
    post(c, sid="session-BBBB")
    assert s.step_calls[0][1] is None                      # first turn: no state
    assert s.step_calls[1][1] == {"turn": 1}               # same session sees turn 1's state
    assert s.step_calls[2][1] is None                      # other session starts fresh


def test_missing_or_unsafe_session_id_gets_a_fresh_one():
    c = client_for(Stages())
    a = post(c, data=False).json()["session_id"]
    b = post(c, sid="../../bad").json()["session_id"]
    assert len(a) == 32 and len(b) == 32 and a != b


def test_failed_stt_does_not_advance_state():
    s = Stages(fail_stt="timeout")
    c = client_for(s)
    j = post(c).json()
    assert j["status"] == "error" and j["failed_stages"][0]["stage"] == "stt"
    s.fail_stt = None
    post(c)
    assert s.step_calls[0][1] is None                      # nothing was stored by the failed turn


def test_empty_and_oversize_uploads_rejected():
    s = Stages()
    c = client_for(s)
    assert post(c, body=b"").status_code == 400
    old = main_mod.MAX_UPLOAD_BYTES
    main_mod.MAX_UPLOAD_BYTES = 10
    try:
        assert post(c, body=b"x" * 11).status_code == 413
        assert post(c, body=b"x" * 10).status_code == 200
    finally:
        main_mod.MAX_UPLOAD_BYTES = old
    assert len(s.stt_calls) == 1                           # rejected uploads never reach STT


def test_missing_audio_field_is_422():
    c = client_for(Stages())
    assert c.post("/ask", data={"session_id": "session-0001"}).status_code == 422


def test_reset_forgets_the_session():
    s = Stages()
    c = client_for(s)
    post(c)
    assert c.post("/reset", data={"session_id": "session-0001"}).json() == {"ok": True}
    post(c)
    assert s.step_calls[1][1] is None


def test_health():
    j = client_for(Stages()).get("/health").json()
    assert j["ok"] is True and j["web"] is False and j["sessions"] == 0


def test_static_web_is_served_without_shadowing_api():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>mic page</html>")
        s = Stages()
        c = TestClient(create_app(web_dir=d, **s.kw()))
        assert "mic page" in c.get("/").text
        assert c.get("/health").json()["web"] is True
        assert post(c).status_code == 200                  # POST /ask still reaches the API
    assert client_for(Stages()).get("/").status_code == 404


def test_gate_serialises_concurrent_requests():
    active, peak, lock = [0], [0], threading.Lock()

    def slow_stt(raw, ctype):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return SimpleNamespace(transcript="x", request_id="r", convert_seconds=0, server_seconds=0)

    s = Stages()
    c = TestClient(create_app(web_dir=None, max_concurrent_gnani=1, **s.kw(stt_fn=slow_stt)))
    threads = [threading.Thread(target=post, args=(c, "session-%04d" % i)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 1, "two STT calls overlapped: %d" % peak[0]


# ------------------------------------------------------------------ runner
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
