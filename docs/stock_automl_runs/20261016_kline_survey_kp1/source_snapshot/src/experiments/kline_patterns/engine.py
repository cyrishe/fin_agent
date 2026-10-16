"""Small formula evaluator and causal shared features for the pattern research catalog.

Expressions are data, not Python eval: only named series with nonpositive offsets,
comparisons, arithmetic, Boolean operations, abs/min/max are supported.
"""
from __future__ import annotations
import ast
from functools import lru_cache
import operator
import numpy as np
import pandas as pd
from src.services.technical_indicator_calculator import calculate_standard_technical_features

REVISION = 'daily_pattern_lab_v2'
OPS = {ast.Add:operator.add, ast.Sub:operator.sub, ast.Mult:operator.mul,
       ast.Div:operator.truediv, ast.BitAnd:operator.and_, ast.BitOr:operator.or_,
       ast.Lt:operator.lt, ast.LtE:operator.le, ast.Gt:operator.gt,
       ast.GtE:operator.ge, ast.Eq:operator.eq, ast.NotEq:operator.ne}

@lru_cache(maxsize=1024)
def parse(expr: str):
    return ast.parse(expr, mode='eval').body


def evaluate(expr: str, features: pd.DataFrame, at: int | None = None):
    """Vector scan or single completed-bar check with identical expression semantics."""
    def run(n):
        if isinstance(n, ast.Constant) and type(n.value) in (int,float,bool): return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub,ast.UAdd)):
            v=run(n.operand); return -v if isinstance(n.op,ast.USub) else v
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name):
            offset=run(n.slice)
            if type(offset) is not int or offset>0: raise ValueError('only integer offsets <= 0 allowed')
            name=n.value.id
            if name not in features: raise ValueError(f'unknown series: {name}')
            if at is None: return features[name].astype('Float64').shift(-offset)
            pos=at+offset
            return float(features[name].iloc[pos]) if pos>=0 and pd.notna(features[name].iloc[pos]) else pd.NA
        if isinstance(n,ast.BinOp) and type(n.op) in OPS:
            a,b=run(n.left),run(n.right)
            if isinstance(n.op,ast.Div):
                if isinstance(b,pd.Series): b=b.mask(b==0)
                elif pd.notna(b) and b==0: b=pd.NA
            return OPS[type(n.op)](a,b)
        if isinstance(n,ast.Compare):
            left=run(n.left); result=True
            for op,right_node in zip(n.ops,n.comparators):
                if type(op) not in OPS: raise ValueError('unsupported comparison')
                right=run(right_node); result=result & OPS[type(op)](left,right);left=right
            return result
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and not n.keywords:
            values=[run(v) for v in n.args]
            if n.func.id=='abs' and len(values)==1:return abs(values[0])
            if n.func.id in ('min','max') and values:
                if any(isinstance(v,pd.Series) for v in values):
                    frame=pd.concat([v if isinstance(v,pd.Series) else pd.Series(v,index=features.index,dtype='Float64') for v in values],axis=1)
                    return getattr(frame,n.func.id)(axis=1,skipna=False).astype('Float64')
                if any(pd.isna(v) for v in values):return pd.NA
                return (min if n.func.id=='min' else max)(values)
        raise ValueError(f'unsupported formula node: {type(n).__name__}')
    return run(parse(expr))


def detect(pattern, features, at=None):
    checks=[evaluate(item['expr'],features,at) for item in pattern['conditions']]
    valid=evaluate('valid[0] > 0',features,at)
    result=valid
    for check in checks: result=result & check
    return result


def prepare(bars:pd.DataFrame)->pd.DataFrame:
    required=['open','high','low','close','volume']
    if not isinstance(bars.index,pd.DatetimeIndex) or not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError('unique ascending daily timestamps required')
    if not set(required)<=set(bars):raise ValueError('OHLCV columns required')
    b=bars[required].astype(float).copy()
    good=np.isfinite(b).all(axis=1)&(b[['open','high','low','close']]>0).all(axis=1)&(b.volume>0)
    good &= (b.high>=b[['open','close','low']].max(axis=1)) & (b.low<=b[['open','close','high']].min(axis=1))
    # Invalid/suspended source rows remain gaps and reset recursive warmup; never compress them away.
    b.loc[~good,required]=np.nan
    f=calculate_standard_technical_features(b)
    f=f.rename(columns={'macd_dif':'dif','macd_dea':'dea','macd_hist':'hist','rsi14':'rsi',
                        'atr14':'atr','boll_upper20':'bu','boll_lower20':'bl','boll_mid20':'bm','boll_width20':'bw'})
    for src,dst in zip(required,['o','h','l','c','v']):f[dst]=b[src]
    f['valid']=good.astype(float)
    f['body']=(f.c-f.o).abs();f['range']=f.h-f.l
    f['upper']=f.h-f[['o','c']].max(axis=1,skipna=False)
    f['lower']=f[['o','c']].min(axis=1,skipna=False)-f.l
    f['body_avg']=f.body.shift(1).rolling(20,min_periods=20).mean()
    f['vbase']=f.v.shift(1).rolling(20,min_periods=20).mean()
    f['v5']=f.v.shift(1).rolling(5,min_periods=5).mean()
    f['ph20']=f.h.shift(1).rolling(20,min_periods=20).max()
    f['pl20']=f.l.shift(1).rolling(20,min_periods=20).min()
    f['ph60']=f.h.shift(1).rolling(60,min_periods=60).max()
    f['pl60']=f.l.shift(1).rolling(60,min_periods=60).min()
    f['ma30']=f.c.rolling(30,min_periods=30).mean()
    f['bw_q20']=f.bw.shift(1).rolling(120,min_periods=120).quantile(.2)
    f['turn']=pd.to_numeric(bars.get('turn_ratio',pd.Series(np.nan,index=bars.index)),errors='coerce')
    rsv=100*(f.c-f.l.rolling(9).min())/(f.h.rolling(9).max()-f.l.rolling(9).min()).replace(0,np.nan)
    # KDJ local convention: first mature RSV seeds K=D=50, then K=(2K+RSV)/3, D=(2D+K)/3.
    ks=[];ds=[];k=d=50.
    for x in rsv:
        if pd.isna(x):ks.append(np.nan);ds.append(np.nan);k=d=50.;continue
        k=(2*k+x)/3;d=(2*d+k)/3;ks.append(k);ds.append(d)
    f['k']=ks;f['d']=ds;f['j']=3*f.k-2*f.d
    return add_pivots(f)


def add_pivots(f):
    """Five-bar strict pivots become visible two bars later. Never write back at the pivot."""
    n=len(f);cols={}
    for side in ('lo','hi'):
        for key in ('p1','p2','p3','i1','i2','i3','dif1','dif2','rsi1','rsi2','neck','neck_inner','neck3','event','sep','age','atr2','prior_move_atr'):
            cols[f'{side}_{key}']=np.full(n,np.nan)
    for side in ('rlo','rhi'):
        for key in ('p1','p2','neck','sep','age'):
            cols[f'{side}_{key}']=np.full(n,np.nan)
    histories={s:[] for s in ('lo','hi','rlo','rhi')}
    values={s:f[key].to_numpy(float) for s,key in [('lo','l'),('hi','h'),('rlo','rsi'),('rhi','rsi')]}
    for t in range(n):
        for side,arr in values.items():
            low=side.endswith('lo'); hist=histories[side];q=t-2;event=False
            if q>=2:
                seg=arr[q-2:q+3]
                if np.isfinite(seg).all() and (arr[q]==(seg.min() if low else seg.max())) and np.sum(seg==arr[q])==1:
                    hist.append(q);event=True
            if len(hist)<2:continue
            a,z=hist[-2:];gap=z-a
            if gap<5 or gap>60 or t-z>30:continue
            # A missing bar anywhere in the pivot interval invalidates its geometry.
            if not np.isfinite(arr[a:t+1]).all():continue
            cols[side+'_p1'][t]=arr[a];cols[side+'_p2'][t]=arr[z]
            cols[side+'_sep'][t]=gap;cols[side+'_age'][t]=t-z
            if side.startswith('r'):
                cols[side+'_neck'][t]=(arr[a:z+1].max() if low else arr[a:z+1].min())
                continue
            opposite=f.h.to_numpy(float) if low else f.l.to_numpy(float)
            cols[side+'_neck'][t]=(opposite[a:z+1].max() if low else opposite[a:z+1].min())
            # The reversal neckline is between the pivots, not a wick on either pivot.
            # Keep the old series for frozen v1 formulas; v2 explicitly uses neck_inner.
            cols[side+'_neck_inner'][t]=(opposite[a+1:z].max() if low else opposite[a+1:z].min())
            if a>=11 and 'c' in f and np.isfinite(f.c.iloc[a-11:a]).all():
                scale=f.atr.iloc[a-1]
                if pd.notna(scale) and scale>0:
                    cols[side+'_prior_move_atr'][t]=(f.c.iloc[a-1]-f.c.iloc[a-11])/scale
            cols[side+'_event'][t]=float(event)
            cols[side+'_i1'][t]=a;cols[side+'_i2'][t]=z
            cols[side+'_atr2'][t]=f.atr.iloc[z]
            for ind in ('dif','rsi'):
                cols[side+'_'+ind+'1'][t]=f[ind].iloc[a];cols[side+'_'+ind+'2'][t]=f[ind].iloc[z]
            if len(hist)>=3:
                first=hist[-3]
                if z-first<=60 and a-first>=5 and np.isfinite(arr[first:t+1]).all():
                    cols[side+'_p3'][t]=arr[first];cols[side+'_i3'][t]=first
                    cols[side+'_neck3'][t]=(opposite[first:z+1].max() if low else opposite[first:z+1].min())
    return pd.concat([f,pd.DataFrame(cols,index=f.index)],axis=1)
