"""Compare live market_radiant_prior (session.jsonl, Polymarket minute bars)
against the training strict prior (Telonex book mid in [spawn-61s, spawn-1s])."""

import gzip
import json
import sys
from pathlib import Path

import pandas as pd

from lol.constants import LOL_DETAILS_DIR, LOL_WINDOWS_DIR
from lol.livestats_frames import prepare_map_livestats_until
from lol.networth import DEFAULT_ITEM_CATALOG_DIR, load_item_catalog
from lol.types import LolLinkRow
from shared.constants.lol import LOL_RAW_TELONEX_DIR
from shared.utils.telonex_book import load_token_book
from importlib import import_module
prepare_mod = import_module("lol.05_prepare_dataset")

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"
LINKS = E / "data/lol/processed/lolesports_links/links.parquet"


def session_priors(dirpath: Path):
    priors = set()
    first_signals = []
    path = dirpath / "session.jsonl"
    if not path.exists():
        return priors, first_signals
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row.get("kind") != "signal":
            continue
        priors.add(row.get("market_radiant_prior"))
        if len(first_signals) < 3:
            first_signals.append((row.get("second"), row.get("market_radiant_prior"), row.get("market_p_radiant"), row.get("reason")))
    return priors, first_signals


def main() -> None:
    links = pd.read_parquet(LINKS)
    catalog = load_item_catalog(DEFAULT_ITEM_CATALOG_DIR)
    tapes = sys.argv[1:] or [
        "grid-3000375-m1", "grid-3000375-m2", "grid-3000375-m3",
        "grid-3000375-m4", "grid-3000375-m5",
        "grid-3000372-m1", "grid-3000372-m2", "grid-3000372-m3", "grid-3000372-m4",
    ]
    for tape in tapes:
        d = TRADER / tape
        meta = json.loads((d / "match.json").read_text())
        hit = links[links["condition_id"] == meta["market"]["condition_id"]]
        if hit.empty:
            print(f"{tape}: no link")
            continue
        link: LolLinkRow = hit.iloc[0].to_dict()
        livestats = prepare_mod  # silence
        from lol.livestats_frames import prepare_map_livestats_until as prep
        res = prep(link, LOL_WINDOWS_DIR, LOL_DETAILS_DIR, catalog, 600)
        priors, first = session_priors(d)
        if res.__class__.__name__ == "LivestatsDrop":
            print(f"{tape}: drop {res.reason}; live priors={priors}")
            continue
        tokens = prepare_mod.parse_token_pair(link)
        window_end = res.spawn_us + 700_000_000
        rb = load_token_book(token_id=tokens.radiant, start_us=res.spawn_us - 120_000_000, end_us=window_end, telonex_root=LOL_RAW_TELONEX_DIR)
        db = load_token_book(token_id=tokens.dire, start_us=res.spawn_us - 120_000_000, end_us=window_end, telonex_root=LOL_RAW_TELONEX_DIR)
        train_prior = None
        if rb and db:
            train_prior = prepare_mod.lookup_strict_prior(rb, db, res.spawn_us)
        print(
            f"{tape}: train_prior={train_prior} live_prior={priors} "
            f"spawn_us={res.spawn_us} first_signals={first[:2]}"
        )


if __name__ == "__main__":
    main()
