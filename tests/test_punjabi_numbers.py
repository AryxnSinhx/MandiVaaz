"""
Tests for app/punjabi_numbers.py.  Run from the project root:
    uv run python -m tests.test_punjabi_numbers      (plain Python, no pytest needed)
or  uv run pytest -q                                  (if you add pytest)

IMPORTANT: these tests prove the CODE is consistent (grouping, spacing, errors).
They CANNOT prove the Punjabi spellings are right - that needs a native speaker's ear.
"""
from app.punjabi_numbers import (HUNDRED, LAKH, THOUSAND, UNITS, number_to_words, price_to_words)


def test_table_shape():
    assert len(UNITS) == 100
    assert all(w and w == w.strip() and " " not in w for w in UNITS)   # one word each, no stray spaces
    assert len(set(UNITS)) == 100                                       # no accidental duplicates


def test_small_numbers_are_table_lookups():
    for i in range(100):
        assert number_to_words(i) == UNITS[i]


def test_hundreds_thousands_lakhs():
    assert number_to_words(100) == f"{UNITS[1]} {HUNDRED}"
    assert number_to_words(101) == f"{UNITS[1]} {HUNDRED} {UNITS[1]}"
    assert number_to_words(2425) == f"{UNITS[2]} {THOUSAND} {UNITS[4]} {HUNDRED} {UNITS[25]}"
    assert number_to_words(2000) == f"{UNITS[2]} {THOUSAND}"           # no trailing 'zero'
    assert number_to_words(100000) == f"{UNITS[1]} {LAKH}"
    assert number_to_words(123456) == (f"{UNITS[1]} {LAKH} {UNITS[23]} {THOUSAND} "
                                       f"{UNITS[4]} {HUNDRED} {UNITS[56]}")
    assert number_to_words(9999999).startswith(f"{UNITS[99]} {LAKH}")


def test_accepts_whole_floats_rejects_fractions():
    assert number_to_words(2425.0) == number_to_words(2425)
    for bad in (2425.5, -1, 10_000_000, "12", None, True):
        try:
            number_to_words(bad)
        except (ValueError, TypeError):
            continue
        raise AssertionError(f"{bad!r} should have been rejected")


def test_price_rounding():
    assert price_to_words(2425.4) == price_to_words(2425)
    assert price_to_words(2425.5) == price_to_words(2426)
    assert price_to_words(0).endswith("ਰੁਪਏ")
    for bad in (-5, None, float("nan")):
        try:
            price_to_words(bad)
        except (ValueError, TypeError):
            continue
        raise AssertionError(f"{bad!r} should have been rejected")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)