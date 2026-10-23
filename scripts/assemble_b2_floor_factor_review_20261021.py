"""Join frozen trades, point-in-time market data and minute diagnostics."""
from decimal import Decimal, ROUND_HALF_UP
import json
import numpy as np
import pandas as pd
from scripts.prepare_b2_floor_factor_review_20261021 import ROOT,BASE,OUT,LEDGER,trades,sha,dump
from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.report_limit_rebound_segments_r18 import DEFINITIONS

FMT={'pct':'0.00%;[Red]-0.00%;0.00%','num':'0.00;[Red]-0.00;0.00','int':'0','text':'@'}
COLS=[]
def col(key,label,group,kind='num',desc='',known='D0收盘'):
    COLS.append(dict(key=key,label=label,group=group,format=FMT[kind],description=desc or label,known=known))

for key,label,kind,desc in [
 ('open_floor','开盘跌停','text','D2开盘价等于历史跌停价'),('close_floor','收盘跌停','text','D2收盘价等于历史跌停价'),
 ('floor_group','跌停分类','text','按D2开盘/收盘两列分组，盘中触及但两端不跌停不进入专项'),
 ('all_locked','全天封死','text','D2日线最高价等于跌停价；只描述价格，不能证明排队订单一定无法成交'),
 ('d2_open','D2开价','num','计划卖出日开盘价'),('d2_close_saved','D2收价','num','原回测计划卖价，未证明跌停时成交'),
 ('d2_ret','D2涨幅','pct','D2收盘/昨收−1'),('open_net','开卖净收益假设','pct','D2开价/原买价×0.999/1.001−1；假设能成交'),
 ('d2_floor','D2跌停价','num','历史ST状态及板块幅度，按分四舍五入')]:
    col(key,label,'D2结果',kind,desc,'D2开盘' if key in ('open_floor','d2_open','open_net') else 'D2收盘')
col('stock_name','名称（当前）','识别','text','当前简称只用于识别，不能作为历史ST或退市因子','当前查询')
col('industry','D0申万行业','市值行业','text','SW2021二级行业，begin≤D0<end；是历史所属行业，不是热点排名')
for k,l in [('total_mv_yi','D0总市值亿'),('float_mv_yi','D0流通市值亿'),('free_mv_yi','D0自由流值亿')]:
    col(k,l,'市值行业',desc='当日历史市值/1亿；总/流通缺当日值时用不晚于D0最近股本×D0收盘价；自由流值缺失不估算')
col('mv_source','市值来源','市值行业','text','区分当日历史值与历史股本估算，估算不使用未来股本')
col('share_age','股本距D0天','市值行业','int','市值采用的历史股本日期至D0的自然日数；当日直接值记0')
col('float_proxy_yi','原估算流值亿','市值行业',desc='原模型 exp(log_float_mv_proxy)/1亿，成交额/(换手率/100)，不是总市值')
col('pe_ttm','D0市盈率TTM','市值行业',desc='只取D0当日数据库历史值；缺失不沿用旧值')
col('pb_mrq','D0市净率MRQ','市值行业',desc='只取D0当日数据库历史值；财务口径可能事后修订，不保证原发布时间快照')
col('score','模型分数','D0量价',desc='冻结D0模型评分，不是经过校准的概率')
for k,l in [('open','D0开价'),('high','D0高价'),('low','D0低价'),('close','D0收价'),('preclose','D0昨收')]:col(k,l,'D0量价')
col('d0_volume_wan','D0成交量万股','D0量价',desc='日线volume股/1万')
col('d0_body','D0实体涨幅','D0量价','pct','D0收盘/D0开盘−1')
for k,l in [('first_open','首板开价'),('first_high','首板高价'),('first_low','首板低价'),('first_close','首板收价')]:col(k,l,'D0及首板',desc='本轮连板的第一天日K；不是D1买入日')
col('first_volume_wan','首板量万股','D0及首板',desc='本轮首板日线成交量/1万')
for k,l,kind in [('opened_after_touch','D0触板后打开','text'),('first_touch','D0首触分钟','text'),
 ('first_close_sealed','D0首封分钟','text'),('final_full_seal','D0持续封板起','text'),
 ('close_sealed_minutes','D0收在板上分钟','int'),('full_sealed_minutes','D0整分钟封板','int'),
 ('observed_reopens','D0可确认开板分钟','int'),('below_after_touch_minutes','D0触板后离板分钟','int'),
 ('amount_pm_fraction','D0下午额占比','pct'),('last30_amount_fraction','D0尾30分额占比','pct'),('minute_quality','D0分钟核验','text')]:
    desc={
      'opened_after_touch':'此前分钟已触板后出现低于涨停价，或同根开在板上后下探/触板后收低，可确认打开；仅首触根低价在板下且收在板上标分钟内顺序不明。否表示无可见离板证据，不证明秒级未炸板。',
      'first_touch':'首根最高价达到涨停价的分钟结束标签',
      'first_close_sealed':'首根收盘价在涨停价的分钟结束标签；不等同逐笔首封秒数',
      'final_full_seal':'最后一根最低价低于涨停价的分钟之后，全体后续分钟最低价都在涨停价的起始分钟；末根仍曾离板则空缺',
      'observed_reopens':'前根收在板上、本根最低价低于板价的分钟数；不是交易所逐笔炸板次数',
      'below_after_touch_minutes':'此前已触板且本分钟最低价低于板价的分钟数',
      'minute_quality':'必须240根完整分钟且OHLC与日线吻合，失败则不生成路径因子',
    }.get(k,l+'；基于D0完整240根分钟K')
    col(k,l,'D0涨停路径',kind,desc)

# All 51 frozen model factors, with human labels and original units documented.
labels={
 'board_streak_days':('原连板数','int','本轮首板至D0连续收盘涨停天数'),
 'streak_return':('本轮连板涨幅','pct','D0收盘/本轮首板前收盘−1'),
 'd0_return':('D0涨幅','pct','D0收盘/昨收−1'),
 'd0_open_gap':('D0开盘涨幅','pct','D0开盘/昨收−1'),
 'd0_range':('D0振幅','pct','(D0最高−最低)/昨收'),
 'd0_lower_shadow':('D0下影','pct','(min(开盘,收盘)−最低)/昨收'),
 'd0_close_position':('D0收盘位置','num','(收盘−最低)/(最高−最低)；一字板分母0保留空值'),
 'first_open_gap':('首板开盘涨幅','pct','本轮首板开盘/前收−1'),
 'first_range':('首板振幅','pct','本轮首板(最高−最低)/前收'),
 'first_one_price':('首板一字','int','本轮首板一字且有成交量'),
 'd0_turnover_pct':('D0换手率','pct','日线turn_ratio/100'),
 'd0_amount_100m':('D0成交额亿','num','D0成交金额元/1亿'),
 'd0_volume_ratio_first':('D0量对首板','num','D0成交量/首板成交量'),
 'd0_turnover_ratio_first':('D0换手对首板','num','D0换手率/首板换手率'),
 'first_turnover_pct':('首板换手率','pct','本轮首板换手率/100'),
 'market_limit_count':('市场涨停家数','int','D0基础日线样本池收盘涨停数'),
 'market_prev_limit_count':('昨日涨停家数','int','D0前交易日基础日线样本池涨停数'),
 'market_up_fraction':('市场上涨比例','pct','D0基础日线样本池上涨股票比例'),
 'market_median_return':('市场涨幅中位','pct','D0基础日线样本池涨幅中位数'),
 'limit_rate':('D0涨停幅度','pct','原特征历史涨跌停制度幅度'),
 'd0_one_price':('D0一字','int','D0高低价相同且有成交'),
 'range_to_limit':('振幅对涨停幅','num','D0振幅/涨停幅度'),
 'gap_to_limit':('开涨对涨停幅','num','D0开盘涨幅/涨停幅度'),
 'market_reseal_fraction':('市场连板延续率','pct','昨日涨停者今日继续收盘涨停的比例'),
 'amount_board_rank':('涨停池额分位','pct','D0涨停池成交额百分位'),
 'turnover_board_rank':('涨停池换手分位','pct','D0涨停池换手率百分位'),
 'log_float_mv_proxy':('估算流值对数','num','原51因子中的成交额/换手率估算流值自然对数'),
 'log_price':('股价对数','num','D0收盘价自然对数'),
}
percent_factors={'normal_return5','normal_return10','normal_turnover5','normal_volatility5','normal_volatility10','normal_range5','normal_drawdown5','board_turnover_sum','board_one_price_fraction','board_range_mean'}
scaled={'d0_turnover_pct','first_turnover_pct','normal_turnover5','board_turnover_sum'}
for key in FEATURES['segments']:
    if key in labels: label,kind,desc=labels[key]
    else:
        label,desc=DEFINITIONS[key];kind='pct' if key in percent_factors else ('int' if key in ('normal_days5','normal_days10','board_price_ok') else 'num')
    group='市场环境' if key.startswith('market_') or key.endswith('_rank') else ('板前及连板' if key in DEFINITIONS or key in ('board_streak_days','streak_return') else 'D0及首板')
    col(key,label,group,kind,desc)
for key,label in [('d1_open','D1开价'),('entry','原买价'),('d1_close','D1收价')]:col(key,label,'D1买入','num',known='D1收盘' if key=='d1_close' else 'D1买入时')
for key,label in [('open_gap','D1开盘涨幅'),('body5','D1四K实体'),('high5','D1四K最高涨幅'),('low5','D1四K最低涨幅'),('pullback5','D1四K回吐'),('close5ret','D1四K收盘涨幅')]:
    col(key,label,'D1买入','pct','原B2四根完整分钟K的冻结字段；字段名虽含5，实际observation_bars=4。实体=四K末收/首开−1，其余涨幅相对D0收盘；回吐=(四K高−末收)/D0收盘','D1前四K结束')
col('position5','D1四K收盘位置','D1买入',desc='(四K末收−四K低)/(四K高−四K低)',known='D1前四K结束')
col('entry_clock','买入时刻代理','D1买入','text','成交K开盘时刻=原分钟结束标签减1分钟，仍是模拟成交价代理','D1买入时')
col('d1_ret','D1全天涨幅','D1收盘','pct','D1收盘/D0收盘−1','D1收盘')
col('d1_turn','D1换手率','D1收盘','pct','D1日线换手率/100','D1收盘')
col('d1_vol_ratio','D1量对D0','D1收盘',desc='D1成交量/D0成交量',known='D1收盘')
col('d2_high','D2高价','D2结果',known='D2收盘')
col('d2_low','D2低价','D2结果',known='D2收盘')
col('d2_turn','D2换手率','D2结果','pct','D2日线换手率/100','D2收盘')
col('d2_vol_ratio','D2量对D1','D2结果',desc='D2成交量/D1成交量',known='D2收盘')
col('original_net','原收卖净收益假设','D2结果','pct','原R61费后收益，沿用D2收盘全额成交假设；本次不重算账户','D2收盘')

def main():
    s=trades(); prices=pd.read_csv(OUT/'prices.csv.gz').set_index(['stk_code','trade_date'])
    values=pd.read_csv(OUT/'valuation.csv.gz').set_index(['stk_code','trade_date'])
    prior=pd.read_csv(OUT/'valuation_prior.csv.gz').set_index(['stk_code','D0'])
    names=pd.read_csv(OUT/'names.csv.gz').set_index('stk_code').stock_name.to_dict()
    ind=pd.read_csv(OUT/'industry.csv.gz').fillna({'end_date':'9999-12-31'})
    st=pd.read_csv(OUT/'st.csv.gz').fillna({'end_date':'9999-12-31'})
    mins={(r['股票'],r['D0']):r for r in json.loads((OUT/'d0_minutes.json').read_text())}
    prior69=json.loads((ROOT/'docs/stock_automl_runs/20261021_b2_d2_limit_follow_r69/results.json').read_text())['rows']
    prior70=json.loads((ROOT/'docs/stock_automl_runs/20261021_b2_floor_minutes_r70/results.json').read_text())['rows']
    expected_open={(x['股票'][:6],x['D0']) for x in prior70 if x['V2放行']=='是' and x['开盘跌停']=='是'}
    expected_close={(x['股票'][:6],x['D0']) for x in prior69 if x['V2放行']=='是' and x['封板']=='跌停'}
    rows=[]
    for seq,r in enumerate(s.to_dict('records'),1):
        code,day=r['stk_code'],r['d0_date']; k=(code,day)
        d0=prices.loc[k];d1=prices.loc[(code,r['d1_date'])];d2=prices.loc[(code,r['卖出日'])]
        assert np.allclose([r[x] for x in ('open','high','low','close')],d0[['open','high','low','close']].astype(float),atol=.001,rtol=0)
        assert abs(d2.close-r['卖价'])<1e-8 and abs(d1.close-r['d1_close'])<1e-8
        status=st[(st.stk_code==code)&st.begin_date.le(r['卖出日'])&st.end_date.gt(r['卖出日'])]
        assert len(status)==1,(code,r['卖出日'])
        rate=.2 if code.startswith(('30','68')) else (.05 if status.iloc[0].st_type in ('S','Y') else .1)
        floor=float((Decimal(str(d2.preclose))*(1-Decimal(str(rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
        op=abs(d2.open-floor)<.001;cl=abs(d2.close-floor)<.001
        assert cl==(int(d2.is_limit_price)==2)
        r.update(sequence=seq,stock_name=names.get(code),open_floor='是' if op else '否',close_floor='是' if cl else '否',
            floor_group='开收均跌停' if op and cl else '仅开盘跌停' if op else '仅收盘跌停' if cl else '开收均非跌停',
            all_locked='是' if abs(d2.high-floor)<.001 else '否',d2_open=d2.open,d2_close_saved=d2.close,d2_high=d2.high,d2_low=d2.low,d2_floor=floor,
            d2_ret=d2.close/d2.preclose-1,open_net=d2.open/r['买价']*.999/1.001-1,original_net=r['费后收益'],
            d0_volume_wan=r['volume']/1e4,first_volume_wan=r['first_volume']/1e4,d0_body=r['close']/r['open']-1,float_proxy_yi=np.exp(r['log_float_mv_proxy'])/1e8,
            d1_ret=d1.close/d1.preclose-1,d1_turn=d1.turn_ratio/100,d1_vol_ratio=d1.volume/r['volume'],
            d2_turn=d2.turn_ratio/100,d2_vol_ratio=d2.volume/d1.volume)
        r['entry_clock']=(pd.Timestamp('2000-01-01 '+r['entry_time'])-pd.Timedelta(minutes=1)).strftime('%H:%M')
        industry=ind[(ind.stk_code==code)&ind.begin_date.le(day)&ind.end_date.gt(day)]
        assert len(industry)<=1
        r['industry']=industry.iloc[0].industry_name if len(industry) else None
        if k in values.index:
            v=values.loc[k];r.update(total_mv_yi=v.total_mv/1e8,float_mv_yi=v.float_mv/1e8,free_mv_yi=v.free_float_mv/1e8,mv_source='D0当日历史值',share_age=0,pe_ttm=v.pe_ttm,pb_mrq=v.pb_mrq)
        else:
            v=prior.loc[k];assert v.trade_date<=day
            r.update(total_mv_yi=v.total_share*r['close']/1e8,float_mv_yi=v.float_share*r['close']/1e8,free_mv_yi=None,mv_source='此前股本估算',share_age=(pd.Timestamp(day)-pd.Timestamp(v.trade_date)).days,pe_ttm=None,pb_mrq=None)
        m=mins[(r['symbol'],day)];r.update({key:m.get(key) for key in ['opened_after_touch','first_touch','first_close_sealed','final_full_seal','close_sealed_minutes','full_sealed_minutes','observed_reopens','below_after_touch_minutes','amount_pm_fraction','last30_amount_fraction']});r['minute_quality']=m['quality']
        for key in scaled:r[key]/=100
        rows.append(r)
    f=pd.DataFrame(rows)
    assert {(r['symbol'],r['d0_date']) for r in rows if r['open_floor']=='是'}==expected_open
    assert {(r['symbol'],r['d0_date']) for r in rows if r['close_floor']=='是'}==expected_close
    union=f.open_floor.eq('是')|f.close_floor.eq('是')
    assert union.sum()==67 and f.open_floor.eq('是').sum()==34 and f.close_floor.eq('是').sum()==52
    assert f.minute_quality.eq('完整且日K价一致').all()
    clean=json.loads(f.to_json(orient='records',force_ascii=False,double_precision=15))
    summary=dict(total=306,opening=34,closing=52,both=19,union=67,comparison=239,
      minute_quality=f.minute_quality.value_counts().to_dict(),opened=f.opened_after_touch.value_counts().to_dict(),
      valuation=f.mv_source.value_counts().to_dict(),max_share_age=int(f.share_age.max()),industry_missing=int(f.industry.isna().sum()))
    dictionary=[dict(c,nonmissing=int(f[c['key']].notna().sum()),model_factor=c['key'] in FEATURES['segments']) for c in COLS]
    dump(OUT/'workbook_data.json',dict(rows=clean,columns=dictionary,summary=summary))
    f[['sequence','symbol','d0_date','d1_date','卖出日']+[c['key'] for c in COLS]].to_csv(OUT/'all_trade_factors.csv.gz',index=False,compression='gzip')
    frozen_paths=[BASE/'signals.pkl',LEDGER,OUT/'valuation.csv.gz',OUT/'valuation_prior.csv.gz',OUT/'prices.csv.gz',OUT/'names.csv.gz',OUT/'industry.csv.gz',OUT/'st.csv.gz',OUT/'d0_minutes.json']
    dump(OUT/'audit.json',dict(summary=summary,all_original_entries_and_exits_match=True,all_D0_OHLC_match=True,prior_open_close_sets_exact=True,
      original_model_factors=51,exported_factors=len(COLS),new_training=False,production_modified=False,
      sources=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p)) for p in frozen_paths]))
    print(json.dumps(summary,ensure_ascii=False));print('columns',len(COLS))

if __name__=='__main__':main()
