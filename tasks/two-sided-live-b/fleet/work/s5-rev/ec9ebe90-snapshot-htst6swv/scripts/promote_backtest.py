"""Point data/backtests/<game>_maker/LIVE at a finished 12-seed validation run.

LIVE is the promotion baseline, so it defaults to a full 12-seed catalog;
experiments may stop at 3 seeds x 4 shards (scripts/run_seeds.sh defaults) —
pass --seeds 3 to promote one anyway. --noxp points LIVE_NOXP instead: the
1-seed no-XP Dota model baseline.

uv run python scripts/promote_backtest.py data/backtests/dota_maker/validation_join_..._name
uv run python scripts/promote_backtest.py data/backtests/dota_maker/validation_join_..._noxp --noxp
make promote-backtest RUN=data/backtests/lol_maker/validation_join_..._name ARGS="--seeds 3"
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from report_seeds import validate_catalog

MAKER_PARENTS = frozenset({"dota_maker", "lol_maker"})


@dataclass(frozen=True)
class PromotionTarget:
    link_name: str
    seeds: int


LIVE = PromotionTarget("LIVE", 12)
LIVE_NOXP = PromotionTarget("LIVE_NOXP", 1)


def promote(run_dir: Path, target: PromotionTarget) -> Path:
    """Replace parent/<link_name> with a relative symlink to a complete catalog."""
    run_dir = run_dir.resolve()
    if run_dir.name == target.link_name:
        raise SystemExit(f"cannot promote {target.link_name} to itself")
    try:
        validate_catalog(run_dir, range(target.seeds))
    except ValueError as error:
        raise SystemExit(f"refusing to promote {run_dir}: {error}") from error
    parent = run_dir.parent
    if parent.name not in MAKER_PARENTS:
        raise SystemExit(f"run must live under dota_maker or lol_maker, got {parent}")
    live = parent / target.link_name
    if live.is_symlink() or live.exists():
        live.unlink()
    live.symlink_to(run_dir.name)
    return live


def main(argv: list[str]) -> None:
    """Promote the run_dir argument and print <link> -> target."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--noxp", action="store_true", help="promote to LIVE_NOXP (1-seed no-XP baseline)"
    )
    parser.add_argument(
        "--seeds",
        type=int,
        help="expected seed count in the catalog (default: 12 for LIVE, 1 for LIVE_NOXP)",
    )
    args = parser.parse_args(argv[1:])
    target = LIVE_NOXP if args.noxp else LIVE
    if args.seeds is not None:
        target = PromotionTarget(target.link_name, args.seeds)
    live = promote(args.run_dir, target)
    print(f"{live} -> {live.readlink()}")


if __name__ == "__main__":
    main(sys.argv)
