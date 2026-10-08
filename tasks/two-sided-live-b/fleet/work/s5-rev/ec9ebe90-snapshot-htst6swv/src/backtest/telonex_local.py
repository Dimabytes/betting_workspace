"""Local Telonex capture: eligibility check and symlink bridge for the framework.

The capture is partitioned by asset id (`<channel>/asset_id=<token>/<date>.parquet`);
the framework looks for `<root>/polymarket/<channel>/<slug>/outcome_id=<index>/<date>.parquet`.
Selection asks whether the day files needed for a replay window exist; a throwaway
tree then bridges the two layouts — symlinks where files pass through unchanged,
persistent cached copies where they are rewritten.

Book day files of schedule-bound archives lose our own live resting size so the
sim does not queue behind its doppelganger; every onchain_fills day file gains
a `side` column with the aggressor on the file's own book.
"""

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from backtest import onchain_side, strip_own_book
from backtest.context import MarketContext, ReplayWindow
from backtest.strip_own_book import load_resting_events, strip_day_book_file
from shared.constants.telonex import (
    ONCHAIN_CHANNEL,
    TELONEX_BOOK_CHANNEL,
    TELONEX_EXCHANGE,
)
from shared.utils.jsonl_io import resolve_jsonl
from shared.utils.log import get_logger
from shared.utils.telonex_capture import asset_channel_dir

logger = get_logger(__name__)

BOOK_CHANNEL = TELONEX_BOOK_CHANNEL
REQUIRED_CHANNELS = (BOOK_CHANNEL, ONCHAIN_CHANNEL)


def window_utc_days(window: ReplayWindow) -> tuple[str, ...]:
    """Inclusive UTC date stems the replay window touches."""
    start_day = window.start.astimezone(UTC).date()
    end_day = window.end.astimezone(UTC).date()
    days: list[str] = []
    day = start_day
    while day <= end_day:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return tuple(days)


def has_local_telonex_days(
    token_ids: tuple[str, str], window: ReplayWindow, capture_root: Path
) -> bool:
    """True when both tokens have every book and onchain_fills day file for the window."""
    needed = set(window_utc_days(window))
    for token_id in token_ids:
        for channel in REQUIRED_CHANNELS:
            channel_dir = asset_channel_dir(capture_root, channel, token_id)
            if not channel_dir.is_dir():
                return False
            present = {path.stem for path in channel_dir.glob("*.parquet")}
            if not needed <= present:
                return False
    return True


def book_rows_in_window(
    token_ids: tuple[str, str], window: ReplayWindow, capture_root: Path
) -> int:
    """Book day-file rows both tokens hold over the window's UTC days."""
    rows = 0
    for token_id in token_ids:
        channel_dir = asset_channel_dir(capture_root, BOOK_CHANNEL, token_id)
        for day in window_utc_days(window):
            path = channel_dir / f"{day}.parquet"
            if path.is_file():
                rows += pq.ParquetFile(path).metadata.num_rows
    return rows


def outcome_dir(root: Path, channel: str, slug: str, index: int) -> Path:
    return root / TELONEX_EXCHANGE / channel / slug / f"outcome_id={index}"


def _symlink_channel_dir(dest: Path, source: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)
    dest.symlink_to(source.resolve(), target_is_directory=True)


def link_market_tokens(
    root: Path,
    context: MarketContext,
    capture_root: Path,
    *,
    archive_dir: Path | None = None,
) -> None:
    """Link books and onchain_fills into the tree, rewriting day files that need it.

    Book days are rewritten only for schedule-bound archives (the resting
    strip); onchain days are always rewritten so the framework sees the
    aggressor on each file's own token.
    """
    window = ReplayWindow(start=context.replay_start, end=context.replay_end)
    days = window_utc_days(window)
    events: tuple[tuple[int, str, Any], ...] | None = None

    def resting(archive: Path) -> tuple[tuple[int, str, Any], ...]:
        """Parse the archive core_trace once, and only on a cache miss."""
        nonlocal events
        if events is None:
            events = load_resting_events(archive)
        return events

    for token_index, token_id in enumerate(context.token_ids):
        book_dir = asset_channel_dir(capture_root, BOOK_CHANNEL, token_id)
        if not book_dir.is_dir():
            raise ValueError(
                f"match {context.match_id}: no local Telonex {BOOK_CHANNEL} for {book_dir}"
            )
        onchain_dir = asset_channel_dir(capture_root, ONCHAIN_CHANNEL, token_id)
        if not onchain_dir.is_dir():
            raise ValueError(
                f"match {context.match_id}: no local Telonex {ONCHAIN_CHANNEL} for {onchain_dir}"
            )
        book_outcome = outcome_dir(root, BOOK_CHANNEL, context.market_slug, token_index)
        if archive_dir is not None:
            _link_stripped_book_days(
                book_outcome,
                book_dir=book_dir,
                days=days,
                token_index=token_index,
                archive_dir=archive_dir,
                market_slug=context.market_slug,
                resting=resting,
            )
        else:
            _symlink_channel_dir(book_outcome, book_dir)
        _link_onchain_days(root, context, onchain_dir, token_index=token_index, days=days)


def _link_stripped_book_days(
    book_outcome: Path,
    *,
    book_dir: Path,
    days: Sequence[str],
    token_index: int,
    archive_dir: Path,
    market_slug: str,
    resting: Callable[[Path], tuple[tuple[int, str, Any], ...]],
) -> None:
    """Rewrite every window day's book file with the live resting size stripped."""
    book_outcome.mkdir(parents=True, exist_ok=True)
    for day in days:
        source_book = book_dir / f"{day}.parquet"
        if not source_book.is_file():
            continue
        key = "|".join(
            (
                "strip",
                _rewrite_code_stamp(str(strip_own_book.__file__)),
                _file_stamp(source_book),
                str(token_index),
                _archive_stamp(archive_dir),
            )
        )
        _link_cached_day(
            key,
            book_outcome / f"{day}.parquet",
            lambda out, book=source_book, index=token_index, archive=archive_dir: (
                strip_day_book_file(
                    book_path=book.resolve(),
                    out_path=out,
                    token_index=index,
                    events=resting(archive),
                )
            ),
            market_slug=market_slug,
            channel=BOOK_CHANNEL,
        )


def _link_onchain_days(
    root: Path,
    context: MarketContext,
    onchain_dir: Path,
    *,
    token_index: int,
    days: Sequence[str],
) -> None:
    """Rewrite every window day's onchain file with the `side` column."""
    onchain_outcome = outcome_dir(root, ONCHAIN_CHANNEL, context.market_slug, token_index)
    onchain_outcome.mkdir(parents=True, exist_ok=True)
    for day in days:
        source_fills = onchain_dir / f"{day}.parquet"
        if not source_fills.is_file():
            continue
        key = "|".join(
            (
                "onchain-side",
                _rewrite_code_stamp(str(onchain_side.__file__)),
                _file_stamp(source_fills),
            )
        )
        _link_cached_day(
            key,
            onchain_outcome / f"{day}.parquet",
            lambda out, fills=source_fills: onchain_side.write_aggressor_side(
                source_path=fills.resolve(), out_path=out
            ),
            market_slug=context.market_slug,
            channel=ONCHAIN_CHANNEL,
        )


DISABLED_CACHE_VALUES = frozenset({"0", "false", "no", "off", ""})
TREE_CACHE_ROOT_ENV = "TELONEX_TREE_CACHE"
TELONEX_CACHE_ROOT_ENV = "TELONEX_CACHE_ROOT"
# Each rewritten channel owns exactly one framework cache version; a rebuild
# evicts only its own, so the onchain side fix never re-materializes books.
CHANNEL_CACHE_VERSIONS = {
    BOOK_CHANNEL: "book-deltas-v1",
    ONCHAIN_CHANNEL: "trade-ticks-v1",
}


@cache
def _rewrite_code_stamp(module_file: str) -> str:
    """Hash of a rewrite module's file, so a logic change invalidates the cache."""
    return hashlib.sha256(Path(module_file).read_bytes()).hexdigest()[:16]


def _file_stamp(path: Path) -> str:
    """Size and mtime of the real file, so a worktree symlink hits the same key."""
    real = path.resolve()
    stat = real.stat()
    return f"{real}|{stat.st_size}|{stat.st_mtime_ns}"


def _archive_stamp(archive_dir: Path) -> str:
    """Identity of the inputs load_resting_events reads."""
    real_dir = archive_dir.resolve()
    parts = [str(real_dir)]
    for name in ("core_trace.jsonl", "session.jsonl"):
        path = resolve_jsonl(real_dir / name)
        parts.append(_file_stamp(path) if path.is_file() else f"{path.resolve()}|-")
    return "|".join(parts)


def tree_cache_root() -> Path | None:
    """Where rewritten day files are kept between runs; None when disabled."""
    configured = os.getenv(TREE_CACHE_ROOT_ENV)
    if configured is not None and configured.strip().casefold() in DISABLED_CACHE_VALUES:
        return None
    if configured is not None and configured.strip():
        return Path(configured.strip()).expanduser()
    xdg = os.getenv("XDG_CACHE_HOME")
    cache_home = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return cache_home / "esports-trader" / "telonex-tree"


def _link_cached_day(
    key: str,
    dest: Path,
    build: Callable[[Path], object],
    *,
    market_slug: str,
    channel: str,
) -> None:
    """Point dest at the cached rewrite of key; build it first on a miss.

    The framework caches materialized records under a path that names the
    market, not the source content, so a miss evicts this market's cache for
    the channel *before* the copy is built: a crash between the two then only
    costs a rebuild, never stale records. A hit means the eviction already
    happened on the miss that produced the copy.
    """
    root = tree_cache_root()
    if root is None:
        clear_telonex_cache_for_market(market_slug, channel=channel)
        build(dest)
        return
    cached = root / f"{hashlib.sha256(key.encode()).hexdigest()}.parquet"
    if not cached.is_file():
        clear_telonex_cache_for_market(market_slug, channel=channel)
        cached.parent.mkdir(parents=True, exist_ok=True)
        staged = cached.with_name(f"{cached.stem}.{os.getpid()}.tmp")
        try:
            build(staged)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        staged.replace(cached)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    dest.symlink_to(cached)


def telonex_cache_root() -> Path | None:
    """Where the framework caches materialized Telonex records; None when disabled."""
    configured = os.getenv(TELONEX_CACHE_ROOT_ENV)
    if configured is not None and configured.strip().casefold() in DISABLED_CACHE_VALUES:
        return None
    if configured is not None and configured.strip():
        return Path(configured.strip()).expanduser()
    xdg = os.getenv("XDG_CACHE_HOME")
    cache_home = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return cache_home / "nautilus_trader" / "telonex"


def clear_telonex_cache_for_market(market_slug: str, *, channel: str) -> None:
    """Evict one market's framework cache for one rewritten channel.

    The framework caches records under
    ``<cache root>/<version>/polymarket/<channel>/<market slug>`` and keys them by
    that path, not by source content, so a rewrite must evict by hand or stale
    records come back. Only that exact directory is removed — other channels
    and other markets keep their cache, and so does a concurrent shard.
    """
    cache_root = telonex_cache_root()
    if cache_root is None:
        return
    market_dir = (
        cache_root / CHANNEL_CACHE_VERSIONS[channel] / TELONEX_EXCHANGE / channel / market_slug
    )
    if market_dir.is_dir():
        # A sibling shard may evict the same market at the same time.
        shutil.rmtree(market_dir, ignore_errors=True)


@contextmanager
def create_telonex_source_tree(
    contexts: Sequence[MarketContext],
    capture_root: Path,
    schedule_archives: Mapping[int, Path],
) -> Generator[Path]:
    """Build a temporary local Telonex view of the given markets and clean it up after.

    `schedule_archives` names the archive dir for matches whose feed plan bound
    one; their book day files are rewritten so our own live resting size does
    not queue the sim behind its doppelganger. Onchain day files are always
    rewritten to carry the file-token aggressor side.
    """
    with tempfile.TemporaryDirectory(prefix="telonex-local-") as temporary_dir:
        root = Path(temporary_dir)
        for index, context in enumerate(contexts, start=1):
            archive = schedule_archives.get(context.match_id)
            if archive is not None and not resolve_jsonl(archive / "core_trace.jsonl").is_file():
                # ponytail: without core_trace no resting events exist to strip;
                # leave the tape as captured rather than crash the run.
                logger.warning("schedule archive without core_trace, skipping strip: %s", archive)
                archive = None
            logger.info(
                "source tree %s/%s: match %s%s",
                index,
                len(contexts),
                context.match_id,
                "" if archive is None else f" (strip {archive.name})",
            )
            link_market_tokens(root, context, capture_root, archive_dir=archive)
        yield root
