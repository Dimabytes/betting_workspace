"""Rsync collector parquet from the VPS into the local Telonex tree.

Books come over a dumb `--ignore-existing` pull. `onchain_fills` follows the
published `manifests/onchain/<date>.json` instead: every remote file is
sha256-verified before it lands, a ready manifest may replace a local legacy
file only at `--onchain-start-date` or later, and anything else that differs
is a reported conflict.

SSH uses host `sun` from ~/.ssh/config and will prompt for the key passphrase.

  uv run python scripts/sync_collector_parquet.py --game dota
  uv run python scripts/sync_collector_parquet.py --game lol --dry-run
  uv run python scripts/sync_collector_parquet.py --game dota --onchain-start-date 2026-09-21
"""

import argparse
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import msgspec
import rsync_pull

REPO_ROOT = Path(__file__).resolve().parents[1]
Game = Literal["dota", "lol"]
GAME_ARCHIVE = {
    "dota": "/var/lib/polymarket-dota-archive",
    "lol": "/var/lib/polymarket-lol-archive",
}
GAME_DEST = {
    "dota": REPO_ROOT / "data" / "raw" / "telonex" / "polymarket",
    "lol": REPO_ROOT / "data" / "lol" / "raw" / "telonex" / "polymarket",
}
CHANNELS = ("book_snapshot_full", "onchain_fills")
ONCHAIN_PATH_RE = re.compile(
    r"^parquet/onchain_fills/asset_id=([^/]+)/(\d{4}-\d{2}-\d{2})\.parquet$"
)


class OnchainFileEntry(msgspec.Struct):
    """One `files[]` record of an on-chain manifest; unknown keys ignored."""

    asset_id: str = msgspec.field(name="assetId")
    path: str
    sha256: str
    bytes: int


class OnchainManifest(msgspec.Struct):
    """The fields this sync reads from `manifests/onchain/<date>.json`."""

    date: str
    status: str
    files: list[OnchainFileEntry]


@dataclass(frozen=True)
class ChannelCoverage:
    """Local parquet coverage for one Telonex/collector channel."""

    channel: str
    assets: int
    files: int
    min_day: str | None
    max_day: str | None


@dataclass(frozen=True)
class GamePaths:
    """Remote archive root and local Telonex dest for one collector game."""

    game: Game
    archive: str
    remote: str
    dest: Path


@dataclass(frozen=True)
class SyncArgs:
    """Parsed CLI for one collector pull."""

    paths: GamePaths
    ssh_host: str
    dry_run: bool
    onchain_start_date: date | None


@dataclass(frozen=True)
class OnchainPlanEntry:
    """One manifest-covered file and the action decided for it."""

    relpath: str
    target: Path
    sha256: str
    action: Literal["install", "kept", "replace", "conflict"]


@dataclass
class OnchainSyncStats:
    """Per-game counters for the manifest-driven onchain phase."""

    manifests: int = 0
    min_day: str | None = None
    max_day: str | None = None
    installed: int = 0
    replaced: int = 0
    kept: int = 0
    conflicts: int = 0
    hash_mismatch: int = 0
    rejected: int = 0

    def note_day(self, day: str) -> None:
        """Grow the ready-day range to include `day`."""
        if self.min_day is None or day < self.min_day:
            self.min_day = day
        if self.max_day is None or day > self.max_day:
            self.max_day = day


def paths_for_game(game: Game) -> GamePaths:
    """Built-in remote/dest for `dota` or `lol`."""
    archive = GAME_ARCHIVE[game]
    return GamePaths(game=game, archive=archive, remote=f"{archive}/parquet", dest=GAME_DEST[game])


def utc_today(now: datetime) -> date:
    """UTC calendar date of `now`."""
    return now.astimezone(UTC).date()


def build_rsync_argv(
    ssh_host: str,
    remote: str,
    dest: Path,
    today_utc: date,
    dry_run: bool,
) -> list[str]:
    """One rsync: collector parquet/ → local Telonex tree, skip existing files."""
    return rsync_pull.rsync_argv(
        ssh_host,
        remote,
        dest,
        [
            "-av",
            "--ignore-existing",
            f"--exclude={today_utc.isoformat()}.parquet",
            "--exclude=trades/",
            "--exclude=onchain_fills/",
        ],
        dry_run,
    )


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def decode_manifest(payload: bytes) -> OnchainManifest:
    """One decoded on-chain manifest; raises msgspec.DecodeError on bad input."""
    return msgspec.json.decode(payload, type=OnchainManifest)


def load_ready_manifests(manifests_dir: Path, today_utc: date) -> tuple[list[OnchainManifest], int]:
    """Ready `<date>.json` manifests strictly older than today + count of files seen."""
    manifests: list[OnchainManifest] = []
    seen = 0
    for path in sorted(manifests_dir.glob("*.json")):
        seen += 1
        try:
            manifest = decode_manifest(path.read_bytes())
            day = date.fromisoformat(manifest.date)
        except (OSError, msgspec.DecodeError, ValueError):
            print(f"warn: undecodable onchain manifest {path.name}; skipped", flush=True)
            continue
        if manifest.status != "ready" or day >= today_utc:
            print(
                f"warn: onchain manifest {path.name} status={manifest.status} "
                f"date={manifest.date}; skipped",
                flush=True,
            )
            continue
        manifests.append(manifest)
    return manifests, seen


def plan_onchain_actions(
    manifest: OnchainManifest,
    dest: Path,
    start_date: date | None,
) -> tuple[list[OnchainPlanEntry], int]:
    """Decide install/kept/replace/conflict per manifest file; malformed → rejected."""
    day = date.fromisoformat(manifest.date)
    entries: list[OnchainPlanEntry] = []
    rejected = 0
    for item in manifest.files:
        match = ONCHAIN_PATH_RE.match(item.path)
        if match is None or match.group(1) != item.asset_id or match.group(2) != manifest.date:
            rejected += 1
            print(
                f"rejected: manifest {manifest.date} path={item.path!r} asset_id={item.asset_id!r}",
                flush=True,
            )
            continue
        target = dest / item.path.removeprefix("parquet/")
        if not target.exists():
            action: Literal["install", "kept", "replace", "conflict"] = "install"
        elif _sha256_file(target) == item.sha256:
            action = "kept"
        elif start_date is not None and day >= start_date:
            action = "replace"
        else:
            action = "conflict"
        entries.append(
            OnchainPlanEntry(relpath=item.path, target=target, sha256=item.sha256, action=action)
        )
    return entries, rejected


def fetchable_relpaths(entries: list[OnchainPlanEntry]) -> list[str]:
    """Archive-relative paths to pull: installs and replaces only."""
    return [e.relpath for e in entries if e.action in ("install", "replace")]


def install_staged_files(
    staged_root: Path, entries: list[OnchainPlanEntry]
) -> tuple[int, int, int]:
    """sha256-verify each staged file, then os.replace into place.

    Returns (installed, replaced, hash_mismatch); a staged file that fails the
    hash check is dropped and the local bytes stay untouched.
    """
    installed = 0
    replaced = 0
    mismatched = 0
    for entry in entries:
        staged = staged_root / entry.relpath
        if not staged.is_file() or _sha256_file(staged) != entry.sha256:
            mismatched += 1
            print(f"hash_mismatch: {entry.relpath}; local file untouched", flush=True)
            continue
        entry.target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, entry.target)
        if entry.action == "replace":
            replaced += 1
        else:
            installed += 1
    return installed, replaced, mismatched


def sync_onchain_fills(
    paths: GamePaths,
    ssh_host: str,
    dest: Path,
    today_utc: date,
    start_date: date | None,
    dry_run: bool,
) -> OnchainSyncStats:
    """Deliver ready onchain_fills days from remote manifests into `dest`."""
    stats = OnchainSyncStats()
    with tempfile.TemporaryDirectory(dir=dest, prefix=".onchain-sync-") as staging:
        staging_root = Path(staging)
        manifests_dir = staging_root / "manifests"
        manifests_dir.mkdir()
        rc = rsync_pull.run_rsync_status(
            rsync_pull.rsync_argv(
                ssh_host,
                f"{paths.archive}/manifests/onchain",
                manifests_dir,
                ["-a"],
                False,
            )
        )
        if rc != 0:
            print(
                "warn: no on-chain manifests on remote; onchain sync skipped",
                flush=True,
            )
            return stats
        manifests, seen = load_ready_manifests(manifests_dir, today_utc)
        stats.manifests = seen
        entries: list[OnchainPlanEntry] = []
        for manifest in manifests:
            stats.note_day(manifest.date)
            planned, rejected = plan_onchain_actions(manifest, dest, start_date)
            stats.rejected += rejected
            for entry in planned:
                if entry.action == "kept":
                    stats.kept += 1
                elif entry.action == "conflict":
                    stats.conflicts += 1
                    print(
                        f"conflict: {entry.relpath} local bytes differ; kept",
                        flush=True,
                    )
                else:
                    entries.append(entry)
        if dry_run:
            stats.installed = sum(1 for e in entries if e.action == "install")
            stats.replaced = sum(1 for e in entries if e.action == "replace")
            return stats
        relpaths = fetchable_relpaths(entries)
        if not relpaths:
            return stats
        list_path = staging_root / "files-from.txt"
        list_path.write_text("".join(f"{relpath}\n" for relpath in relpaths))
        files_root = staging_root / "files"
        files_root.mkdir()
        rc = rsync_pull.run_rsync_status(
            [
                "rsync",
                "-a",
                "--partial",
                f"--files-from={list_path}",
                f"{ssh_host}:{paths.archive}/",
                f"{files_root}/",
            ]
        )
        if rc != 0:
            print("warn: onchain files rsync failed; installing what staged", flush=True)
        stats.installed, stats.replaced, stats.hash_mismatch = install_staged_files(
            files_root, entries
        )
        return stats


def print_onchain_stats(stats: OnchainSyncStats) -> None:
    """One summary line for the onchain phase."""
    ready = f"{stats.min_day}..{stats.max_day}" if stats.min_day is not None else "-"
    print(
        f"onchain: manifests={stats.manifests} ready_days={ready} "
        f"installed={stats.installed} replaced={stats.replaced} kept={stats.kept} "
        f"conflicts={stats.conflicts} hash_mismatch={stats.hash_mismatch} "
        f"rejected={stats.rejected}",
        flush=True,
    )


def scan_channel(root: Path, channel: str) -> ChannelCoverage:
    """Count asset dirs and day files under one channel."""
    channel_dir = root / channel
    if not channel_dir.is_dir():
        return ChannelCoverage(channel=channel, assets=0, files=0, min_day=None, max_day=None)
    assets = 0
    days: list[str] = []
    for asset_dir in channel_dir.iterdir():
        if not asset_dir.is_dir() or not asset_dir.name.startswith("asset_id="):
            continue
        assets += 1
        days.extend(path.stem for path in asset_dir.glob("*.parquet"))
    ordered = sorted(set(days))
    return ChannelCoverage(
        channel=channel,
        assets=assets,
        files=len(days),
        min_day=ordered[0] if ordered else None,
        max_day=ordered[-1] if ordered else None,
    )


def print_coverage(root: Path) -> None:
    """Print local book coverage after the transfer."""
    for channel in CHANNELS:
        coverage = scan_channel(root, channel)
        print(
            f"{coverage.channel}: assets={coverage.assets} files={coverage.files} "
            f"min={coverage.min_day} max={coverage.max_day}",
            flush=True,
        )


def parse_argv(argv: list[str]) -> SyncArgs:
    """CLI: required `--game`, optional `--ssh-host` / `--dry-run` / `--onchain-start-date`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", choices=("dota", "lol"), required=True)
    parser.add_argument("--ssh-host", default=rsync_pull.DEFAULT_SSH_HOST)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--onchain-start-date",
        type=date.fromisoformat,
        default=None,
        help="YYYY-MM-DD; ready manifests at/after this date may replace local files",
    )
    parsed = parser.parse_args(argv)
    return SyncArgs(
        paths=paths_for_game(parsed.game),
        ssh_host=parsed.ssh_host,
        dry_run=parsed.dry_run,
        onchain_start_date=parsed.onchain_start_date,
    )


def main(argv: list[str]) -> None:
    """Rsync collector parquet into the Telonex tree, then print coverage."""
    args = parse_argv(argv)
    dest = rsync_pull.prepared_dest(args.paths.dest)
    print(f"game={args.paths.game} remote={args.paths.remote} dest={dest}", flush=True)
    today = utc_today(datetime.now(tz=UTC))
    rsync_pull.run_rsync(
        build_rsync_argv(args.ssh_host, args.paths.remote, dest, today, args.dry_run)
    )
    stats = sync_onchain_fills(
        args.paths,
        args.ssh_host,
        dest,
        today,
        args.onchain_start_date,
        args.dry_run,
    )
    print_onchain_stats(stats)
    print_coverage(dest)


if __name__ == "__main__":
    main(sys.argv[1:])
