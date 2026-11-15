"""导出本研究的回归森林为纯数组并使用 NumPy 推理；仅导出阶段读取原 joblib。"""
from pathlib import Path
import sys,numpy as np,pandas as pd

def export(src,dst):
 import joblib
 b=joblib.load(src);p=b['model'];imp=p.named_steps['simpleimputer'];forest=p.named_steps['randomforestregressor'];a={'features':np.array(b['features'],dtype='U'),'statistics':imp.statistics_,'indicator_features':imp.indicator_.features_,'tree_count':np.array([len(forest.estimators_)])}
 for i,t in enumerate(forest.estimators_):
  tree=t.tree_;a[f'left_{i}']=tree.children_left;a[f'right_{i}']=tree.children_right;a[f'feature_{i}']=tree.feature;a[f'threshold_{i}']=tree.threshold;a[f'value_{i}']=tree.value[:,0,0]
 np.savez_compressed(dst,**a)

def predict(path,frame):
 with np.load(path,allow_pickle=False) as a:
  values=frame[list(a['features'])].to_numpy(dtype=float);values[~np.isfinite(values)]=np.nan;missing=np.isnan(values);stats=a['statistics'];keep=~np.isnan(stats);x=values[:,keep].copy();x[np.isnan(x)]=np.broadcast_to(stats[keep],x.shape)[np.isnan(x)];x=np.column_stack((x,missing[:,a['indicator_features']])).astype(np.float32);total=np.zeros(len(x),dtype=float)
  for i in range(int(a['tree_count'][0])):
   left=a[f'left_{i}'];right=a[f'right_{i}'];feature=a[f'feature_{i}'];threshold=a[f'threshold_{i}'];value=a[f'value_{i}'];nodes=np.zeros(len(x),dtype=np.int64);active=left[nodes]!=-1
   while active.any():
    rows=np.flatnonzero(active);n=nodes[rows];nodes[rows]=np.where(x[rows,feature[n]]<=threshold[n],left[n],right[n]);active=left[nodes]!=-1
   total+=value[nodes]
  return total/int(a['tree_count'][0])

if __name__=='__main__':
 if sys.argv[1]=='export':export(sys.argv[2],sys.argv[3])
 else:raise SystemExit('用法：python 通用树推理.py export 模型.joblib 输出.npz；predict() 接受数据帧')
