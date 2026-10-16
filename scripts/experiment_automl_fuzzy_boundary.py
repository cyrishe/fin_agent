"""Compare hard, downweighted-boundary and fuzzy three-class losses on fixed rolling folds."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp, softmax
from sklearn.linear_model import LogisticRegression

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import BINARY, DATA
from scripts.experiment_automl_matrix_10bar import PRICE_COLUMNS


PRICES = Path("docs/stock_automl_runs/20261009_tenbar_matrix/tenbar_price_extrema.csv.gz")
REFERENCE = Path("docs/stock_automl_runs/20261009_rolling_three_class_cross/daily_top5.csv")
OUT = Path("docs/stock_automl_runs/20261009_fuzzy_boundary")
WINDOWS = (10, 15, 20)
WIDTH = .002  # Two tenths of one percentage point on either side of each threshold.
C = .2


def prepare(train, test):
    numeric = [c for c in FEATURES if c not in BINARY]
    binary = [c for c in FEATURES if c in BINARY]
    center = train[numeric].mean().to_numpy()
    scale = train[numeric].std(ddof=0).replace(0, 1).to_numpy()
    def transform(frame):
        values = np.column_stack(((frame[numeric].to_numpy()-center)/scale,
                                  frame[binary].to_numpy()))
        return np.column_stack((np.ones(len(frame)), values))
    return transform(train), transform(test)


def targets(returns, method):
    r = np.asarray(returns)
    hard = np.select([r > .01, r < 0], [2, 0], default=1)
    q = np.eye(3)[hard]
    weights = np.ones(len(r))
    if method == "边界降权":
        near = np.minimum(abs(r), abs(r-.01))
        weights = 1 - .5*np.maximum(0, 1-near/WIDTH)
    elif method == "模糊标签":
        negative = np.clip((WIDTH-r)/(2*WIDTH), 0, 1)
        positive = np.clip((r-(.01-WIDTH))/(2*WIDTH), 0, 1)
        q = np.column_stack((negative, 1-negative-positive, positive))
        if not (q.min() >= -1e-12 and np.allclose(q.sum(axis=1), 1)):
            raise ValueError("Fuzzy memberships are invalid")
    elif method != "硬标签":
        raise ValueError(method)
    return q, weights


def fit_softmax(x, q, weights):
    n, p = x.shape
    wsum = weights.sum()
    regularization = 1/(C*n)
    def objective(flat):
        beta = flat.reshape(3,p)
        logits = x@beta.T
        logp = logits-logsumexp(logits, axis=1, keepdims=True)
        probs = np.exp(logp)
        loss = -(weights[:,None]*q*logp).sum()/wsum + \
               .5*regularization*np.square(beta[:,1:]).sum()
        grad = ((weights[:,None]*(probs-q)).T@x)/wsum
        grad[:,1:] += regularization*beta[:,1:]
        return loss, grad.ravel()
    result = minimize(objective, np.zeros(3*p), jac=True, method="L-BFGS-B",
                      options={"maxiter":300, "ftol":1e-10, "gtol":1e-7})
    if not result.success and np.linalg.norm(result.jac) > 1e-4:
        raise ValueError(f"Softmax did not converge: {result.message}")
    return result.x.reshape(3,p)


def summarize(rows, scope):
    result=[]
    for key, group in rows.groupby(["window","method","train_price","val_price"],sort=False):
        chosen=group[group["rank"].le(2)]
        hit=int(chosen.label.eq(1).sum())
        neutral=int(chosen.label.eq(0).sum())
        critical=int(chosen.label.eq(-1).sum())
        pool_hit=group.label.eq(1).mean()
        pool_pass=group.label.ne(-1).mean()
        result.append({"训练窗口":key[0],"训练法":key[1],"训练价格":key[2],
                       "验证价格":key[3],"评价范围":scope,
                       "测试日数":group.signal_date.nunique(),"候选总数":len(group),
                       "前二总数":len(chosen),"达标":hit,"中性":neutral,
                       "严重错误":critical,"达标Lift":hit/len(chosen)/pool_hit,
                       "非负Lift":(hit+neutral)/len(chosen)/pool_pass})
    return result


def main():
    base=pd.read_csv(DATA,dtype={"symbol6":str})
    prices=pd.read_csv(PRICES,dtype={"symbol6":str})
    data=base.merge(prices,on=["next_date","symbol6"],validate="one_to_one")
    dates=sorted(data.signal_date.unique())
    if len(data)!=14292 or len(dates)!=40 or not data.signal_return.between(.03,.06).all():
        raise ValueError("Candidate pool changed")
    for price,col in PRICE_COLUMNS.items():
        ret=data[col]/data.entry_1440-1
        data[f"return_{col}"]=ret
        data[f"class_{col}"]=np.select([ret>.01,ret<0],[1,-1],default=0)
    scored=[]; picks=[]
    for window in WINDOWS:
        for ix in range(window,len(dates)):
            train_dates=dates[ix-window:ix]
            train=data[data.signal_date.isin(train_dates)]
            test=data[data.signal_date.eq(dates[ix])]
            x_train,x_test=prepare(train,test)
            for train_price,col in PRICE_COLUMNS.items():
                returns=train[f"return_{col}"].to_numpy()
                for method in ("硬标签","边界降权","模糊标签"):
                    q,weights=targets(returns,method)
                    if method == "模糊标签":
                        beta=fit_softmax(x_train,q,weights)
                        probabilities=softmax(x_test@beta.T,axis=1)
                    else:
                        model=LogisticRegression(C=C,max_iter=2000,solver="lbfgs")
                        classes=np.array([-1,0,1])[q.argmax(axis=1)]
                        # Keep the total weight unchanged, so C retains its meaning.
                        model.fit(x_train[:,1:],classes,
                                  sample_weight=weights/weights.mean())
                        probabilities=model.predict_proba(x_test[:,1:])
                    frame=test[["signal_date","next_date","symbol6","name",
                                "entry_1440"]].copy()
                    frame["window"]=window
                    frame["train_start"]=train_dates[0]
                    frame["train_end"]=train_dates[-1]
                    frame["method"]=method
                    frame["train_price"]=train_price
                    frame["p_negative"]=probabilities[:,0]
                    frame["p_neutral"]=probabilities[:,1]
                    frame["p_positive"]=probabilities[:,2]
                    frame["score"]=frame.p_positive-frame.p_negative
                    for val_price,vcol in PRICE_COLUMNS.items():
                        frame[f"return_{vcol}"]=test[f"return_{vcol}"].to_numpy()
                        frame[f"class_{vcol}"]=test[f"class_{vcol}"].to_numpy()
                    frame=frame.sort_values(["score","symbol6"],ascending=[False,True])
                    frame["rank"]=np.arange(1,len(frame)+1)
                    picks.append(frame.head(2))
                    for val_price,vcol in PRICE_COLUMNS.items():
                        piece=frame[["signal_date","window","method","train_price",
                                     "rank"]].copy()
                        piece["val_price"]=val_price
                        piece["label"]=frame[f"class_{vcol}"].to_numpy()
                        scored.append(piece)
            print(f"window {window}: {dates[ix]}",flush=True)
    full=pd.concat(scored,ignore_index=True)
    common=dates[max(WINDOWS):]
    summary=pd.DataFrame(summarize(full,"窗口全部折外日期")+
                         summarize(full[full.signal_date.isin(common)],"共同20个折外日期"))
    top2=pd.concat(picks,ignore_index=True)
    reference=pd.read_csv(REFERENCE,dtype={"symbol6":str})
    ref=reference[reference["rank"].le(2)][["window","train_price","signal_date","rank","symbol6"]]
    baseline=top2[top2.method.eq("硬标签")][["window","train_price","signal_date","rank","symbol6"]]
    baseline=baseline.merge(ref,on=["window","train_price","signal_date","rank"],
                            suffixes=("_new","_old"),validate="one_to_one")
    baseline_match=int((baseline.symbol6_new==baseline.symbol6_old).sum())
    OUT.mkdir(parents=True,exist_ok=True)
    summary.to_csv(OUT/"summary.csv",index=False)
    top2.to_csv(OUT/"daily_top2.csv",index=False)
    (OUT/"manifest.json").write_text(json.dumps({
        "methods":{"硬标签":"three classes with hard thresholds at 0% and 1%",
                   "边界降权":"hard classes; weight falls linearly from 1 to 0.5 within 0.2 percentage point of either threshold",
                   "模糊标签":"linear soft memberships across +/-0.2 percentage point at each threshold"},
        "score":"P(>1%)-P(<0%)", "C":C,"baseline_top2_match":baseline_match,
        "baseline_top2_total":len(baseline),"common_test_dates":common,
        "source_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (DATA,PRICES,REFERENCE)}},ensure_ascii=False,indent=2)+"\n")
    print('baseline match',baseline_match,len(baseline))
    print(summary[summary['评价范围'].eq('共同20个折外日期')].to_string(index=False))


if __name__=='__main__':
    main()
