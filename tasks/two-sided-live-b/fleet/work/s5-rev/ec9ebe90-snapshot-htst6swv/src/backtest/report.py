"""Terminal two-column report for a backtest summary payload."""

from collections.abc import Mapping
from typing import Any, cast

from backtest.chart import render_balance_chart
from backtest.postprocess import BOARD_FILL_WINDOW_SECONDS
from backtest.report_types import ArmPayload, MarkoutBlock, SummaryPayload


def _money(value: float, digits: int) -> str:
    """Format a dollar amount with a leading $."""
    amount = f"{abs(value):.{digits}f}"
    if value < 0:
        return f"-${amount}"
    return f"${amount}"


def _format_kv(label: str, value: str) -> str:
    """One two-column terminal row; width fits 'final balance with rebate'."""
    return f"  {label:<26}{value}"


def _cents(probability_points: float) -> str:
    """Format a probability-point value as cents per share."""
    return f"{probability_points * 100:.3f}¢"


def _fill_row(name: str, block: MarkoutBlock | None) -> str:
    """One markout row: name, point estimate, 95% CI in cents."""
    if block is None:
        return f"  {name:<12}{'n/a':>10}"
    estimate = _cents(block["estimate"])
    interval = f"[{block['ci_low'] * 100:7.3f}, {block['ci_high'] * 100:7.3f}]"
    return f"  {name:<12}{estimate:>10}    {interval}"


def _coverage_eligible(payload: SummaryPayload) -> int | None:
    """coverage.eligible from summary.json, or None on a single-match run."""
    coverage = payload.get("coverage")
    if coverage is None:
        return None
    raw = coverage.get("eligible")
    if type(raw) is int:
        return raw
    return None


def _manifest_text(manifest: Mapping[str, object], key: str) -> str:
    """Stringify one manifest scalar for the identity line; empty when absent."""
    raw = manifest.get(key)
    if type(raw) is str:
        return raw
    if type(raw) is int:
        return str(raw)
    if type(raw) is float:
        if raw == int(raw):
            return str(int(raw))
        return str(raw)
    return ""


_RUN_KNOBS = (
    ("model_name", "model", "", ""),
    ("train_lag_seconds", "trainlag", "", "s"),
    ("backtest_lag_seconds", "btlag", "", "s"),
    ("cadence_mean_interval", "cadence", "", "s"),
    ("signal_cadence_seed", "seed", "", ""),
)

_FOLLOW300_KNOBS = (
    ("max_signal_age_seconds", "sigage", "", "s"),
    ("max_exit_age_seconds", "exitage", "", "s"),
    ("sell_full_age_seconds", "sellfull", "", "s"),
    ("sell_ask_age_seconds", "sellask", "", "s"),
    ("buy_cutoff_second", "cut", "", ""),
    ("min_abs_delta", "delta", "", ""),
    ("exit_abs_delta", "xdelta", "", ""),
    ("min_entry_price", "minpx", "", ""),
    ("max_entry_price", "maxpx", "", ""),
    ("exit_settle_seconds", "settle", "", "s"),
    ("layer_usdc", "size", "$", ""),
)


def _on_off(value: object) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    return ""


def _phase_jump_rows(manifest: Mapping[str, object]) -> list[str]:
    """Phase and jump only when they differ from a flat half-spread."""
    rows: list[str] = []
    half = manifest.get("half_spread_ticks")
    h_mid = manifest.get("h_mid")
    h_late = manifest.get("h_late")
    if (
        type(half) is int
        and type(h_mid) is int
        and type(h_late) is int
        and (h_mid != half or h_late != half)
    ):
        rows.append(_format_kv("phase", f"{half}/{h_mid}/{h_late}"))
    c_jump = manifest.get("c_jump")
    if type(c_jump) is int and c_jump:
        rows.append(_format_kv("jump", f"+{c_jump}"))
    return rows


def _two_sided_rows(manifest: Mapping[str, object]) -> list[str]:
    """Quote knobs. Follow300's delta and clip stay off this header."""
    rows: list[str] = []
    half_spread = _manifest_text(manifest, "half_spread_ticks")
    if half_spread:
        rows.append(_format_kv("h", f"{half_spread} t"))
    rows.extend(_phase_jump_rows(manifest))
    model_k = manifest.get("model_k")
    if type(model_k) is int or type(model_k) is float:
        rows.append(_format_kv("k", f"{model_k:g}"))
    size_shares = manifest.get("size_shares")
    if (type(size_shares) is int or type(size_shares) is float) and size_shares > 0:
        rows.append(_format_kv("size", f"{size_shares:g} sh"))
    else:
        level = manifest.get("level_usdc")
        if type(level) is int or type(level) is float:
            digits = 0 if level == int(level) else 2
            rows.append(_format_kv("size", _money(level, digits)))
    band_hi = manifest.get("band_hi")
    if type(band_hi) is int or type(band_hi) is float:
        rows.append(_format_kv("band", f"[{1.0 - band_hi:.2f}, {band_hi:.2f}]"))
    kill = _on_off(manifest.get("kill_gate"))
    if kill:
        rows.append(_format_kv("kill", kill))
    spike = _on_off(manifest.get("mid_spike"))
    if spike:
        rows.append(_format_kv("spike", spike))
    queue = _on_off(manifest.get("queue_position"))
    if queue:
        rows.append(_format_kv("queue", queue))
    return rows


def _identity_rows(manifest: Mapping[str, object], arm: ArmPayload) -> list[str]:
    """Arm name plus one row per known strategy knob."""
    rows = [f"{arm['placement']}/{arm['fill_model']}", ""]
    knobs = _RUN_KNOBS
    if manifest.get("strategy") != "two-sided":
        knobs = (*_RUN_KNOBS, *_FOLLOW300_KNOBS)
    for key, label, prefix, suffix in knobs:
        text = _manifest_text(manifest, key)
        if text:
            rows.append(_format_kv(label, f"{prefix}{text}{suffix}"))
    if manifest.get("strategy") == "two-sided":
        rows.extend(_two_sided_rows(manifest))
    return rows


def _seconds(value: float) -> str:
    """Compact hold duration."""
    return f"{value:.0f}s"


def _hold_rows(arm: ArmPayload) -> list[str]:
    """Hold min/p50/p90, or n/a when nothing flattened."""
    hold = arm["hold"]
    if hold["closed"] == 0:
        return [_format_kv("hold", "n/a")]
    return [
        _format_kv("min", _seconds(hold["min_seconds"])),
        _format_kv("p50", _seconds(hold["p50_seconds"])),
        _format_kv("p90", _seconds(hold["p90_seconds"])),
    ]


def _span_text(seconds: float) -> str:
    """Calendar length of the fill tape."""
    if seconds <= 0:
        return "n/a"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.0f}d"


def _pct(ratio: float | None) -> str:
    """Wallet ROI as a whole percent, or n/a when capital is zero."""
    if ratio is None:
        return "n/a"
    return f"{ratio * 100:.0f}%"


def _optional_wallet_value(wallet: Mapping[str, object], key: str) -> float | None:
    """One wallet float, or None when an archived summary predates that key."""
    raw = wallet.get(key)
    if type(raw) is float or type(raw) is int:
        return float(raw)
    return None


def _optional_money(wallet: Mapping[str, object], key: str) -> str:
    """Money text for a wallet field that old artifacts may not carry."""
    value = _optional_wallet_value(wallet, key)
    if value is None:
        return "n/a"
    return _money(value, 2)


def _deposit_ratio(wallet: Mapping[str, object], net_pnl: float) -> str:
    """net_pnl per dollar of reserve-inclusive deposit; not a constant-budget ROI."""
    deposit = _optional_wallet_value(wallet, "required_cash_with_reserves")
    if deposit is None or deposit <= 0:
        return "n/a"
    return f"{net_pnl / deposit:.3f}"


def _capital_rows(arm: ArmPayload) -> list[str]:
    """Deposit rows: fills-only estimate, reserve-inclusive deposit, and peak reserve."""
    wallet = cast(Mapping[str, object], arm["wallet"])
    return [
        _format_kv("cash est (fills only)", _money(arm["wallet"]["required_cash"], 2)),
        _format_kv("deposit w/ reserves", _optional_money(wallet, "required_cash_with_reserves")),
        _format_kv(
            "deposit w/ res @close",
            _optional_money(wallet, "required_cash_with_reserves_at_close"),
        ),
        _format_kv("peak reserved", _optional_money(wallet, "peak_reserved")),
    ]


def _signal_rows(arm: ArmPayload, manifest: Mapping[str, Any]) -> list[str]:
    """Cohort composition: feed-source and model counts plus exclusion reasons."""
    rows = ["SIGNALS"]
    for label, count in sorted(arm["signal_groups"].items()):
        rows.append(_format_kv(label, str(count)))
    reason_counts: dict[str, int] = {}
    for reason in manifest["archive_exclusions"].values():
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    excluded_total = sum(reason_counts.values())
    if reason_counts:
        detail = ", ".join(f"{reason} {count}" for reason, count in sorted(reason_counts.items()))
        excluded_text = f"{excluded_total}   ({detail})"
    else:
        excluded_text = "0"
    rows.append(_format_kv("excluded", excluded_text))
    for name, count in sorted(arm["model_groups"].items()):
        rows.append(_format_kv(f"model {name}", str(count)))
    return rows


def _board_rows(arm: ArmPayload) -> list[str]:
    """Post-board-tick fill slice for the canonical signal contract."""
    board = arm["board"]
    return [
        "",
        f"BOARD FILLS <={BOARD_FILL_WINDOW_SECONDS:g}s",
        _format_kv("fills", f"{board['fills']}  ({board['share'] * 100:.1f}% of fills)"),
        _fill_row("buy 300s", board["buy_300s"]),
        _fill_row("sell 300s", board["sell_300s"]),
    ]


def _merge_rows(arm: ArmPayload) -> list[str]:
    """Pair credits. Zero on Follow300, so the line stays off that report."""
    merge_usdc = arm.get("merge_usdc", 0.0)
    if merge_usdc == 0.0:
        return []
    return [
        _format_kv("merge usdc", _money(merge_usdc, 2)),
        _format_kv("leftover settlement", _money(arm.get("leftover_settlement_usdc", 0.0), 2)),
    ]


def _pnl_rows(arm: ArmPayload) -> list[str]:
    """Capital, PnL, ROI, and per-unit rows for one arm."""
    wallet = arm["wallet"]
    spark = wallet["equity_spark"]
    rows = [
        "PNL",
        _format_kv("span", _span_text(wallet["span_seconds"])),
        *_capital_rows(arm),
        _format_kv("pnl before rebate", _money(arm["pnl_before_rebate"], 2)),
        _format_kv("ROI before rebate", _pct(wallet["roi_before_rebate"])),
        _format_kv("rebate", _money(arm["maker_rebate"], 2)),
        _format_kv("taker fee", _money(arm["taker_fee"], 2)),
        *_merge_rows(arm),
        _format_kv("pnl with rebate", _money(arm["net_pnl"], 2)),
        _format_kv("final balance with rebate", _money(wallet["final_balance_with_rebate"], 2)),
        _format_kv("ROI with rebate", _pct(wallet["roi_with_rebate"])),
        _format_kv(
            "net / deposit w/ reserves",
            _deposit_ratio(cast(Mapping[str, object], wallet), arm["net_pnl"]),
        ),
        _format_kv("pnl per match", _money(arm["pnl_per_eligible_match"], 3)),
        _format_kv("pnl per match with rebate", _money(arm["net_pnl_per_match"], 3)),
        _format_kv("median match pnl", _money(arm["median_match_pnl"], 3)),
        _format_kv("pnl per share", _cents(arm["pnl_per_bought_share"])),
        _format_kv("pnl per share with rebate", _cents(arm["pnl_per_bought_share_with_rebate"])),
        _format_kv("maps at once", str(wallet["maps_at_once"])),
    ]
    if spark:
        rows.extend(
            [
                "",
                "BALANCE",
                render_balance_chart(spark, wallet["required_cash"]),
            ]
        )
    return rows


def format_terminal_report(payload: SummaryPayload) -> str:
    """Spaced two-column terminal report per arm. JSON stays in summary.json."""
    selected = payload["selected"]
    coverage_eligible = _coverage_eligible(payload)
    eligible_text = "n/a" if coverage_eligible is None else str(coverage_eligible)
    blocks: list[str] = []
    for arm in payload["arms"]:
        completed = arm["completed"]
        traded = arm["traded"]
        buys_per_traded = f"{arm['buy_fills'] / traded:.1f} / traded" if traded else "n/a"
        wallet = arm["wallet"]
        clip = arm["median_buy_order_notional"]
        median_clip_text = "n/a" if clip is None else _money(clip, 2)
        markout = arm["markout"]
        lines = [
            *_identity_rows(payload["manifest"], arm),
            "",
            "RUN",
            _format_kv("eligible", eligible_text),
            _format_kv("selected", str(selected)),
            _format_kv("completed", str(completed)),
            _format_kv("terminated", str(arm["terminated"])),
            _format_kv("traded matches", str(traded)),
            _format_kv("no-trade", str(arm["no_trades"])),
            _format_kv("traded + no-trade", str(completed)),
            _format_kv("buy fills", f"{arm['buy_fills']}  ({buys_per_traded})"),
            _format_kv("sell fills", str(arm["sell_fills"])),
            _format_kv("turnover", _money(arm["buy_turnover"], 2)),
            _format_kv("median clip", median_clip_text),
            _format_kv("fill rate", f"{arm['fill_rate'] * 100:.1f}%"),
        ]
        if arm["terminated"] > 0:
            lines.append("")
            lines.append("  WARNING: terminated > 0; do not trust total PnL")
        lines.extend(
            [
                "",
                *_signal_rows(arm, payload["manifest"]),
                "",
                *_pnl_rows(arm),
                "",
                "FILLS",
                f"  {'':<12}{'est':>10}    95% CI",
                _fill_row("buy  30s", markout["buy_30s"]),
                _fill_row("buy 300s", markout["buy_300s"]),
                _fill_row("sell 30s", markout["sell_30s"]),
                _fill_row("sell 300s", markout["sell_300s"]),
                *_board_rows(arm),
                "",
                "HOLD",
                *_hold_rows(arm),
                "",
                "RISK",
                _format_kv("loss rate", f"{arm['loss_match_rate'] * 100:.1f}%"),
                _format_kv("CVaR 5%", _money(arm["cvar_5"], 3)),
                _format_kv("worst match", _money(arm["worst_match"], 3)),
                _format_kv("lowest capital point", _money(wallet["lowest_capital"], 2)),
            ]
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
