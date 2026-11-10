"""
app/templates.py -- the Punjabi sentences Mandi Vaaz speaks.

Rule of this module: it only turns FACTS into TEXT. No network, no database,
no decisions. The agent decides WHAT to say (a Status plus some facts); this
module decides HOW to say it in Punjabi.

Canonical keys (English, lowercase) such as "potato" or "ludhiana" must match
the names used in app/aliases.py. If they differ, edit CROPS_PA / MANDIS_PA.

NOTE: all Punjabi wording below is UNVERIFIED by a native speaker.
Edit the TEXT dict (one place) after a native speaker reviews it.

Run a demo:   uv run python -m app.templates
Run tests:    uv run python -m tests.test_templates
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Callable, Optional, Sequence, Union

# A function that turns an integer into Punjabi words, e.g. 2425 -> "ਦੋ ਹਜ਼ਾਰ ...".
# We pass it IN (instead of importing it here) so this module stays independent
# and so we can switch it off if Timbre turns out to read digits correctly.
NumberToWords = Callable[[int], str]


# --------------------------------------------------------------------------
# 1. Vocabulary tables
# --------------------------------------------------------------------------
SUPPORTED_CROPS = ("potato", "onion", "tomato")  # crops we can really answer

CROPS_PA = {
    "potato": "ਆਲੂ",
    "onion": "ਪਿਆਜ਼",
    "tomato": "ਟਮਾਟਰ",
    "wheat": "ਕਣਕ",
    "cotton": "ਕਪਾਹ",
    "mustard": "ਸਰ੍ਹੋਂ",
    "moong": "ਮੂੰਗੀ",
    "paddy": "ਝੋਨਾ",
    "maize": "ਮੱਕੀ",
}

MANDIS_PA = {
    "ludhiana": "ਲੁਧਿਆਣਾ",
    "khanna": "ਖੰਨਾ",
    "jalandhar": "ਜਲੰਧਰ",
    "patiala": "ਪਟਿਆਲਾ",
    "bathinda": "ਬਠਿੰਡਾ",
    "amritsar": "ਅੰਮ੍ਰਿਤਸਰ",
    "moga": "ਮੋਗਾ",
    "sangrur": "ਸੰਗਰੂਰ",
    "barnala": "ਬਰਨਾਲਾ",
}

MONTHS_PA = [
    "ਜਨਵਰੀ", "ਫ਼ਰਵਰੀ", "ਮਾਰਚ", "ਅਪ੍ਰੈਲ", "ਮਈ", "ਜੂਨ",
    "ਜੁਲਾਈ", "ਅਗਸਤ", "ਸਤੰਬਰ", "ਅਕਤੂਬਰ", "ਨਵੰਬਰ", "ਦਸੰਬਰ",
]

# Mandis offered as examples when we ask "which mandi?" (ones with real data).
DEFAULT_MANDI_EXAMPLES = ("ludhiana", "jalandhar", "patiala")


# --------------------------------------------------------------------------
# 2. The sentences. Formal Punjabi (ਤੁਸੀਂ). Gender-neutral on purpose: no
#    "ਦੱਸ ਸਕਦਾ/ਸਕਦੀ ਹਾਂ", so the voice works whichever Timbre voice is used.
#    {curly} words are slots filled by str.format().
# --------------------------------------------------------------------------
TEXT = {
    # --- answer pieces (joined together by answer()) ---
    "price": "{mandi} ਵਿੱਚ {crop} ਦਾ ਭਾਅ {price} ਰੁਪਏ ਪ੍ਰਤੀ ਕੁਇੰਟਲ ਹੈ।",
    "record_date": "ਇਹ ਭਾਅ {date} ਦੇ ਰਿਕਾਰਡ ਮੁਤਾਬਕ ਹੈ।",
    "stale_warning": "ਇਹ ਅੱਜ ਦਾ ਭਾਅ ਨਹੀਂ ਹੈ, ਪੁਰਾਣਾ ਰਿਕਾਰਡ ਹੈ।",
    "fallback_mandi": "{asked} ਦਾ ਭਾਅ ਨਹੀਂ ਮਿਲਿਆ, ਇਸ ਲਈ ਨੇੜਲੀ ਮੰਡੀ {used} ਦਾ ਭਾਅ ਦੱਸਿਆ ਜਾ ਰਿਹਾ ਹੈ।",
    "fallback_day": "ਮੰਗੇ ਗਏ ਦਿਨ ਦਾ ਭਾਅ ਨਹੀਂ ਮਿਲਿਆ।",
    # --- questions back to the farmer ---
    "clarify_crop": "ਤੁਸੀਂ ਕਿਸ ਫ਼ਸਲ ਦਾ ਭਾਅ ਪੁੱਛ ਰਹੇ ਹੋ? {options} ਵਿੱਚੋਂ ਦੱਸੋ।",
    "clarify_mandi": "ਤੁਸੀਂ ਕਿਸ ਮੰਡੀ ਦਾ ਭਾਅ ਜਾਣਨਾ ਚਾਹੁੰਦੇ ਹੋ? ਜਿਵੇਂ {examples}।",
    "confirm": "ਕੀ ਤੁਸੀਂ {mandi} ਵਿੱਚ {crop} ਦਾ ਭਾਅ ਪੁੱਛ ਰਹੇ ਹੋ? ਹਾਂ ਜਾਂ ਨਹੀਂ ਕਹੋ।",
    # --- honest "we do not have it" replies ---
    "no_data_crop": "ਮਾਫ਼ ਕਰਨਾ, {crop} ਦਾ ਭਾਅ ਉਪਲਬਧ ਨਹੀਂ ਹੈ। ਇਸ ਵੇਲੇ ਸਿਰਫ਼ {supported} ਦੇ ਭਾਅ ਉਪਲਬਧ ਹਨ।",
    "no_data_mandi": "ਮਾਫ਼ ਕਰਨਾ, {mandi} ਮੰਡੀ ਦਾ ਡਾਟਾ ਉਪਲਬਧ ਨਹੀਂ ਹੈ।",
    "no_data_combo": "ਮਾਫ਼ ਕਰਨਾ, {mandi} ਵਿੱਚ {crop} ਦਾ ਕੋਈ ਰਿਕਾਰਡ ਨਹੀਂ ਮਿਲਿਆ।",
    "unknown_crop_word": "ਇਸ ਫ਼ਸਲ",  # used when the parser heard no crop name at all
    # --- failures ---
    "not_understood": "ਮਾਫ਼ ਕਰਨਾ, ਮੈਨੂੰ ਸਮਝ ਨਹੀਂ ਆਇਆ। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਬੋਲੋ।",
    "error": "ਮਾਫ਼ ਕਰਨਾ, ਕੁਝ ਗੜਬੜ ਹੋ ਗਈ ਹੈ। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ।",
}

CONJ_AND = "ਅਤੇ"
CONJ_OR = "ਜਾਂ"


# --------------------------------------------------------------------------
# 3. Small helpers
# --------------------------------------------------------------------------
def crop_pa(key: str) -> str:
    """Canonical crop key -> Punjabi word. Unknown key is returned unchanged."""
    return CROPS_PA.get(key, key)


def mandi_pa(key: str) -> str:
    """Canonical mandi key -> Punjabi word. Unknown key is returned unchanged."""
    return MANDIS_PA.get(key, key)


def _join_pa(items: Sequence[str], conj: str) -> str:
    """['a','b','c'], 'ਜਾਂ' -> 'a, b ਜਾਂ c'   (one item -> 'a')."""
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def spoken_price(value: float, to_words: Optional[NumberToWords] = None) -> str:
    """2425.0 -> words (if a converter is given) or the digits '2425'."""
    n = int(round(value))
    return to_words(n) if to_words else str(n)


def spoken_date(d: Union[str, date], to_words: Optional[NumberToWords] = None) -> str:
    """'2026-03-31' (or a date) -> '<31> ਮਾਰਚ <2026>' with numbers as words or digits."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])  # [:10] drops any time part like 'T00:00:00'
    day = to_words(d.day) if to_words else str(d.day)
    year = to_words(d.year) if to_words else str(d.year)
    return f"{day} {MONTHS_PA[d.month - 1]} {year}"


# --------------------------------------------------------------------------
# 4. One builder per situation
# --------------------------------------------------------------------------
def answer(
    crop: str,
    mandi: str,
    price: float,
    last_date: Union[str, date],
    *,
    stale: bool,  # required on purpose: the caller must decide, never forget
    to_words: Optional[NumberToWords] = None,
    asked_mandi: Optional[str] = None,
    day_missed: bool = False,
) -> str:
    """A price reply. Optional extra sentences: fallback mandi, missed day, stale."""
    parts = []
    if asked_mandi and asked_mandi != mandi:
        parts.append(TEXT["fallback_mandi"].format(asked=mandi_pa(asked_mandi), used=mandi_pa(mandi)))
    if day_missed:
        parts.append(TEXT["fallback_day"])
    parts.append(
        TEXT["price"].format(
            mandi=mandi_pa(mandi), crop=crop_pa(crop), price=spoken_price(price, to_words)
        )
    )
    parts.append(TEXT["record_date"].format(date=spoken_date(last_date, to_words)))
    if stale:
        parts.append(TEXT["stale_warning"])
    return " ".join(parts)


def clarify_crop(options: Sequence[str] = SUPPORTED_CROPS) -> str:
    names = [crop_pa(c) for c in options]
    return TEXT["clarify_crop"].format(options=_join_pa(names, CONJ_OR))


def clarify_mandi(examples: Sequence[str] = DEFAULT_MANDI_EXAMPLES) -> str:
    names = [mandi_pa(m) for m in examples]
    return TEXT["clarify_mandi"].format(examples=_join_pa(names, CONJ_OR))


def confirm(crop: str, mandi: str) -> str:
    return TEXT["confirm"].format(crop=crop_pa(crop), mandi=mandi_pa(mandi))


def no_data_crop(crop: Optional[str] = None) -> str:
    crop_text = crop_pa(crop) if crop else TEXT["unknown_crop_word"]
    supported = _join_pa([crop_pa(c) for c in SUPPORTED_CROPS], CONJ_AND)
    return TEXT["no_data_crop"].format(crop=crop_text, supported=supported)


def no_data_mandi(mandi: str) -> str:
    return TEXT["no_data_mandi"].format(mandi=mandi_pa(mandi))


def no_data_combo(crop: str, mandi: str) -> str:
    return TEXT["no_data_combo"].format(crop=crop_pa(crop), mandi=mandi_pa(mandi))


def not_understood() -> str:
    return TEXT["not_understood"]


def error() -> str:
    return TEXT["error"]


# --------------------------------------------------------------------------
# 5. Status -> builder (the agent returns a Status + facts; main.py calls render)
# --------------------------------------------------------------------------
class Status(str, Enum):
    ANSWER = "answer"
    CLARIFY_CROP = "clarify_crop"
    CLARIFY_MANDI = "clarify_mandi"
    CONFIRM = "confirm"
    NO_DATA_CROP = "no_data_crop"
    NO_DATA_MANDI = "no_data_mandi"
    NO_DATA_COMBO = "no_data_combo"
    NOT_UNDERSTOOD = "not_understood"
    ERROR = "error"


RENDERERS = {
    Status.ANSWER: answer,
    Status.CLARIFY_CROP: clarify_crop,
    Status.CLARIFY_MANDI: clarify_mandi,
    Status.CONFIRM: confirm,
    Status.NO_DATA_CROP: no_data_crop,
    Status.NO_DATA_MANDI: no_data_mandi,
    Status.NO_DATA_COMBO: no_data_combo,
    Status.NOT_UNDERSTOOD: not_understood,
    Status.ERROR: error,
}


def render(status: Union[Status, str], **slots) -> str:
    """Look up the builder for `status` and call it with the facts in `slots`."""
    try:
        builder = RENDERERS[Status(status)]
    except ValueError:
        raise ValueError(f"Unknown status: {status!r}") from None
    return builder(**slots)


# --------------------------------------------------------------------------
# Demo: prints every reply so you can read (and later listen to) them.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Gurmukhi on a Windows console

    demo = [
        (Status.ANSWER, dict(crop="potato", mandi="ludhiana", price=2425, last_date="2026-03-31", stale=True)),
        (Status.ANSWER, dict(crop="onion", mandi="khanna", price=1800, last_date="2026-03-31",
                             stale=True, asked_mandi="barnala", day_missed=True)),
        (Status.CLARIFY_CROP, {}),
        (Status.CLARIFY_MANDI, {}),
        (Status.CONFIRM, dict(crop="tomato", mandi="patiala")),
        (Status.NO_DATA_CROP, dict(crop="wheat")),
        (Status.NO_DATA_MANDI, dict(mandi="barnala")),
        (Status.NO_DATA_COMBO, dict(crop="potato", mandi="sangrur")),
        (Status.NOT_UNDERSTOOD, {}),
        (Status.ERROR, {}),
    ]
    for status, slots in demo:
        print(f"[{status.value}]")
        print("  ", render(status, **slots))