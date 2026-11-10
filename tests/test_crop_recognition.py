"""Regression tests for unsupported-crop recognition and false crop matches."""

from app.agent import step
from app.parser import parse
from app.templates import Status


def test_question_word_what_is_not_misread_as_wheat():
    parsed = parse("What is the price in Jalandhar mandi?")
    assert parsed["crop"] is None
    assert parsed["mandis"] == ["jalandhar"]


def test_rice_is_recognised_as_unsupported_crop():
    parsed = parse("What is the price of rice in Jalandhar?")
    assert parsed["crop"] == "rice"
    reply = step("What is the price of rice in Jalandhar?")
    assert reply.status is Status.NO_DATA_CROP
    assert "ਚਾਵਲ" in reply.render()


def test_punglish_rice_is_recognised_as_unsupported_crop():
    reply = step("rice ka kya rate hai patiala mein?")
    assert reply.status is Status.NO_DATA_CROP
    assert "ਚਾਵਲ" in reply.render()


def test_rice_followup_after_mandi_clarification_is_not_reprompted():
    first = step("What is the price in Jalandhar mandi?")
    assert first.status is Status.CLARIFY_CROP

    second = step("Rice", first.state)
    assert second.status is Status.NO_DATA_CROP
    assert "ਚਾਵਲ" in second.render()


def test_exact_wheat_is_still_recognised_but_not_claimed_available():
    parsed = parse("What is wheat price in Jalandhar mandi?")
    assert parsed["crop"] == "wheat"
    reply = step("What is wheat price in Jalandhar mandi?")
    assert reply.status is Status.NO_DATA_CROP


def test_supported_crop_matching_still_works():
    parsed = parse("Aloo da rate Ludhiana mandi vich?")
    assert parsed["crop"] == "potato"
    assert parsed["mandis"] == ["ludhiana"]


if __name__ == "__main__":
    tests = [
        test_question_word_what_is_not_misread_as_wheat,
        test_rice_is_recognised_as_unsupported_crop,
        test_punglish_rice_is_recognised_as_unsupported_crop,
        test_rice_followup_after_mandi_clarification_is_not_reprompted,
        test_exact_wheat_is_still_recognised_but_not_claimed_available,
        test_supported_crop_matching_still_works,
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)} regression tests passed")
