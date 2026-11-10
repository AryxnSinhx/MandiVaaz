"""
punjabi_numbers.py - turn whole numbers and rupee prices into Punjabi (Gurmukhi) words.

Why this file exists
  The TTS (text-to-speech) engine reads TEXT. If we hand it the digits "2425" we depend on
  the engine guessing the language and the reading style. If we hand it the words
  "ਦੋ ਹਜ਼ਾਰ ਚਾਰ ਸੌ ਪੱਚੀ" there is nothing left to guess. This module makes that conversion.

Design (Indian number system, not Western):
  Punjabi groups digits as  lakh | thousand | hundred | last-two-digits
  e.g. 1,23,456 = 1 lakh, 23 thousand, 4 hundred, 56
  Every number 0-99 has its OWN word in Punjabi (no "twenty + one" rule), so 0-99 is a
  lookup table. Everything bigger is built by splitting into groups and joining.

Run a quick check:   python -m app.punjabi_numbers 2425 18 100000
"""
from __future__ import annotations

import sys

# Index = the number itself. 0..99. Spellings are from the author's knowledge and have
# NOT been checked by a native speaker yet - review before the demo (see lecture notes).
UNITS = [
    # 0-9
    "ਸਿਫ਼ਰ", "ਇੱਕ", "ਦੋ", "ਤਿੰਨ", "ਚਾਰ", "ਪੰਜ", "ਛੇ", "ਸੱਤ", "ਅੱਠ", "ਨੌਂ",
    # 10-19
    "ਦਸ", "ਗਿਆਰਾਂ", "ਬਾਰਾਂ", "ਤੇਰਾਂ", "ਚੌਦਾਂ", "ਪੰਦਰਾਂ", "ਸੋਲਾਂ", "ਸਤਾਰਾਂ", "ਅਠਾਰਾਂ", "ਉੱਨੀ",
    # 20-29
    "ਵੀਹ", "ਇੱਕੀ", "ਬਾਈ", "ਤੇਈ", "ਚੌਵੀ", "ਪੱਚੀ", "ਛੱਬੀ", "ਸਤਾਈ", "ਅਠਾਈ", "ਉਨੱਤੀ",
    # 30-39
    "ਤੀਹ", "ਇਕੱਤੀ", "ਬੱਤੀ", "ਤੇਤੀ", "ਚੌਂਤੀ", "ਪੈਂਤੀ", "ਛੱਤੀ", "ਸੈਂਤੀ", "ਅਠੱਤੀ", "ਉਨਤਾਲੀ",
    # 40-49
    "ਚਾਲੀ", "ਇਕਤਾਲੀ", "ਬਤਾਲੀ", "ਤਰਤਾਲੀ", "ਚਵਾਲੀ", "ਪੈਂਤਾਲੀ", "ਛਿਆਲੀ", "ਸੰਤਾਲੀ", "ਅਠਤਾਲੀ", "ਉਣੰਜਾ",
    # 50-59
    "ਪੰਜਾਹ", "ਇਕਵੰਜਾ", "ਬਵੰਜਾ", "ਤਰਵੰਜਾ", "ਚੌਵੰਜਾ", "ਪਚਵੰਜਾ", "ਛਪੰਜਾ", "ਸਤਵੰਜਾ", "ਅਠਵੰਜਾ", "ਉਨਾਹਠ",
    # 60-69
    "ਸੱਠ", "ਇਕਾਹਠ", "ਬਾਹਠ", "ਤਰੇਹਠ", "ਚੌਂਹਠ", "ਪੈਂਠ", "ਛਿਆਹਠ", "ਸਤਾਹਠ", "ਅਠਾਹਠ", "ਉਣੱਤਰ",
    # 70-79
    "ਸੱਤਰ", "ਇਕੱਤਰ", "ਬਹੱਤਰ", "ਤਿਹੱਤਰ", "ਚੌਹੱਤਰ", "ਪਚੱਤਰ", "ਛਿਹੱਤਰ", "ਸਤੱਤਰ", "ਅਠੱਤਰ", "ਉਣਾਸੀ",
    # 80-89
    "ਅੱਸੀ", "ਇਕਆਸੀ", "ਬਿਆਸੀ", "ਤਿਰਾਸੀ", "ਚੁਰਾਸੀ", "ਪਚਾਸੀ", "ਛਿਆਸੀ", "ਸਤਾਸੀ", "ਅਠਾਸੀ", "ਉਨਾਨਵੇਂ",
    # 90-99
    "ਨੱਬੇ", "ਇਕਾਨਵੇਂ", "ਬਾਨਵੇਂ", "ਤਿਰਾਨਵੇਂ", "ਚੌਰਾਨਵੇਂ", "ਪਚਾਨਵੇਂ", "ਛਿਆਨਵੇਂ", "ਸਤਾਨਵੇਂ", "ਅਠਾਨਵੇਂ", "ਨਿੜਾਨਵੇਂ",
]

HUNDRED = "ਸੌ"
THOUSAND = "ਹਜ਼ਾਰ"
LAKH = "ਲੱਖ"
RUPEES = "ਰੁਪਏ"

MAX_SUPPORTED = 10_000_000 - 1   # up to 99,99,999 (one crore is out of scope for mandi prices)


def number_to_words(n: int) -> str:
    """0 -> 'ਸਿਫ਼ਰ', 2425 -> 'ਦੋ ਹਜ਼ਾਰ ਚਾਰ ਸੌ ਪੱਚੀ'. Whole numbers 0..99,99,999 only."""
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        raise TypeError(f"expected a number, got {type(n).__name__}")
    if isinstance(n, float):
        if n != int(n):
            raise ValueError(f"{n} is not a whole number; round it first (see price_to_words)")
        n = int(n)
    if n < 0 or n > MAX_SUPPORTED:
        raise ValueError(f"{n} is outside 0..{MAX_SUPPORTED}")

    # Indian grouping: split with floor-division (//) and remainder (%)
    lakh = n // 100_000            # 0..99
    thousand = (n // 1000) % 100   # 0..99
    hundred = (n // 100) % 10      # 0..9
    last = n % 100                 # 0..99

    parts = []
    if lakh:
        parts += [UNITS[lakh], LAKH]
    if thousand:
        parts += [UNITS[thousand], THOUSAND]
    if hundred:
        parts += [UNITS[hundred], HUNDRED]
    if last or not parts:          # say the tail, or say 'zero' if everything was zero
        parts.append(UNITS[last])
    return " ".join(parts)


def price_to_words(price: float) -> str:
    """Rupee price (may be 2425.0 or 2425.5) -> words + 'ਰੁਪਏ'. Rounds to the nearest rupee, halves up."""
    if price is None or isinstance(price, bool) or price != price:   # price != price is True only for NaN
        raise ValueError("price must be a real number")
    if price < 0:
        raise ValueError("price cannot be negative")
    return f"{number_to_words(int(price + 0.5))} {RUPEES}"


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles often default to a non-UTF-8 code page
    except AttributeError:
        pass
    for arg in sys.argv[1:] or ["0", "7", "18", "100", "2425", "100000"]:
        print(f"{arg:>10} -> {price_to_words(float(arg))}")