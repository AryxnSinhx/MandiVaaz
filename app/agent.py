"""
app/agent.py -- the decision-maker of Mandi Vaaz (a small state machine, no LLM).

One call to step() = one farmer utterance in, one Reply out:

    transcript --parse()--> slots (crop, mandi, day) --merge with memory--> decide:
        ask which crop / ask which mandi / confirm a doubtful hearing /
        refuse honestly (no data) / look up the price and answer

The agent returns FACTS (a Status plus slots). It never writes Punjabi itself:
app/templates.py does that. The agent also never talks to the network: parse and
price lookup are passed in (defaults = the real ones), so tests can use fakes.

Memory between turns lives in a State object. main.py keeps one State per browser
session and hands it back on the next call. step() never modifies the State it
receives; it returns a new one.

Try it without a microphone:   uv run python -m app.agent
Run tests:                     uv run python -m tests.test_agent
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from app.aliases import normalize
from app.lookup import get_price
from app.parser import EXACT, parse
from app.templates import SUPPORTED_CROPS, Status, render

# --------------------------------------------------------------------------
# Settings you can change in one place
# --------------------------------------------------------------------------
# Mandis we deliberately answer with an honest "no data" (decision of Nov 4:
# Barnala is absent from the database, Sangrur has almost nothing).
# Make this an empty set () to let lookup.py try nearest-mandi fallback for them.
UNANSWERABLE_MANDIS = frozenset({"barnala", "sangrur"})

MAX_ATTEMPTS = 3        # after this many questions in a row, give up politely
MAX_MANDIS_PER_REPLY = 2  # a compare question gets at most two answers (voice replies must stay short)
MAX_YESNO_TOKENS = 3    # a yes/no answer is short; longer speech is treated as a new question

# Spoken yes / no words in Punjabi, Hindi and Roman spellings. Both sides pass through
# normalize() so they are compared in the same form the parser uses.
_YES = {normalize(w) for w in (
    "ਹਾਂ", "ਹਾਂਜੀ", "ਜੀ", "ਠੀਕ", "haan", "han", "ha", "haa", "ji", "theek", "ok", "okay", "yes", "yeah",
    "हाँ", "हां", "जी", "ठीक")}
_NO = {normalize(w) for w in (
    "ਨਹੀਂ", "ਨਹੀ", "ਨਾਂਹ", "ਨਾ", "nahi", "nahin", "na", "no", "नहीं", "नही", "ना")}


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------
@dataclass
class State:
    """What the agent remembers between turns of ONE conversation."""
    crop: Optional[str] = None
    crop_score: int = 0
    mandis: List[str] = field(default_factory=list)
    mandi_scores: List[int] = field(default_factory=list)
    day: Optional[str] = None            # "today" | "yesterday" | None (= latest available)
    awaiting: Optional[str] = None       # "crop" | "mandi" | "confirm" | None
    attempts: int = 0                    # questions asked so far in this conversation


@dataclass
class Reply:
    """What the agent decided. `items` is a list because a compare question gives two answers."""
    items: List[Tuple[Status, Dict]]
    state: State                         # store this, pass it to the next step()
    parsed: Optional[dict] = None        # raw parser output, kept for the evaluation script

    @property
    def status(self) -> Status:
        return self.items[0][0]

    def render(self, to_words: Optional[Callable[[int], str]] = None) -> str:
        """Punjabi text for the whole reply. Pass to_words=number_to_words to speak prices in words."""
        texts = []
        for status, slots in self.items:
            if status is Status.ANSWER:
                slots = {**slots, "to_words": to_words}
            texts.append(render(status, **slots))
        return " ".join(texts)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _yes_no(text: str) -> Optional[str]:
    """'yes', 'no', or None when the utterance is long, empty, or has both/neither."""
    tokens = normalize(text).split()
    if not tokens or len(tokens) > MAX_YESNO_TOKENS:
        return None
    words = set(tokens)
    yes, no = bool(words & _YES), bool(words & _NO)
    if yes == no:
        return None
    return "yes" if yes else "no"


def _ask(state: State, status: Status, awaiting: Optional[str], slots: Optional[dict] = None) -> Reply:
    """Reply with a question and remember what we are waiting for. Gives up after MAX_ATTEMPTS."""
    attempts = state.attempts + 1
    if attempts >= MAX_ATTEMPTS:
        # If we were collecting a crop, keep the response crop-specific instead
        # of falling back to the generic "I did not understand" message.
        final_status = Status.CROP_NOT_RECOGNIZED if awaiting == "crop" else Status.NOT_UNDERSTOOD
        return Reply([(final_status, {})], State())
    return Reply([(status, slots or {})], replace(state, awaiting=awaiting, attempts=attempts))


def _answer(state: State, today, db, price_fn) -> Reply:
    """Look up each requested mandi and build one item per mandi. Conversation then resets."""
    items: List[Tuple[Status, Dict]] = []
    requested = state.mandis[:MAX_MANDIS_PER_REPLY]
    for mandi in requested:
        if mandi in UNANSWERABLE_MANDIS:
            items.append((Status.NO_DATA_MANDI, {"mandi": mandi}))
            continue
        pr = price_fn(state.crop, mandi, state.day, today, db)
        if not pr.get("found") or pr.get("modal") is None:
            items.append((Status.NO_DATA_COMBO, {"crop": state.crop, "mandi": mandi}))
            continue
        if pr["fallback_mandi"] and len(requested) > 1:
            # In a comparison a substitute mandi is misleading (and may repeat another answer).
            items.append((Status.NO_DATA_COMBO, {"crop": state.crop, "mandi": mandi}))
            continue
        asked_a_day = state.day in ("today", "yesterday")
        items.append((Status.ANSWER, {
            "crop": state.crop,
            "mandi": pr["mandi_used"],          # the mandi the price really comes from
            "asked_mandi": pr["mandi"],         # the mandi the farmer asked for
            "price": pr["modal"],
            "last_date": pr["date"],
            "stale": pr["stale"],
            # The stale warning already says "not today's price", so only add the extra
            # "that day was not found" sentence when the price is NOT stale.
            "day_missed": asked_a_day and pr["date"] != pr["wanted_day"] and not pr["stale"],
        }))
    return Reply(items, State())


# --------------------------------------------------------------------------
# The state machine
# --------------------------------------------------------------------------
def step(
    text: str,
    state: Optional[State] = None,
    *,
    today=None,
    db=None,
    parse_fn: Callable[[str], dict] = parse,
    price_fn: Callable = get_price,
) -> Reply:
    """One farmer utterance in -> one Reply out. Does not modify `state`."""
    base = state or State()
    state = replace(base, mandis=list(base.mandis), mandi_scores=list(base.mandi_scores))  # private copy

    if not text or not text.strip():
        return _ask(state, Status.NOT_UNDERSTOOD, state.awaiting)

    # 1. If we asked "did you mean ...?", a short yes/no settles it.
    if state.awaiting == "confirm":
        verdict = _yes_no(text)
        if verdict == "yes":
            return _answer(state, today, db, price_fn)
        if verdict == "no":  # wrong guess: forget the slots and start over
            return _ask(State(attempts=state.attempts), Status.CLARIFY_CROP, "crop")
        # neither: the farmer is probably saying the question again; fall through and re-parse it

    # 2. Read the sentence and merge what it says into what we already knew.
    p = parse_fn(text)
    ambiguous = p["status"] == "ambiguous_crop"
    if ambiguous:
        state.crop, state.crop_score = None, 0
    elif p["crop"] is not None:
        state.crop, state.crop_score = p["crop"], p["crop_score"]
    if p["mandis"]:
        state.mandis, state.mandi_scores = list(p["mandis"]), list(p["mandi_scores"])
    if p["day"]:
        state.day = p["day"]

    # 3. Decide, most urgent problem first.
    # An explicit crop-like phrase ("banana price", "bajra ka bhav", etc.)
    # whose crop token is absent from our alias dictionary gets a precise
    # crop-recognition response. Keep the mandi in state so the farmer can
    # simply reply with a supported crop and continue the same request.
    if p.get("crop_candidate"):
        state.crop, state.crop_score = None, 0
        return _ask(state, Status.CROP_NOT_RECOGNIZED, "crop")

    # When we have just asked for a crop, a reply that still does not match
    # any known crop is a crop-recognition failure, not a generic NLP failure.
    if state.awaiting == "crop" and p["crop"] is None and not ambiguous:
        return _ask(state, Status.CROP_NOT_RECOGNIZED, "crop")

    if ambiguous:
        return _ask(state, Status.CLARIFY_CROP, "crop")
    if state.crop is None and not state.mandis:
        return _ask(state, Status.NOT_UNDERSTOOD, None)
    if state.crop is None:
        return _ask(state, Status.CLARIFY_CROP, "crop")
    if state.crop not in SUPPORTED_CROPS:  # say so at once; no point asking "which mandi?" first
        return Reply([(Status.NO_DATA_CROP, {"crop": state.crop})], State(), p)
    if not state.mandis:
        return _ask(state, Status.CLARIFY_MANDI, "mandi")

    weakest = min(state.mandi_scores) if state.mandi_scores else 0
    if state.crop_score < EXACT or weakest < EXACT:
        # confirm the weakest-heard mandi (or the first one if only the crop was weak)
        doubtful = state.mandis[state.mandi_scores.index(weakest)] if weakest < EXACT else state.mandis[0]
        return _ask(state, Status.CONFIRM, "confirm", {"crop": state.crop, "mandi": doubtful})

    reply = _answer(state, today, db, price_fn)
    reply.parsed = p
    return reply


# --------------------------------------------------------------------------
# Interactive demo (type what the farmer would say; STT is skipped)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    from app.punjabi_numbers import number_to_words

    sys.stdout.reconfigure(encoding="utf-8")
    st: Optional[State] = None
    lines = sys.argv[1:]
    print("Type a question (empty line to quit).")
    while True:
        try:
            line = lines.pop(0) if lines else input("farmer> ")
        except EOFError:
            break
        if not line.strip():
            break
        r = step(line, st)
        st = r.state
        print(f"  status={[s.value for s, _ in r.items]}  awaiting={st.awaiting}  attempts={st.attempts}")
        print("  reply:", r.render(to_words=number_to_words))