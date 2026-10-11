"""Pure intent + selection-parsing helpers."""

from __future__ import annotations

from app.router.intent import (
    classify_intent,
    parse_selection_choice,
    parse_selection_rank,
    season_prompt_needed,
)


def test_classify_intent_non_media_when_no_keywords() -> None:
    decision = classify_intent("what time is it in madrid", has_session_state=False)
    assert decision.intent == "non_media"


def test_classify_intent_download_when_media_keyword() -> None:
    decision = classify_intent("Download Interstellar", has_session_state=False)
    assert decision.intent == "download"


def test_classify_intent_selection_with_pending_session() -> None:
    decision = classify_intent("first option", has_session_state=True)
    assert decision.intent == "selection"


def test_classify_intent_selection_requires_session() -> None:
    decision = classify_intent("first option", has_session_state=False)
    assert decision.intent in {"download", "non_media"}


def test_parse_selection_rank_supports_ordinal_words() -> None:
    assert parse_selection_rank("download only season 4 from first option") == 1


def test_parse_selection_rank_does_not_treat_season_number_as_rank() -> None:
    assert parse_selection_rank("download season 4") is None


def test_parse_selection_choice_accepts_option_id_without_season_confusion() -> None:
    choice = parse_selection_choice(
        "download season 4 option_id opt-01-abc123def4"
    )
    assert choice is not None
    assert choice.option_id == "opt-01-abc123def4"
    assert choice.rank is None


def test_season_prompt_needed_when_only_word_present() -> None:
    assert season_prompt_needed("Get Crazy Ex Girlfriend season") is True


def test_season_prompt_not_needed_when_number_present() -> None:
    assert season_prompt_needed("Get Crazy Ex Girlfriend season 4") is False
