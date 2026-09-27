"""Settlement tail in the Dota LIVE backtest: who is held to the end and why."""
import pandas as pd

BASE = "data/backtests/dota_maker/LIVE"
for seed in (0, 1, 2):
    r = pd.read_parquet(f"{BASE}/seed{seed}/results.parquet")
    f = pd.read_parquet(f"{BASE}/seed{seed}/fills.parquet")
    held = r[r["terminal_position"].abs() > 0]
    print(f"seed{seed}: maps={len(r)} engine_pnl={r.engine_pnl.sum():.0f} cash_flow={r.cash_flow.sum():.0f} "
          f"settle_part={r.engine_pnl.sum()-r.cash_flow.sum():.0f} held_maps={len(held)} "
          f"held_qty={held.terminal_position.sum():.0f} settlement_applied={int(r.settlement_applied.sum())} "
          f"dust_maps={(r.dust_position.abs()>0).sum()}")
    print("  terminal_side counts:", held.terminal_side.value_counts().to_dict())
    print("  slug without -game:", int((~r.slug.str.contains('-game')).sum()), "of", len(r))
r = pd.read_parquet(f"{BASE}/seed0/results.parquet")
held = r[r["terminal_position"].abs() > 0].copy()
held["settle_pnl"] = held.engine_pnl - held.cash_flow
cols = ["match_id", "slug", "terminal_token_index", "terminal_side", "terminal_position", "dust_position",
        "cash_flow", "engine_pnl", "settle_pnl", "stop_reason", "feed_source", "signal_mode"]
print(held[cols].sort_values("settle_pnl").to_string())
