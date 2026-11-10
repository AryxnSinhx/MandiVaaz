"""Tests for app/templates.py.   Run:  uv run python -m tests.test_templates"""
import sys

from app import templates as T
from app.templates import Status, render


def fake_words(n: int) -> str:
    """Stand-in for the real number-to-words function, so tests do not depend on it."""
    return f"<{n}>"


def test_answer_contains_all_facts():
    text = render(Status.ANSWER, crop="potato", mandi="ludhiana", price=2425.0,
                  last_date="2026-03-31", stale=False, to_words=fake_words)
    for piece in ("ਆਲੂ", "ਲੁਧਿਆਣਾ", "<2425>", "<31>", "ਮਾਰਚ", "<2026>"):
        assert piece in text, piece
    assert T.TEXT["stale_warning"] not in text


def test_stale_flag_adds_warning():
    text = T.answer("onion", "khanna", 1800, "2026-03-31", stale=True)
    assert T.TEXT["stale_warning"] in text


def test_stale_is_required():
    try:
        T.answer("onion", "khanna", 1800, "2026-03-31")  # forgot stale=
    except TypeError:
        return
    raise AssertionError("answer() must refuse to run without stale=")


def test_mandi_fallback_names_both_mandis():
    text = T.answer("onion", "khanna", 1800, "2026-03-31", stale=True, asked_mandi="barnala")
    assert "ਬਰਨਾਲਾ" in text and "ਖੰਨਾ" in text


def test_no_fallback_sentence_when_same_mandi():
    text = T.answer("onion", "khanna", 1800, "2026-03-31", stale=True, asked_mandi="khanna")
    assert "ਨੇੜਲੀ" not in text


def test_digits_used_when_no_converter():
    assert T.spoken_price(2425.4) == "2425"
    assert T.spoken_date("2026-03-31") == "31 ਮਾਰਚ 2026"


def test_date_accepts_string_with_time_and_date_object():
    from datetime import date
    assert T.spoken_date("2026-03-31T00:00:00") == T.spoken_date(date(2026, 3, 31))


def test_no_data_crop_lists_supported_crops_only():
    text = T.no_data_crop("wheat")
    assert "ਕਣਕ" in text
    for c in T.SUPPORTED_CROPS:
        assert T.crop_pa(c) in text
    assert "ਇਸ ਫ਼ਸਲ" in T.no_data_crop(None)


def test_every_status_renders_clean_text():
    slots = {
        Status.ANSWER: dict(crop="potato", mandi="ludhiana", price=100, last_date="2026-03-31", stale=True),
        Status.CONFIRM: dict(crop="potato", mandi="ludhiana"),
        Status.NO_DATA_CROP: dict(crop="wheat"),
        Status.NO_DATA_MANDI: dict(mandi="barnala"),
        Status.NO_DATA_COMBO: dict(crop="potato", mandi="sangrur"),
    }
    for status in Status:
        text = render(status, **slots.get(status, {}))
        assert text.strip(), status
        assert "{" not in text and "}" not in text, f"unfilled slot in {status}"


def test_render_rejects_unknown_status():
    try:
        render("nonsense")
    except ValueError:
        return
    raise AssertionError("unknown status must raise ValueError")


def test_tables_cover_every_mandi_and_crop_we_name():
    assert len(T.MANDIS_PA) == 9
    for c in T.SUPPORTED_CROPS:
        assert c in T.CROPS_PA
    for m in T.DEFAULT_MANDI_EXAMPLES:
        assert m in T.MANDIS_PA


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")