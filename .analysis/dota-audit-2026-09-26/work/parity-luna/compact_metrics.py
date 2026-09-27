import pandas as pd
from pathlib import Path
R=Path('/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/parity-luna')
j=pd.read_csv(R/'same_map_live_backtest.csv')
per=pd.read_csv(R/'backtest_per_seed.csv')
for _,r in j.iterrows():
    subset=per[(per.condition_id==r.condition_id)]
    if len(subset):
        j.loc[j.condition_id==r.condition_id,'bt_pnl_common']=subset.bt_pnl.mean()
        j.loc[j.condition_id==r.condition_id,'bt_buy_common']=subset.bt_buy.mean()
j['both']=(j.live_buy>0)&(j.bt_buy>0)
for src in ['grid','oddin']:
 for period in ['2026-08-30_to_31','2026-09-01_to_19','2026-09-20_to_26']:
    x=j[(j.source==src)&(j.period==period)]
    common=x[x.both & x.live_pnl.notna()]
    def sums(z,p,b):
      return (len(z),int((z[p]>0).sum()),round(z[p].fillna(0).sum(),2),round(z[b].sum(),2),round(100*z[p].fillna(0).sum()/z[b].sum(),4) if z[b].sum() else None)
    print('PERIOD',src,period,'joined',len(x),'traded_live',int((x.live_buy>0).sum()),'traded_bt',int((x.bt_buy>0).sum()),'both',int(x.both.sum()),'complete_both',len(common))
    print('  ALL_LIVE complete maps/pnl/buy/return',sums(x[x.live_pnl.notna()],'live_pnl','live_buy'))
    print('  ALL_BT maps/pnl/buy/return',sums(x,'bt_pnl','bt_buy'))
    print('  SAME_COMMON_LIVE maps/positive/pnl/buy/return',sums(common,'live_pnl','live_buy'))
    print('  SAME_COMMON_BT maps/positive/pnl/buy/return',sums(common,'bt_pnl_common','bt_buy_common'))
    if len(common):
     print('  SAME_COMMON timings live/BT first BUY median',round(common.live_first_buy_sec.median(),1),round(common.bt_first_buy_sec.median(),1),'buy px mean',round(common.live_buy_price.mean(),4),round(common.bt_buy_price.mean(),4),'fills sums live/BT',round(common.live_fills.sum(),1),round(common.bt_fills.sum(),1),'BUY fills',round(common.live_buy_fills.sum(),1),round(common.bt_buy_fills.sum(),1))
     print('  SAME_COMMON markout per-map mean 30s live/BT',round(common.live_m30.mean(),5),round(common.bt_m30.mean(),5),'300s',round(common.live_m300.mean(),5),round(common.bt_m300.mean(),5))
    print('  HELD live any >5',int((x.live_held_shares>5).sum()),'held with completed pnl',int(((x.live_held_shares>5)&x.live_pnl.notna()).sum()),'all joined map count',len(x))
print('COMBINED period 9/1-19 GRID')
x=j[(j.source=='grid')&(j.period=='2026-09-01_to_19')]
print(x[x.both & x.live_pnl.notna()][['dir','date','live_buy','live_pnl','bt_buy','bt_pnl','live_held_shares','live_win']].to_string(index=False))
