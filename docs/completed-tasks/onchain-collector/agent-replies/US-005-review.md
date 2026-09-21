NO COMMENTS

# US-005 review — VPS enable and delete the Python on-chain collector

Commit `ecd2a570` in `esports-trader` (16 files, −1052 net). Mac-side cutover only. No structural regression, no file crosses 1k (`sync_collector_parquet.py` 148 → 421), no spaghetti grafted onto book rsync beyond the load-bearing `--exclude=onchain_fills/`, then a separate manifest-driven phase.

Honor constraints hold:

- `poly-maker` is not in this commit.
- Collector schema is untouched; 20-col legacy stays skipped (Phase A evidence: 31,092 excluded).
- `scripts/sync_collector_parquet.py` imports stdlib + `msgspec` + `rsync_pull` only. Local `_sha256_file` is the right copy — `shared.*` would break the viewer spawn.
- VPS `onchain` start is not in the commit; Phase B stays operator-only.

Deletion is the judo: `telonex_onchain.py` collector/planner gone, surviving helpers moved verbatim to `telonex_capture.py`, five consumers swapped one import, Makefile targets/`PHONY` dropped with the scripts. `run_rsync_status` is the one new seam `run_rsync` needed. Onchain policy (ready + date < today, path regex, start-date gate, sha256 then `os.replace`) lives in named functions the tests hit directly. No second downloader left behind.

Approve as implemented.
