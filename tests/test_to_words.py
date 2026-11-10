"""tests/test_to_words.py - run with:  uv run python -m tests.test_to_words

Checks the "speak numbers as Punjabi words" change (scripts/apply_to_words_fix.py).
All fakes; the real number_to_words is only imported where noted.
"""
import sys
import types
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main as main_mod
from app.main import create_app
from app.pipeline import run_turn
from tests.test_main import MP3, FakeStatus, Stages, post


class RecordingReply:
    """A reply whose render() records how it was called and uses to_words if given."""

    def __init__(self, call_it=True):
        self.status, self.state = FakeStatus("answer"), {"t": 1}
        self.calls = []
        self.call_it = call_it          # False: only record, never run the function

    def render(self, to_words=None):
        self.calls.append(to_words)
        return "price " + (to_words(200) if (to_words and self.call_it) else "200")


def stages_with(reply):
    s = Stages()
    s.step = lambda text, state: reply
    return s


def fake_numbers_module():
    """Stand-in for app.punjabi_numbers so the test does not need your real file."""
    mod = types.ModuleType("app.punjabi_numbers")
    mod.number_to_words = lambda n: "WORDS(%d)" % n
    return mod


class patched_numbers:
    def __enter__(self):
        self.old = sys.modules.get("app.punjabi_numbers")
        sys.modules["app.punjabi_numbers"] = fake_numbers_module()

    def __exit__(self, *a):
        if self.old is None:
            sys.modules.pop("app.punjabi_numbers", None)
        else:
            sys.modules["app.punjabi_numbers"] = self.old


def test_pipeline_passes_to_words_to_render():
    reply = RecordingReply()
    fn = lambda n: "W%d" % n
    r = run_turn(b"a", "audio/webm", None, **stages_with(reply).kw(to_words=fn))
    assert reply.calls == [fn] and r.reply_text == "price W200"


def test_pipeline_without_to_words_calls_render_plainly():
    reply = RecordingReply()
    r = run_turn(b"a", "audio/webm", None, **stages_with(reply).kw())
    assert reply.calls == [None] and r.reply_text == "price 200"


def test_app_default_is_real_to_words():
    reply = RecordingReply(call_it=False)      # just record; do not import your real module
    c = TestClient(create_app(web_dir=None, **stages_with(reply).kw()))
    assert post(c).status_code == 200
    assert reply.calls == [main_mod.real_to_words]


def test_app_can_switch_words_off():
    reply = RecordingReply()
    c = TestClient(create_app(web_dir=None, to_words=None, **stages_with(reply).kw()))
    j = post(c).json()
    assert reply.calls == [None] and j["reply_text"] == "price 200"


def test_real_to_words_imports_number_to_words_not_price_to_words():
    with patched_numbers():
        assert main_mod.real_to_words(200) == "WORDS(200)"


def test_end_to_end_reply_text_contains_words_not_digits():
    reply = RecordingReply()
    with patched_numbers():
        c = TestClient(create_app(web_dir=None, **stages_with(reply).kw()))
        j = post(c).json()
    assert j["reply_text"] == "price WORDS(200)"
    assert j["audio_b64"]                      # the spoken text is the words version


def test_tts_receives_the_words_text():
    reply = RecordingReply()
    s = stages_with(reply)
    with patched_numbers():
        c = TestClient(create_app(web_dir=None, **s.kw()))
        post(c)
    assert s.tts_calls == ["price WORDS(200)"]


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
