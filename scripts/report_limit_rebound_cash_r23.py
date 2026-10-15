"""Compact Chinese account tables for the user's two-part cash rotation."""
import json
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import OUT,dump


def main(out=OUT,fee_mode=False):
    OUT=out
    s=json.loads((OUT/'summary.json').read_text());m=json.loads((OUT/'monthly.json').read_text())
    n=pd.read_pickle(OUT/'daily_nav.pkl');l=pd.read_pickle(OUT/'ledger.pkl')
    notes=[
        '沿用原6训2验月度形态规则＋前五分钟实体>−8%；Top3按原时序选择，新增实体门槛拦截后当天不替补。无重训。',
        '资金：初始100万元；早晨空仓时买入现有现金一半，仍持有昨日股票时买入剩余全部现金；金额随盈亏变化，无借款。',
        'D1早晨买、D2收盘卖：D2是买入后的下一交易日。先买后卖；当天收盘卖出资金从次晨可用，重复同股按不同批次记账。',
        '买点是原09:36—09:40标签分钟开价代理；09:36标签对应09:35—09:36这一分钟。已观察完整前五分钟后才执行。',
        '允许零碎股；假设买价及D2收盘价可全额成交，未扣手续费、税费、滑点，未模拟资金量的冲击或涨跌停排队成交。',
        '2026年1月起，首个交易日01-05；现有行情截至10-14，10月不完整。末笔10-14买入未到D2，按10-14收盘估值，卖出日/价留空。',
        '账户收益按每日现金＋持仓收盘市值计算；跨月持仓收益分到实际持有月份。月收益=月末资产/上月末资产−1。',
        '回撤均为收盘净资产回撤，不是盘中最大损失；月内回撤从上月末资产重新起峰。全期回撤可能跨越多个月。',
        '夏普=全体交易日日收益均值/样本标准差×√252，无风险收益设0，包含无交易日；各月夏普同法年化，小样本波动较大。',
        '买卖明细的待D2行：收益和盈亏是截至期末的浮动值，期末估价单列；已卖行是实际模拟卖出值。金额列单位万元，价格元。',
        '该月数据不参与当月模型及原形态规则选择；历史区间和−8%门槛此前已研究，本次是既定策略资金回放，不是新盲测。',
        '已核对106笔分钟买价、422项日线价格和186日现金资产；数据异常排除0笔。旧固定每笔50万元试算作废，以本R23B为准。',
    ]
    overview=[
        ('统计区间','2026-01-01至10-14','10月未结束；行情截止10-14'),
        ('初始资金万',100.,'两份资金轮换'),('期末资产万',s['final_nav']/1e4,'现金＋收盘持仓市值'),
        ('总盈利万',s['total_profit']/1e4,'含未平仓浮盈'),('总收益',s['total_return'],'期末资产/初始资金−1'),
        ('最大回撤',s['max_drawdown'],f"收盘资产；{s['drawdown_peak_date']}至{s['drawdown_trough_date']}"),
        ('夏普',s['sharpe'],'252交易日年化；无风险收益0'),
        ('已实现万',s['realized']/1e4,'105笔已卖出盈亏'),('浮盈万',s['unrealized']/1e4,'末笔按10-14收盘计价'),
        ('期末现金万',s['closing_cash']/1e4,'卖出所得已计入'),('持仓市值万',s['closing_market_value']/1e4,'1笔待D2卖出'),
        ('买入笔数',s['entries'],'信号全部执行'),('空仓半买',s['half_cash_entries'],'买前无持仓'),
        ('余款全买',s['all_cash_entries'],'买前仍有昨日持仓'),('资金漏买',s['cash_skips'],'因可用现金为0漏买'),
        ('最多持仓',s['max_positions'],'当天买入后、收盘卖出前'),('已平胜率',s['closed_win_rate'],'已卖出且收益>0的占比'),
    ]
    if fee_mode:
        notes[1]='资金：初始100万元；空仓时使用现有现金一半，仍有昨日持仓时使用全部余款。该现金预算包含买入手续费；成交金额=预算/1.001，无透支。'
        notes[4]='费用：每次买入和每次卖出分别按成交金额收0.1%，卖出净回款=成交金额×0.999；按用户统一费用假设，不另加税费、最低佣金或滑点。允许零碎股，假设可全额成交。'
        notes[9]='买卖明细的净盈亏/净收益：已卖行扣双边费用；待D2行含已付买费，按期末收盘估值，尚未发生的卖费留空且未预扣。买入支出包含买费，卖出回款为扣费后金额。'
        notes[-1]='复用R23B已核验的106个信号及价格；零费回放逐日复现旧净值，再独立核对费用、预算、现金和收益。R23C为买卖各0.1%的费后版本。'
        overview.insert(5,('手续费万',s['total_fees']/1e4,'106次买费＋105次卖费；末笔未卖不预扣卖费'))
        overview.insert(6,('单边费率',s['fee_rate'],'每次买入和每次卖出分别收取'))
    monthly=[{'月份':x['month'],'买入笔':x['entries'],'月初资产万':x['opening_nav']/1e4,
        '月末资产万':x['closing_nav']/1e4,'月盈亏万':x['profit']/1e4,'月收益':x['return_'],
        '月内回撤':x['drawdown'],'夏普':x['sharpe'],**({'手续费万':x['fees']/1e4} if fee_mode else {})} for x in m]
    daily=[{'日期':r.date,'买入万':r.buy/1e4,'卖出回款万':r.sales/1e4,'现金万':r.cash/1e4,
        '持仓市值万':r.market_value/1e4,'总资产万':r.nav/1e4,'日收益':r.return_,
        '全期回撤':r.drawdown,'持仓笔':r.positions,**({'手续费万':r.fees/1e4} if fee_mode else {})} for r in n.itertuples()]
    if fee_mode:
        daily=[{({'买入万':'买入支出万','卖出回款万':'卖出净款万'}.get(k,k)):v for k,v in r.items()} for r in daily]
    trades=[]
    for r in l.itertuples():
        ret=r.return_ if pd.notna(r.exit_date) else r.mark_price*r.shares/r.cost-1
        pnl=r.pnl if pd.notna(r.exit_date) else r.shares*r.mark_price-r.cost
        trades.append({'日期':r.entry_date,'买点':r.entry_time,'D0排名':r.d0_rank,'用款':r.allocation,
            '买前现金万':r.cash_before/1e4,'买入万':r.cost/1e4,'买入价':r.buy,'股数':r.shares,
            '卖出日':r.exit_date,'卖出价':r.sell,'期末估价':None if pd.notna(r.exit_date) else r.mark_price,
            '盈亏万':pnl/1e4,'收益':ret,'状态':'已卖' if pd.notna(r.exit_date) else '待D2'})
        if fee_mode:
            trades[-1]={({'买入万':'买入支出万','盈亏万':'净盈亏万','收益':'净收益'}.get(k,k)):v for k,v in trades[-1].items()}
            trades[-1].update({'买费元':r.buy_fee,'卖费元':r.sell_fee})
    data={'资金总览':[{'指标':k,'数值':v,'口径':t} for k,v,t in overview],
          '月度表现':monthly,'每日资金':daily,'买卖明细':trades}
    clean=lambda x:json.loads(pd.DataFrame(x).to_json(orient='records',force_ascii=False,double_precision=15))
    dump(OUT/'workbook_data.json',{k:clean(v) for k,v in data.items()});dump(OUT/'workbook_notes.json',notes)
    tag='R23C' if fee_mode else 'R23B'
    doc=OUT.parents[2]/('docs/stock_automl_runs/20261015_limit_rebound_fee001_r23c' if fee_mode else 'docs/stock_automl_runs/20261015_limit_rebound_cash_rotation_r23b')
    doc.mkdir(parents=True,exist_ok=True)
    table='\n'.join(f"|{x['month']}|{x['entries']}|{x['profit']/1e4:+.2f}|{x['return_']:+.2%}|{x['drawdown']:.2%}|{x['sharpe']:.2f}|" for x in m)
    (doc/'README.md').write_text(f'''# {tag}：100万元、两份资金轮换、D2收盘卖出{'、买卖各0.1%费用' if fee_mode else ''}

## 结果

截至2026-10-14，期末资产 **{s['final_nav']/1e4:.2f}万元**，总盈利 **{s['total_profit']/1e4:.2f}万元**，总收益 **{s['total_return']:.2%}**；收盘最大回撤 **{s['max_drawdown']:.2%}**，年化夏普 **{s['sharpe']:.2f}**。

现金{s['closing_cash']/1e4:.2f}万元、末笔持仓市值{s['closing_market_value']/1e4:.2f}万元；已实现盈利{s['realized']/1e4:.2f}万元、浮盈{s['unrealized']/1e4:.2f}万元。106笔买入、105笔卖出、1笔待D2；47次空仓用一半现金，59次持仓时用剩余全部现金。无资金漏买，最多同时2笔。单次现金支出{s['min_entry']/1e4:.2f}万至{s['max_entry']/1e4:.2f}万元。
{'共支付手续费'+format(s['total_fees']/1e4,'.4f')+'万元；扣费改变后续资金与股数，期末资产差不等于仅从旧期末资产减去费用。' if fee_mode else ''}

## 月度账户结果

|月份|买入笔|盈亏万|月收益|月内回撤|年化夏普|
|---|---:|---:|---:|---:|---:|
{table}

全期最大回撤从{s['drawdown_peak_date']}高点至{s['drawdown_trough_date']}低点，跨月计算；月内回撤各自重新起峰，不能用最大单月值代替全期值。

## 口径与边界

{chr(10).join('- '+x for x in notes)}

## 复现及材料

{'`python -m scripts.replay_limit_rebound_fees_r23c` → `python -m scripts.verify_limit_rebound_fees_r23c`；报告调用 report_limit_rebound_cash_r23.main(out, fee_mode=True)。' if fee_mode else '`python -m scripts.replay_limit_rebound_cash_r23` → `python -m scripts.verify_limit_rebound_cash_r23` → `python -m scripts.report_limit_rebound_cash_r23`。'}

运行目录：`{OUT}`；保存冻结规格、106条原始信号、逐笔账本、每日资金、汇总、核验及Excel。日线模型/规则继承R18/R19/R21B，哈希见specification.json。无新模型训练；本地已保存，云端未同步。

用户更正前的固定50万元试算位于`limit_rebound_cash_r23_20261015`，已作废，不用于本结论。
''')


if __name__=='__main__':main()
