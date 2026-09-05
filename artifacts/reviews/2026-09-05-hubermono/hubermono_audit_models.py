import hashlib
import json
from pathlib import Path
import lightgbm as lgb
import numpy as np
import pandas as pd
from shared.constants.paths import TRAINING_DATASET_PATH, VALIDATION_DATASET_PATH, PRODUCTION_TRAINING_DATASET_PATH
from shared.utils.gbm import FEATURE_COLUMNS, LGB_PARAMS, build_price_delta_labels
from train_model.train_model import select_validation_fit_frame, lagged_source_features

ROOT=Path('/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader')
OUT=Path('/private/tmp/hubermono-audit')
OUT.mkdir(exist_ok=True)
train=pd.read_parquet(TRAINING_DATASET_PATH)
prod=pd.read_parquet(PRODUCTION_TRAINING_DATASET_PATH)
cols=list(dict.fromkeys(FEATURE_COLUMNS+['match_id','start_time','event_id','market_status','signal_market_p_radiant_300s']))
valid_all=pd.read_parquet(VALIDATION_DATASET_PATH,columns=cols)
valid=select_validation_fit_frame(valid_all).copy()
assert not set(train.match_id)&set(valid.match_id)
assert set(prod.match_id)==set(train.match_id)|set(valid.match_id)
assert not train.duplicated(['match_id','second']).any()
assert not prod.duplicated(['match_id','second']).any()
assert not valid.duplicated(['match_id','second']).any()
pd.testing.assert_frame_equal(train.reset_index(drop=True),prod[prod.match_id.isin(train.match_id)].reset_index(drop=True))
for label,frame in [('train',train),('validation',valid),('production',prod)]:
    print('dataset',label,len(frame),frame.match_id.nunique(),pd.to_datetime(frame.start_time.min(),unit='s'),pd.to_datetime(frame.start_time.max(),unit='s'),'seconds',frame.second.min(),frame.second.max(),'rows/match',frame.groupby('match_id').size().describe().round(2).to_dict(),flush=True)
X=lagged_source_features(valid)
y=build_price_delta_labels(valid)
current=valid.market_p_radiant.to_numpy()
models={
    'l1_research':ROOT/'data/new_model/archive/research/20260904T222046Z',
    'hm_research':ROOT/'data/new_model/research',
    'l1_production':ROOT/'data/new_model/archive/production/20260904T222047Z',
    'hm_production':ROOT/'data/new_model/production',
}
predictions={}
def row_cluster_ci(values,group,replicates=2000,seed=20260810):
    g=pd.DataFrame({'value':values,'group':np.asarray(group)}).groupby('group',sort=False).value.agg(['sum','count'])
    ix=np.random.default_rng(seed).integers(len(g),size=(replicates,len(g)))
    b=g['sum'].to_numpy()[ix].sum(axis=1)/g['count'].to_numpy()[ix].sum(axis=1)
    return np.quantile(b,[.025,.975]).tolist()
tail_rows=[]
for label,p in models.items():
    model=lgb.Booster(model_file=str(p/'model.txt'))
    meta=json.loads((p/'model.json').read_text())
    data_path=PRODUCTION_TRAINING_DATASET_PATH if 'production' in label else TRAINING_DATASET_PATH
    assert hashlib.sha256(data_path.read_bytes()).hexdigest()==meta['train_dataset_sha256']
    if 'research' in label:
        assert hashlib.sha256(VALIDATION_DATASET_PATH.read_bytes()).hexdigest()==meta['validation_dataset_sha256']
    assert model.feature_name()==FEATURE_COLUMNS==meta['features']
    raw=model.predict(X,num_threads=4)
    pred=np.clip(current+raw,0,1)-current
    predictions[label]=pred
    gain=np.abs(y)-np.abs(y-pred)
    metrics={'trees':model.num_trees(),'mae_gain_c':gain.mean()*100,'bias_c':(pred-y).mean()*100,'dir300_c':(np.where(pred>=0,1,-1)*y).mean()*100,'mae_ci_c':np.asarray(row_cluster_ci(gain,valid.event_id))*100}
    print('MODEL',label,metrics,flush=True)
    if 'research' in label:
        m=meta['metrics']
        for k,actual in [('trees',metrics['trees']),('mae_gain_300_cents',metrics['mae_gain_c']),('model_bias_300_cents',metrics['bias_c']),('dir_300_cents',metrics['dir300_c']),('mae_gain_300_ci_low_cents',metrics['mae_ci_c'][0]),('mae_gain_300_ci_high_cents',metrics['mae_ci_c'][1])]:
            assert np.isclose(m[k],actual,atol=1e-10),(label,k,m[k],actual)
    print('model params',label,{k:model.params.get(k) for k in ['objective','alpha','learning_rate','min_data_in_leaf','num_leaves','monotone_constraints','monotone_constraints_method']},flush=True)
    counts=[t['num_leaves'] for t in model.dump_model()['tree_info']]
    print('leaves',label,'mean',np.mean(counts),'total',sum(counts),flush=True)
    counts_to_try=[10,20,30,40] if label.startswith('l1') else [50,100,150,200,216,226,235]
    for n in counts_to_try:
        small_raw=model.predict(X,num_iteration=n,num_threads=4)
        tail=np.abs(raw-small_raw)*100
        row={'model':label,'trees_kept':n,'tail_mean_abs_c':tail.mean(),'tail_p95_abs_c':np.quantile(tail,.95),'tail_max_abs_c':tail.max(),'gate_change_fraction':np.mean((np.abs(raw)>=.01)!=(np.abs(small_raw)>=.01)),'direction_change_fraction':np.mean((raw>=0)!=(small_raw>=0)),'mae_gain_c':(np.abs(y)-np.abs(y-(np.clip(current+small_raw,0,1)-current))).mean()*100}
        tail_rows.append(row)
        print('TAIL',row,flush=True)
pd.DataFrame(tail_rows).to_csv(OUT/'tree-tail-diagnostics.csv',index=False)
paired=np.abs(y-predictions['l1_research'])-np.abs(y-predictions['hm_research'])
print('PAIRED_MAE_GAIN_C',paired.mean()*100,'series_ci',np.array(row_cluster_ci(paired,valid.event_id,20000))*100,flush=True)
train_y=build_price_delta_labels(train)
hm=lgb.Booster(model_file=str(models['hm_research']/'model.txt'))
residual=train_y-hm.predict(train[FEATURE_COLUMNS],num_threads=4)
print('fraction train residual outside Huber delta',np.mean(np.abs(residual)>.02),flush=True)
dates=pd.to_datetime(valid.start_time,unit='s',utc=True)
for label in ['l1','hm']:
    diff=np.abs(predictions[label+'_production']-predictions[label+'_research'])*100
    print('PRODUCTION_SHIFT',label,'mean_abs_c',diff.mean(),'p95_abs_c',np.quantile(diff,.95),'gate_change',np.mean((np.abs(predictions[label+'_production'])>=.01)!=(np.abs(predictions[label+'_research'])>=.01)),flush=True)

params=dict(LGB_PARAMS)
params['num_threads']=4
history={}
booster=lgb.train(params,lgb.Dataset(train[FEATURE_COLUMNS],train_y),num_boost_round=600,valid_sets=[lgb.Dataset(X,y)],valid_names=['val'],callbacks=[lgb.record_evaluation(history)])
saved_raw=hm.predict(X,num_threads=4)
repeat_raw=booster.predict(X,num_iteration=236,num_threads=4)
print('REFIT_RESEARCH_236 max_prediction_difference',np.max(np.abs(repeat_raw-saved_raw)),flush=True)
curve=np.array(history['val']['l1'])*100
best=int(np.argmin(curve))+1
print('CURVE_600 best',best,'best_mae',curve.min(),'points',{n:float(curve[n-1]) for n in [50,100,150,200,236,300,400,472,600]},flush=True)
pd.DataFrame({'trees':np.arange(1,len(curve)+1),'validation_mae_c':curve}).to_csv(OUT/'hm-learning-curve.csv',index=False)
booster.save_model(OUT/'hm-research-600.txt')

# A chronological diagnostic: tune on dates before August, then refit minute data
# through July and evaluate August onward. This is reused historical data, not a
# new untouched test set. Keep all hyperparameters fixed.
cut=int(pd.Timestamp('2026-08-01',tz='UTC').timestamp())
early=valid.start_time<cut
late=~early
expanded=prod[prod.start_time<cut]
print('CHRONO_DIAGNOSTIC',{'early_valid_matches':valid.loc[early,'match_id'].nunique(),'late_matches':valid.loc[late,'match_id'].nunique(),'expanded_train_matches':expanded.match_id.nunique()},flush=True)
for label in ['l1','hm']:
    p=dict(params)
    if label=='l1':
        p['objective']='regression_l1'
        for key in ['alpha','monotone_constraints','monotone_constraints_method']:p.pop(key,None)
    selected=lgb.train(p,lgb.Dataset(train[FEATURE_COLUMNS],train_y),num_boost_round=3000,valid_sets=[lgb.Dataset(X.loc[early],y[early])],callbacks=[lgb.early_stopping(100,verbose=False)])
    n=selected.num_trees()
    for mode in ['research','refit','half','double']:
        if mode=='research':model=selected
        else:
            rounds=n if mode=='refit' else max(1,n//2) if mode=='half' else n*2
            model=lgb.train(p,lgb.Dataset(expanded[FEATURE_COLUMNS],build_price_delta_labels(expanded)),num_boost_round=rounds)
        pr=model.predict(X.loc[late],num_threads=4)
        pr=np.clip(current[late]+pr,0,1)-current[late]
        gain=np.abs(y[late])-np.abs(y[late]-pr)
        print('CHRONO_RESULT',label,mode,'trees',model.num_trees(),'gain_c',gain.mean()*100,'bias_c',(pr-y[late]).mean()*100,'dir_c',(np.where(pr>=0,1,-1)*y[late]).mean()*100,flush=True)
print('ALL MODEL METADATA, DATASET AND RESEARCH METRIC CHECKS PASSED',flush=True)
