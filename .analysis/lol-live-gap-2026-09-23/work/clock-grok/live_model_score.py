"""Rebuild live LoL feature rows from GRID archives and rescore catalogs.

Replay uses trader.grid_feed.replay_grid_records + GridFrameReducer + the LoL
profile. market_p and the live prior come from the aligned session.jsonl signal.
The training prior is lookup_strict_prior on the book in [horn-61s, horn).
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from pathlib import Path

import numpy as np

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace"
    "/.analysis/lol-live-gap-2026-09-23"
)
OUT = R / "work" / "clock-grok" / "live_model_score.json"
TRADER = E / "data" / "trader"
MODELS = E / "data" / "lol" / "models"

# Catalogs scored on the same rows. 0919 is the first retrain after 332e1c17;
# 0506 sits between that retrain and the current 0921 catalogs.
SCORE_CATALOGS: list[tuple[str, Path]] = [
    ("pre332_research_20260915T210420Z", MODELS / "archive/research/20260915T210420Z"),
    ("pre332_production_20260915T210431Z", MODELS / "archive/production/20260915T210431Z"),
    ("post332_research_20260919T112924Z", MODELS / "archive/research/20260919T112924Z"),
    ("post332_production_20260919T112946Z", MODELS / "archive/production/20260919T112946Z"),
    ("mid_research_20260921T050646Z", MODELS / "archive/research/20260921T050646Z"),
    ("mid_production_20260921T050658Z", MODELS / "archive/production/20260921T050658Z"),
    ("current_research_20260921T095801Z", MODELS / "research"),
    ("current_production_20260921T095813Z", MODELS / "production"),
]

POST332_NAME = "20260919T112924Z"
SEC_LO = 0
SEC_HI = 480
WALL_GAP_S = 15.0
GAME_GAP_S = 10
REPRO_ABS = 1e-4
WORKERS = 6


def _single_booster(booster):
    """One pre-ensemble model.txt. The booster is bound on the method, not the loop."""

    class _Single:
        feature_names = ()

        def predict_one_thread(self, data, _booster=booster):
            return np.asarray(_booster.predict(data, num_threads=1), dtype=np.float64)

    predictor = _Single()
    predictor.feature_names = _booster_features(booster)
    return predictor


def _booster_features(booster):
    return tuple(booster.feature_name())


def _index_catalogs() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in MODELS.rglob("model.json"):
        name = json.loads(path.read_text())["name"]
        found.setdefault(name, str(path.parent))
    return found


def _find_window(hay: np.ndarray, needle: np.ndarray) -> int | None:
    n = int(needle.shape[0])
    m = int(hay.shape[0])
    if n == 0 or n > m:
        return None
    candidates = np.flatnonzero(hay[: m - n + 1] == needle[0])
    for start in candidates.tolist():
        if np.array_equal(hay[start : start + n], needle):
            return int(start)
    return None


def _greedy_pairs(ev: np.ndarray, sg: np.ndarray) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    ei = 0
    n = int(ev.shape[0])
    for si, sec in enumerate(sg.tolist()):
        while ei < n and int(ev[ei]) < sec:
            ei += 1
        if ei < n and int(ev[ei]) == sec:
            pairs.append((ei, si))
            ei += 1
    return pairs


def _train_prior(radiant: str, dire: str, spawn_us: int) -> float | None:
    prep = importlib.import_module("lol.05_prepare_dataset")
    from shared.constants.lol import LOL_RAW_TELONEX_DIR
    from shared.utils.telonex_book import US_PER_SECOND, load_token_book

    window = prep.LOL_PRIOR_WINDOW_SECONDS * US_PER_SECOND
    books = []
    for token in (radiant, dire):
        book = load_token_book(
            token_id=token,
            start_us=spawn_us - window,
            end_us=spawn_us,
            telonex_root=LOL_RAW_TELONEX_DIR,
        )
        if book is None:
            return None
        books.append(book)
    prior = prep.lookup_strict_prior(books[0], books[1], spawn_us)
    return None if prior is None else float(prior)


def extract_map(archive_dir: str) -> dict:
    """Replay one trader dir. Returns arrays plus alignment metadata, or an error."""
    from shared.utils.match_time import parse_utc
    from trader.game_profile import GAME_PROFILES
    from trader.grid_archive import iter_grid_archive_records
    from trader.grid_feed import GridFrameReducer, GridOrientationError, replay_grid_records
    from trader.live_feed import MatchPhase
    from trader.paths import GRID_STATE_ARCHIVE_FILENAME

    path = Path(archive_dir)
    try:
        meta = json.loads((path / "match.json").read_text())
        if meta.get("game") != "lol":
            return {"skip": "not_lol"}
        session_path = path / "session.jsonl"
        if not session_path.is_file():
            return {"skip": "no_session"}
        mode = None
        model_name = None
        signals: list[dict] = []
        for line in session_path.open():
            row = json.loads(line)
            kind = row.get("kind")
            if kind == "session_start":
                mode = row.get("execution_mode")
                model_name = (row.get("model") or {}).get("name")
            elif kind == "signal":
                signals.append(row)
        if mode not in ("live", "paper"):
            return {"skip": f"mode_{mode}"}
        market = meta["market"]
        reducer = GridFrameReducer(
            int(meta["map_number"]),
            market["outcome_0_name"],
            market["outcome_1_name"],
            GAME_PROFILES["lol"],
        )
        events = list(
            replay_grid_records(
                iter_grid_archive_records(path / GRID_STATE_ARCHIVE_FILENAME),
                reducer,
            )
        )
        if not events or not signals:
            return {"skip": "empty"}
        ev_sec = np.asarray([event.snapshot.second for event in events], dtype=np.int32)
        sg_sec = np.asarray([int(row["second"]) for row in signals], dtype=np.int32)
        start = _find_window(ev_sec, sg_sec)
        if start is not None:
            pairs = [(start + i, i) for i in range(len(signals))]
            align = "exact_signal_window"
        else:
            start = _find_window(sg_sec, ev_sec)
            if start is not None:
                pairs = [(i, start + i) for i in range(len(events))]
                align = "exact_event_window"
            else:
                pairs = _greedy_pairs(ev_sec, sg_sec)
                align = "greedy"
        if not pairs:
            return {"skip": "no_align", "n_events": len(events), "n_signals": len(signals)}

        game = np.empty((len(pairs), 10), dtype=np.float64)
        market_p = np.empty(len(pairs), dtype=np.float64)
        live_prior = np.empty(len(pairs), dtype=np.float64)
        session_delta = np.empty(len(pairs), dtype=np.float64)
        received = np.empty(len(pairs), dtype=np.float64)
        second = np.empty(len(pairs), dtype=np.int32)
        paused = np.empty(len(pairs), dtype=np.bool_)
        in_progress = np.empty(len(pairs), dtype=np.bool_)
        is_model = np.empty(len(pairs), dtype=np.bool_)
        for i, (ei, si) in enumerate(pairs):
            snap = events[ei].snapshot
            top = snap.top
            sig = signals[si]
            game[i] = (
                snap.second,
                snap.radiant_nw_adv,
                snap.radiant_nw,
                snap.dire_nw,
                snap.radiant_xp_adv,
                snap.deaths_radiant,
                snap.deaths_dire,
                top.top1_nw_adv,
                top.radiant_top1_nw_ratio,
                top.dire_top1_nw_ratio,
            )
            mp = sig.get("market_p_radiant")
            pr = sig.get("market_radiant_prior")
            fair = sig.get("radiant_fair")
            market_p[i] = np.nan if mp is None else float(mp)
            live_prior[i] = np.nan if pr is None else float(pr)
            if fair is None or mp is None:
                session_delta[i] = np.nan
            else:
                session_delta[i] = float(fair) - float(mp)
            received[i] = parse_utc(events[ei].received_at_utc).timestamp()
            second[i] = int(snap.second)
            paused[i] = bool(snap.paused)
            in_progress[i] = snap.phase is MatchPhase.IN_PROGRESS
            is_model[i] = sig.get("reason") == "model" and fair is not None and mp is not None

        horn = meta.get("horn_at_utc")
        spawn_us = None
        train_prior = None
        prior_error = None
        if horn:
            spawn_us = round(parse_utc(horn).timestamp() * 1_000_000)
            yes = market["yes_token_id"]
            no = market["no_token_id"]
            if market["yes_is_radiant"]:
                radiant, dire = yes, no
            else:
                radiant, dire = no, yes
            try:
                train_prior = _train_prior(radiant, dire, spawn_us)
            except Exception as exc:  # noqa: BLE001 — one map must not kill the pool
                prior_error = f"{type(exc).__name__}: {exc}"
        return {
            "id": path.name,
            "mode": mode,
            "model": model_name,
            "horn": horn,
            "spawn_us": spawn_us,
            "train_prior": train_prior,
            "prior_error": prior_error,
            "align": align,
            "n_events": len(events),
            "n_signals": len(signals),
            "n_pairs": len(pairs),
            "game": game,
            "market_p": market_p,
            "live_prior": live_prior,
            "session_delta": session_delta,
            "received": received,
            "second": second,
            "paused": paused,
            "in_progress": in_progress,
            "is_model": is_model,
        }
    except GridOrientationError as exc:
        return {"id": path.name, "error": f"orientation: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"id": path.name, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}


def _future_wall(received: np.ndarray, mids: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """First finite mid at or after received+300s, within WALL_GAP_S. NaN if none."""
    out = np.full(received.shape[0], np.nan)
    finite = np.flatnonzero(np.isfinite(mids) & ok)
    if finite.size == 0:
        return out
    times = received[finite]
    values = mids[finite]
    targets = received + 300.0
    idx = np.searchsorted(times, targets, side="left")
    for i, j in enumerate(idx.tolist()):
        if j >= times.shape[0]:
            continue
        if times[j] - targets[i] <= WALL_GAP_S:
            out[i] = values[j] - mids[i]
    return out


def _future_game(second: np.ndarray, mids: np.ndarray, ok: np.ndarray) -> np.ndarray:
    out = np.full(second.shape[0], np.nan)
    finite = np.flatnonzero(np.isfinite(mids) & ok)
    if finite.size == 0:
        return out
    secs = second[finite]
    values = mids[finite]
    order = np.argsort(secs, kind="mergesort")
    secs = secs[order]
    values = values[order]
    idx = np.searchsorted(secs, second + 300, side="left")
    for i, j in enumerate(idx.tolist()):
        if j >= secs.shape[0]:
            continue
        if int(secs[j]) - (int(second[i]) + 300) <= GAME_GAP_S:
            out[i] = values[j] - mids[i]
    return out


def _metrics(pred: np.ndarray, realized: np.ndarray) -> dict:
    corr = float("nan")
    if pred.size >= 2 and np.std(pred) > 0 and np.std(realized) > 0:
        corr = float(np.corrcoef(pred, realized)[0, 1])
    gate2 = np.abs(pred) >= 0.02
    gate3 = np.abs(pred) >= 0.03

    def edge(mask: np.ndarray) -> tuple[float, int]:
        n = int(mask.sum())
        if n == 0:
            return float("nan"), 0
        return float(np.mean(np.sign(pred[mask]) * realized[mask])), n

    e2, n2 = edge(gate2)
    e3, n3 = edge(gate3)
    return {
        "n": int(pred.size),
        "corr": corr,
        "edge_2c": e2,
        "n_2c": n2,
        "edge_3c": e3,
        "n_3c": n3,
        "share_2c": float(gate2.mean()) if pred.size else float("nan"),
    }


def _score_block(
    rows: list[dict],
    predictors: dict,
    prior_key: str,
    future_key: str,
    proved_only: bool,
) -> dict:
    """Stack every map's eligible ticks and score each catalog on that same stack."""
    chunks: list[tuple[str, np.ndarray, np.ndarray]] = []
    maps = 0
    for row in rows:
        if proved_only and not row["proved"]:
            continue
        train_prior = row["train_prior"]
        if prior_key == "train_prior" and (train_prior is None or not np.isfinite(train_prior)):
            continue
        mask = (
            row["in_progress"]
            & ~row["paused"]
            & (row["second"] >= SEC_LO)
            & (row["second"] <= SEC_HI)
            & np.isfinite(row["market_p"])
            & np.isfinite(row[future_key])
        )
        if prior_key == "live_prior_col":
            mask = mask & np.isfinite(row["live_prior"])
        if proved_only:
            mask = mask & row["repro_ok"]
        if not mask.any():
            continue
        x = np.column_stack(
            [
                row["game"][mask],
                np.full(int(mask.sum()), train_prior if prior_key == "train_prior" else np.nan),
                row["market_p"][mask],
            ]
        )
        if prior_key == "live_prior_col":
            x[:, 10] = row["live_prior"][mask]
        chunks.append((row["id"], x, row[future_key][mask]))
        maps += 1
    if not chunks:
        return {"n_maps": 0, "n_rows": 0, "models": {}}
    realized = np.concatenate([item[2] for item in chunks])
    features = np.concatenate([item[1] for item in chunks])
    models = {}
    for name, predictor in predictors.items():
        raw = predictor.predict_one_thread(features)
        models[name] = _metrics(raw, realized)
    return {"n_maps": maps, "n_rows": int(realized.size), "models": models}


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    dirs = []
    for path in sorted(TRADER.iterdir()):
        match = path / "match.json"
        if not match.is_file():
            continue
        if json.loads(match.read_text()).get("game") != "lol":
            continue
        dirs.append(path)
    if limit is not None:
        dirs = dirs[:limit]
    catalog_by_name = _index_catalogs()
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    print(f"maps_queued {len(dirs)}", flush=True)
    extracted: list[dict] = []
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=get_context("spawn")) as pool:
        for i, result in enumerate(pool.map(extract_map, [str(p) for p in dirs], chunksize=1)):
            extracted.append(result)
            if (i + 1) % 25 == 0 or i + 1 == len(dirs):
                print(f"extracted {i + 1}/{len(dirs)}", flush=True)

    import lightgbm as lgb

    from shared.utils.gbm import FEATURE_COLUMNS, ModelCatalogError, load_predictor

    rows = [row for row in extracted if "game" in row]
    predictors_by_label: dict[str, object] = {}
    for label, path in SCORE_CATALOGS:
        predictors_by_label[label] = load_predictor(path)
        features = list(predictors_by_label[label].feature_names)
        if features != list(FEATURE_COLUMNS):
            raise SystemExit(f"{label} features {features}")
    predictors_by_name: dict[str, object] = {}
    for row in rows:
        name = row.get("model")
        if not name or name in predictors_by_name:
            continue
        path = catalog_by_name.get(name)
        if path is None:
            row["model_missing"] = True
            continue
        model_dir = Path(path)
        try:
            predictor = load_predictor(model_dir)
        except ModelCatalogError:
            txt = model_dir / "model.txt"
            meta = json.loads((model_dir / "model.json").read_text())
            if not txt.is_file() or list(meta["features"]) != list(FEATURE_COLUMNS):
                row["model_missing"] = True
                continue
            booster = lgb.Booster(model_file=str(txt))
            if list(booster.feature_name()) != list(FEATURE_COLUMNS):
                row["model_missing"] = True
                continue
            # Default-arg bind: the loop variable `booster` would otherwise
            # be the last single-file catalog for every older model.
            predictor = _single_booster(booster)
        if list(predictor.feature_names) != list(FEATURE_COLUMNS):
            row["model_missing"] = True
            continue
        predictors_by_name[name] = predictor

    repro_rows = []
    for row in rows:
        predictor = predictors_by_name.get(row.get("model"))
        n = int(row["game"].shape[0])
        err = np.full(n, np.nan)
        repro_ok = np.zeros(n, dtype=np.bool_)
        summary_clip = 0
        if predictor is not None:
            mask = row["is_model"] & np.isfinite(row["live_prior"]) & np.isfinite(row["market_p"])
            if mask.any():
                x = np.column_stack(
                    [row["game"][mask], row["live_prior"][mask], row["market_p"][mask]]
                )
                raw = predictor.predict_one_thread(x)
                fair = np.clip(row["market_p"][mask] + raw, 0.0, 1.0)
                delta = fair - row["market_p"][mask]
                err_m = delta - row["session_delta"][mask]
                summary_clip = int(np.sum(np.abs(raw - row["session_delta"][mask]) > 1e-4))
                err[mask] = err_m
                repro_ok[mask] = np.abs(err_m) <= REPRO_ABS
        finite = err[np.isfinite(err)]
        proved = False
        summary = {"n_model": int(finite.size)}
        if finite.size:
            abs_err = np.abs(finite)
            summary.update(
                {
                    "median_abs": float(np.median(abs_err)),
                    "p95_abs": float(np.quantile(abs_err, 0.95)),
                    "max_abs": float(abs_err.max()),
                    "frac_1e4": float(np.mean(abs_err <= 1e-4)),
                    "frac_1e3": float(np.mean(abs_err <= 1e-3)),
                    "n_clipped": summary_clip,
                }
            )
            # Median exact, and at least 90% of model rows within 1e-4.
            # Scoring still keeps only the per-row matches, so a few greedy
            # mis-pairs do not drop the rest of the map.
            proved = finite.size >= 20 and summary["median_abs"] <= 1e-4 and summary["frac_1e4"] >= 0.90
        row["repro_ok"] = repro_ok
        row["proved"] = proved
        row["repro"] = summary
        tape_ok = row["in_progress"] & np.isfinite(row["market_p"])
        row["wall_move"] = _future_wall(row["received"], row["market_p"], tape_ok)
        row["game_move"] = _future_game(row["second"], row["market_p"], tape_ok)
        row["live_prior_col"] = row["live_prior"]  # per tick
        repro_rows.append(
            {
                "id": row["id"],
                "mode": row["mode"],
                "model": row["model"],
                "align": row["align"],
                "n_events": row["n_events"],
                "n_signals": row["n_signals"],
                "n_pairs": row["n_pairs"],
                "proved": proved,
                "train_prior": row["train_prior"],
                "prior_error": row["prior_error"],
                "horn": row["horn"],
                **summary,
            }
        )

    def era(name: str | None) -> str:
        if not name:
            return "unknown"
        return "post332" if name >= POST332_NAME else "pre332"

    proved_rows = [row for row in rows if row["proved"]]
    pre_rows = [row for row in proved_rows if era(row.get("model")) == "pre332"]
    post_rows = [row for row in proved_rows if era(row.get("model")) == "post332"]

    blocks = {}
    for label, subset in (
        ("all_proved", proved_rows),
        ("session_pre332", pre_rows),
        ("session_post332", post_rows),
    ):
        for prior_key, prior_label in (("live_prior_col", "live_prior"), ("train_prior", "train_prior")):
            for future_key, future_label in (("wall_move", "wall300"), ("game_move", "game300")):
                key = f"{label}__{prior_label}__{future_label}"
                blocks[key] = _score_block(
                    subset, predictors_by_label, prior_key, future_key, proved_only=True
                )

    skips: dict[str, int] = {}
    errors = []
    for result in extracted:
        if "skip" in result:
            skips[result["skip"]] = skips.get(result["skip"], 0) + 1
        elif "error" in result:
            errors.append({"id": result.get("id"), "error": result["error"]})

    proved_n = sum(1 for row in repro_rows if row["proved"])
    align_counts: dict[str, int] = {}
    for row in repro_rows:
        align_counts[row["align"]] = align_counts.get(row["align"], 0) + 1
    prior_ok = sum(1 for row in repro_rows if row["proved"] and row["train_prior"] is not None)

    payload = {
        "queued": len(dirs),
        "with_rows": len(rows),
        "proved_maps": proved_n,
        "train_prior_on_proved": prior_ok,
        "skips": skips,
        "n_errors": len(errors),
        "errors_head": errors[:15],
        "align": align_counts,
        "repro_abs": REPRO_ABS,
        "sec": [SEC_LO, SEC_HI],
        "wall_gap_s": WALL_GAP_S,
        "game_gap_s": GAME_GAP_S,
        "blocks": blocks,
        "maps": repro_rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, default=str))
    print(
        f"wrote {OUT} rows {len(rows)} proved {proved_n} errors {len(errors)} prior {prior_ok}",
        flush=True,
    )
    # Compact stdout so the run log has the headline without opening the json.
    for key in (
        "all_proved__live_prior__wall300",
        "all_proved__live_prior__game300",
        "all_proved__train_prior__wall300",
        "all_proved__train_prior__game300",
    ):
        block = blocks[key]
        print(f"BLOCK {key} maps {block['n_maps']} rows {block['n_rows']}", flush=True)
        for name, metrics in block["models"].items():
            print(
                f"  {name} corr {metrics['corr']:.4f} "
                f"e2 {100 * metrics['edge_2c']:.3f}c n2 {metrics['n_2c']} "
                f"e3 {100 * metrics['edge_3c']:.3f}c n3 {metrics['n_3c']} "
                f"share2 {metrics['share_2c']:.3f}",
                flush=True,
            )


if __name__ == "__main__":
    main()
