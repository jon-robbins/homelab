"""Season matching and selection parsing helper coverage."""

from test_api import (
    test_canonical_option_id_is_deterministic,
    test_extract_season_prefers_specific_path_segment,
    test_parse_selection_rank_accepts_option_id_without_season_confusion,
    test_parse_selection_rank_does_not_treat_season_number_as_rank,
    test_parse_selection_rank_supports_ordinal_words,
    test_season_matcher_accepts_exact_season_pack,
    test_season_matcher_ignores_single_episode_release,
    test_season_matcher_rejects_season_range_pack,
    test_season_path_matcher_targets_requested_season_files,
)

__all__ = [
    "test_season_matcher_ignores_single_episode_release",
    "test_season_matcher_rejects_season_range_pack",
    "test_season_matcher_accepts_exact_season_pack",
    "test_season_path_matcher_targets_requested_season_files",
    "test_extract_season_prefers_specific_path_segment",
    "test_parse_selection_rank_supports_ordinal_words",
    "test_parse_selection_rank_does_not_treat_season_number_as_rank",
    "test_parse_selection_rank_accepts_option_id_without_season_confusion",
    "test_canonical_option_id_is_deterministic",
]
