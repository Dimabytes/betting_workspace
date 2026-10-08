"""Print spawn_wall - loading_anchor_ts percentiles from livestats archives."""

from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from typing import cast

from lol.constants import LOL_LINKS_PATH, LOL_PREPARE_WORKERS, LOL_WINDOWS_DIR
from lol.livestats_frames import is_spawn_sides, parse_sides, read_archive_frames
from lol.parquet_io import read_parquet_rows
from lol.types import LolLinkRow


def spawn_delta_seconds(esports_game_id: str, loading_anchor_ts: int) -> float | None:
    """Unbounded first spawn-shaped frame minus loading_anchor_ts, or None."""
    frames = read_archive_frames(LOL_WINDOWS_DIR, esports_game_id)
    if frames is None or not frames:
        return None
    anchor = float(loading_anchor_ts)
    for item in frames:
        parsed = parse_sides(item.payload)
        if isinstance(parsed, int):
            continue
        if is_spawn_sides(parsed):
            return item.wall_seconds - anchor
    return None


def percentile(values: list[float], quantile: float) -> float:
    """Nearest-rank percentile of a non-empty sorted list."""
    return values[int(quantile * (len(values) - 1))]


def main() -> None:
    """Scan every linked archive and print spawn-anchor delta counts."""
    links = cast(list[LolLinkRow], read_parquet_rows(LOL_LINKS_PATH))
    game_ids = [str(link["esports_game_id"]) for link in links]
    anchors = [int(link["loading_anchor_ts"]) for link in links]
    deltas: list[float] = []
    missing = 0
    workers = min(len(game_ids), LOL_PREPARE_WORKERS)
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))
    try:
        for done, delta in enumerate(
            pool.map(spawn_delta_seconds, game_ids, anchors, chunksize=16), start=1
        ):
            if delta is None:
                missing += 1
            else:
                deltas.append(delta)
            if done % 500 == 0 or done == len(game_ids):
                print(f"scanned {done}/{len(game_ids)}", flush=True)
    finally:
        pool.shutdown(wait=True)
    deltas.sort()
    print(f"links={len(links)} with_spawn={len(deltas)} no_spawn_shape={missing}")
    if not deltas:
        return
    print(
        "delta seconds: "
        f"min={deltas[0]:.1f} p50={percentile(deltas, 0.5):.1f} "
        f"p90={percentile(deltas, 0.9):.1f} p95={percentile(deltas, 0.95):.1f} "
        f"p99={percentile(deltas, 0.99):.1f} max={deltas[-1]:.1f}"
    )
    for limit in (0, 90, 300, 900, 1800, 3600):
        if limit == 0:
            n = sum(1 for value in deltas if value < 0)
            print(f"delta<0: {n}")
            continue
        n = sum(1 for value in deltas if value > limit)
        print(f"delta>{limit}: {n}")
    late = [value for value in deltas if value > 90]
    if not late:
        return
    print(
        f"late>90 n={len(late)} min={late[0]:.1f} p50={percentile(late, 0.5):.1f} "
        f"p90={percentile(late, 0.9):.1f} p99={percentile(late, 0.99):.1f} max={late[-1]:.1f}"
    )


if __name__ == "__main__":
    main()
