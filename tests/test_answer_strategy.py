from services.chat_service import determine_answer_strategy


def test_determine_answer_strategy_prioritizes_safety_fallback():
    assert determine_answer_strategy(False, {}) == "model_reply"
    assert determine_answer_strategy(True, {}) == "composer_repair"
    assert determine_answer_strategy(True, {"fallback_applied": True}) == "safety_fallback"
