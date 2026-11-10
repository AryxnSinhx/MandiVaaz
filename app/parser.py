"""
parser.py - turn a transcript into {intent, crop, mandis, day, status}.

Term: SLOT FILLING
  Plain idea: the sentence is a form with blanks (crop, mandi, day). We read the sentence and
  fill whichever blanks we can, then the agent decides what to do about the empty or doubtful ones.

Term: STATUS (what the agent controller should do next)
  ok              both slots found with exact-looking matches -> look up the price
  confirm         a slot was found only by a fuzzy match -> repeat it back to the farmer first
  need_mandi      crop found, mandi missing -> ask "which mandi?"
  need_crop       mandi found, crop missing -> ask "which crop?"
  ambiguous_crop  two different crops matched about equally -> ask which one
  unknown         nothing recognised -> ask to repeat

The STT gives no confidence score, so doubt can only come from these match scores.
"""
import re

from app.aliases import CROPS, MANDIS, find_all, normalize

EXACT = 95        # score at/above this = exact word or Indic alias glued inside a word
AMBIG_GAP = 5     # two crops within this many points = ambiguous

# Exclude common question/filler words from fuzzy crop matching. In
# particular, "what" scores 88.9 against "wheat" in RapidFuzz, so the old
# matcher misclassified questions such as "What is the price in Jalandhar?"
# as wheat requests. Exact aliases are still matched normally; only these
# generic tokens are removed before fuzzy matching.
_CROP_MATCH_STOPWORDS = frozenset({
    "what", "which", "when", "where", "who", "how", "much",
    "is", "are", "was", "were", "the", "a", "an", "in", "at", "of",
    "for", "to", "with", "and", "price", "rate", "market", "mandi",
    "today", "please", "tell", "me", "ka", "ki", "kya", "hai", "mein",
    "main", "da", "di", "de",
})

# Rice is not one of the crops supported by the price database. Recognise it
# explicitly so the agent can reject it honestly instead of treating it as a
# missing crop. Keep these as whole-token aliases: "rice" must not match the
# ending of the word "price".
UNSUPPORTED_CROP_ALIASES = {
    "rice": ("rice", "chawal", "chaawal", "chaval", "ਚਾਵਲ", "ਚੌਲ", "चावल"),
}


def _find_unsupported_crops(text: str) -> dict:
    """Find explicitly named unsupported crops by exact whole-token aliases."""
    tokens = set(normalize(text).split())
    found = {}
    for crop, aliases in UNSUPPORTED_CROP_ALIASES.items():
        if any(normalize(alias) in tokens for alias in aliases):
            found[crop] = 100
    return found

_TODAY = {"ਅੱਜ", "ਅਜ", "aaj", "aj", "ajj", "आज", "today"}
_KAL = {"ਕੱਲ੍ਹ", "ਕੱਲ", "ਕੱਲ੍ਹ", "kal", "कल", "yesterday"}
_PAST = {"tha", "thi", "the", "था", "थी", "थे", "ਸੀ", "ਸੀ", "ਸੀਗਾ"}
_COMPARE = {"compare", "comparison", "कम्पेयर", "कंपेयर", "ਕੰਪੇਅਰ", "ਤੁਲਨਾ", "tulna"}


def _day(tokens):
    t = {normalize(x) for x in tokens}
    if t & {normalize(x) for x in _TODAY}:
        return "today"
    if t & {normalize(x) for x in _KAL} and t & {normalize(x) for x in _PAST}:
        return "yesterday"
    return None  # "latest available"; bare "kal" is ambiguous (yesterday/tomorrow)


def parse(text: str) -> dict:
    toks = normalize(text).split()
    # Remove generic question words before fuzzy crop matching, while keeping
    # the existing typo-tolerance threshold for genuine crop words.
    crop_text = " ".join(
        token for token in toks if token not in _CROP_MATCH_STOPWORDS
    )
    crops = find_all(crop_text, CROPS)
    crops.update(_find_unsupported_crops(text))
    mandis = find_all(text, MANDIS)

    crop, crop_score, ambiguous = None, 0, False
    if crops:
        ranked = sorted(crops.items(), key=lambda kv: -kv[1])
        crop, crop_score = ranked[0]
        ambiguous = len(ranked) > 1 and ranked[0][1] - ranked[1][1] < AMBIG_GAP
    mlist = sorted(mandis.items(), key=lambda kv: -kv[1])

    compare_word = bool(set(toks) & {normalize(x) for x in _COMPARE})
    intent = "compare" if (compare_word or len(mlist) >= 2) else "price"

    if not crop and not mlist:
        status = "unknown"
    elif ambiguous:
        status = "ambiguous_crop"
    elif not crop:
        status = "need_crop"
    elif not mlist or (intent == "compare" and len(mlist) < 2):
        status = "need_mandi"  # a compare question with only one mandi heard must not get a one-mandi answer
    elif crop_score < EXACT or min(s for _, s in mlist) < EXACT:
        status = "confirm"
    else:
        status = "ok"

    return {"intent": intent, "crop": None if ambiguous else crop, "crop_score": crop_score,
            "mandis": [m for m, _ in mlist], "mandi_scores": [s for _, s in mlist],
            "day": _day(toks), "status": status, "text": text}


if __name__ == "__main__":
    import sys
    for line in sys.argv[1:] or ["Ludhiana mandi vich wheat da rate ki hai aaj?",
                                 "ਥਾਣਾ ਮੰਡੀ ਵਿੱਚ ਅੱਜ ਕਣਕ ਦਾ ਭਾਅ ਕੀ ਹੈ?",
                                 "Kal Patiala mein pyaaz ka bhaav kya tha?"]:
        r = parse(line)
        print({k: r[k] for k in ("intent", "crop", "mandis", "day", "status")}, "<-", line)