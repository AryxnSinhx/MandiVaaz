"""
aliases.py - alias table + fuzzy matcher shared by the STT test and (later) the real parser.

Term: ALIAS TABLE
  Plain idea: one crop can be written/spoken many ways (ਕਣਕ, गेहूं, kanak, wheat).
  We keep a list of spellings per crop/mandi so all of them map to ONE canonical id.

Term: FUZZY MATCH (rapidfuzz)
  Plain idea: if the STT writes a word slightly wrong, we still accept it when it is
  "close enough" (similarity score 0-100). 80 = about 4 of 5 letters agree.

IMPORTANT: the spellings below are my best guesses. After the STT test, open
side_by_side.csv, see how the STT ACTUALLY spells things, and add those spellings here.
"""
import re
import unicodedata

from rapidfuzz import fuzz

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)
_NUKTA = {0x0A3C, 0x093C}  # Gurmukhi / Devanagari dot-below mark; STT is inconsistent with it


def normalize(text: str) -> str:
    """NFC, lowercase, drop punctuation/nukta/zero-width chars, collapse spaces."""
    t = unicodedata.normalize("NFC", text or "").translate(_ZERO_WIDTH).lower()
    out = []
    for ch in t:
        if ord(ch) in _NUKTA:
            continue
        cat = unicodedata.category(ch)
        # keep Letters, Marks (vowel signs!), Numbers, spaces; everything else -> space
        out.append(ch if (cat[0] in "LMN" or ch.isspace()) else " ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


CROPS = {
    "wheat": {"agmarknet": ["wheat"], "aliases": [
        "ਕਣਕ", "ਕਣਕਾਂ", "कनक", "कणक", "गेहूं", "गेहूँ", "गेंहू", "गहू", "गेहु",
        "ਵੀਟ", "वीट", "ਗੇਹੂ", "ਗਿਹੂ", "kanak", "gehu", "gehun", "gehoon", "wheat"]},
    "paddy": {"agmarknet": ["paddy"], "aliases": [
        "ਝੋਨਾ", "ਝੋਨੇ", "ਧਾਨ", "ਪੈਡੀ", "झोना", "झोने", "धान",
        "पैडी", "ਪੈੜੀ", "ਪੈਡੀ", "jhona", "jhone", "dhaan", "dhan", "paddy"]},
    "cotton": {"agmarknet": ["cotton"], "aliases": [
        "ਨਰਮਾ", "ਨਰਮੇ", "ਕਪਾਸ", "ਕੋਟਨ", "ਕਪਾਹ", "ਕਾਟਨ", "नरमा", "नरमे", "कपास", "कॉटन",
        "narma", "narme", "kapas", "cotton"]},
    "maize": {"agmarknet": ["maize"], "aliases": [
        "ਮੱਕੀ", "ਮੱਕਾ", "ਮੱਕੇ", "ਮਕੀ", "मक्का", "मक्की", "मकई",
        "ਮੇਜ਼", "ਮੇਜ", "ਕੌਰਨ", "मेज़", "मेज", "makki", "makka", "makai", "maize", "corn"]},
    "potato": {"agmarknet": ["potato"], "aliases": [
        "ਆਲੂ", "ਆਲੂਆਂ", "आलू", "आलु", "ਪੋਟੈਟੋ", "ਪੋਟੇਟੋ", "ਪਟੈਟੋ", "पोटैटो", "पोटेटो", "alu", "aloo", "aaloo", "potato"]},
    "mustard": {"agmarknet": ["mustard"], "aliases": [
        "ਸਰ੍ਹੋਂ", "ਸਰੋਂ", "ਸਰ੍ਹੋ", "सरसों", "सरसो", "ਸਰਸੋਂ", "ਸਰਸੋ", "ਮਸਟਰਡ", "ਮਸਟਡ", "मस्टर्ड", "sarson", "sarhon", "saroh", "sarho", "mustard"]},
    "moong": {"agmarknet": ["moong", "green gram"], "aliases": [
        "ਮੂੰਗੀ", "ਮੂੰਗ", "मूंग", "मूँग", "मूंगी", "moongi", "moong", "mung"]},
    "onion": {"agmarknet": ["onion"], "aliases": [
        "ਪਿਆਜ਼", "ਪਿਆਜ", "ਪਿਆਜ਼ਾਂ", "ਗੰਢੇ", "ਗੰਢਾ", "प्याज", "प्याज़",
        "ਆਨੀਅਨ", "ਓਨੀਅਨ", "ਅਨੀਅਨ", "ਆਨਿਅਨ", "अनियन", "ओनियन", "pyaaz", "pyaz", "pyaj", "piaz", "onion"]},
    "tomato": {"agmarknet": ["tomato"], "aliases": [
        "ਟਮਾਟਰ", "ਟਮਾਟਰਾਂ", "टमाटर", "ਟੋਮੈਟੋ", "ਟਮੈਟੋ", "टोमैटो", "tamatar", "tomato"]},
}

MANDIS = {
    "ludhiana": {"agmarknet": ["ludhiana"], "aliases": [
        "ਲੁਧਿਆਣਾ", "ਲੁਧਿਆਣੇ", "लुधियाना", "लुधियाने", "ludhiana", "ludhiane", "ludhyana"]},
    "khanna": {"agmarknet": ["khanna"], "aliases": [
        "ਖੰਨਾ", "ਖੰਨੇ", "खन्ना", "khanna", "khanne"]},
    "bathinda": {"agmarknet": ["bathinda"], "aliases": [
        "ਬਠਿੰਡਾ", "ਬਠਿੰਡੇ", "बठिंडा", "भटिंडा", "बटिंडा", "bathinda", "bhatinda", "batinda"]},
    "jalandhar": {"agmarknet": ["jalandhar", "jullundur"], "aliases": [
        "ਜਲੰਧਰ", "जालंधर", "जलंधर", "jalandhar", "jalandar", "jullundur", "jalander"]},
    "amritsar": {"agmarknet": ["amritsar"], "aliases": [
        "ਅੰਮ੍ਰਿਤਸਰ", "ਅੰਮਰਿਤਸਰ", "ਅਮ੍ਰਿਤਸਰ", "अमृतसर", "अम्रितसर", "amritsar", "amritsir"]},
    "patiala": {"agmarknet": ["patiala"], "aliases": [
        "ਪਟਿਆਲਾ", "ਪਟਿਆਲੇ", "पटियाला", "पटियाले", "patiala", "patiale"]},
    "moga": {"agmarknet": ["moga"], "aliases": [
        "ਮੋਗਾ", "ਮੋਗੇ", "मोगा", "मोगे", "moga", "moge"]},
    "sangrur": {"agmarknet": ["sangrur"], "aliases": [
        "ਸੰਗਰੂਰ", "संगरूर", "sangrur"]},
    "barnala": {"agmarknet": ["barnala"], "aliases": [
        "ਬਰਨਾਲਾ", "ਬਰਨਾਲੇ", "बरनाला", "barnala"]},
}


def _is_indic(s: str) -> bool:
    return any(ord(c) > 0x0900 for c in s)


def find_all(text: str, table: dict, threshold: int = 80) -> dict:
    """Return {canonical_id: best_score} for every entry whose alias appears in text.

    Exact word = 100. Indic alias glued inside a longer word = 95 (STT often glues words).
    Otherwise best fuzzy ratio between any transcript word and any alias, if >= threshold.
    """
    norm = normalize(text)
    toks = norm.split()
    found = {}
    for canon, entry in table.items():
        best = 0
        for alias in entry["aliases"]:
            a = normalize(alias)
            if len(a) < 3:
                continue
            if a in toks:
                s = 100
            elif _is_indic(a) and a in norm:
                s = 95
            else:
                s = max((fuzz.ratio(t, a) for t in toks if len(t) >= 3), default=0)
            best = max(best, s)
        if best >= threshold:
            found[canon] = best
    return found