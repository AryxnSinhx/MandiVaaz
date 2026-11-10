"""tests/test_web_files.py - run with:  uv run python -m tests.test_web_files

No Node needed. Checks that the page files fit together and that the real
server serves them. (The page LOGIC is tested in tests/web/app.test.js.)
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

WEB = Path(__file__).resolve().parent.parent / "web"


def _html():
    return (WEB / "index.html").read_text(encoding="utf-8")


def _js():
    return (WEB / "app.js").read_text(encoding="utf-8")


def test_every_id_used_by_app_js_exists_in_the_html():
    used = set(re.findall(r'getElementById\("([^"]+)"\)', _js()))
    present = set(re.findall(r'\bid="([^"]+)"', _html()))
    assert used, "found no getElementById calls - did the pattern change?"
    missing = used - present
    assert not missing, "app.js looks for ids that index.html lacks: %s" % sorted(missing)


def test_html_loads_app_js_and_declares_utf8_and_viewport():
    html = _html()
    assert '<script src="app.js"></script>' in html
    assert '<meta charset="utf-8">' in html and "viewport" in html


def test_no_innerhtml_anywhere_in_app_js():
    # server text must only ever go in through textContent
    assert "innerHTML" not in _js()


def test_page_states_the_data_limits_plainly():
    html = _html()
    assert "March 2026" in html and "not today" in html
    for crop in ("potato", "onion", "tomato"):
        assert crop in html


def test_server_serves_the_real_page_and_still_answers_the_api():
    c = TestClient(create_app(web_dir=WEB, stt_fn=None, step_fn=None, tts_fn=None,
                              error_text_fn=lambda: ""))
    home = c.get("/")
    assert home.status_code == 200 and "Mandi Vaaz" in home.text
    js = c.get("/app.js")
    assert js.status_code == 200 and "createApp" in js.text
    assert c.get("/health").json()["web"] is True


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
