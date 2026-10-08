import gzip
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

import httpx
import pandas as pd
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from archive_index.schedule import load_archive_pauses
from collect.common.catalog_types import MatchLinkRow, PregameQuoteRow
from collect.common.paths import (
    GRID_GAME_WINDOWS_PATH,
    MATCH_LINKS_PATH,
    PREGAME_QUOTES_PATH,
    RAW_POLYMARKET_DOTA_PRICES_HISTORY_DIR,
    UNIVERSE_PATH,
)
from collect.common.timestamps import require_ts
from shared.constants import api
from shared.constants.api import QUOTE_TRAILING_SECONDS
from shared.types.opendota import OpenDotaPause
from shared.types.polymarket import PricePoint, PricesHistoryPayload
from shared.utils.http import get_json, http_client
from shared.utils.json_io import read_gzip_json
from shared.utils.log import print_count
from shared.utils.match_time import get_spawn_unix_from_horn
from shared.utils.opendota import try_load_opendota_pauses
from shared.utils.parquet_io import write_parquet
from shared.utils.parsing import opt_int, opt_str, parse_ts
from shared.utils.price_history import last_aligned_pre_anchor_pair, parse_history
from shared.utils.telonex_book import PAIR_SUM_TOLERANCE, normalize_pair_mids

PREGAME_QUOTE_COLUMNS: list[str] = list(PregameQuoteRow.__annotations__)


@dataclass(frozen=True)
class PriceTarget:
    condition_id: str
    match_id: int
    anchor_ts: int
    radiant_token_id: str
    dire_token_id: str

    @property
    def window_start_ts(self) -> int:
        return self.anchor_ts - QUOTE_TRAILING_SECONDS

    @property
    def radiant_then_dire(self) -> tuple[str, str]:
        return (self.radiant_token_id, self.dire_token_id)


def _fallback_pauses(
    links: pd.DataFrame, spawn_by_condition: Mapping[str, int]
) -> dict[int, list[OpenDotaPause] | None]:
    """match_id -> pauses for no-spawn rows; OpenDota first, else the archive schedule.

    A present key with None means the boundary was unobserved: unknown, not
    empty. OpenDota wins where both sources resolved the match — the s06
    precedence order.
    """
    spawnless = links[~links["map_condition_id"].astype(str).isin(spawn_by_condition)]
    match_ids = [
        match_id
        for row in spawnless.itertuples(index=False)
        if (match_id := opt_int(row.match_id)) is not None
    ]
    pauses_by_match: dict[int, list[OpenDotaPause] | None] = dict(
        try_load_opendota_pauses(tuple(match_ids))
    )
    for match_id, pauses in load_archive_pauses(spawnless).items():
        pauses_by_match.setdefault(match_id, pauses)
    return pauses_by_match


def load_anchors() -> dict[str, int]:
    """Pre-horn anchor per condition: the GRID spawn, else horn - 90s - pre-horn pauses."""
    windows = pd.read_parquet(GRID_GAME_WINDOWS_PATH)
    spawn_by_condition: dict[str, int] = {
        str(row.condition_id): require_ts(row.spawn_at, "spawn_at")
        for row in windows.itertuples(index=False)
    }
    links = pd.read_parquet(MATCH_LINKS_PATH)
    pauses_by_match = _fallback_pauses(links, spawn_by_condition)
    anchors: dict[str, int] = {}
    for row in links.itertuples(index=False):
        condition_id = str(row.map_condition_id)
        spawn_unix = spawn_by_condition.get(condition_id)
        if spawn_unix is not None:
            anchors[condition_id] = spawn_unix
            continue
        horn_unix = parse_ts(opt_str(row.archive_horn_at_utc))
        match_id = opt_int(row.match_id)
        pauses = pauses_by_match.get(match_id) if match_id is not None else None
        if horn_unix is None or pauses is None:
            continue
        anchors[condition_id] = get_spawn_unix_from_horn(horn_unix, pauses)
    return anchors


def load_universe_token_pairs(condition_ids: set[str]) -> dict[str, list[str]]:
    frame = pd.read_parquet(UNIVERSE_PATH, columns=["conditionId", "token_id_0", "token_id_1"])
    complete = frame.dropna()
    token_pairs: dict[str, list[str]] = {}
    for condition_id, token_id_0, token_id_1 in zip(
        complete["conditionId"], complete["token_id_0"], complete["token_id_1"], strict=True
    ):
        if condition_id in condition_ids:
            token_pairs[condition_id] = [str(token_id_0), str(token_id_1)]
    return token_pairs


def build_targets(
    anchors: Mapping[str, int], token_pairs: Mapping[str, list[str]]
) -> list[PriceTarget]:
    frame = pd.read_parquet(MATCH_LINKS_PATH)
    links = cast(list[MatchLinkRow], frame.to_dict(orient="records"))
    targets: list[PriceTarget] = []
    for link in links:
        condition_id = link["map_condition_id"]
        anchor_ts = anchors.get(condition_id)
        if anchor_ts is None:
            continue
        outcome_tokens = token_pairs.get(condition_id)
        if outcome_tokens is None:
            raise ValueError(f"{condition_id} has no complete token pair in the universe")
        radiant_index = link["radiant_token_index"]
        targets.append(
            PriceTarget(
                condition_id=condition_id,
                match_id=link["match_id"],
                anchor_ts=anchor_ts,
                radiant_token_id=outcome_tokens[radiant_index],
                dire_token_id=outcome_tokens[1 - radiant_index],
            )
        )
    return targets


def payload_path(condition_id: str, token_id: str) -> Path:
    return RAW_POLYMARKET_DOTA_PRICES_HISTORY_DIR / f"{condition_id}_{token_id}.json.gz"


def read_cached_history(condition_id: str, token_id: str) -> PricesHistoryPayload | None:
    path = payload_path(condition_id, token_id)
    if not path.exists():
        return None
    return cast(PricesHistoryPayload, read_gzip_json(path))


def cache_covers_window(payload: PricesHistoryPayload | None, target: PriceTarget) -> bool:
    if payload is None:
        return False
    return payload["startTs"] <= target.window_start_ts and payload["endTs"] >= target.anchor_ts


def pending_targets(targets: list[PriceTarget]) -> list[PriceTarget]:
    pending: list[PriceTarget] = []
    for target in targets:
        covered = all(
            cache_covers_window(read_cached_history(target.condition_id, token_id), target)
            for token_id in target.radiant_then_dire
        )
        if not covered:
            pending.append(target)
    return pending


def fetch_token_history(
    client: httpx.Client, token_id: str, target: PriceTarget
) -> list[PricePoint]:
    body = get_json(
        client,
        f"{api.POLYMARKET_CLOB_API}/prices-history",
        {
            "market": token_id,
            "fidelity": 1,
            "startTs": target.window_start_ts,
            "endTs": target.anchor_ts,
        },
    )
    return parse_history(body)


def write_payload(token_id: str, target: PriceTarget, history: list[PricePoint]) -> None:
    payload: PricesHistoryPayload = {
        "conditionId": target.condition_id,
        "token_id": token_id,
        "startTs": target.window_start_ts,
        "endTs": target.anchor_ts,
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "history": history,
    }
    path = payload_path(target.condition_id, token_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tmp.replace(path)


def fetch_pending(pending: list[PriceTarget]) -> None:
    total = len(pending)
    with http_client() as client:
        for position, target in enumerate(pending, start=1):
            for token_id in target.radiant_then_dire:
                history = fetch_token_history(client, token_id, target)
                write_payload(token_id, target, history)
                print(
                    f"saved {position}/{total} {target.condition_id} "
                    f"{token_id[-8:]}: {len(history)} points",
                    flush=True,
                )


def collect_quotes(targets: list[PriceTarget]) -> list[PregameQuoteRow]:
    rows: list[PregameQuoteRow] = []
    for target in targets:
        radiant_payload = read_cached_history(target.condition_id, target.radiant_token_id)
        dire_payload = read_cached_history(target.condition_id, target.dire_token_id)
        if radiant_payload is None or dire_payload is None:
            continue
        pair = last_aligned_pre_anchor_pair(
            radiant_payload["history"], dire_payload["history"], target.anchor_ts
        )
        if pair is None:
            continue
        radiant_prior = normalize_pair_mids(
            radiant_mid=pair.left.price, dire_mid=pair.right.price, tolerance=PAIR_SUM_TOLERANCE
        )
        if radiant_prior is None:
            continue
        rows.append(
            {
                "condition_id": target.condition_id,
                "match_id": target.match_id,
                "anchor_ts": target.anchor_ts,
                "radiant_prior": radiant_prior,
                "radiant_price": pair.left.price,
                "dire_price": pair.right.price,
                "radiant_quote_ts": pair.left.quote_ts,
                "dire_quote_ts": pair.right.quote_ts,
            }
        )
    return rows


def main(
    fetch: Annotated[
        bool, typer.Option("--fetch", help="Fetch the quote windows the cache is missing.")
    ] = False,
) -> None:
    anchors = load_anchors()
    token_pairs = load_universe_token_pairs(set(anchors))
    targets = build_targets(anchors, token_pairs)
    print_count("price_targets", len(targets))

    if fetch:
        pending = pending_targets(targets)
        print_count("targets_needing_fetch", len(pending))
        fetch_pending(pending)

    rows = collect_quotes(targets)
    frame = pd.DataFrame(rows, columns=PREGAME_QUOTE_COLUMNS)
    write_parquet(frame, PREGAME_QUOTES_PATH)
    print_count("pregame_quote_rows", len(frame))
    print(f"saved: {PREGAME_QUOTES_PATH}")


if __name__ == "__main__":
    app = typer.Typer()
    app.command(
        help="Publish the pre-game market prior for every consistently quoted admitted map market."
    )(main)
    app()
