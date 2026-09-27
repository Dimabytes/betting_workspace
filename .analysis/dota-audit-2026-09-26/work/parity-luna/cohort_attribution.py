from pathlib import Path
import pandas as pd

R = Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
joined = pd.read_csv(R / 'same_map_live_backtest.csv')
per_seed = pd.read_csv(R / 'backtest_per_seed.csv')
horn = pd.read_csv(R / 'horn_market_alignment.csv')
signals = pd.read_csv(R / 'signal_parity_grid_20260901_19.csv')

joined['condition_id'] = joined.condition_id.str.lower()
horn['condition_id'] = horn.condition_id.str.lower()
signals['condition_id'] = signals.condition_id.str.lower()
direct = set(horn.loc[horn.horn_residual.abs() <= 5, 'condition_id'])
joined = joined[(joined.source == 'grid') & (joined.period == '2026-09-01_to_19')].copy()
joined = joined.merge(
    per_seed.groupby('condition_id', as_index=False)[['bt_pnl', 'bt_buy']].mean(),
    on='condition_id', suffixes=('', '_3seed'),
)
joined['horn_group'] = joined.condition_id.isin(direct).map({True:'direct_horn_shift',False:'no_direct_horn_shift'})
joined['both_complete'] = (joined.live_buy > 0) & (joined.bt_buy_3seed > 0) & joined.live_pnl.notna()
for group, x in joined.groupby('horn_group'):
    z=x[x.both_complete]
    print('PNL_GROUP', group, 'joined',len(x),'common_complete',len(z),
      'live_pnl',round(z.live_pnl.sum(),2),'live_buy',round(z.live_buy.sum(),2),
      'live_roi_pct',round(100*z.live_pnl.sum()/z.live_buy.sum(),4) if z.live_buy.sum() else None,
      'bt_mean_pnl',round(z.bt_pnl_3seed.sum(),2),'bt_mean_buy',round(z.bt_buy_3seed.sum(),2),
      'bt_roi_pct',round(100*z.bt_pnl_3seed.sum()/z.bt_buy_3seed.sum(),4) if z.bt_buy_3seed.sum() else None)

signals = signals[signals.condition_id.isin(set(joined.condition_id))].copy()
signals['horn_group'] = signals.condition_id.isin(direct).map({True:'direct_horn_shift',False:'no_direct_horn_shift'})
for group,x in signals.groupby('horn_group'):
    print('SIGNAL_GROUP',group,'rows',len(x),'maps',x.condition_id.nunique())
    for col in ['absdiff_market_radiant_prior','absdiff_market_p_radiant','absdiff_radiant_nw_adv','absdiff_radiant_xp_adv']:
      s=x[col].dropna()
      print(' ',col,'p50/p90/p95',s.quantile(.5),s.quantile(.9),s.quantile(.95))
    d=(x.research_live_input_delta-x.live_delta).dropna()
    print(' model_version_delta_abs_p50/p90/p95',d.abs().quantile(.5),d.abs().quantile(.9),d.abs().quantile(.95))
    f=x.dropna(subset=['research_live_input_delta','live_delta'])
    print(' threshold_flip_n',int((f.research_live_input_delta.abs().ge(.02)!=f.live_delta.abs().ge(.02)).sum()),'threshold_rows',len(f),'sign_flip_n',int((f.research_live_input_delta.mul(f.live_delta)<0).sum()))
