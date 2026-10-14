"""Human-readable CO4 factor table and month-by-month association heatmap."""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from scripts.build_automl_close_open_factor_panel import DATA, REPORT


NAMES = {
    'signal_return':'当日涨幅','volume_ratio':'量比','turnover_so_far_pct':'截至14:40换手率',
    'float_mv_100m_cny':'流通市值代理','volume_4of5_increasing':'历史成交量递增',
    'ma_bull_5_10_20':'均线多头排列','all_intraday_lows_above_ma':'日内低点高于均线',
    'open_gap':'早盘跳空','from_open':'开盘后涨幅','lag_return':'昨日涨幅',
    'lag_close_position':'昨日收盘位置','lag_upper_shadow':'昨日上影线',
    'prior5_return':'前5日涨幅','prior10_return':'前10日涨幅','prior20_return':'前20日涨幅',
    'prior5_volatility':'前5日波动','prior20_volatility':'前20日波动','prior5_range':'前5日振幅',
    'prior5_contract_fraction':'前5日缩量比例','relative_turnover5':'相对5日换手率',
    'relative_turnover20':'相对20日换手率','distance_ma5':'距MA5','distance_ma20':'距MA20',
    'ma5_over_ma10':'MA5/MA10间距','ma10_over_ma20':'MA10/MA20间距',
    'day_max_drawdown':'日内最大回撤收益','day_high_age':'距日内高点时间',
    'day_low_age':'距日内低点时间','day_volume_hhi':'日内成交量集中度','day_up_fraction':'日内上涨分钟占比',
    'morning_return':'上午涨幅','afternoon_return':'下午涨幅','afternoon_volume_fraction':'下午成交量占比',
}
METRICS = {'return':'涨幅','range':'振幅','efficiency':'路径效率','realized_vol':'波动',
           'close_position':'价格位置','up_volume_fraction':'上涨量占比','volume_fraction':'成交量占比',
           'vwap_distance':'距VWAP','volume_acceleration':'量加速'}


def title(name):
    if name in NAMES:
        return NAMES[name]
    head,rest = name.split('_',1)
    prefix = '日内' if head == 'day' else f'尾盘{head[4:]}分钟'
    return prefix+METRICS.get(rest,rest)


def run():
    stats = pd.read_csv(REPORT/'factor_summary.csv')
    screen = pd.read_csv(REPORT/'discovery_screen.csv')
    rows = []
    for item in screen.sort_values('discovery_effect_pp',ascending=False).itertuples(index=False):
        parts = stats[stats.factor.eq(item.factor)].set_index('phase')
        a,b,c = (parts.loc[p] for p in ('discovery','validation','forward'))
        rows.append({'因子':title(item.factor),'有利方向':'较高' if item.direction == 1 else '较低',
                     '发现差值_百分点':a.quality_lift_pp,'验证差值_百分点':b.quality_lift_pp,
                     '验证同向月':f'{int(b.positive_months)}/{int(b.months)}',
                     '前推差值_百分点':c.quality_lift_pp,
                     '前推同向月':f'{int(c.positive_months)}/{int(c.months)}',
                     '验证通过':bool(b.supported),'两段均通过':bool(b.supported and c.supported),
                     '代码名':item.factor})
    pd.DataFrame(rows).round(3).to_csv(REPORT/'factor_overview_zh.csv',index=False,encoding='utf-8-sig')
    monthly = pd.read_csv(REPORT/'factor_monthly.csv')
    reps = screen[screen.discovery_representative].sort_values('discovery_effect_pp',ascending=False).factor.tolist()
    table = monthly.pivot(index='factor',columns='month',values='quality_lift').reindex(reps)*100
    plt.rcParams['font.sans-serif'] = ['Arial Unicode MS','PingFang SC','DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig,ax = plt.subplots(figsize=(15,max(5,len(reps)*.3+2)))
    cap = max(5,float(np.nanquantile(np.abs(table.to_numpy()),.95)))
    im = ax.imshow(table.to_numpy(),aspect='auto',cmap='RdBu',vmin=-cap,vmax=cap)
    ax.set_xticks(range(len(table.columns)),[m[2:] for m in table.columns],rotation=60,ha='right')
    ax.set_yticks(range(len(reps)),[title(n) for n in reps],fontsize=9)
    for cut in (5.5,11.5):
        ax.axvline(cut,color='black',lw=1.5)
    ax.set_title('CO4：发现集固定方向，逐月检查关联\n强势率增益 − 弱势率变化（百分点）；行按发现集证据排序',pad=15)
    fig.colorbar(im,ax=ax,label='正值：有利方向的强弱差更高',fraction=.025,pad=.02)
    fig.text(.01,.015,'2025年1—6月发现；7—12月验证；2026年前推。10月未完结。指标为标签关联，非交易收益。',fontsize=10)
    fig.tight_layout(rect=(0,.04,1,1))
    fig.savefig(REPORT/'monthly_factor_association.png',dpi=160)
    plt.close(fig)
    # Amount / volume should lie inside the corresponding price range. This
    # diagnoses source rounding/inconsistency, without filtering on outcomes.
    panel = pd.read_csv(DATA/'factor_panel.csv.gz',float_precision='round_trip')
    quality = []
    for prefix in ('day','last5','last15','last30','last60'):
        span = panel[f'{prefix}_range']*panel.preclose
        low = panel.signal_price-panel[f'{prefix}_close_position']*span
        high = low+span
        vwap = panel.signal_price/(1+panel[f'{prefix}_vwap_distance'])
        error = (low-vwap).clip(lower=0)+(vwap-high).clip(lower=0)
        d = pd.DataFrame({'month':panel.signal_date.str[:7],'error':error,
                          'relative_error':error/panel.signal_price})
        for month,g in d.groupby('month'):
            quality.append({'month':month,'window':prefix,'rows':len(g),
                            'known':int(g.error.notna().sum()),
                            'outside_by_over_one_cent':int(g.error.gt(.01000001).sum()),
                            'outside_by_over_half_percent':int(g.relative_error.gt(.005).sum()),
                            'relative_error_p99':g.relative_error.quantile(.99),
                            'relative_error_max':g.relative_error.max()})
    pd.DataFrame(quality).to_csv(REPORT/'vwap_source_quality_monthly.csv',index=False)


if __name__ == '__main__':
    run()
