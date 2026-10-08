"""Configuration and artifact details internal to the LoL pipeline."""

import os
from datetime import timedelta

from shared.constants.dataset import VALIDATION_START_TIME
from shared.constants.lol import (
    LOL_DATA_DIR,
    LOL_DATASETS_DIR,
    LOL_MODELS_DIR,
    LOL_RAW_TELONEX_DIR,
    LOL_UNIVERSE_DIR,
)
from shared.constants.telonex import TELONEX_BOOK_CHANNEL

LOL_GAMMA_TAG_ID = 65
LOL_GAMMA_LIMIT = 500
LOL_UNIVERSE_VALIDATION_PATH = LOL_UNIVERSE_DIR / "markets.validation_only.parquet"
LOLESPORTS_API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LOLESPORTS_ESPORTS_API = "https://esports-api.lolesports.com/persisted/gw"
LOLESPORTS_WINDOW_API = "https://feed.lolesports.com/livestats/v1/window"
LOLESPORTS_DETAILS_API = "https://feed.lolesports.com/livestats/v1/details"
LOL_SERIES_START_WINDOW_SECONDS = 4 * 60 * 60
LOL_RAW_LOLESPORTS_DIR = LOL_DATA_DIR / "raw" / "lolesports"
LOL_GAMES_DIR = LOL_DATA_DIR / "processed" / "lolesports"
# No TTL or size cap; delete this directory by hand if it grows stale.
LOL_MAP_BUILD_CACHE_DIR = LOL_DATA_DIR / "processed" / "map_builds"
LOL_GAMES_PATH = LOL_GAMES_DIR / "games.parquet"
LOL_LINKS_DIR = LOL_DATA_DIR / "processed" / "lolesports_links"
LOL_LINKS_PATH = LOL_LINKS_DIR / "links.parquet"
LOL_LINK_AUDIT_PATH = LOL_LINKS_DIR / "audit.parquet"
LOL_FETCH_END_SECOND = 7200
LOL_FETCH_MAX_WALL_SECONDS = 10800
LOL_FETCH_STALL_RESPONSE_LIMIT = 10
LOL_WINDOW_STEP_SECONDS = 10
LOL_MAX_CONCURRENCY = 128

# Processes, not threads: JSON parse is GIL-bound (~1 core). One worker per CPU.
LOL_PREPARE_WORKERS = os.cpu_count() or 8
LOL_PAUSE_MIN_GAP_SECONDS = 5
LOL_WINDOWS_DIR = LOL_RAW_LOLESPORTS_DIR / "windows"
LOL_DETAILS_DIR = LOL_RAW_LOLESPORTS_DIR / "details"
LOL_DOWNLOAD_AUDIT_PATH = LOL_GAMES_DIR / "download_audit.parquet"
LOL_LEAGUE_SLUGS = frozenset(
    {
        "lck",
        "lec",
        "lpl",
        "ldl",
        "lcs",
        "lcp",
        "worlds",
        "msi",
        "nacl",
        "cblol",
    }
)
LOL_LEAGUE_ALIASES = {
    "lol champions korea": "lck",
    "league of legends champions korea": "lck",
}
REASON_GAME_WINNER = "game_winner"
REASON_MATCH_WINNER_DECIDER = "match_winner_decider"
REASON_MALFORMED_TOKENS = "malformed_tokens"
REASON_UNSUPPORTED_CONTRACT = "unsupported_contract"
REASON_UNSUPPORTED_BO2 = "unsupported_bo2"
REASON_SERIES_ONLY = "series_only"
REASON_MISSING_BEST_OF = "missing_best_of"
REASON_UNRESOLVED_MARKET = "unresolved_market"
REASON_ACCEPTED = "accepted"
REASON_NO_CANDIDATES_IN_WINDOW = "no_candidates_in_window"
REASON_MULTIPLE_ELIGIBLE_SERIES = "multiple_eligible_series"
REASON_SERIES_CLAIM_TIE = "series_claim_tie"
REASON_SERIES_CLAIMED_BY_RICHER_EVENT = "series_claimed_by_richer_event"
REASON_LEAGUE_MISMATCH = "league_mismatch"
REASON_BO_MISMATCH = "bo_mismatch"
REASON_MISSING_TEAMS = "missing_teams"
REASON_MISSING_SCHEDULE = "missing_schedule"
REASON_NO_MARKET = "no_market"
REASON_UNSUPPORTED_FALLBACK = "unsupported_fallback"
REASON_ORIENTATION_AMBIGUOUS = "orientation_ambiguous"
REASON_MISSING_SIDE = "missing_side"
REASON_DOWNLOAD_COMPLETE = "complete"
REASON_WALL_TIME_LIMIT = "wall_time_limit"
REASON_HTTP_404 = "http_404"
REASON_EMPTY_BODY = "empty_body"
REASON_RETRIES_EXHAUSTED = "retries_exhausted"
REASON_FEED_ENDED_NO_FLAG = "feed_ended_no_flag"
REASON_DETAILS_RETRIES_EXHAUSTED = "details_retries_exhausted"
TELONEX_CATALOG_BATCH_ROWS = 65536
LOL_TELONEX_BOOKS_DIR = LOL_RAW_TELONEX_DIR / TELONEX_BOOK_CHANNEL
LOL_TELONEX_PROCESSED_DIR = LOL_DATA_DIR / "processed" / "telonex"
LOL_TELONEX_CATALOG_PATH = LOL_TELONEX_PROCESSED_DIR / "catalog.parquet"
LOL_TELONEX_AUDIT_PATH = LOL_TELONEX_PROCESSED_DIR / "download_audit.parquet"
TELONEX_BOOK_REQUIRED_COLUMNS = frozenset(
    {
        "timestamp_us",
        "local_timestamp_us",
        "exchange",
        "market_id",
        "slug",
        "asset_id",
        "outcome",
        "bids",
        "asks",
    }
)

TELONEX_CATALOG_REQUIRED_COLUMNS = (
    "market_id",
    "asset_id_0",
    "asset_id_1",
    "book_snapshot_full_from",
    "book_snapshot_full_to",
)
REASON_TELONEX_DOWNLOADED = "downloaded"
REASON_TELONEX_SKIPPED_VALID = "skipped_valid"
REASON_TELONEX_HTTP_404 = "http_404"
REASON_TELONEX_RETRIES_EXHAUSTED = "retries_exhausted"
REASON_TELONEX_MISSING_FROM_CATALOG = "missing_from_catalog"
REASON_TELONEX_TOKEN_MISMATCH = "token_id_mismatch"
REASON_TELONEX_MISSING_INTERVAL = "missing_interval"
LOL_GRID_START_SECOND = 0
# Training and early-stopping rows stop at 540: late-map LoL labels carry MAE
# signal but no directional edge, and they outnumber entry-window rows 2:1.
LOL_TRAIN_END_SECOND = 540
LOL_FRAME_MAX_AGE_SECONDS = 2
LOL_SPAWN_GOLD = 500

# First spawn-shaped frame must land in [loading_anchor_ts, +this].
# Observed max gap is 248s (184 maps >90s); 900s is the 15-minute restart margin.
LOL_SPAWN_SEARCH_SECONDS = 900
LOL_CONSUMED_ABSENCE_SECONDS = 3.0

# One-player Cash Back undo (≤320 gold) keeps the frame; a bigger or multi-player drop skips it.
LOL_CASHBACK_UNDO_MAX_GOLD = 320

# Drop the map when skipped/considered frames exceed this (quiet prefix otherwise).
LOL_MAX_SKIPPED_FRAME_FRACTION = 0.20
LOL_PRIOR_WINDOW_SECONDS = 61
LOL_TARGET_HORIZON_SECONDS = 300

# Inclusive first validation PM event; same unix cutoff as Dota.
LOL_VALIDATION_START_TIME = VALIDATION_START_TIME
LOL_TRAINING_PATH = LOL_DATASETS_DIR / "training.parquet"
LOL_PRODUCTION_TRAINING_PATH = LOL_DATASETS_DIR / "production_training.parquet"
LOL_PREPARE_AUDIT_PATH = LOL_DATASETS_DIR / "audit.parquet"
LOL_RESEARCH_ARCHIVE_DIR = LOL_MODELS_DIR / "archive" / "research"
LOL_PRODUCTION_ARCHIVE_DIR = LOL_MODELS_DIR / "archive" / "production"
LOL_TRAIN_GRID_SECONDS = 1
LOL_LAG_CANDIDATE_MAX_SECONDS = 60
LOL_LABEL_COLUMNS: list[str] = ["signal_market_p_radiant_300s"]
LOL_DATASET_COLUMNS: list[str] = [
    "match_id",
    "start_time",
    "event_id",
    "second",
    "state_ts_us",
    "radiant_win",
    "radiant_nw_adv",
    "radiant_nw",
    "dire_nw",
    "radiant_xp_adv",
    "deaths_radiant",
    "deaths_dire",
    "top1_nw_adv",
    "radiant_top1_nw_ratio",
    "dire_top1_nw_ratio",
    "top3_nw_adv",
    "radiant_top3_nw_ratio",
    "dire_top3_nw_ratio",
    "market_radiant_prior",
    "market_p_radiant",
    *LOL_LABEL_COLUMNS,
]
LOL_DATASET_INTEGER_COLUMNS: list[str] = [
    "match_id",
    "start_time",
    "second",
    "state_ts_us",
    "radiant_nw_adv",
    "radiant_nw",
    "dire_nw",
    "radiant_xp_adv",
    "deaths_radiant",
    "deaths_dire",
    "top1_nw_adv",
    "top3_nw_adv",
]
REASON_NO_LIVESTATS = "no_livestats"
REASON_NO_DETAILS = "no_details"
REASON_NO_PATCH_VERSION = "no_patch_version"
REASON_NO_SPAWN_FRAME = "no_spawn_frame"
REASON_LIVESTATS_INVARIANT_VIOLATION = "livestats_invariant_violation"
REASON_WINDOW_DETAILS_MISMATCH = "window_details_mismatch"
REASON_NEGATIVE_NET_WORTH = "negative_net_worth"
REASON_MISSING_BOOKS = "missing_books"
REASON_MISSING_PRIOR = "missing_prior"
REASON_ZERO_USABLE_ROWS = "zero_usable_rows"
REASON_ABORTED_FEED = "aborted_feed"
REASON_ZERO_USABLE_FRAMES = "zero_usable_frames"
REASON_ZERO_LABELED_ROWS = "zero_labeled_rows"

# Stage 05 grids, labels, and training rows follow the whole match; the archive
# coverage and metrics window stops at shared BUY_CUTOFF_SECOND, where we buy.
# LOL_FETCH_END_SECOND is the fetch/prepare safety cap.
LOL_PREPARE_END_SECOND = LOL_FETCH_END_SECOND
LOL_REPLAY_DRAIN = timedelta(minutes=1)
REASON_MISSING_LIVESTATS = "missing_livestats"
