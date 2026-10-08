import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect.common.opendota_candidates import opendota_params
from collect.common.window_ids import load_admitted_match_ids
from shared.constants import api
from shared.types.opendota import OpenDotaMatch, OpenDotaMatchCache
from shared.utils.http import get_json, http_client
from shared.utils.log import print_count
from shared.utils.opendota import opendota_match_cache_path


def write_raw_payload(match_id: int, match: OpenDotaMatch) -> None:
    payload: OpenDotaMatchCache = {
        "match_id": int(match_id),
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "data": match,
    }
    out = opendota_match_cache_path(match_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tmp.replace(out)


def fetch_one(client: httpx.Client, params: dict[str, Any], match_id: int) -> None:
    match: OpenDotaMatch = get_json(client, f"{api.OPENDOTA_API}/matches/{match_id}", params)
    write_raw_payload(match_id, match)


def main(
    budget: Annotated[
        int, typer.Option(help="Max API requests this run (free tier: 2,000/day).")
    ] = 1900,
    sleep: Annotated[
        float,
        typer.Option(
            help="Seconds after each request in sequential mode; ignored with --workers>1."
        ),
    ] = 1.1,
    workers: Annotated[
        int,
        typer.Option(help="Parallel HTTP workers. Paid key (~3000/min): try 40-60 with --sleep 0."),
    ] = 1,
) -> None:
    params = opendota_params()
    match_ids = load_admitted_match_ids()
    to_fetch = [mid for mid in match_ids if not opendota_match_cache_path(mid).exists()][
        : max(0, budget)
    ]
    print(
        f"to_fetch={len(to_fetch)} workers={workers} budget={budget} "
        f"api_key={'yes' if params.get('api_key') else 'no'}",
        flush=True,
    )

    failed = 0
    with http_client() as client:
        if workers <= 1:
            for match_id in to_fetch:
                try:
                    fetch_one(client, params, match_id)
                except Exception as exc:
                    failed += 1
                    print(f"failed OpenDota match {match_id}: {exc}", flush=True)
                time.sleep(sleep)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                match_id_by_future = {
                    pool.submit(fetch_one, client, params, mid): mid for mid in to_fetch
                }
                for fut in as_completed(match_id_by_future):
                    match_id = match_id_by_future[fut]
                    try:
                        fut.result()
                    except Exception as exc:
                        failed += 1
                        print(f"failed OpenDota match {match_id}: {exc}", flush=True)

    print_count("matches_in_corpus", len(match_ids))
    print_count("saved_this_run", len(to_fetch) - failed)
    print_count("failed_this_run", failed)


if __name__ == "__main__":
    app = typer.Typer()
    app.command(help="Fetch the OpenDota parsed match cache for linked dataset matches.")(main)
    app()
