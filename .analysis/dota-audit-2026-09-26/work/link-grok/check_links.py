"""Linking and side-orientation checks. Read-only. Writes JSON next to this file."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import duckdb
import pandas as pd

from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.team_names import TEAM_ALIASES, orient_outcomes

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
OUT = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace"
    "/.analysis/dota-audit-2026-09-26/work/link-grok"
)
BOOKS = E / "data/new_processed/market_seconds/v9c88adc2"
FLIP_HI = 0.90
FLIP_LO = 0.10
PRIOR_GAP = 0.08
END_SLACK_S = 180


def dump(name: str, payload: object) -> None:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}", flush=True)


def gamma_vs_teams() -> dict[str, object]:
    index = json.loads(
        (E / "data/raw/polymarket_dota/universe/events/gamma_markets_index.json").read_text()
    )
    gamma: dict[str, dict[str, object]] = {}
    for row in index["markets"]:
        gamma[str(row["condition_id"]).lower()] = row
    universe = pd.read_parquet(E / "data/new_processed/universe/universe.parquet")
    counts: Counter[str] = Counter()
    reverse_rows: list[dict[str, object]] = []
    token_order_mismatch = 0
    missing_gamma = 0
    null_teams = 0
    for row in universe.to_dict(orient="records"):
        cid = str(row["conditionId"]).lower()
        g = gamma.get(cid)
        if g is None:
            missing_gamma += 1
            continue
        tokens = g.get("token_ids") or []
        if list(tokens)[:2] != [row["token_id_0"], row["token_id_1"]]:
            token_order_mismatch += 1
        names = g.get("outcome_names")
        team_a, team_b = row["team_a"], row["team_b"]
        if (
            not isinstance(names, list)
            or len(names) != 2
            or pd.isna(team_a)
            or pd.isna(team_b)
            or not team_a
            or not team_b
        ):
            null_teams += 1
            counts["no_names"] += 1
            continue
        verdict = orient_outcomes(str(names[0]), str(names[1]), str(team_a), str(team_b), TEAM_ALIASES)
        key = {True: "yes_is_team_a", False: "yes_is_team_b", None: "unresolved"}[verdict]
        counts[key] += 1
        if verdict is False and row["inventory_status"] == "candidate":
            reverse_rows.append(
                {
                    "condition_id": row["conditionId"],
                    "kind": row["contract_kind"],
                    "game_number": row["game_number"],
                    "team_a": team_a,
                    "team_b": team_b,
                    "outcome_0": names[0],
                    "outcome_1": names[1],
                    "slug": row["market_slug"],
                    "title": row["event_title"],
                }
            )
    return {
        "universe_rows": int(len(universe)),
        "missing_gamma": missing_gamma,
        "token_order_mismatch": token_order_mismatch,
        "null_names_or_teams": null_teams,
        "orientation": dict(counts),
        "candidate_yes_is_team_b": len(reverse_rows),
        "reverse_candidates": reverse_rows[:80],
        "reverse_candidate_total": len(reverse_rows),
    }


def book_vs_winner() -> dict[str, object]:
    catalog = pd.read_parquet(
        E / "data/new_processed/match_catalog/match_catalog.parquet",
        columns=[
            "match_id",
            "condition_id",
            "event_id",
            "radiant_token_index",
            "duration",
            "radiant_prior",
            "radiant_win",
            "market_slug",
            "archive_id",
        ],
    )
    con = duckdb.connect()
    ends = con.execute(
        f"""
        SELECT match_id,
               arg_max(market_p_radiant, second) FILTER (WHERE market_status = 'ok') AS last_p,
               max(second) FILTER (WHERE market_status = 'ok') AS last_second,
               count(*) FILTER (WHERE market_status = 'ok') AS ok_rows,
               arg_min(market_p_radiant, second) FILTER (
                   WHERE market_status = 'ok' AND second >= 0
               ) AS first_p,
               min(second) FILTER (WHERE market_status = 'ok' AND second >= 0) AS first_second
        FROM read_parquet('{BOOKS}/*.parquet')
        GROUP BY match_id
        """
    ).df()
    joined = catalog.merge(ends, on="match_id", how="left")
    have = joined[joined["last_p"].notna()]
    near_end = have[have["duration"] - have["last_second"] <= END_SLACK_S].copy()
    win = near_end["radiant_win"].astype(bool)
    p = near_end["last_p"]
    flips = near_end[(win & (p <= FLIP_LO)) | (~win & (p >= FLIP_HI))]
    soft_flips = near_end[(win & (p < 0.5)) | (~win & (p > 0.5))]
    prior_mask = (
        near_end["first_p"].notna()
        & ((near_end["radiant_prior"] - 0.5).abs() >= PRIOR_GAP)
        & ((near_end["first_p"] - 0.5).abs() >= PRIOR_GAP)
        & ((near_end["radiant_prior"] - 0.5) * (near_end["first_p"] - 0.5) < 0)
    )
    prior = near_end[prior_mask]
    cols = [
        "match_id",
        "condition_id",
        "market_slug",
        "radiant_token_index",
        "radiant_win",
        "radiant_prior",
        "duration",
        "last_second",
        "last_p",
        "first_second",
        "first_p",
        "archive_id",
    ]
    flips[cols].to_csv(OUT / "book_flips.csv", index=False)
    prior[cols].to_csv(OUT / "prior_flips.csv", index=False)
    return {
        "book_version": BOOKS.name,
        "catalog": int(len(catalog)),
        "with_any_ok_book": int(len(have)),
        "missing_book": int(len(catalog) - len(have)),
        "near_end": int(len(near_end)),
        "hard_flip_p_le_0.10_or_ge_0.90": int(len(flips)),
        "soft_flip_other_side_of_0.5": int(len(soft_flips)),
        "prior_opposite_first_ingame": int(len(prior)),
        "last_p_quantiles_near_end": {
            "win_true": _quantiles(near_end[win], "last_p"),
            "win_false": _quantiles(near_end[~win], "last_p"),
        },
        "hard_flips": _records(flips[cols]),
    }


def _quantiles(frame: pd.DataFrame, col: str) -> dict[str, float]:
    if frame.empty:
        return {}
    s = frame[col].dropna()
    return {str(q): float(s.quantile(q)) for q in (0.05, 0.5, 0.95)}


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    clean = frame.copy()
    for column in clean.columns:
        if str(clean[column].dtype) == "Int64":
            clean[column] = clean[column].astype(object)
    return json.loads(clean.to_json(orient="records"))


def link_conflicts() -> dict[str, object]:
    links = pd.read_parquet(E / "data/new_processed/match_links/match_links.parquet")
    universe = pd.read_parquet(
        E / "data/new_processed/universe/universe.parquet",
        columns=["conditionId", "contract_kind", "game_number", "best_of", "market_slug", "inventory_status"],
    ).rename(columns={"conditionId": "condition_id"})
    catalog = pd.read_parquet(
        E / "data/new_processed/match_catalog/match_catalog.parquet",
        columns=["match_id", "condition_id", "market_slug"],
    )
    cat = catalog.merge(universe, on="condition_id", how="left", suffixes=("", "_u"))
    kind_counts = cat["contract_kind"].value_counts(dropna=False).to_dict()
    null_game = universe[(universe["contract_kind"] == "map_winner") & universe["game_number"].isna()]
    mismatches = links[links["identity_conflict"].notna()]
    audit_path = E / "data/new_processed/match_links/match_link_audit.parquet"
    audit_counts: dict[str, int] = {}
    if audit_path.is_file():
        audit = pd.read_parquet(audit_path)
        audit_counts = {str(k): int(v) for k, v in audit["resolution"].value_counts().items()}
    index = pd.read_parquet(E / "data/archive_index/index.parquet")
    dota = index[index["game"] == "dota"] if "game" in index.columns else index
    admission = {str(k): int(v) for k, v in dota["admission"].value_counts().items()}
    slug_dups = catalog.groupby("market_slug").size()
    slug_dups = slug_dups[slug_dups > 1].sort_values(ascending=False)
    return {
        "catalog_by_contract_kind": {str(k): int(v) for k, v in kind_counts.items()},
        "map_winner_null_game_number": int(len(null_game)),
        "null_game_slugs": _records(null_game[["condition_id", "market_slug", "best_of"]].head(30)),
        "identity_conflicts": _records(mismatches),
        "audit_resolutions": audit_counts,
        "dota_index_admission": admission,
        "catalog_duplicate_slugs": int(len(slug_dups)),
        "duplicate_slug_rows": [
            {"market_slug": str(slug), "n": int(n)} for slug, n in slug_dups.head(20).items()
        ],
        "catalog_missing_universe_kind": int(cat["contract_kind"].isna().sum()),
    }


def live_archives() -> dict[str, object]:
    catalog = pd.read_parquet(
        E / "data/new_processed/match_catalog/match_catalog.parquet",
        columns=["match_id", "condition_id", "radiant_token_index", "radiant_win", "market_slug", "duration"],
    )
    by_match = {int(r["match_id"]): r for r in catalog.to_dict(orient="records")}
    by_cid = {str(r["condition_id"]).lower(): r for r in catalog.to_dict(orient="records")}
    trader = E / "data/trader"
    winner_vs_catalog = []
    orient_vs_catalog = []
    slug_map_mismatch = []
    session_flips = []
    n_meta = 0
    n_final = 0
    n_session_p = 0
    for path in sorted(trader.glob("*/match.json")):
        n_meta += 1
        try:
            meta = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        final = meta.get("final") or {}
        winner = final.get("winner")
        market = meta.get("market") or {}
        cid = str(market.get("condition_id") or "").lower()
        steam = meta.get("steam_match_id")
        yes = market.get("yes_is_radiant")
        map_number = meta.get("map_number")
        slug = str(market.get("market_slug") or "")
        if winner in ("radiant", "dire"):
            n_final += 1
        cat = by_cid.get(cid)
        if cat is None and steam is not None:
            try:
                cat = by_match.get(int(steam))
            except (TypeError, ValueError):
                cat = None
        if cat is not None and winner in ("radiant", "dire"):
            catalog_win = bool(cat["radiant_win"])
            live_win = winner == "radiant"
            if catalog_win != live_win and str(cat["condition_id"]).lower() == cid:
                winner_vs_catalog.append(
                    {
                        "archive": path.parent.name,
                        "steam": steam,
                        "cid": cid,
                        "live_winner": winner,
                        "catalog_win": catalog_win,
                        "slug": slug,
                        "map_number": map_number,
                    }
                )
        if cat is not None and isinstance(yes, bool) and str(cat["condition_id"]).lower() == cid:
            catalog_yes = int(cat["radiant_token_index"]) == 0
            if catalog_yes != yes:
                orient_vs_catalog.append(
                    {
                        "archive": path.parent.name,
                        "cid": cid,
                        "live_yes_is_radiant": yes,
                        "catalog_rti": int(cat["radiant_token_index"]),
                        "slug": slug,
                        "catalog_match": int(cat["match_id"]),
                        "steam": steam,
                    }
                )
        slug_game = _slug_game(slug)
        if slug_game is not None and map_number is not None and int(map_number) != slug_game:
            slug_map_mismatch.append(
                {"archive": path.parent.name, "slug": slug, "map_number": map_number, "slug_game": slug_game}
            )
        last_p, last_second = _last_session_p(path.parent)
        if last_p is None or winner not in ("radiant", "dire"):
            continue
        n_session_p += 1
        duration = final.get("duration_seconds")
        near = (
            isinstance(duration, int)
            and isinstance(last_second, int)
            and duration - last_second <= END_SLACK_S
        )
        flipped = (winner == "radiant" and last_p <= FLIP_LO) or (winner == "dire" and last_p >= FLIP_HI)
        if flipped and (near or last_p <= FLIP_LO or last_p >= FLIP_HI):
            session_flips.append(
                {
                    "archive": path.parent.name,
                    "winner": winner,
                    "last_p": last_p,
                    "last_second": last_second,
                    "duration": duration,
                    "near_end": near,
                    "yes_is_radiant": yes,
                    "slug": slug,
                    "map_number": map_number,
                    "cid": cid,
                }
            )
    return {
        "match_json": n_meta,
        "with_winner": n_final,
        "session_with_p_and_winner": n_session_p,
        "winner_disagrees_catalog_same_cid": len(winner_vs_catalog),
        "winner_disagrees": winner_vs_catalog[:40],
        "orientation_disagrees_catalog": len(orient_vs_catalog),
        "orientation_disagrees": orient_vs_catalog[:40],
        "slug_map_number_mismatch": len(slug_map_mismatch),
        "slug_map_mismatches": slug_map_mismatch[:40],
        "session_hard_flips": len(session_flips),
        "session_flips": session_flips[:60],
    }


def _slug_game(slug: str) -> int | None:
    marker = "-game"
    if marker not in slug:
        return None
    tail = slug.rsplit(marker, 1)[-1]
    digits = ""
    for ch in tail:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def _last_session_p(archive: Path) -> tuple[float | None, int | None]:
    path = archive / "session.jsonl"
    last_p: float | None = None
    last_second: int | None = None
    try:
        handle = open_maybe_gz(path)
    except OSError:
        return None, None
    with handle:
        for line in handle:
            if '"market_p_radiant"' not in line or '"polymarket"' not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("venue") != "polymarket":
                continue
            p = row.get("market_p_radiant")
            if isinstance(p, (int, float)):
                last_p = float(p)
                second = row.get("second")
                last_second = int(second) if isinstance(second, int) else last_second
    return last_p, last_second


def main() -> None:
    print("gamma", flush=True)
    dump("gamma_orientation.json", gamma_vs_teams())
    print("books", flush=True)
    dump("book_check.json", book_vs_winner())
    print("links", flush=True)
    dump("link_structure.json", link_conflicts())
    print("live", flush=True)
    dump("live_archives.json", live_archives())


if __name__ == "__main__":
    main()
