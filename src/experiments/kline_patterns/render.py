"""One deterministic chart template for all actual matched and near-miss windows."""
from pathlib import Path
from threading import Lock
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.font_manager import FontProperties
import numpy as np
# Matplotlib's bundled font keeps server rendering independent of OS fonts.
FONT=FontProperties(family='DejaVu Sans')
_RENDER_LOCK = Lock()


def render(*args, **kwargs):
 # pyplot has process-global state; serialize only drawing, not model calls.
 with _RENDER_LOCK:
  return _render(*args, **kwargs)

def _render(pattern,f,at,code,path,display_bars=80,*,target_start=None,target_end=None,target_id='C1',annotate_values=False,compact=False,display_until=None):
 # Target indices come from the detector/caller, never from the display window.
 if target_start is not None:
  target_end=at if target_end is None else target_end
  if not 0<=target_start<=target_end<=at<len(f):raise ValueError('target must lie within the as-of series')
 display_until=at if display_until is None else display_until
 if not at<=display_until<len(f):raise ValueError('display end must include the signal and lie within the series')
 start=max(0,at-display_bars+1)
 if target_start is not None:start=min(start,max(0,target_start-2))
 v=f.iloc[start:display_until+1];x=np.arange(len(v));last=len(v)-1
 show_values=annotate_values and target_start is not None
 panels=[0,1]
 if not compact or pattern['family']=='MACD':panels.append(2)
 if not compact or pattern['family'] in ('RSI','指标组合'):panels.append(3)
 heights={0:4.2,1:1.4,2:1.6,3:1.4}
 fig,active=plt.subplots(len(panels),1,figsize=(12.8,3.5+1.5*(len(panels)-1)+(2 if show_values else 0)),dpi=110,sharex=True,gridspec_kw={'height_ratios':[heights[i] for i in panels],'hspace':.13})
 axes=dict(zip(panels,active))
 fig.patch.set_facecolor('#f5f7fb')
 for ax in active:
  ax.set_facecolor('white');ax.grid(alpha=.16);ax.spines[['top','right']].set_visible(False);ax.tick_params(labelsize=8)
 ax=axes[0]
 for j,(_,r) in enumerate(v.iterrows()):
  if not np.isfinite([r.o,r.h,r.l,r.c]).all():continue
  color='#d9534f' if r.c>r.o else '#159878' if r.c<r.o else '#6b7280'
  ax.vlines(j,r.l,r.h,color=color,lw=.9)
  height=max(abs(r.c-r.o),(v.h.max()-v.l.min())*.0008)
  ax.add_patch(Rectangle((j-.31,min(r.o,r.c)),.62,height,facecolor=color,edgecolor=color,lw=.5))
 expressions=' '.join(c['expr'] for c in pattern['conditions'])
 price_lines=[(key,color) for key,color in [('ma5','#c79011'),('ma20','#287fbe'),('ma60','#8c66bf'),('ma10','#159878'),('ma30','#bb6599')] if (key+'[' in expressions or ((not compact or pattern['family']=='基础') and key in ('ma5','ma20','ma60')))]
 for key,color in price_lines:ax.plot(x,v[key],color=color,lw=1,label=key.upper())
 if pattern['family']=='波动突破':
  ax.plot(x,v.bu,color='#778595',lw=.9,ls='--',label='BOLL ±2σ');ax.plot(x,v.bl,color='#778595',lw=.9,ls='--')
 focus=max(0,last-min(pattern['focus_bars'],10)+1)
 if not compact and target_start is None and pattern['focus_bars']<30:ax.axvspan(focus-.5,last+.5,color='#e9bc42',alpha=.12)
 # All chart titles are neutral so optional visual audit is not told the numeric verdict.
 ax.set_title(f'{code}  |  {v.index[0]:%Y-%m-%d} to {v.index[-1]:%Y-%m-%d}  |  Daily',fontproperties=FONT,fontsize=13,loc='left',pad=13)
 ax.set_ylabel('Adjusted price',fontproperties=FONT,fontsize=9)
 if price_lines or pattern['family']=='波动突破':ax.legend(loc='upper left',fontsize=8,ncol=4,frameon=False)
 if 'div_' in pattern['id'] or pattern['id'].startswith('double_'):
  side='lo' if ('bull' in pattern['id'] or 'bottom' in pattern['id']) else 'hi'
  a,z=f.iloc[at][[side+'_i1',side+'_i2']]
  if np.isfinite([a,z]).all():
   a,z=int(a),int(z)
   pricecol='l' if side=='lo' else 'h';ax.plot([a-start,z-start],[f[pricecol].iloc[a],f[pricecol].iloc[z]],color='#6a51a3',lw=1.5,marker='o',ms=3)
   if 2 in axes:axes[2].plot([a-start,z-start],[f.dif.iloc[a],f.dif.iloc[z]],color='#6a51a3',lw=1.2,ls='--')
   if 3 in axes:axes[3].plot([a-start,z-start],[f.rsi.iloc[a],f.rsi.iloc[z]],color='#6a51a3',lw=1.2,ls='--')
   if pattern['id'].startswith('double_'):
    neck=side+('_neck_inner' if '_neck_inner[' in expressions else '_neck')
    ax.axhline(f.iloc[at][neck],color='#6a51a3',lw=1,ls=':')
 colors=np.where(v.c>v.o,'#d9534f',np.where(v.c<v.o,'#159878','#6b7280'))
 axes[1].bar(x,v.v/1e4,width=.65,color=colors);axes[1].plot(x,v.vbase/1e4,color='#566475',lw=.8,label='Prior 20-day mean volume');axes[1].set_ylabel('Volume / 10k shares',fontproperties=FONT,fontsize=9)
 axes[1].legend(loc='upper left',prop=FONT,fontsize=7,frameon=False)
 annotations=[]
 # The reference line extends through the observed follow-through, not just the box.
 if pattern['id'].startswith('inside_break_'):
  edge=f.h.iloc[at-2] if pattern['id'].endswith('bull') else f.l.iloc[at-2]
  label='母线高点' if pattern['id'].endswith('bull') else '母线低点'
  ax.hlines(edge,at-2-start-.4,last+.4,color='#6a51a3',ls=':',lw=1)
  ax.annotate('Mother high' if pattern['id'].endswith('bull') else 'Mother low',(at-2-start,edge),xytext=(-6,-16),textcoords='offset points',ha='right',fontproperties=FONT,fontsize=8,color='#6a51a3',bbox={'facecolor':'white','alpha':.8,'edgecolor':'none','pad':1})
  annotations.append(label)
 if 2 in axes:
  axes[2].bar(x,v['hist'],width=.65,color=np.where(v['hist']>=0,'#d9534f','#159878'),alpha=.7)
  axes[2].plot(x,v.dif,color='#d18b08',lw=1,label='DIF 12,26');axes[2].plot(x,v.dea,color='#287fbe',lw=1,label='DEA 9');axes[2].axhline(0,color='#7e8998',lw=.6);axes[2].legend(fontsize=8,frameon=False,loc='upper left',ncol=2);axes[2].set_ylabel('MACD',fontsize=9)
 if 3 in axes:
  if pattern['id']=='kdj_high_turn':
   for k,color in [('k','#d18b08'),('d','#287fbe'),('j','#8c66bf')]:axes[3].plot(x,v[k],color=color,lw=1,label=k.upper())
   axes[3].axhline(80,color='#aaa',ls='--',lw=.6);axes[3].set_ylabel('KDJ 9',fontsize=9)
  else:
   axes[3].plot(x,v.rsi,color='#8654b5',lw=1.1,label='RSI14');axes[3].set_ylim(0,100);axes[3].set_ylabel('RSI14',fontsize=9)
   for level in (30,50,70):axes[3].axhline(level,color='#aaa',ls='--',lw=.6)
 ticks=np.arange(len(v)) if len(v)<=24 else np.unique(np.r_[np.linspace(0,last,12).astype(int),at-start,([] if target_start is None else [target_start-start,target_end-start])]);active[-1].set_xticks(ticks,[v.index[i].strftime('%m-%d') for i in ticks],fontsize=8,rotation=30 if len(v)>12 else 0)
 for a in active:a.axvline(last,color='#4b637b',lw=.7,ls=':');a.set_xlim(-1,last+1)
 boxes=[]
 if target_start is not None:
  left,right=target_start-start-.48,target_end-start+.48
  for panel,a in axes.items():
   if panel==0:
    segment=f.iloc[target_start:target_end+1]
    lo,hi=float(segment.l.min()),float(segment.h.max());pad=(a.get_ylim()[1]-a.get_ylim()[0])*.025
    bottom,top=lo-pad,hi+pad
   else:bottom,top=a.get_ylim()
   box=Rectangle((left,bottom),right-left,top-bottom,fill=False,edgecolor='#3046ae',lw=1.5,ls='--')
   a.add_patch(box);boxes.append(box)
  for label,index in [('A',target_start),('B',target_end)]:
   axes[0].annotate(f'{target_id}:{label}',(index-start,float(f.h.iloc[index])),xytext=(0,16 if label=='A' else 32),textcoords='offset points',ha='center',fontsize=9,color='#3046ae',arrowprops={'arrowstyle':'-','color':'#3046ae'})
  fig.text(.08,.975,f'{target_id} target | A {f.index[target_start]:%Y-%m-%d} → B {f.index[target_end]:%Y-%m-%d} | signal {f.index[at]:%Y-%m-%d}',fontproperties=FONT,fontsize=10,color='#3046ae')
  if display_until>at:
   for a in active:a.axvline(at-start+.5,color='#8c96a7',lw=.7,ls='--')
   ax.text(at-start+.7,.96,'Post-signal bars',transform=ax.get_xaxis_transform(),fontproperties=FONT,fontsize=9,color='#526171',va='top')
 value_records=[]
 if show_values:
  indices=list(range(target_start,target_end+1)) if target_end-target_start<5 else [target_start,target_end]
  cols=['o','h','l','c','v'] + (['vbase'] if compact else [])
  expressions=' '.join(c['expr'] for c in pattern['conditions'])
  if any(key+'[' in expressions for key in ['dif','dea','hist']):cols+=['dif','dea','hist']
  if 'rsi[' in expressions:cols+=['rsi']
  cols += [key for key in ['ma5','ma10','ma20','ma30','ma60','k','d','j'] if key+'[' in expressions]
  labels={'o':'Open','h':'High','l':'Low','c':'Close','v':'Vol (10k shares)','vbase':'Prior20 (10k)','dif':'DIF','dea':'DEA','hist':'DIF-DEA','rsi':'RSI14'}
  labels.update({key:key.upper() for key in cols if key not in labels})
  rows=[]
  for i in indices:
   label='A/B' if target_start==target_end else 'A' if i==target_start else 'B' if i==target_end else ''
   row=f.iloc[i];values={k:float(row[k]) if np.isfinite(row[k]) else None for k in cols}
   value_records.append({'label':label,'date':f.index[i].strftime('%Y-%m-%d'),**values})
   rows.append([label,f.index[i].strftime('%Y-%m-%d')]+['—' if values[k] is None else f'{values[k]/10000:.4f}' if k in ('v','vbase') else f'{values[k]:.4f}' for k in cols])
  table_height=.025*(len(rows)+1)
  table_ax=fig.add_axes([.08,.055,.89,table_height]);table_ax.axis('off')
  table=table_ax.table(cellText=rows,colLabels=['Point','Date']+[labels[k] for k in cols],loc='center',cellLoc='center',bbox=[0,0,1,1],colWidths=[.05,.13]+[.82/len(cols)]*len(cols))
  table.auto_set_font_size(False);table.set_fontsize(8);table.scale(1,1)
  for (r,c),cell in table.get_celld().items():cell.set_edgecolor('#d0d8e5');cell.set_facecolor('#eaf0fb' if r==0 else 'white')
  note='all target candles' if len(indices)==target_end-target_start+1 else 'target endpoints only'
  fig.text(.08,.055+table_height+.025,f'{target_id} same-source values | {note} | adjusted prices | volume: 10k shares',fontproperties=FONT,fontsize=9,color='#3046ae')
 footnote='Same-source adjusted daily bars | Red: close > open; green: close < open; grey: equal'
 if 2 in axes:footnote+=' | MACD histogram = DIF - DEA'
 if target_start is not None:footnote+=' | Box: target; following bars: observed evolution' if display_until>at else ' | Through signal date, no later bars'
 fig.text(.08,.018,footnote,fontproperties=FONT,fontsize=9,color='#526171')
 fig.subplots_adjust(left=.08,right=.97,top=.88 if boxes else .92,bottom=.055+table_height+.10 if show_values else .11 if len(v)>12 else .08)
 fig.savefig(path,facecolor=fig.get_facecolor())
 width,height=fig.canvas.get_width_height()
 metadata={'display_start':v.index[0].strftime('%Y-%m-%d'),'display_end':v.index[-1].strftime('%Y-%m-%d'),'display_bars':len(v),'panels':[('price','volume','MACD','RSI/KDJ')[i] for i in panels],'image_size':[width,height],'annotations':annotations}
 if boxes:
  metadata.update({'target_id':target_id,'start_date':f.index[target_start].strftime('%Y-%m-%d'),'end_date':f.index[target_end].strftime('%Y-%m-%d'),'confirmation_date':f.index[at].strftime('%Y-%m-%d'),'image_size':[width,height],'bbox_origin':'top-left; pixel xyxy; panels in metadata order','bboxes':[]})
  for box in boxes:
   b=box.get_window_extent();metadata['bboxes'].append([round(b.x0,2),round(height-b.y1,2),round(b.x1,2),round(height-b.y0,2)])
  if show_values:metadata['displayed_values']=value_records
 plt.close(fig)
 return metadata
