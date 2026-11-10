"""
Tests for app/agent.py.   Run:  uv run python -m tests.test_agent

parse and get_price are replaced by FAKES, so these tests need no database, no API
and no microphone. They test the agent's DECISIONS, not the parser or the data.
"""
import sys

from app import agent
from app.agent import Reply, State, step
from app.templates import Status

TODAY = None  # the fake price function ignores it


def P(crop=None, mandis=(), day=None, status="ok", crop_score=100, mandi_scores=None, intent="price"):
    """Build a fake parser result with the same keys as the real parse()."""
    mandis = list(mandis)
    return {"intent": intent, "crop": crop, "crop_score": crop_score, "mandis": mandis,
            "mandi_scores": mandi_scores if mandi_scores is not None else [100] * len(mandis),
            "day": day, "status": status, "text": ""}


def fake_parser(table):
    """text -> prepared parse dict. Unknown text behaves like 'nothing recognised'."""
    return lambda text: table.get(text, P(status="unknown"))


class FakePrices:
    """Records calls; returns a found price unless told otherwise."""

    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def __call__(self, crop, mandi, day=None, today=None, db=None):
        self.calls.append((crop, mandi, day))
        if (crop, mandi) in self.results:
            return self.results[(crop, mandi)]
        return {"found": True, "crop": crop, "mandi": mandi, "mandi_used": mandi, "fallback_mandi": False,
                "date": "2026-03-31", "modal": 2425.0, "stale": True,
                "wanted_day": "2026-11-05", "days_old": 219}


def run(text, parse_table, prices=None, state=None):
    prices = prices or FakePrices()
    return step(text, state, parse_fn=fake_parser(parse_table), price_fn=prices), prices


# ---------------------------------------------------------------- answering
def test_complete_exact_question_is_answered():
    r, prices = run("q", {"q": P("potato", ["ludhiana"])})
    assert r.status is Status.ANSWER
    slots = r.items[0][1]
    assert slots["crop"] == "potato" and slots["mandi"] == "ludhiana" and slots["stale"] is True
    assert prices.calls == [("potato", "ludhiana", None)]
    assert r.state == State()  # conversation reset after answering


def test_answer_renders_to_punjabi_text():
    r, _ = run("q", {"q": P("potato", ["ludhiana"])})
    text = r.render(to_words=lambda n: f"<{n}>")
    assert "ਲੁਧਿਆਣਾ" in text and "<2425>" in text and "ਪੁਰਾਣਾ ਰਿਕਾਰਡ" in text


def test_fallback_mandi_is_passed_on():
    result = {"found": True, "crop": "onion", "mandi": "moga", "mandi_used": "ludhiana", "fallback_mandi": True,
              "date": "2026-03-31", "modal": 1800.0, "stale": True, "wanted_day": "x", "days_old": 1}
    r, _ = run("q", {"q": P("onion", ["moga"])}, FakePrices({("onion", "moga"): result}))
    slots = r.items[0][1]
    assert slots["mandi"] == "ludhiana" and slots["asked_mandi"] == "moga"
    assert "ਨੇੜਲੀ" in r.render()


def test_day_missed_only_when_asked_differs_and_not_already_stale():
    # stale price: the stale warning already covers it, so no extra sentence
    r, _ = run("q", {"q": P("potato", ["ludhiana"], day="today")})
    assert r.items[0][1]["day_missed"] is False and r.items[0][1]["stale"] is True
    # fresh-ish price (not stale) from a different day than the one asked: say so
    fresh = {"found": True, "crop": "potato", "mandi": "ludhiana", "mandi_used": "ludhiana",
             "fallback_mandi": False, "date": "2026-11-03", "modal": 2400.0, "stale": False,
             "wanted_day": "2026-11-05", "days_old": 2}
    r, _ = run("q", {"q": P("potato", ["ludhiana"], day="today")}, FakePrices({("potato", "ludhiana"): fresh}))
    assert r.items[0][1]["day_missed"] is True
    # no day asked = "latest available": can never be missed
    r, _ = run("q", {"q": P("potato", ["ludhiana"], day=None)}, FakePrices({("potato", "ludhiana"): fresh}))
    assert r.items[0][1]["day_missed"] is False


# ---------------------------------------------------------------- honest refusals
def test_unsupported_crop_refused_immediately_even_without_mandi():
    r, prices = run("q", {"q": P("wheat", [], status="need_mandi")})
    assert r.status is Status.NO_DATA_CROP and prices.calls == []


def test_unanswerable_mandi_never_hits_the_database():
    r, prices = run("q", {"q": P("potato", ["barnala"])})
    assert r.status is Status.NO_DATA_MANDI and prices.calls == []


def test_lookup_not_found_gives_no_data_combo():
    nothing = {("potato", "moga"): {"found": False, "crop": "potato", "mandi": "moga"}}
    r, _ = run("q", {"q": P("potato", ["moga"])}, FakePrices(nothing))
    assert r.status is Status.NO_DATA_COMBO


def test_missing_modal_price_is_treated_as_no_data():
    broken = {("potato", "moga"): {"found": True, "modal": None, "mandi": "moga", "mandi_used": "moga"}}
    r, _ = run("q", {"q": P("potato", ["moga"])}, FakePrices(broken))
    assert r.status is Status.NO_DATA_COMBO


# ---------------------------------------------------------------- asking questions
def test_missing_mandi_asks_then_merges_the_next_answer():
    table = {"a": P("potato", [], status="need_mandi"), "b": P(None, ["jalandhar"], status="need_crop")}
    r1, _ = run("a", table)
    assert r1.status is Status.CLARIFY_MANDI and r1.state.awaiting == "mandi" and r1.state.crop == "potato"
    r2, prices = run("b", table, state=r1.state)       # farmer only says the mandi
    assert r2.status is Status.ANSWER and prices.calls == [("potato", "jalandhar", None)]


def test_missing_crop_asks_then_merges_the_next_answer():
    table = {"a": P(None, ["patiala"], status="need_crop"), "b": P("tomato", [])}
    r1, _ = run("a", table)
    assert r1.status is Status.CLARIFY_CROP and r1.state.mandis == ["patiala"]
    r2, prices = run("b", table, state=r1.state)
    assert r2.status is Status.ANSWER and prices.calls == [("tomato", "patiala", None)]


def test_ambiguous_crop_asks_which_crop():
    r, _ = run("q", {"q": P(None, ["ludhiana"], status="ambiguous_crop", crop_score=90)})
    assert r.status is Status.CLARIFY_CROP


def test_nothing_recognised_says_not_understood():
    r, _ = run("noise", {})
    assert r.status is Status.NOT_UNDERSTOOD


def test_empty_text_says_not_understood():
    r = step("   ", None, parse_fn=fake_parser({}), price_fn=FakePrices())
    assert r.status is Status.NOT_UNDERSTOOD


def test_gives_up_after_max_attempts_and_resets():
    table = {"q": P("potato", [], status="need_mandi")}
    state = None
    seen = []
    for _ in range(agent.MAX_ATTEMPTS):
        r, _ = run("q", table, state=state)
        seen.append(r.status)
        state = r.state
    assert seen[-1] is Status.NOT_UNDERSTOOD and state == State()


# ---------------------------------------------------------------- confirmation
WEAK = {"q": P("potato", ["ludhiana"], status="confirm", crop_score=100, mandi_scores=[85])}


def test_weak_parse_asks_for_confirmation_of_the_weak_mandi():
    r, prices = run("q", WEAK)
    assert r.status is Status.CONFIRM and prices.calls == []
    assert r.items[0][1] == {"crop": "potato", "mandi": "ludhiana"} and r.state.awaiting == "confirm"


def test_yes_confirms_and_answers_without_reparsing():
    r1, _ = run("q", WEAK)
    for yes in ("ਹਾਂ", "haan ji", "yes", "हाँ"):
        r2, prices = run(yes, {}, state=r1.state)      # parse table empty: parser must not be needed
        assert r2.status is Status.ANSWER, yes
        assert prices.calls == [("potato", "ludhiana", None)]


def test_no_clears_slots_and_starts_over():
    r1, _ = run("q", WEAK)
    r2, prices = run("nahi", {}, state=r1.state)
    assert r2.status is Status.CLARIFY_CROP and r2.state.crop is None and r2.state.mandis == []
    assert prices.calls == []


def test_long_sentence_is_a_new_question_not_a_yes_no():
    # contains the filler word "na" but is far too long to be an answer to "did you mean...?"
    r1, _ = run("q", WEAK)
    long_q = "ludhiana mandi vich aloo da rate dasso na"
    r2, _ = run(long_q, {long_q: P("potato", ["ludhiana"])}, state=r1.state)
    assert r2.status is Status.ANSWER


def test_yes_no_helper_edge_cases():
    assert agent._yes_no("haan") == "yes" and agent._yes_no("nahi") == "no"
    assert agent._yes_no("haan nahi") is None      # both -> undecided
    assert agent._yes_no("") is None
    assert agent._yes_no("one two three four") is None


# ---------------------------------------------------------------- compare
def test_compare_answers_each_mandi_and_flags_the_unanswerable_one():
    table = {"q": P("potato", ["ludhiana", "sangrur"], intent="compare")}
    r, prices = run("q", table)
    assert [s for s, _ in r.items] == [Status.ANSWER, Status.NO_DATA_MANDI]
    assert prices.calls == [("potato", "ludhiana", None)]


def test_compare_never_uses_a_substitute_mandi():
    sub = {"found": True, "crop": "potato", "mandi": "khanna", "mandi_used": "ludhiana", "fallback_mandi": True,
           "date": "2026-03-31", "modal": 2425.0, "stale": True, "wanted_day": "x", "days_old": 1}
    table = {"q": P("potato", ["ludhiana", "khanna"], intent="compare")}
    r, _ = run("q", table, FakePrices({("potato", "khanna"): sub}))
    assert [s for s, _ in r.items] == [Status.ANSWER, Status.NO_DATA_COMBO]


def test_single_question_may_still_use_a_substitute_mandi():
    sub = {"found": True, "crop": "potato", "mandi": "khanna", "mandi_used": "ludhiana", "fallback_mandi": True,
           "date": "2026-03-31", "modal": 2425.0, "stale": True, "wanted_day": "x", "days_old": 1}
    r, _ = run("q", {"q": P("potato", ["khanna"])}, FakePrices({("potato", "khanna"): sub}))
    assert r.status is Status.ANSWER


def test_compare_is_capped():
    table = {"q": P("potato", ["ludhiana", "khanna", "jalandhar"], intent="compare")}
    r, _ = run("q", table)
    assert len(r.items) == agent.MAX_MANDIS_PER_REPLY


# ---------------------------------------------------------------- hygiene
def test_step_does_not_modify_the_state_it_receives():
    original = State(crop="potato", mandis=["ludhiana"], mandi_scores=[100], awaiting="mandi", attempts=1)
    snapshot = State(crop="potato", mandis=["ludhiana"], mandi_scores=[100], awaiting="mandi", attempts=1)
    run("q", {"q": P(None, ["jalandhar"], status="need_crop")}, state=original)
    assert original == snapshot


def test_every_crop_and_mandi_the_parser_can_return_has_a_punjabi_word():
    from app.aliases import CROPS, MANDIS
    from app.templates import CROPS_PA, MANDIS_PA
    assert set(CROPS) <= set(CROPS_PA), set(CROPS) - set(CROPS_PA)
    assert set(MANDIS) <= set(MANDIS_PA), set(MANDIS) - set(MANDIS_PA)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} tests passed")