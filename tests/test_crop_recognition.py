"""Regression tests for explicit crop-slot recognition and clarification."""

from app.agent import step
from app.parser import parse
from app.templates import Status


def test_no_crop_mentioned_asks_for_a_crop_not_generic_understanding():
    parsed = parse("What is the price in Jalandhar mandi?")
    assert parsed["crop"] is None
    assert parsed["crop_candidate"] is None
    reply = step("What is the price in Jalandhar mandi?")
    assert reply.status is Status.CLARIFY_CROP
    assert "ਫ਼ਸਲ ਦਾ ਨਾਮ" in reply.render()


def test_unknown_crop_in_english_is_explicitly_rejected():
    parsed = parse("What is the price of banana in Jalandhar?")
    assert parsed["crop"] is None
    assert parsed["crop_candidate"] == "banana"
    reply = step("What is the price of banana in Jalandhar?")
    assert reply.status is Status.CROP_NOT_RECOGNIZED
    assert "ਪਛਾਣ ਨਹੀਂ ਸਕਿਆ" in reply.render()
    assert "ਆਲੂ" in reply.render()


def test_unknown_crop_in_punglish_is_explicitly_rejected():
    parsed = parse("Bajra ka kya rate hai Patiala mein?")
    assert parsed["crop_candidate"] == "bajra"
    reply = step("Bajra ka kya rate hai Patiala mein?")
    assert reply.status is Status.CROP_NOT_RECOGNIZED


def test_unknown_crop_in_punjabi_is_explicitly_rejected():
    parsed = parse("ਕੇਲੇ ਦਾ ਰੇਟ ਜਲੰਧਰ ਮੰਡੀ ਵਿੱਚ")
    assert parsed["crop_candidate"] == "ਕੇਲੇ"
    reply = step("ਕੇਲੇ ਦਾ ਰੇਟ ਜਲੰਧਰ ਮੰਡੀ ਵਿੱਚ")
    assert reply.status is Status.CROP_NOT_RECOGNIZED


def test_unknown_crop_followup_does_not_fall_back_to_not_understood():
    first = step("What is the price in Jalandhar mandi?")
    assert first.status is Status.CLARIFY_CROP
    second = step("Dragonfruit", first.state)
    assert second.status is Status.CROP_NOT_RECOGNIZED
    assert second.state.awaiting == "crop"


def test_user_can_correct_unknown_crop_and_continue_with_stored_mandi():
    first = step("Banana price in Jalandhar")
    assert first.status is Status.CROP_NOT_RECOGNIZED
    assert first.state.mandis == ["jalandhar"]

    def fake_price(crop, mandi, day, today, db):
        assert crop == "potato"
        assert mandi == "jalandhar"
        return {"found": False, "modal": None}

    corrected = step("Potato", first.state, price_fn=fake_price)
    assert corrected.status is Status.NO_DATA_COMBO


def test_rice_remains_known_but_unsupported():
    reply = step("Rice ka kya rate hai Patiala mein?")
    assert reply.status is Status.NO_DATA_CROP
    assert "ਚਾਵਲ" in reply.render()


def test_question_word_what_is_not_misread_as_wheat():
    parsed = parse("What is the price in Jalandhar mandi?")
    assert parsed["crop"] is None


if __name__ == "__main__":
    tests = [
        test_no_crop_mentioned_asks_for_a_crop_not_generic_understanding,
        test_unknown_crop_in_english_is_explicitly_rejected,
        test_unknown_crop_in_punglish_is_explicitly_rejected,
        test_unknown_crop_in_punjabi_is_explicitly_rejected,
        test_unknown_crop_followup_does_not_fall_back_to_not_understood,
        test_user_can_correct_unknown_crop_and_continue_with_stored_mandi,
        test_rice_remains_known_but_unsupported,
        test_question_word_what_is_not_misread_as_wheat,
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)} crop-recognition tests passed")
