"""B2 business rule: prior-month NAV windows and a frozen D0-month switch.

A verified flat close is a scale-equivalent starting point under B2's fractional
shares, proportional fees and cash allocation. It is not a monthly cash reset.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math

V2_ID = 'B2_V2'
V2_SHA = '54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00'
V2_KEY = ('涨停冷静', 'v2.0')
FEE = .001


def digest(value):
    # MySQL binary JSON can round the final float digit. Fingerprint semantic
    # numbers at 12 significant digits; dates, identities and pause remain exact.
    def stable(x):
        if isinstance(x, float):
            if not math.isfinite(x):
                raise ValueError('非有限数不能进入证据哈希')
            return format(x, '.12g')
        if isinstance(x, dict):
            return {k:stable(v) for k,v in x.items()}
        if isinstance(x, (list,tuple)):
            return [stable(v) for v in x]
        return x
    return hashlib.sha256(json.dumps(stable(value), ensure_ascii=False, sort_keys=True,
                                    default=str, allow_nan=False).encode()).hexdigest()


def prior_month(month):
    return (datetime.fromisoformat(month + '-01') - timedelta(days=1)).strftime('%Y-%m')


def replay(days, trades, closes, initial=1_000_000.):
    """Morning purchase precedes D2 close sale. First day must be a flat seed."""
    cash = initial
    holdings = []
    nav = {days[0]: cash}
    for day in days[1:]:
        entries = [t for t in trades if t['buy_day'] == day]
        if len(entries) > 1:
            raise ValueError('影子账户每天只能买入一只')
        for trade in entries:
            budget = cash if holdings else cash / 2
            price = float(trade['buy_price'])
            if not math.isfinite(price) or price <= 0:
                raise ValueError('影子买价无效')
            shares = budget / (price * (1 + FEE))
            cash -= budget
            holdings.append({**trade, 'shares': shares})
        remaining = []
        for lot in holdings:
            if lot['exit_day'] <= day:
                if lot['exit_day'] != day or not lot.get('sell_price'):
                    raise ValueError('影子账户D2未结清，不能伪造已卖收益')
                price = float(lot['sell_price'])
                if abs(price - closes[(lot['symbol'], day)]) > .011:
                    raise ValueError('影子卖价与日线不一致')
                cash += lot['shares'] * price * (1 - FEE)
            else:
                remaining.append(lot)
        holdings = remaining
        value = cash + sum(lot['shares'] * closes[(lot['symbol'], day)] for lot in holdings)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('影子净值无效')
        nav[day] = value
    return nav


def calculate_gate(month, calendar, trades, closes, coverage, candidate_count,
                   decided_at, source):
    """coverage proves each D0 morning decision, including the last prior D0.

    Final prior-month candidate count is known after its D1 scan in the new
    month. Returns still stop at prior-month close. No future sales enter NAV.
    """
    previous = prior_month(month)
    month_days = [d for d in calendar if d.startswith(previous)]
    if len(month_days) < 10:
        raise ValueError('上月市场交易日历不足10日')
    end = calendar.index(month_days[-1])
    if end < 10:
        raise ValueError('缺窗口前一交易日')
    base10, base5 = calendar[end-10], calendar[end-5]
    cutoff = calendar[end]
    if any(d not in coverage for d in month_days):
        raise ValueError('上月影子D0记录或早盘最终判断不完整')
    if candidate_count != sum(t['d0'].startswith(previous) for t in trades):
        raise ValueError('上月最终候选计数不一致')
    # A flat close requires the preceding D0's verified no-entry result. D2
    # scheduled exits must also have actually settled when they precede seed.
    seeds = []
    for i, day in enumerate(calendar):
        if i < 2 or day > base10:
            continue
        if not all(calendar[j] in coverage for j in (i-2, i-1)):
            continue
        if any(t['buy_day'] <= day < t['exit_day'] for t in trades):
            continue
        if any(t['buy_day'] <= day and t['exit_day'] <= day and not t.get('sell_price') for t in trades):
            continue
        seeds.append(day)
    if not seeds:
        raise ValueError('窗口前没有可验证的空仓基点，不能随意重置账户')
    seed = seeds[-1]
    start = calendar.index(seed)
    needed_d0 = calendar[start:end]  # last D0's D1 is outside the NAV cutoff
    if any(d not in coverage for d in needed_d0):
        raise ValueError('影子重放区间缺早盘最终判断')
    relevant = [t for t in trades if seed < t['buy_day'] <= cutoff]
    for t in relevant:
        if calendar[calendar.index(t['d0'])+1] != t['buy_day'] or calendar[calendar.index(t['d0'])+2] != t['exit_day']:
            raise ValueError('影子交易D0/D1/D2不连续')
    nav = replay(calendar[start:end+1], relevant, closes)
    r5, r10 = nav[cutoff]/nav[base5]-1, nav[cutoff]/nav[base10]-1
    result = {'month': month, 'prior_month': previous, 'cutoff': cutoff,
              'window5': month_days[-5:], 'window10': month_days[-10:],
              'base5': base5, 'base10': base10, 'r5': r5, 'r10': r10,
              'sum_return': r5+r10, 'candidate_count': candidate_count,
              'paused': r5+r10 < 0 or candidate_count == 0,
              'flat_seed': seed, 'normalized_nav': nav,
              'decided_at': decided_at, 'definition_sha256': V2_SHA,
              'timing_note': '上月收盘收益；上月D0候选最终确认后、新月首次买入前冻结',
              'source': source}
    result['sha256'] = digest(result)
    return result


def check_gate(gate, d0, before=None):
    if gate.get('month') != d0[:7] or gate.get('definition_sha256') != V2_SHA:
        raise ValueError('月度开关归属或定义不一致')
    if gate.get('sha256') != digest({k:v for k,v in gate.items() if k != 'sha256'}):
        raise ValueError('月度开关哈希不符')
    if before is not None and datetime.fromisoformat(gate['decided_at']) >= before:
        raise ValueError('停摆决定晚于买入，禁止倒写信号')
    return gate['paused']
