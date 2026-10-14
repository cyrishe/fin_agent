"""Declared secondary check: highest training OOF Top1 among the SAME 26 configs.

Added after the primary AP holdout check. This is exploratory, not a retrospectively
pre-registered test. No new features, parameters or holdout-driven threshold edits.
"""
import json
import joblib
import pandas as pd
from scripts.experiment_automl_c1430_cv import (
    OUT,KEY,FEATURES,configs,splits,fit,score,metrics,dump)

def main():
    board=pd.read_csv(OUT/'cv_leaderboard.csv').sort_values(['top1_hits','mean_lift','name'],ascending=[False,False,True])
    name=str(board.iloc[0]['name'])
    dump('top1_selection_addendum.json',dict(model=name,
         rule='Highest aggregate training OOF Top1 hits among original 26; ties by mean AP lift.',
         timing='Added after primary AP-selected holdout results. Exploratory secondary analysis.',
         new_search_configs=0))
    result=json.loads((OUT/'results.json').read_text())
    if name in result['models']: return
    c=next(c for c in configs() if c['name']==name)
    sets=splits(pd.read_pickle(OUT/'numeric_snapshot.pkl'))
    m=fit(c,sets['train']);refit=fit(c,sets['refit'])
    joblib.dump(dict(config=c,columns=FEATURES[c['features']],model=m),OUT/f'{name}_train.joblib')
    joblib.dump(dict(config=c,columns=FEATURES[c['features']],model=refit),OUT/f'{name}_refit.joblib')
    oof=pd.read_pickle(OUT/f'{name}_oof.pkl')
    r={'config':c,'features':FEATURES[c['features']],'oof':metrics(oof,oof.score.to_numpy())}
    for part,model in [('train',m),('valid',m),('test',refit)]:
        z=sets[part][KEY+['label_next3']].copy();z['score']=score(model,c,sets[part])
        z.to_pickle(OUT/f'{name}_{part}_scores.pkl');r[part]=metrics(z,z.score.to_numpy())
    result['models'][name]=r;result['selected'].append(name)
    dump('results.json',result)
    print(json.dumps({name:r},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
