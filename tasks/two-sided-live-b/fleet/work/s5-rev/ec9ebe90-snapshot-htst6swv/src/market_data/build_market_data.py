import hashlib
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from shared.constants.dataset import (
    MODEL_START_SECOND,
    MODEL_TARGET_HORIZON_SECONDS,
)
from shared.constants.paths import (
    MARKET_SECONDS_DIR,
    MATCH_CATALOG_PATH,
    RAW_TELONEX_POLYMARKET_DIR,
)
from shared.constants.strategy import MAX_ENTRY_SPREAD_TICKS
from shared.types.dataset import MarketSecondRow
from shared.types.opendota import OpenDotaPause, RadiantTokenIndex
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_catalog import CatalogEntry, load_match_catalog
from shared.utils.match_time import datetime_to_ns, get_state_available_ts
from shared.utils.parquet_io import write_parquet
from shared.utils.telonex_book import (
    MAX_BOOK_AGE_SECONDS,
    NS_PER_US,
    PAIR_SUM_TOLERANCE,
    US_PER_SECOND,
    find_asof_quote,
    load_token_book,
    lookup_market_p_after,
    resolve_market_pair,
)

CACHE_BUSTING_PARAMETERS = (
    MAX_BOOK_AGE_SECONDS,
    PAIR_SUM_TOLERANCE,
    MAX_ENTRY_SPREAD_TICKS,
    30,
    MODEL_TARGET_HORIZON_SECONDS,
    MODEL_START_SECOND,
)
CACHE_VERSION = hashlib.sha256(repr(CACHE_BUSTING_PARAMETERS).encode()).hexdigest()[:8]

logger = get_logger(__name__)


def market_seconds_cache_path(match_id: int) -> Path:
    return MARKET_SECONDS_DIR / f"v{CACHE_VERSION}" / f"match_id={match_id}.parquet"


@dataclass(frozen=True)
class MarketCacheJob:
    match_id: int
    condition_id: str
    event_id: str
    token_ids: tuple[str, str]
    radiant_token_index: RadiantTokenIndex
    horn: datetime
    pauses: list[OpenDotaPause]
    duration_seconds: int
    telonex_root: Path
    cache_path: Path


def resolve_catalog_row(row: CatalogEntry, telonex_root: Path) -> MarketCacheJob:
    return MarketCacheJob(
        match_id=row.match_id,
        condition_id=row.condition_id,
        event_id=row.event_id,
        token_ids=row.gamma.token_ids,
        radiant_token_index=row.radiant_token_index,
        horn=row.horn_at,
        pauses=row.pauses,
        duration_seconds=row.duration,
        telonex_root=telonex_root,
        cache_path=market_seconds_cache_path(row.match_id),
    )


def load_market_data_sources(
    catalog_path: Path,
    telonex_root: Path,
) -> tuple[MarketCacheJob, ...]:
    catalog = load_match_catalog(catalog_path)
    logger.info("market-data selected=%s", len(catalog))
    return tuple(resolve_catalog_row(row, telonex_root=telonex_root) for row in catalog.values())


def build_market_second_rows(job: MarketCacheJob) -> list[MarketSecondRow] | None:
    seconds = range(MODEL_START_SECOND, job.duration_seconds + 1)
    state_us_by_second = [
        datetime_to_ns(get_state_available_ts(horn=job.horn, second=second, pauses=job.pauses))
        // NS_PER_US
        for second in seconds
    ]
    start_us = state_us_by_second[0] - int(MAX_BOOK_AGE_SECONDS * US_PER_SECOND)
    end_us = state_us_by_second[-1] + MODEL_TARGET_HORIZON_SECONDS * US_PER_SECOND

    books = [
        load_token_book(
            token_id=token_id, start_us=start_us, end_us=end_us, telonex_root=job.telonex_root
        )
        for token_id in job.token_ids
    ]
    radiant_book = books[job.radiant_token_index]
    dire_book = books[1 - job.radiant_token_index]
    if radiant_book is None or dire_book is None:
        return None

    rows: list[MarketSecondRow] = []
    for second, state_us in zip(seconds, state_us_by_second, strict=True):
        market = resolve_market_pair(
            find_asof_quote(radiant_book, state_us),
            find_asof_quote(dire_book, state_us),
        )
        signal_30s = lookup_market_p_after(radiant_book, dire_book, state_us, 30)
        signal_300s = lookup_market_p_after(
            radiant_book, dire_book, state_us, MODEL_TARGET_HORIZON_SECONDS
        )
        rows.append(
            {
                "match_id": job.match_id,
                "condition_id": job.condition_id,
                "event_id": job.event_id,
                "second": second,
                "state_ts_us": state_us,
                "market_status": market.status,
                "market_p_radiant": market.market_p_radiant,
                "signal_market_p_radiant_30s": signal_30s,
                "signal_market_p_radiant_300s": signal_300s,
            }
        )
    return rows


def try_build_one_market_cache(job: MarketCacheJob) -> None:
    rows = build_market_second_rows(job)
    if rows is None:
        return
    write_parquet(pd.DataFrame(rows), job.cache_path)


def dispatch_builds(jobs: tuple[MarketCacheJob, ...]) -> None:
    if not jobs:
        return
    available_workers = max(1, os.cpu_count() or 1)
    worker_count = min(available_workers, len(jobs))
    logger.info("market-data building %s matches on %s workers", len(jobs), worker_count)
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = {pool.submit(try_build_one_market_cache, job): job.match_id for job in jobs}
        for completed_count, future in enumerate(as_completed(futures), start=1):
            future.result()
            logger.info(
                "market-data %s/%s %s completed",
                completed_count,
                len(jobs),
                futures[future],
            )


def run_market_data_build(
    jobs: tuple[MarketCacheJob, ...],
) -> None:
    queued = tuple(job for job in jobs if not job.cache_path.exists())
    dispatch_builds(queued)

    logger.info(
        "market-data skipped=%s queued=%s",
        len(jobs) - len(queued),
        len(queued),
    )


def main() -> None:
    setup_logging()
    logger.info("market-data loading sources")
    jobs = load_market_data_sources(
        catalog_path=MATCH_CATALOG_PATH,
        telonex_root=RAW_TELONEX_POLYMARKET_DIR,
    )
    run_market_data_build(
        jobs=jobs,
    )


if __name__ == "__main__":
    main()
