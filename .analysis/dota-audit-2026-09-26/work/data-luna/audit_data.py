from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from shared.constants.paths import MARKET_SECONDS_DIR, RAW_TELONEX_POLYMARKET_DIR, MATCH_CATALOG_PATH
from shared.constants.dataset import VALIDATION_START_TIME, TRAIN_LAG_SECONDS, MODEL_TARGET_HORIZON_SECONDS
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import datetime_to_ns, get_state_available_ts
from shared.utils.telonex_book import load_token_book, NS_PER_US, US_PER_SECOND
from market_data.build_market_data import market_seconds_cache_path, resolve_catalog_row, build_market_second_rows
from prepare_dataset.prepare_dataset import load_market_second_rows

ROOT = Path('data/new_processed')
print('HEAD_CACHE_VERSION', market_seconds_cache_path(0).parent.name)
print('CACHE_VERSIONS')
for d in sorted(MARKET_SECONDS_DIR.iterdir()):
    if d.is_dir():
        files=list(d.glob('*.parquet'))
        print(d.name, 'files',len(files),'bytes',sum(p.stat().st_size for p in files))

cat_raw=pd.read_parquet(MATCH_CATALOG_PATH)
cat=load_match_catalog(MATCH_CATALOG_PATH)
print('\nCATALOG rows',len(cat_raw),'loaded',len(cat))
for col in ['match_id','condition_id','event_id','token_id_0','token_id_1']:
    print('DUP',col,int(cat_raw[col].duplicated(keep=False).sum()),'duplicate_groups',int(cat_raw[col].duplicated().sum()))
print('NULLS',cat_raw.isna().sum().to_dict())
print('RADIANT_INDEX',cat_raw.radiant_token_index.value_counts(dropna=False).to_dict())
print('WINNER_SOURCE',cat_raw.winner_source.value_counts(dropna=False).to_dict())
print('HORN_SOURCE',cat_raw.horn_source.value_counts(dropna=False).to_dict())
print('PAUSE_SOURCE',cat_raw.pauses_source.value_counts(dropna=False).to_dict())
cat_raw['start_s']=cat_raw.apply(lambda r: pd.Timestamp(r['spawn_at'] if isinstance(r['spawn_at'],str) else r['horn_at']).timestamp(),axis=1)
cat_raw['month']=pd.to_datetime(cat_raw.start_s,unit='s',utc=True).dt.strftime('%Y-%m')
cat_raw['split']=np.where(cat_raw.start_s<VALIDATION_START_TIME,'train','validation')
print('MONTH_COUNTS')
print(cat_raw.groupby(['month','split']).size().to_string())
print('SPLIT_COUNTS',cat_raw.split.value_counts().to_dict(),'boundary_near_90s',int((abs(cat_raw.start_s-VALIDATION_START_TIME)<=90).sum()))
spawn=pd.to_datetime(cat_raw.spawn_at,utc=True,errors='coerce')
horn=pd.to_datetime(cat_raw.horn_at,utc=True,errors='coerce')
ended=pd.to_datetime(cat_raw.ended_at,utc=True,errors='coerce')
closed=pd.to_datetime(cat_raw.market_closed_at,utc=True,errors='coerce')
print('SPAWN_MINUS_HORN_SEC', (horn-spawn).dt.total_seconds().dropna().describe(percentiles=[.05,.5,.95]).to_dict())
print('ENDED_MINUS_HORN_DURATION_SEC',((ended-horn).dt.total_seconds()-cat_raw.duration).describe(percentiles=[.01,.05,.5,.95,.99]).to_dict())
print('CLOSED_MINUS_ENDED_SEC', (closed-ended).dt.total_seconds().describe(percentiles=[.01,.05,.5,.95,.99]).to_dict())

current_dir=market_seconds_cache_path(0).parent
cache_present={int(p.stem.split('=')[1]) for p in current_dir.glob('match_id=*.parquet')}
cat_ids=set(cat_raw.match_id.astype('int64'))
print('\nCACHE_PRESENCE current_files',len(cache_present),'catalog_ids',len(cat_ids),'catalog_has_cache',len(cat_ids&cache_present),'not_catalog',len(cache_present-cat_ids))
cat_raw['has_current_cache']=cat_raw.match_id.astype('int64').isin(cache_present)
print('CACHE_BY_SPLIT_MONTH')
print(cat_raw.groupby(['split','month']).has_current_cache.agg(['sum','count']).to_string())
idx=pd.read_parquet(ROOT/'stratz_match_index/stratz_match_index.parquet')
idx['match_id']=idx.match_id.astype('int64')
cat_idx=cat_raw[['match_id','month','split','has_current_cache']].merge(idx,on='match_id',how='left',suffixes=('','_idx'))
print('STRATZ_INDEX status among catalog',cat_idx.status.value_counts(dropna=False).to_dict())
print('STRATZ_REASON',cat_idx.reason.value_counts(dropna=False).to_dict())
print('STRATZ_PLAYBACK_AVAILABLE',cat_idx.playback_available.value_counts(dropna=False).to_dict())
print('STRATZ_STATUS_BY_SPLIT',pd.crosstab(cat_idx.split,cat_idx.status,dropna=False).to_dict())

train=pd.read_parquet(ROOT/'dataset/training_dataset.parquet')
val=pd.read_parquet(ROOT/'dataset/validation_dataset.parquet')
prod=pd.read_parquet(ROOT/'dataset/production/training_dataset.parquet')
game=pd.read_parquet(ROOT/'dataset/game_features.parquet')
for name,df in [('train',train),('validation',val),('production',prod),('game_features',game)]:
    print('\nDATASET',name,'rows',len(df),'maps',df.match_id.nunique(),'duplicate_map_second',int(df.duplicated(['match_id','second' if 'second' in df else 'game_second']).sum()))
    if 'second' in df: print('seconds',int(df.second.min()),int(df.second.max()))
print('DATASET_SPLIT_MAP_COUNTS train/val',train.match_id.nunique(),val.match_id.nunique())
print('DATASET_ROWS_MONTH_SPLIT')
train_month=train[['match_id']].drop_duplicates().merge(cat_raw[['match_id','month','split']],on='match_id',how='left')
val_month=val[['match_id']].drop_duplicates().merge(cat_raw[['match_id','month','split']],on='match_id',how='left')
print('train',train_month.groupby('month').size().to_dict())
print('val',val_month.groupby('month').size().to_dict())
print('PROD_SPLIT_ROWS',pd.read_parquet(ROOT/'dataset/production/split.parquet').split.value_counts().to_dict())

# Row status / price quality and target landing checks.
print('\nVALIDATION_STATUS',val.market_status.value_counts(dropna=False).to_dict())
val_ok=val[val.market_status.eq('ok')].copy()
print('VAL_BAD_ROWS_WITH_CURRENT_MID',int((~val.market_status.eq('ok') & val.market_p_radiant.notna()).sum()))
print('VAL_OK_NULL_CURRENT_MID',int((val.market_status.eq('ok') & val.market_p_radiant.isna()).sum()))
print('VAL_STATUS_BY_MONTH')
print(val[['match_id','market_status']].merge(cat_raw[['match_id','month']],on='match_id').groupby(['month','market_status']).size().to_string())
val_dur=cat_raw.set_index('match_id').duration.to_dict()
val['duration']=val.match_id.map(val_dur)
val['label_after_end']=(val.second+MODEL_TARGET_HORIZON_SECONDS)>val.duration
val['label_abs_delta']=(val.signal_market_p_radiant_300s-val.market_p_radiant).abs()
valid_labels=val[val.signal_market_p_radiant_300s.notna()]
post=valid_labels[valid_labels.label_after_end]
print('VAL_LABELS',len(valid_labels),'postend',len(post),'share',len(post)/max(len(valid_labels),1),'maps_post',post.match_id.nunique(),'post_abs_delta_mean_median_p95',post.label_abs_delta.mean(),post.label_abs_delta.median(),post.label_abs_delta.quantile(.95))
print('VAL_POSTEND_BY_MONTH',post[['match_id']].merge(cat_raw[['match_id','month']],on='match_id').groupby('month').size().to_dict())
# Training labels use source state second + lag + horizon.
train['duration']=train.match_id.map(val_dur)
train['label_after_end']=(train.second+TRAIN_LAG_SECONDS+MODEL_TARGET_HORIZON_SECONDS)>train.duration
train['label_abs_delta']=(train.signal_market_p_radiant_300s-train.market_p_radiant).abs()
train_labels=train[train.signal_market_p_radiant_300s.notna()]
train_post=train_labels[train_labels.label_after_end]
print('TRAIN_LABELS',len(train_labels),'postend',len(train_post),'share',len(train_post)/max(len(train_labels),1),'maps_post',train_post.match_id.nunique(),'post_abs_delta_mean_median_p95',train_post.label_abs_delta.mean(),train_post.label_abs_delta.median(),train_post.label_abs_delta.quantile(.95))
print('TRAIN_POSTEND_BY_MONTH',train_post[['match_id']].merge(cat_raw[['match_id','month']],on='match_id').groupby('month').size().to_dict())
# How often postgame labels approach the terminal outcome.
for name,df in [('train_post',train_post),('val_post',post)]:
    target=df.radiant_win.astype(float)
    error=(df.signal_market_p_radiant_300s-target).abs()
    print(name,'within_5c_terminal',int((error<=.05).sum()),'share',float((error<=.05).mean()) if len(error) else None,'median_terminal_error',error.median(),'mean_delta',df.label_abs_delta.mean())
# Compare existing forward label to the stored target-second quote for validation rows inside cache.
checks=0; mismatches=0; bad_target_nonnull=0; target_missing=0
for match_id,g in val.groupby('match_id',sort=False):
    by_second={int(r.second):r for r in g.itertuples(index=False)}
    for r in g.itertuples(index=False):
        if pd.isna(r.signal_market_p_radiant_300s): continue
        target=by_second.get(int(r.second)+300)
        if target is None:
            target_missing+=1
        elif target.market_status!='ok' or pd.isna(target.market_p_radiant):
            bad_target_nonnull+=1
        else:
            checks+=1
            if abs(float(r.signal_market_p_radiant_300s)-float(target.market_p_radiant))>1e-9: mismatches+=1
print('VAL_LABEL_EXACT_TO_TARGET_ROW',checks,'mismatches',mismatches,'nonnull_label_missing_target_row',target_missing,'nonnull_label_bad_target_row',bad_target_nonnull)

# Overall price path properties from the validation table.
price=val_ok.sort_values(['match_id','second']).copy()
price['prev_mid']=price.groupby('match_id').market_p_radiant.shift(1)
price['jump']=abs(price.market_p_radiant-price.prev_mid)
print('VAL_MID_RANGE',float(price.market_p_radiant.min()),float(price.market_p_radiant.max()),'jumps_gt_10c',int((price.jump>.1).sum()),'gt_25c',int((price.jump>.25).sum()),'gt_50c',int((price.jump>.5).sum()))
price['same_mid']=price.groupby('match_id').market_p_radiant.diff().eq(0)
print('VAL_CONSEC_SAME_MID_SHARE',float(price.same_mid.mean()))
print('VAL_FINAL_OUTCOME')
final_rows=[]
for match_id,g in val.groupby('match_id',sort=False):
    g=g.sort_values('second')
    ok=g[g.market_p_radiant.notna()]
    if ok.empty: continue
    last=ok.iloc[-1]
    final_rows.append((int(match_id),bool(last.radiant_win),float(last.market_p_radiant),int(last.second),int(last.duration)))
fr=pd.DataFrame(final_rows,columns=['match_id','radiant_win','last_p','last_second','duration'])
if not fr.empty:
    fr['aligned']=(fr.last_p>=.5)==fr.radiant_win
    fr['near_terminal']=np.where(fr.radiant_win,fr.last_p>=.95,fr.last_p<=.05)
    print('maps',len(fr),'last_price_alignment',fr.aligned.mean(),'near_terminal',fr.near_terminal.mean(),'radiant_win_count',fr.radiant_win.sum())
    print('misaligned',fr[~fr.aligned].head(15).to_dict('records'))

# Dataset source distributions; validation second is market second, so shift by 10 to state time.
features=['second','radiant_nw_adv','radiant_nw','dire_nw','radiant_xp_adv','deaths_radiant','deaths_dire','top1_nw_adv','radiant_top1_nw_ratio','dire_top1_nw_ratio','market_radiant_prior','market_p_radiant']
tr=train.copy(); va=val_ok[(val_ok.second>=-50)&(val_ok.second<=550)&val_ok.signal_market_p_radiant_300s.notna()].copy(); va['second']=va.second-10
print('\nFEATURE_DISTRIBUTIONS train_rows',len(tr),'val_rows',len(va))
for f in features:
    a=tr[f].dropna().astype(float); b=va[f].dropna().astype(float)
    if not len(a) or not len(b): continue
    qs=[.01,.05,.25,.5,.75,.95,.99]
    qa=a.quantile(qs).round(3).to_dict(); qb=b.quantile(qs).round(3).to_dict()
    ks=ks_2samp(a.to_numpy(),b.to_numpy(),method='asymp')
    print(f,'train_q',qa,'val_q',qb,'ks',round(float(ks.statistic),4),'p',float(ks.pvalue))
print('TRAIN_ZERO_FEATURES', {f:int((train[f]==0).sum()) for f in ['radiant_nw','dire_nw','radiant_xp_adv','deaths_radiant','deaths_dire']})
print('VAL_ZERO_FEATURES', {f:int((val[f]==0).sum()) for f in ['radiant_nw','dire_nw','radiant_xp_adv','deaths_radiant','deaths_dire']})
print('TRAIN_NW_ZERO_BY_MONTH')
print(train[['match_id','radiant_nw','dire_nw']].merge(cat_raw[['match_id','month']],on='match_id').assign(any_nw_zero=lambda x:(x.radiant_nw==0)|(x.dire_nw==0)).groupby('month').any_nw_zero.agg(['sum','count']).to_string())

# Source cache sample: one map per month, compare in-cache values with rebuilt values from raw.
print('\nMONTHLY_CACHE_SAMPLE')
selected=[]
for month,g in cat_raw.groupby('month',sort=True):
    cand=g[g.match_id.astype('int64').isin(cache_present)]
    if cand.empty: continue
    # Prefer playback-available maps when there is one.
    ids=set(idx.loc[idx.playback_available.fillna(False),'match_id'].astype('int64'))
    preferred=cand[cand.match_id.astype('int64').isin(ids)]
    row=(preferred if not preferred.empty else cand).sort_values('match_id').iloc[len(preferred if not preferred.empty else cand)//2]
    selected.append(int(row.match_id))
for match_id in selected:
    entry=cat[match_id]
    stored=pd.read_parquet(market_seconds_cache_path(match_id))
    ok=stored[stored.market_p_radiant.notna()]
    last=ok.sort_values('second').iloc[-1] if not ok.empty else None
    first=ok[ok.second>=0].sort_values('second').iloc[0] if not ok[ok.second>=0].empty else None
    row=cat_raw[cat_raw.match_id.astype('int64').eq(match_id)].iloc[0]
    month=row.month
    status=stored.market_status.value_counts().to_dict()
    same_share=float(ok.sort_values('second').market_p_radiant.diff().eq(0).mean()) if len(ok)>1 else None
    max_jump=float(ok.sort_values('second').market_p_radiant.diff().abs().max()) if len(ok)>1 else None
    exact='not_checked'
    try:
        rebuilt=build_market_second_rows(resolve_catalog_row(entry,RAW_TELONEX_POLYMARKET_DIR))
        if rebuilt is None: exact='no_raw_book'
        else:
            rebuilt_df=pd.DataFrame(rebuilt)
            merged=stored.merge(rebuilt_df,on=['match_id','second'],suffixes=('_old','_new'))
            fields=['market_status','market_p_radiant','signal_market_p_radiant_30s','signal_market_p_radiant_300s']
            diffs=0; comp=0
            for f in fields:
                a=merged[f+'_old']; b=merged[f+'_new']
                both=a.notna()&b.notna(); comp+=int(both.sum())
                if f=='market_status': diffs+=int((a!=b).sum())
                else: diffs+=int(((a-b).abs()>1e-9).fillna(a.isna()!=b.isna()).sum())
            exact=f'compared={comp},field_diffs={diffs}'
    except Exception as e: exact='ERROR:'+type(e).__name__+':'+str(e)[:120]
    print(month,match_id,'rows',len(stored),'status',status,'first_game_mid',None if first is None else (int(first.second),float(first.market_p_radiant)),'last_mid',None if last is None else (int(last.second),float(last.market_p_radiant)),'winner',entry.radiant_win,'same_mid_share',same_share,'max_jump',max_jump,'raw_rebuild',exact)

# Sample raw top-book snapshot cadence/crossing/depth around map windows.
print('\nRAW_BOOK_SAMPLES')
for match_id in selected:
    entry=cat[match_id]
    job=resolve_catalog_row(entry,RAW_TELONEX_POLYMARKET_DIR)
    state_start=datetime_to_ns(get_state_available_ts(horn=job.horn,second=-60,pauses=job.pauses))//NS_PER_US-5*US_PER_SECOND
    state_end=datetime_to_ns(get_state_available_ts(horn=job.horn,second=job.duration_seconds,pauses=job.pauses))//NS_PER_US+300*US_PER_SECOND
    per=[]
    for token in job.token_ids:
        book=load_token_book(token_id=token,start_us=state_start,end_us=state_end,telonex_root=RAW_TELONEX_POLYMARKET_DIR)
        if book is None: per.append((token,0,None));continue
        intervals=np.diff(np.array(book.timestamps_us,dtype=np.int64))/US_PER_SECOND
        two=sum(b is not None and a is not None for b,a in zip(book.bids,book.asks))
        crossed=sum(b is not None and a is not None and b>=a for b,a in zip(book.bids,book.asks))
        per.append((token,len(book.timestamps_us),{'two_sided':two,'crossed':crossed,'dupe_ts':len(book.timestamps_us)-len(set(book.timestamps_us)),'median_gap':float(np.median(intervals)) if len(intervals) else None,'p95_gap':float(np.quantile(intervals,.95)) if len(intervals) else None,'zero_or_negative_gap':int((intervals<=0).sum()),'subsecond_pct':float((intervals<1).mean()) if len(intervals) else None}))
    print(cat_raw.loc[cat_raw.match_id.astype('int64').eq(match_id),'month'].iloc[0],match_id,per)

# Live session/core logs do not necessarily persist model input features; quantify what is retained.
print('\nLIVE_ROOTS')
trader=Path('data/trader')
print('dirs',sum(1 for p in trader.iterdir() if p.is_dir()))
