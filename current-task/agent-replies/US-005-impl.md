# US-005 impl — VPS enable and delete the Python on-chain collector

Status: Mac-side done, Phase A (agent rsync) done. Phase B is operator-only (VPS docker / .env / SSH password).

Commit: esports-trader `ecd2a570` — `feat: [US-005] - VPS enable and delete the Python on-chain collector` (16 files, all pre-commit hooks green: ruff, ruff-format, basedpyright).

## What changed (esports-trader)

- `src/shared/utils/telonex_onchain.py` split: surviving helpers moved verbatim to new `src/shared/utils/telonex_capture.py` (`asset_channel_dir`, `FatalDownloadError`, `ValidationResult`, `validate_target`, `write_arrow_table`, `download_url`, `retry_delay`). `dest_parquet`, `ONCHAIN_*`, planner/`SyncStats` machinery deleted with the module.
- Import swaps (one line/block each): `src/lol/02_fetch_telonex_books.py`, `src/lol/replay.py`, `src/lol/map_build_cache.py`, `src/backtest/telonex_local.py` (only the line-35 import hunk staged — parallel feature's other hunks untouched), `src/shared/utils/telonex_tape.py`.
- Deleted: `scripts/parity_onchain_rpc.py`, `scripts/sync_onchain_fills.py`, `tests/test_parity_onchain_rpc.py`, `tests/test_sync_onchain_fills.py`, `src/shared/utils/telonex_onchain.py`; Makefile targets `sync-onchain` + `parity-onchain-rpc` incl. `.PHONY`/help lines.
- `scripts/rsync_pull.py`: new `run_rsync_status(argv) -> int`; `run_rsync` delegates (identical `SystemExit(rc)` behavior).
- `scripts/sync_collector_parquet.py` rewritten: books keep `--ignore-existing` plus new `--exclude=onchain_fills/`; `onchain_fills` is now manifest-driven — pull `manifests/onchain/*.json` into a `TemporaryDirectory(dir=dest)` via `run_rsync_status` (nonzero rc → warn + skip, pre-enable safe), msgspec-decode (`OnchainManifest{date,status,files[]}`, `assetId` renamed, unknown keys ignored), keep `status=="ready"` and `date < today_utc`; per file `path` must match `^parquet/onchain_fills/asset_id=([^/]+)/(\d{4}-\d{2}-\d{2})\.parquet$` with token==`asset_id` and stem==manifest date else `rejected`; missing → `install`, same sha256 → `kept`, differs → `replace` only when `date >= --onchain-start-date` else `conflict` (loud line + count); install+replace pulled via `--files-from` rsync, each staged file sha256-re-verified then `os.replace` (same filesystem, atomic), mismatch → `hash_mismatch`, local bytes kept. New CLI `--onchain-start-date YYYY-MM-DD` (without it nothing is ever replaced). Per-game line `onchain: manifests=N ready_days=a..b installed=X replaced=Y kept=Z conflicts=C hash_mismatch=H rejected=R`; `CHANNELS` now `("book_snapshot_full","onchain_fills")`. No `shared.*` imports (viewer spawns it bare).
- `docs/as-is.md`: pipeline line now says `onchain_fills` arrives via the same script from ready manifests.
- `tests/test_sync_collector_parquet.py`: 13 new/updated cases — manifest decode/filters (non-ready, today, undecodable), full action table (install/kept/replace/conflict), 4-way path rejection, files-from contents, staged install + mismatch, `--onchain-start-date` parse/bad-date `SystemExit`.

## Verification run

```
pytest tests/test_sync_collector_parquet.py tests/test_lol_fetch_telonex_books.py \
  tests/test_telonex_tape.py tests/test_lol_map_build_cache.py  → 45 passed
import smoke backtest.telonex_local / lol.replay / telonex_tape / telonex_capture → ok
pre-commit (ruff + ruff-format + basedpyright) → green on the commit tree
```

Deviation worth knowing: whole-project basedpyright (`pass_filenames:false`) failed on the parallel feature's untracked `feed_schedules`/`test_feed_schedules.py` in the main worktree, so the commit was made from a detached `git worktree` (HEAD + staged diff, real hooks ran green on the exact commit tree), then `git update-ref refs/heads/main ecd2a570`. No parallel files touched; their hunks remain unstaged.

## Phase A evidence (done from this Mac, `ssh sun` BatchMode as root)

- `yarn install && yarn build` in polymarket-collector @ `0a4b00b`; `dist/onchain-import-main.js` current.
- Inventory: dota **19,318** files / **12,060** skipped; lol **65,302** / **19,032** skipped → **84,620** verified 26-column files shipped, **31,092** 20-column legacy files excluded — plan §2 decision **(a) documented exclusion**; they stay Mac-only.
- rsync `--files-from` → `sun:/var/lib/polymarket-onchain-state/import-staging/{dota,lol}`: remote counts 19,318 / 65,302 parquet, exact match.
- Inventories → `sun:/var/lib/polymarket-onchain-state/import/`; `chown -R 10001:10001 /var/lib/polymarket-onchain-state` done (import runs as the image `USER 10001`).

## Phase B — operator runbook (on VPS `sun`, collector checkout)

```sh
git pull
docker compose build archive        # refreshes polymarket-collector:latest; runs yarn check+build inside

# Edit .env — copy VALUES BY HAND from esports-trader/.env on the Mac (never commit/echo):
#   ONCHAIN_RPC_URL_A = <ALCHEMY_POL_ENDPOINT>
#   ONCHAIN_RPC_URL_B = <ALCHEMY_POL_ENDPOINT_RESERVE>
# Leave ONCHAIN_START_DATE unset until the report.

# 1) Dry-run first (no copies, no lock):
docker compose run --rm --no-deps onchain \
  node dist/onchain-import-main.js accept \
  --inventory /onchain-state/import/dota-inventory.json \
  --inventory /onchain-state/import/lol-inventory.json \
  --staging-root /onchain-state/import-staging --dry-run
sudo jq '{onchainStartDate, startDateRule, games, gaps: (.gaps|length)}' \
  /var/lib/polymarket-onchain-state/import/bootstrap-report.json

# 2) Real import. Choose ONCHAIN_START_DATE = report's onchainStartDate (default:
#    run date, rule "run_date"), OR pass --start-date <yesterday-UTC> so the first
#    pass publishes a real completed day this session (free-tier ~10-block
#    getLogs cap → a full day ≈ tens of minutes; "today" means first publish
#    lands just after tomorrow 00:05 UTC).
docker compose run --rm --no-deps onchain \
  node dist/onchain-import-main.js accept \
  --inventory /onchain-state/import/dota-inventory.json \
  --inventory /onchain-state/import/lol-inventory.json \
  --staging-root /onchain-state/import-staging [--start-date YYYY-MM-DD]

# 3) Set ONCHAIN_START_DATE=<chosen date> in .env, then:
docker compose up -d onchain        # creates ONLY this service; never `down`
docker compose logs -f onchain      # pass report JSON every 5 min
```

Import runs as UID 10001 via the image `USER`; `accept` holds `.onchain.lock` on the state dir — it must finish before `up -d onchain` or the second writer LockErrors.

## Post-publish verification

- Operator: `sudo ls /var/lib/polymarket-{dota,lol}-archive/manifests/onchain/` → `<date>.json` for a completed UTC day; `sudo jq '{status, files:(.files|length), covered:(.coverage.tokensCovered|length)}' <file>` → `ready`.
- Operator: pass log shows the day `published` with `committedChunks == totalChunks`; Telegram `onchain pass` fires once.
- Agent (Mac): `uv run python scripts/sync_collector_parquet.py --game dota --onchain-start-date <date>` → `installed > 0` for the published day, zero `hash_mismatch`, `conflicts` only for legitimately-diverging legacy bytes; coverage line shows `onchain_fills`.
- Rollback: archives and imported files stay; `docker compose stop onchain` — the Python downloader is not resurrected.
