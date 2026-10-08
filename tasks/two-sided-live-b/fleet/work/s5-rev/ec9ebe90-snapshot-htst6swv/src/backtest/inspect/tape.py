"""Per-match bid/ask/mid/fair series, resting-order segments, and fill markers."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false

from dataclasses import dataclass
from datetime import timedelta
from math import ceil, floor
from pathlib import Path

import pandas as pd

from backtest.inspect.catalog import Game
from backtest.marks import token_mid
from backtest.paths import FILLS_FILENAME, QUOTE_EVENTS_FILENAME, RESULTS_FILENAME
from backtest.quote_store import QUOTE_EVENT_PARTS_DIRNAME
from market_data.build_market_data import market_seconds_cache_path
from shared.constants.lol import (
    LOL_BACKTEST_AUDIT_PATH,
    LOL_BACKTEST_MARKET_SECONDS_PATH,
    LOL_RAW_TELONEX_DIR,
    LOL_VALIDATION_PATH,
)
from shared.constants.paths import (
    MATCH_CATALOG_PATH,
    RAW_TELONEX_POLYMARKET_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.utils.match_catalog import MatchCatalog, load_match_catalog
from shared.utils.match_time import NS_PER_SECOND, datetime_to_ns, parse_utc
from shared.utils.telonex_book import NS_PER_US, TokenBook, find_asof_quote, load_token_book
from viewer.types import (
    GameState,
    OrderSegment,
    Series,
    TapeMarker,
    TokenTape,
    empty_game_state,
    last_per_second,
    token_predicted_delta,
)

CLOSE_KINDS = frozenset({"canceled", "cancel_ack", "rejected", "denied", "expired"})
BOOK_LEAD = timedelta(minutes=2)
BOOK_TAIL = timedelta(minutes=1)
_FILL_COLUMNS = (
    "match_id",
    "token_index",
    "side",
    "price",
    "quantity",
    "ts_ns",
    "order_id",
    "level_index",
    "predicted_delta",
    "fair",
)
_QUOTE_COLUMNS = (
    "match_id",
    "ts_ns",
    "kind",
    "token_index",
    "side",
    "price",
    "quantity",
    "predicted_delta",
    "fair",
    "book_p_radiant",
    "order_id",
    "level_index",
)


@dataclass(frozen=True)
class MatchTokens:
    """Both CLOB token ids and which index pays Radiant."""

    token_ids: tuple[str, str]
    radiant_token_index: int


_STATE_COLUMNS = (
    "match_id",
    "second",
    "state_ts_us",
    "radiant_nw",
    "dire_nw",
    "radiant_nw_adv",
    "top1_nw_adv",
    "radiant_xp_adv",
    "deaths_radiant",
    "deaths_dire",
)


def seconds_from_horn(ts_ns: int, horn_ns: int) -> float:
    """Wall seconds after the horn; negative before it."""
    return (ts_ns - horn_ns) / NS_PER_SECOND


def _empty_series() -> Series:
    return Series(seconds=(), y=())


def game_state_from_frame(frame: pd.DataFrame, horn_ns: int) -> GameState:
    """Build GameState from a match slice of the validation parquet."""
    if frame.empty:
        return empty_game_state()
    ordered = frame.sort_values("second")
    seconds = tuple(
        seconds_from_horn(int(raw_ts) * NS_PER_US, horn_ns)
        for raw_ts in ordered["state_ts_us"].tolist()
    )
    return GameState(
        seconds=seconds,
        game_seconds=tuple(float(value) for value in ordered["second"].tolist()),
        radiant_nw=tuple(float(value) for value in ordered["radiant_nw"].tolist()),
        dire_nw=tuple(float(value) for value in ordered["dire_nw"].tolist()),
        radiant_nw_adv=tuple(float(value) for value in ordered["radiant_nw_adv"].tolist()),
        top1_nw_adv=tuple(float(value) for value in ordered["top1_nw_adv"].tolist()),
        radiant_xp_adv=tuple(float(value) for value in ordered["radiant_xp_adv"].tolist()),
        deaths_radiant=tuple(float(value) for value in ordered["deaths_radiant"].tolist()),
        deaths_dire=tuple(float(value) for value in ordered["deaths_dire"].tolist()),
    )


def load_game_state(game: Game, match_id: int, horn_at: str) -> GameState:
    """Exact-second gold/XP/deaths for one map, or empty when the dataset is missing."""
    path = VALIDATION_DATASET_PATH if game == "dota" else LOL_VALIDATION_PATH
    if not path.is_file():
        return empty_game_state()
    frame = pd.read_parquet(
        path, columns=list(_STATE_COLUMNS), filters=[("match_id", "==", match_id)]
    )
    return game_state_from_frame(frame, datetime_to_ns(parse_utc(horn_at)))


def _latch_present(fair: float, predicted_delta: float, book_p_radiant: float) -> bool:
    """False when quote-event context is the empty latch (all zeros)."""
    return fair != 0.0 or predicted_delta != 0.0 or book_p_radiant != 0.0


def event_token_fair(
    *,
    kind: str,
    fair: float,
    predicted_delta: float,
    book_p_radiant: float,
    event_token_index: int,
    token_index: int,
    radiant_token_index: int,
) -> float | None:
    """Token-space fair from one quote event, or None when this row has no latch."""
    if not _latch_present(fair, predicted_delta, book_p_radiant):
        return None
    if kind == "no_quote":
        return token_mid(fair, token_index, radiant_token_index)
    if event_token_index != token_index:
        return None
    return fair


def load_quote_events(seed_dir: Path, match_id: int) -> pd.DataFrame:
    """Quote events for one map: per-match part, else a filter on the compacted tape."""
    part = seed_dir / QUOTE_EVENT_PARTS_DIRNAME / f"{match_id}.parquet"
    if part.is_file():
        return pd.read_parquet(part, columns=list(_QUOTE_COLUMNS))
    compacted = seed_dir / QUOTE_EVENTS_FILENAME
    if not compacted.is_file():
        return pd.DataFrame(columns=list(_QUOTE_COLUMNS))
    frame = pd.read_parquet(
        compacted, columns=list(_QUOTE_COLUMNS), filters=[("match_id", "==", match_id)]
    )
    return frame


def load_match_fills(seed_dir: Path, match_id: int) -> pd.DataFrame:
    """Fills for one map, or an empty frame when the seed has no fills file."""
    path = seed_dir / FILLS_FILENAME
    if not path.is_file():
        return pd.DataFrame(columns=list(_FILL_COLUMNS))
    return pd.read_parquet(
        path, columns=list(_FILL_COLUMNS), filters=[("match_id", "==", match_id)]
    )


def _submitted_orders(events: pd.DataFrame, token_index: int) -> pd.DataFrame:
    """Submitted quote events for one token that carry an order id."""
    is_submitted = events["kind"].eq("submitted")
    on_token = events["token_index"].eq(token_index)
    has_order = events["order_id"].ne("")
    return events.loc[is_submitted & on_token & has_order]


def bought_token_indexes(fills: pd.DataFrame, radiant_token_index: int) -> tuple[int, ...]:
    """Token indexes with BUY fills; Radiant when the map has no buys."""
    if fills.empty:
        return (radiant_token_index,)
    buys = fills.loc[fills["side"].eq("BUY"), "token_index"]
    indexes = tuple(sorted({int(value) for value in buys.tolist()}))
    return indexes if indexes else (radiant_token_index,)


def build_order_segments(
    events: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    horn_ns: int,
    game_end_ns: int,
    token_index: int,
) -> tuple[OrderSegment, ...]:
    """One horizontal rest per submitted order on `token_index`."""
    if events.empty:
        return ()
    submitted = _submitted_orders(events, token_index)
    fill_end: dict[str, int] = {}
    if not fills.empty:
        token_fills = fills.loc[fills["token_index"].eq(token_index)]
        for row in token_fills.itertuples(index=False):
            order_id = str(row.order_id)
            ts_ns = int(row.ts_ns)
            previous = fill_end.get(order_id)
            if previous is None or ts_ns > previous:
                fill_end[order_id] = ts_ns
    segments: list[OrderSegment] = []
    for row in submitted.itertuples(index=False):
        order_id = str(row.order_id)
        start_ns = int(row.ts_ns)
        order_events = events.loc[events["order_id"].eq(order_id)]
        closes = order_events.loc[order_events["kind"].isin(CLOSE_KINDS), "ts_ns"]
        if not closes.empty:
            end_ns = int(closes.min())
        elif order_id in fill_end:
            end_ns = fill_end[order_id]
        else:
            end_ns = game_end_ns
        if end_ns < start_ns:
            end_ns = start_ns
        segments.append(
            OrderSegment(
                order_id=order_id,
                side=str(row.side),
                token_index=token_index,
                level_index=int(row.level_index),
                price=float(row.price),
                start_s=seconds_from_horn(start_ns, horn_ns),
                end_s=seconds_from_horn(end_ns, horn_ns),
            )
        )
    return tuple(segments)


def _quote_fair_pred(
    events: pd.DataFrame,
    *,
    token_index: int,
    radiant_token_index: int,
    horn_ns: int,
) -> tuple[Series, Series]:
    """1 Hz-ish fair and predicted-delta (cents) for one token."""
    if events.empty:
        return _empty_series(), _empty_series()
    fair_seconds: list[float] = []
    fair_values: list[float] = []
    pred_seconds: list[float] = []
    pred_values: list[float] = []
    for row in events.itertuples(index=False):
        if not _latch_present(
            float(row.fair), float(row.predicted_delta), float(row.book_p_radiant)
        ):
            continue
        second = seconds_from_horn(int(row.ts_ns), horn_ns)
        pred_token = token_predicted_delta(
            float(row.predicted_delta), token_index, radiant_token_index
        )
        pred_seconds.append(second)
        pred_values.append(pred_token * 100.0)
        fair = event_token_fair(
            kind=str(row.kind),
            fair=float(row.fair),
            predicted_delta=float(row.predicted_delta),
            book_p_radiant=float(row.book_p_radiant),
            event_token_index=int(row.token_index),
            token_index=token_index,
            radiant_token_index=radiant_token_index,
        )
        if fair is None:
            continue
        fair_seconds.append(second)
        fair_values.append(fair)
    return last_per_second(fair_seconds, fair_values), last_per_second(pred_seconds, pred_values)


_dota_catalog_mtime: float | None = None
_dota_catalog_cache: MatchCatalog | None = None
_lol_audit_mtime: float | None = None
_lol_audit_cache: pd.DataFrame | None = None


def _dota_catalog() -> MatchCatalog:
    """Reload when match_catalog.parquet changes (Streamlit processes live long)."""
    global _dota_catalog_mtime, _dota_catalog_cache
    mtime = MATCH_CATALOG_PATH.stat().st_mtime if MATCH_CATALOG_PATH.is_file() else None
    if _dota_catalog_cache is None or mtime != _dota_catalog_mtime:
        _dota_catalog_cache = load_match_catalog(MATCH_CATALOG_PATH)
        _dota_catalog_mtime = mtime
    return _dota_catalog_cache


def _lol_audit() -> pd.DataFrame:
    """Reload when the LoL audit parquet changes."""
    global _lol_audit_mtime, _lol_audit_cache
    mtime = LOL_BACKTEST_AUDIT_PATH.stat().st_mtime if LOL_BACKTEST_AUDIT_PATH.is_file() else None
    if _lol_audit_cache is None or mtime != _lol_audit_mtime:
        _lol_audit_cache = pd.read_parquet(
            LOL_BACKTEST_AUDIT_PATH,
            columns=["match_id", "token_id_0", "token_id_1", "radiant_token_index"],
        )
        _lol_audit_mtime = mtime
    return _lol_audit_cache


def load_match_tokens(game: Game, match_id: int) -> MatchTokens | None:
    """Token ids for a map, or None when the catalog/audit row is missing."""
    if game == "dota":
        if not MATCH_CATALOG_PATH.is_file():
            return None
        catalog = _dota_catalog()
        if match_id not in catalog:
            return None
        entry = catalog[match_id]
        return MatchTokens(
            token_ids=entry.gamma.token_ids, radiant_token_index=int(entry.radiant_token_index)
        )
    if not LOL_BACKTEST_AUDIT_PATH.is_file():
        return None
    frame = _lol_audit()
    rows = frame.loc[frame["match_id"].eq(match_id)]
    if rows.empty:
        return None
    row = rows.iloc[0]
    return MatchTokens(
        token_ids=(str(row["token_id_0"]), str(row["token_id_1"])),
        radiant_token_index=int(row["radiant_token_index"]),
    )


def load_mid_from_seed(
    seed_dir: Path,
    match_id: int,
    token_index: int,
    radiant_token_index: int,
    horn_ns: int,
) -> Series:
    """Fallback mid series written by live-cohort from core_trace BookUpdates."""
    path = seed_dir / "book_mids.parquet"
    if not path.is_file():
        return _empty_series()
    frame = pd.read_parquet(
        path,
        columns=["match_id", "state_ts_us", "market_p_radiant", "market_status"],
        filters=[("match_id", "==", match_id)],
    )
    if frame.empty:
        return _empty_series()
    ok = frame.loc[frame["market_status"].eq("ok") & frame["market_p_radiant"].notna()]
    seconds: list[float] = []
    values: list[float] = []
    for raw_ts, raw_p in zip(
        ok["state_ts_us"].tolist(), ok["market_p_radiant"].tolist(), strict=True
    ):
        seconds.append(seconds_from_horn(int(raw_ts) * NS_PER_US, horn_ns))
        values.append(token_mid(float(raw_p), token_index, radiant_token_index))
    return Series(seconds=tuple(seconds), y=tuple(values))


def load_bid_ask_from_seed(
    seed_dir: Path,
    match_id: int,
    token_index: int,
    radiant_token_index: int,
    horn_ns: int,
) -> tuple[Series, Series]:
    """Fallback TOB bid/ask from live-cohort book_mids.parquet (radiant space)."""
    path = seed_dir / "book_mids.parquet"
    if not path.is_file():
        return _empty_series(), _empty_series()
    frame = pd.read_parquet(path, filters=[("match_id", "==", match_id)])
    if frame.empty or not {"bid", "ask", "state_ts_us", "market_status"}.issubset(frame.columns):
        return _empty_series(), _empty_series()
    ok = frame.loc[
        frame["market_status"].eq("ok")
        & frame["bid"].notna()
        & frame["ask"].notna()
        & (frame["bid"] > 0)
        & (frame["ask"] > 0)
    ]
    bid_seconds: list[float] = []
    bids: list[float] = []
    ask_seconds: list[float] = []
    asks: list[float] = []
    for raw_ts, raw_bid, raw_ask in zip(
        ok["state_ts_us"].tolist(), ok["bid"].tolist(), ok["ask"].tolist(), strict=True
    ):
        second = seconds_from_horn(int(raw_ts) * NS_PER_US, horn_ns)
        bid = float(raw_bid)
        ask = float(raw_ask)
        if token_index != radiant_token_index:
            # complementary outcome: swap and invert
            bid, ask = 1.0 - ask, 1.0 - bid
        bid_seconds.append(second)
        bids.append(bid)
        ask_seconds.append(second)
        asks.append(ask)
    return Series(tuple(bid_seconds), tuple(bids)), Series(tuple(ask_seconds), tuple(asks))


def load_mid_series(
    game: Game, match_id: int, token_index: int, radiant_token_index: int, horn_ns: int
) -> Series:
    """Ok market-seconds mids converted into the plotted token."""
    if game == "dota":
        path = market_seconds_cache_path(match_id)
        if not path.is_file():
            return _empty_series()
        frame = pd.read_parquet(path, columns=["state_ts_us", "market_p_radiant", "market_status"])
    else:
        if not LOL_BACKTEST_MARKET_SECONDS_PATH.is_file():
            return _empty_series()
        frame = pd.read_parquet(
            LOL_BACKTEST_MARKET_SECONDS_PATH,
            columns=["match_id", "state_ts_us", "market_p_radiant", "market_status"],
            filters=[("match_id", "==", match_id)],
        )
    ok = frame.loc[frame["market_status"].eq("ok") & frame["market_p_radiant"].notna()]
    seconds: list[float] = []
    values: list[float] = []
    for raw_ts, raw_p in zip(
        ok["state_ts_us"].tolist(), ok["market_p_radiant"].tolist(), strict=True
    ):
        seconds.append(seconds_from_horn(int(raw_ts) * NS_PER_US, horn_ns))
        values.append(token_mid(float(raw_p), token_index, radiant_token_index))
    return Series(seconds=tuple(seconds), y=tuple(values))


def downsample_bid_ask(
    book: TokenBook, horn_ns: int, start_s: int, end_s: int
) -> tuple[Series, Series]:
    """1 Hz as-of bid and ask; skips seconds with no two-sided quote."""
    bid_seconds: list[float] = []
    bids: list[float] = []
    ask_seconds: list[float] = []
    asks: list[float] = []
    for second in range(start_s, end_s + 1):
        target_us = (horn_ns + second * NS_PER_SECOND) // NS_PER_US
        quote = find_asof_quote(book, target_us).quote
        if quote is None:
            continue
        bid_seconds.append(float(second))
        bids.append(quote.bid)
        ask_seconds.append(float(second))
        asks.append(quote.ask)
    return Series(tuple(bid_seconds), tuple(bids)), Series(tuple(ask_seconds), tuple(asks))


def _telonex_root(game: Game) -> Path:
    return RAW_TELONEX_POLYMARKET_DIR if game == "dota" else LOL_RAW_TELONEX_DIR


def _load_bid_ask(
    *,
    game: Game,
    tokens: MatchTokens | None,
    token_index: int,
    horn_ns: int,
    game_end_ns: int,
) -> tuple[Series, Series]:
    if tokens is None:
        return _empty_series(), _empty_series()
    token_id = tokens.token_ids[token_index]
    start_us = (horn_ns - int(BOOK_LEAD.total_seconds() * NS_PER_SECOND)) // NS_PER_US
    end_us = (game_end_ns + int(BOOK_TAIL.total_seconds() * NS_PER_SECOND)) // NS_PER_US
    book = load_token_book(
        token_id=token_id, start_us=start_us, end_us=end_us, telonex_root=_telonex_root(game)
    )
    if book is None:
        return _empty_series(), _empty_series()
    start_s = floor(seconds_from_horn(start_us * NS_PER_US, horn_ns))
    end_s = ceil(seconds_from_horn(end_us * NS_PER_US, horn_ns))
    return downsample_bid_ask(book, horn_ns, start_s, end_s)


def _markers(
    events: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    token_index: int,
    horn_ns: int,
) -> tuple[tuple[TapeMarker, ...], tuple[TapeMarker, ...]]:
    submits: list[TapeMarker] = []
    if not events.empty:
        submitted = _submitted_orders(events, token_index)
        for row in submitted.itertuples(index=False):
            submits.append(
                TapeMarker(
                    second=seconds_from_horn(int(row.ts_ns), horn_ns),
                    price=float(row.price),
                    side=str(row.side),
                    kind="submit",
                    level_index=int(row.level_index),
                    quantity=float(row.quantity),
                )
            )
    fill_marks: list[TapeMarker] = []
    if not fills.empty:
        token_fills = fills.loc[fills["token_index"].eq(token_index)]
        for row in token_fills.itertuples(index=False):
            fill_marks.append(
                TapeMarker(
                    second=seconds_from_horn(int(row.ts_ns), horn_ns),
                    price=float(row.price),
                    side=str(row.side),
                    kind="fill",
                    level_index=int(row.level_index),
                    quantity=float(row.quantity),
                )
            )
    return tuple(submits), tuple(fill_marks)


def _infer_radiant_token_index(
    *,
    fills: pd.DataFrame,
    token_index: int,
    mid_radiant_median: float | None,
) -> int | None:
    """Guess radiant index when catalog is missing: fills sit near mid or 1-mid."""
    if fills.empty or mid_radiant_median is None:
        return None
    token_fills = fills.loc[fills["token_index"].eq(token_index)]
    if token_fills.empty:
        return None
    price = float(token_fills["price"].median())
    as_radiant = abs(price - mid_radiant_median)
    as_dire = abs(price - (1.0 - mid_radiant_median))
    if as_radiant <= as_dire:
        return token_index  # this token is radiant
    return 1 - token_index  # this token is dire ⇒ radiant is the other


def _market_p_radiant_median(game: Game, match_id: int) -> float | None:
    if game == "dota":
        path = market_seconds_cache_path(match_id)
        if not path.is_file():
            return None
        frame = pd.read_parquet(path, columns=["market_p_radiant", "market_status"])
    else:
        if not LOL_BACKTEST_MARKET_SECONDS_PATH.is_file():
            return None
        frame = pd.read_parquet(
            LOL_BACKTEST_MARKET_SECONDS_PATH,
            columns=["match_id", "market_p_radiant", "market_status"],
            filters=[("match_id", "==", match_id)],
        )
    ok = frame.loc[frame["market_status"].eq("ok") & frame["market_p_radiant"].notna()]
    if ok.empty:
        return None
    return float(ok["market_p_radiant"].median())


def _radiant_token_index_from_seed(seed_dir: Path, match_id: int) -> int | None:
    """Live-cohort stores the strategy radiant index when the match catalog is missing."""
    results_path = seed_dir / RESULTS_FILENAME
    if results_path.is_file():
        frame = pd.read_parquet(results_path, filters=[("match_id", "==", match_id)])
        if not frame.empty and "radiant_token_index" in frame.columns:
            value = int(frame["radiant_token_index"].iloc[0])
            if value in (0, 1):
                return value
    book_path = seed_dir / "book_mids.parquet"
    if book_path.is_file():
        frame = pd.read_parquet(book_path, filters=[("match_id", "==", match_id)])
        if not frame.empty and "radiant_token_index" in frame.columns:
            value = int(frame["radiant_token_index"].iloc[0])
            if value in (0, 1):
                return value
    return None


def load_match_tapes(
    *,
    game: Game,
    seed_dir: Path,
    match_id: int,
    horn_at: str,
    game_ended_at: str,
) -> tuple[TokenTape, ...]:
    """One tape per bought token (Radiant if the map never bought)."""
    if not horn_at:
        raise ValueError(f"match {match_id} missing horn_at")
    horn_ns = datetime_to_ns(parse_utc(horn_at))
    if game_ended_at:
        game_end_ns = datetime_to_ns(parse_utc(game_ended_at))
    else:
        # live-cohort rows may omit end; keep a wide window for quote overlays
        game_end_ns = horn_ns + 6 * 3600 * 1_000_000_000
    events = load_quote_events(seed_dir, match_id)
    fills = load_match_fills(seed_dir, match_id)
    tokens = load_match_tokens(game, match_id)
    if tokens is not None:
        radiant_token_index = tokens.radiant_token_index
    else:
        seeded = _radiant_token_index_from_seed(seed_dir, match_id)
        if seeded is not None:
            radiant_token_index = seeded
        else:
            bought = bought_token_indexes(fills, 0)
            guessed = _infer_radiant_token_index(
                fills=fills,
                token_index=bought[0],
                mid_radiant_median=_market_p_radiant_median(game, match_id),
            )
            radiant_token_index = 0 if guessed is None else guessed
    tapes: list[TokenTape] = []
    for token_index in bought_token_indexes(fills, radiant_token_index):
        side_name = "radiant" if token_index == radiant_token_index else "dire"
        fair, pred_cents = _quote_fair_pred(
            events,
            token_index=token_index,
            radiant_token_index=radiant_token_index,
            horn_ns=horn_ns,
        )
        bid, ask = _load_bid_ask(
            game=game,
            tokens=tokens,
            token_index=token_index,
            horn_ns=horn_ns,
            game_end_ns=game_end_ns,
        )
        if not bid.seconds and not ask.seconds:
            bid, ask = load_bid_ask_from_seed(
                seed_dir, match_id, token_index, radiant_token_index, horn_ns
            )
        submits, fill_marks = _markers(events, fills, token_index=token_index, horn_ns=horn_ns)
        mid = load_mid_series(game, match_id, token_index, radiant_token_index, horn_ns)
        if not mid.seconds:
            mid = load_mid_from_seed(seed_dir, match_id, token_index, radiant_token_index, horn_ns)
        tapes.append(
            TokenTape(
                token_index=token_index,
                side_name=side_name,
                mid=mid,
                bid=bid,
                ask=ask,
                fair=fair,
                pred_cents=pred_cents,
                segments=build_order_segments(
                    events, fills, horn_ns=horn_ns, game_end_ns=game_end_ns, token_index=token_index
                ),
                submits=submits,
                fills=fill_marks,
            )
        )
    return tuple(tapes)
