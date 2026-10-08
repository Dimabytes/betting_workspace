"""Recapture the follow300 smoke goldens from the current policy, model, and dataset.

The goldens replay seed-0 maps against on-disk inputs that git does not track:
data/new_processed/dataset and the telonex captures. Rebuild either one and the
tapes move, so tests/test_follow300_replay.py fails with no commit to blame.
Run this after a deliberate policy change or a dataset rebuild, and say in the
commit message which one it was.
"""

import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [
    str(REPO / "src"),
    str(REPO.parent / "prediction-market-backtesting"),
]

from backtest.extraction_identity import write_identity_golden  # noqa: E402
from backtest.replay_inputs import (  # noqa: E402
    SMOKE_INPUTS_FILENAME,
    SMOKE_MAPS,
    SmokeMap,
    collect_smoke_inputs,
)
from backtest.seed0_replay import replay_seed0_identity  # noqa: E402
from shared.utils.json_io import write_json  # noqa: E402

SMOKE_DIR = REPO / "tests" / "fixtures" / "follow300_changes" / "smoke"


def capture_map(smoke: SmokeMap) -> Path:
    path = SMOKE_DIR / f"{smoke.game}_{smoke.match_id}_seed0.json"
    with TemporaryDirectory() as report_dir:
        identity = replay_seed0_identity(
            game=smoke.game,
            match_id=smoke.match_id,
            archive_id=smoke.archive_id,
            report_dir=Path(report_dir),
        )
    write_identity_golden(path=path, identity=identity)
    return path


def main() -> int:
    for smoke in SMOKE_MAPS:
        path = capture_map(smoke)
        print(f"wrote {path.relative_to(REPO)}")
    inputs_path = SMOKE_DIR / SMOKE_INPUTS_FILENAME
    write_json(inputs_path, asdict(collect_smoke_inputs()))
    print(f"wrote {inputs_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
