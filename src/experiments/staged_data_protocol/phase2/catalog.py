from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping


DATAVIEW_ALIASES = {
    "basic_info": "base_info",
    "qoute": "quote",
    "tech factors": "tech_factors",
    "margin_trade": "margin",
    "holder": "shareholder",
    "holders": "shareholder",
    "shareholders": "shareholder",
    "pledge_ratio": "pledge",
    "profitnotice": "performance_notice",
    "profit_notice": "performance_notice",
    "performance_forecast": "performance_notice",
    "capital_action": "corporate_action",
    "equity_event": "corporate_action",
    "segment": "business_segment",
    "hotness": "state",
    "intraday": "quote",
    "intraday_quote": "quote",
    "intraday_kline": "quote",
    "minute_quote": "quote",
    "minute_qoute": "quote",
    "minute_kline": "quote",
    "realtime_minute_quote": "quote",
    "realtime_minute_qoute": "quote",
}

STOCK_DATAVIEW_ALIASES = {
    "history_quote": "quote",
    "realtime_minute_qoute": "quote",
    "realtime_minute_quote": "quote",
    "intraday_quote": "quote",
}

REALTIME_STOCK_DATAVIEW_NAMES = {
    "realtime_minute_qoute",
    "realtime_minute_quote",
    "intraday",
    "intraday_quote",
    "intraday_kline",
    "minute_quote",
    "minute_qoute",
    "minute_kline",
}

HISTORY_STOCK_DATAVIEW_NAMES = {
    "history_quote",
}


def field(*aliases: str) -> Dict[str, List[str]]:
    return {"aliases": list(aliases)}


def stock_identity_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        "code": field("股票代码", "证券代码", "code"),
        "name": field("股票名称", "证券名称", "name"),
    }


def quote_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "tradedate": field("交易日期", "日期", "tradedate"),
        "preclose": field("昨收价", "前收盘价", "preclose"),
        "open": field("开盘价", "open"),
        "close": field("收盘价", "最新价", "close"),
        "high": field("最高价", "high"),
        "low": field("最低价", "low"),
        "avg_price": field("均价", "avg_price"),
        "differ": field("涨跌额", "differ"),
        "pct": field("涨幅", "涨跌幅", "pct"),
        "turn_ratio": field("换手率", "turn_ratio"),
        "amount": field("成交额", "amount"),
        "volumn": field("成交量", "volume", "volumn"),
    }


def stock_quote_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **quote_fields(),
        "amplitude": field("振幅", "amplitude"),
        "adjpreclose": field("复权昨收价", "adjpreclose"),
        "adjopen": field("复权开盘价", "adjopen"),
        "adjhigh": field("复权最高价", "adjhigh"),
        "adjlow": field("复权最低价", "adjlow"),
        "adjclose": field("复权收盘价", "adjclose"),
        "is_limit_price": field("是否涨跌停", "is_limit_price"),
    }


def intraday_quote_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "tradedate": field("交易日期", "日期", "trade_date"),
        "minute_index": field("分钟序号", "分钟索引"),
        "minute_time": field("分钟时间", "时间"),
        "snapshot_time": field("快照时间", "采样时间"),
        "snapshot_slot": field("分钟时点", "时刻"),
        "preclose": field("昨收价", "前收盘价"),
        "open": field("分钟开盘价", "开盘价"),
        "close": field("分钟收盘价", "最新价", "收盘价"),
        "high": field("最高价"),
        "low": field("最低价"),
        "differ": field("涨跌额"),
        "pct": field("涨跌幅", "涨幅"),
        "amount": field("分钟成交额", "成交额"),
        "volumn": field("分钟成交量", "成交量", "volume"),
        "minute_amount": field("分钟成交额", "amount"),
        "minute_volumn": field("分钟成交量", "volume", "minute_volume"),
    }


def intraday_quote_kd_methods() -> Dict[str, List[str]]:
    methods = ["avg", "max", "min", "median", "percentile"]
    return {
        "minute_amount": methods,
        "minute_volumn": methods,
        "amount": methods,
        "volumn": methods,
        "pct": methods,
        "close": methods,
    }


def unified_stock_quote_fields() -> Dict[str, Dict[str, List[str]]]:
    return {**stock_quote_fields(), **intraday_quote_fields()}


def unified_stock_quote_kd_methods() -> Dict[str, List[str]]:
    return {
        **quote_kd_methods(),
        "minute_amount": ["avg", "max", "min", "median", "percentile"],
        "minute_volumn": ["avg", "max", "min", "median", "percentile"],
    }


def fund_quote_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **quote_fields(),
        "nav_unit": field("单位净值", "nav_unit"),
        "amplitude": field("振幅", "amplitude"),
        "discount": field("折价率", "discount"),
        "unit_total": field("累计净值", "unit_total"),
    }


def bond_quote_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "tradedate": field("交易日期", "日期", "tradedate"),
        "preclose": field("昨收价", "前收盘价", "preclose"),
        "open": field("开盘价", "open"),
        "close": field("收盘价", "最新价", "close"),
        "high": field("最高价", "high"),
        "low": field("最低价", "low"),
        "pct": field("涨幅", "涨跌幅", "pct"),
        "turn_ratio": field("换手率", "turn_ratio"),
        "amount": field("成交额", "amount"),
        "volumn": field("成交量", "volume", "volumn"),
    }


def quote_kd_methods() -> Dict[str, List[str]]:
    return {
        "pct": ["sum", "max", "min", "avg", "median"],
        "amount": ["sum", "avg", "max", "min", "median"],
        "volumn": ["sum", "avg", "max", "min", "median"],
        "close": ["max", "min", "avg", "median", "high"],
        "amplitude": ["sum", "avg", "max", "min", "median"],
    }


def moneyflow_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "tradedate": field("交易日期", "日期"),
        "huge_buy": field("超大单流入"),
        "huge_sell": field("超大单流出"),
        "huge_ratio": field("超大单净流入占比"),
        "large_buy": field("大单流入"),
        "large_sell": field("大单流出"),
        "large_net": field("大单净流入"),
        "large_ratio": field("大单净流入占比"),
        "medium_buy": field("中单流入"),
        "medium_sell": field("中单流出"),
        "small_buy": field("小单流入"),
        "small_sell": field("小单流出"),
        "main_buy": field("主力流入"),
        "main_sell": field("主力流出"),
        "main_net": field("主力净流入", "主力资金流向"),
        "main_ratio": field("主力净流入占比"),
    }


def moneyflow_kd_methods() -> Dict[str, List[str]]:
    methods = ["sum", "avg", "max", "min", "median"]
    return {
        "large_net": methods,
        "main_net": methods,
    }


def pricevalue_kd_methods() -> Dict[str, List[str]]:
    return {
        "pe": ["percentile"],
        "pe_lyr": ["percentile"],
        "pe_ttm": ["percentile"],
        "pe_ttm_deduct": ["percentile"],
        "pe_dynamic": ["percentile"],
        "pb": ["percentile"],
        "pb_mrq": ["percentile"],
        "pb_lf": ["percentile"],
        "ps": ["percentile"],
        "ps_lyr": ["percentile"],
        "ps_ttm": ["percentile"],
        "pcf_cfottm": ["percentile"],
        "pcf_ncfttm": ["percentile"],
        "pcf_cfolyr": ["percentile"],
        "pcf_ncflyr": ["percentile"],
        "prr_lyr": ["percentile"],
        "market_value": ["percentile"],
        "free_float_mv": ["percentile"],
        "float_mv": ["percentile"],
        "total_share": ["percentile"],
        "float_share": ["percentile"],
        "turn_ratio": ["percentile"],
    }


def margin_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "tradedate": field("交易日期", "日期"),
        "financing_balance": field("融资余额"),
        "financing_buy": field("融资买入额"),
        "financing_repay": field("融资偿还额"),
        "financing_net_buy": field("融资净买入", "融资买入净额"),
        "securities_lending_balance": field("融券余额"),
        "securities_lending_balancevol": field("融券余量"),
        "securities_lending_sell_vol": field("融券卖出量"),
        "securities_lending_repay_vol": field("融券偿还量"),
        "margin_balance": field("融资融券余额", "两融余额"),
        "margin_diff": field("融资融券差额", "两融差额"),
        "financing_balance_float_mv_ratio": field("融资余额占流通市值比例"),
    }


def margin_kd_methods() -> Dict[str, List[str]]:
    methods = ["sum", "avg", "max", "min", "median", "change", "pct_change"]
    return {
        "financing_buy": methods,
        "financing_repay": methods,
        "financing_net_buy": methods,
        "securities_lending_sell_vol": methods,
        "securities_lending_repay_vol": methods,
        "financing_balance": methods,
        "securities_lending_balance": methods,
        "securities_lending_balancevol": methods,
        "margin_balance": methods,
        "margin_diff": methods,
        "financing_balance_float_mv_ratio": methods,
    }


def shareholder_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "source": field("来源", "数据来源", "source"),
        "ann_date": field("公告日期", "披露日期"),
        "end_date": field("截止日期", "报告截止日"),
        "report_period": field("报告期"),
        "holder_num": field("股东户数", "股东人数"),
        "holder_total_num": field("股东总户数"),
        "holder_name": field("股东名称"),
        "holder_id": field("股东ID"),
        "holder_type": field("股东类型"),
        "holder_rank": field("股东排名"),
        "holder_quantity": field("持股数量"),
        "holder_pct": field("持股比例", "持股占比"),
        "holder_restrictedquantity": field("限售持股数量"),
        "ple_or_frz_shares": field("质押或冻结股份"),
        "holder_accpledge": field("累计质押股份"),
        "holder_accfrozen": field("累计冻结股份"),
        "holder_pledge_ratio": field("股东质押比例"),
        "share_type": field("股份类型"),
    }


def pledge_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "source": field("来源", "数据来源", "source"),
        "ann_date": field("公告日期", "披露日期"),
        "begin_date": field("开始日期", "质押开始日期"),
        "end_date": field("截止日期", "结束日期"),
        "holder_name": field("股东名称"),
        "holder_id": field("股东ID"),
        "holder_type": field("股东类型"),
        "pledgor": field("质权人", "质押方"),
        "pledgor_type": field("质权人类型"),
        "share_nature": field("股份性质"),
        "share_unrestricted_num": field("无限售股份数"),
        "share_restricted_num": field("限售股份数"),
        "pledge_num": field("质押笔数"),
        "pledge_sharenum": field("质押股数"),
        "pledge_ratio": field("质押比例"),
        "pledge_shares": field("质押股份数"),
        "discharge_date": field("解押日期"),
        "is_discharge": field("是否解押"),
        "total_holding_shr": field("总持股数"),
        "total_pledge_shr": field("总质押股数"),
        "pledge_ratio_comp": field("占公司总股本比例"),
        "amt_frozen_ratio": field("冻结比例"),
        "pledge_total_num": field("累计质押数量"),
        "frozen_total_num": field("累计冻结数量"),
        "holder_quantity": field("持股数量"),
    }


def corporate_action_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "source": field("来源", "事项类型", "source"),
        "ann_date": field("公告日期", "披露日期"),
        "end_date": field("报告期截止日"),
        "record_date": field("股权登记日"),
        "ex_date": field("除权除息日"),
        "list_date": field("上市日期"),
        "div_progress": field("分红进度"),
        "per_shareqty": field("每股送转数量"),
        "div_cash_pre_tax": field("税前现金分红"),
        "div_cash_after_tax": field("税后现金分红"),
        "div_total_cash": field("现金分红总额"),
        "div_payout_date": field("红利发放日"),
        "div_object": field("分红对象"),
        "div_conversed_rate": field("转增比例"),
        "div_bonus_rate": field("送股比例"),
        "ai_type": field("增发类型"),
        "ai_progress": field("增发进度"),
        "ai_price": field("增发价格"),
        "ai_raised_funds": field("增发募资金额"),
        "ai_amount": field("增发数量"),
        "ai_code": field("增发代码"),
        "ai_name": field("增发名称"),
        "ri_progress": field("配股进度"),
        "ri_price": field("配股价格"),
        "ri_raised_funds": field("配股募资金额"),
        "ri_amount": field("配股数量"),
        "ri_amount_act": field("实际配股数量"),
        "ri_ratio": field("配股比例"),
        "ri_ratio_act": field("实际配股比例"),
        "holder_name": field("股东名称"),
        "free_date": field("解禁日期"),
        "free_num": field("解禁数量"),
        "flow_num": field("流通数量"),
        "limited_num": field("限售数量"),
        "limited_explain": field("限售说明"),
        "limited_source": field("限售来源"),
        "issue_price": field("发行价格"),
        "ipo_price": field("IPO发行价"),
        "online_issue_vol": field("网上发行量"),
        "offline_issue_vol": field("网下发行量"),
        "ipo_collection": field("IPO募资金额"),
        "purchase_code": field("申购代码"),
        "purchase_name": field("申购名称"),
        "ipo_amount": field("IPO发行数量"),
        "online_max_apply": field("网上申购上限"),
        "online_ratio": field("网上中签率"),
        "intent_letter_pub_date": field("招股意向书日期"),
        "ipo_sub_date": field("申购日期"),
        "par_value": field("面值"),
        "issue_vol": field("发行量"),
        "issue_cost": field("发行费用"),
        "diluted_pe_ratio": field("摊薄市盈率"),
        "ipo_type": field("IPO类型"),
        "is_failure": field("是否发行失败"),
        "listed_standard": field("上市标准"),
        "outstanding_shares": field("发行后流通股"),
        "issue_total_mv": field("发行总市值"),
    }


def performance_notice_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "report_period": field("报告期", "业绩预告报告期"),
        "ann_date": field("公告日期", "披露日期"),
        "currency": field("货币代码", "币种"),
        "perf_type_code": field("业绩预告类型代码"),
        "performance_type": field("业绩预告类型", "预增", "预减", "扭亏"),
        "performance_content": field("业绩预告内容"),
        "performance_reason": field("业绩变动原因"),
        "performance_summary": field("业绩预告摘要"),
        "net_profit_chg_ratio": field("净利润变动比例"),
        "net_profit_chg_lower": field("净利润变动下限"),
        "net_profit_chg_upper": field("净利润变动上限"),
        "net_profit_lower": field("净利润下限"),
        "net_profit_upper": field("净利润上限"),
        "last_year_net_profit": field("上年同期净利润"),
        "deduct_np_lower": field("扣非净利润下限"),
        "deduct_np_upper": field("扣非净利润上限"),
        "last_year_deduct_np": field("上年同期扣非净利润"),
        "revenue_lower": field("营业收入下限"),
        "revenue_upper": field("营业收入上限"),
        "last_year_revenue": field("上年同期营业收入"),
    }


def business_segment_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **stock_identity_fields(),
        "source": field("来源", "数据来源", "source"),
        "report_period": field("报告期"),
        "ann_date": field("公告日期", "披露日期"),
        "segment_type": field("分部类型", "业务分部类型"),
        "project_name": field("项目名称", "业务名称", "产品名称", "地区名称"),
        "project_speci_name": field("项目细分名称"),
        "segment_sales": field("分部收入", "主营收入"),
        "segment_cost": field("分部成本", "主营成本"),
        "segment_profit": field("分部利润", "毛利"),
        "gross_profit_margin": field("毛利率"),
        "pct_segment_sales": field("收入占比"),
        "pct_segment_profit": field("利润占比"),
        "pct_segment_cost": field("成本占比"),
        "inc_segment_sales": field("收入同比增速"),
        "inc_segment_profit": field("利润同比增速"),
        "inc_segment_cost": field("成本同比增速"),
        "inc_profit_margin": field("毛利率变化"),
        "project_level": field("项目层级"),
        "info_compname": field("客户或供应商名称"),
        "salesamount": field("销售或采购金额"),
        "pct": field("占比"),
        "interchange_code": field("类型代码", "1客户2供应商"),
    }


def constitution_fields(subject: str) -> Dict[str, Dict[str, List[str]]]:
    return {
        f"{subject}_code": field(f"{subject}代码", "主体代码"),
        f"{subject}_name": field(f"{subject}名称", "主体名称"),
        "stock_code": field("股票代码", "证券代码", "成分股代码"),
        "stock_name": field("股票名称", "证券名称", "成分股名称"),
        "weight": field("权重", "持仓权重"),
    }


def industry_constitution_fields() -> Dict[str, Dict[str, List[str]]]:
    return {
        **constitution_fields("industry"),
        "level": field("行业级别", "级别"),
        "level1_industry_name": field("一级行业名称"),
        "level2_industry_name": field("二级行业名称"),
        "level3_industry_name": field("三级行业名称"),
    }


FINANCE_CATALOG: Dict[str, Any] = {
    "subjects": {
        "stock": {
            "dataviews": {
                "base_info": {
                    "api": "stock.base_info",
                    "desc": "个股/上市公司基础信息",
                    "fields": {
                        **stock_identity_fields(),
                        "industry": field("所属行业", "行业"),
                        "listed_date": field("上市日期"),
                    },
                },
                "quote": {
                    "api": "stock.quote",
                    "desc": "个股统一行情：realtime=0 返回历史日K，realtime=1 返回从09:30开始的分钟K序列，realtime=2 返回最新一分钟K（最新行情）",
                    "fields": unified_stock_quote_fields(),
                    "kd": unified_stock_quote_kd_methods(),
                    "rules": [
                        "realtime=0：历史日K。",
                        "realtime=1：分钟K从09:30开始，盘前快照不返回；09:30的open取当日开盘价，amount/volumn取该时点累计值，后续分钟按上一分钟差分。",
                        "realtime=2：仅返回09:30以后最新一分钟K，用作最新行情。",
                    ],
                },
                "moneyflow": {
                    "api": "stock.moneyflow",
                    "desc": "个股资金流向",
                    "fields": moneyflow_fields(),
                    "kd": moneyflow_kd_methods(),
                },
                "margin": {
                    "api": "stock.margin",
                    "desc": "个股融资融券数据",
                    "fields": margin_fields(),
                    "kd": margin_kd_methods(),
                },
                "shareholder": {
                    "api": "stock.shareholder",
                    "desc": "个股股东户数、前十大股东、第一大股东和流通股东明细",
                    "fields": shareholder_fields(),
                },
                "pledge": {
                    "api": "stock.pledge",
                    "desc": "个股股权质押、解押、冻结和质押比例明细",
                    "fields": pledge_fields(),
                },
                "corporate_action": {
                    "api": "stock.corporate_action",
                    "desc": "个股分红送转、增发、配股、限售解禁和IPO等股本事件",
                    "fields": corporate_action_fields(),
                },
                "performance_notice": {
                    "api": "stock.performance_notice",
                    "desc": "个股业绩预告、预增预减、扭亏续亏和预告上下限",
                    "fields": performance_notice_fields(),
                },
                "business_segment": {
                    "api": "stock.business_segment",
                    "desc": "个股主营业务分部、产品地区收入和前五大客户供应商",
                    "fields": business_segment_fields(),
                },
                "pricevalue": {
                    "api": "stock.pricevalue",
                    "desc": "个股估值指标",
                    "fields": {
                        **stock_identity_fields(),
                        "tradedate": field("交易日期", "日期"),
                        "pe": field("PE", "市盈率"),
                        "pe_lyr": field("静态市盈率", "PE LYR"),
                        "pe_ttm": field("滚动市盈率", "PE TTM"),
                        "pe_ttm_deduct": field("扣非滚动市盈率"),
                        "pe_dynamic": field("动态市盈率"),
                        "pb": field("PB", "市净率"),
                        "pb_mrq": field("市净率MRQ"),
                        "pb_lf": field("市净率LF"),
                        "ps": field("PS", "市销率"),
                        "ps_lyr": field("静态市销率"),
                        "ps_ttm": field("滚动市销率"),
                        "pcf_cfottm": field("经营现金流TTM市现率"),
                        "pcf_ncfttm": field("净现金流TTM市现率"),
                        "pcf_cfolyr": field("经营现金流LYR市现率"),
                        "pcf_ncflyr": field("净现金流LYR市现率"),
                        "prr_lyr": field("市研率"),
                        "market_value": field("市值", "总市值"),
                        "free_float_mv": field("自由流通市值"),
                        "float_mv": field("流通市值"),
                        "total_share": field("总股本"),
                        "float_share": field("流通股本"),
                        "turn_ratio": field("换手率"),
                    },
                    "kd": pricevalue_kd_methods(),
                },
                "financial_3_table": {
                    "api": "stock.financial_3_table",
                    "desc": "财务三张表和常用财务指标",
                    "fields": {
                        **stock_identity_fields(),
                        "report_date": field("报告期", "财报期"),
                        "report_period": field("报告期", "财报期"),
                        "ann_date": field("公告日期", "披露日期"),
                        "statement_type": field("报表类型"),
                        "currency": field("货币代码", "币种"),
                        "revenue": field("营收", "营业收入"),
                        "total_revenue": field("营业总收入"),
                        "operating_revenue": field("营业收入"),
                        "profit": field("利润", "净利润"),
                        "net_profit": field("净利润"),
                        "parent_net_profit": field("归母净利润"),
                        "deducted_parent_net_profit": field("扣非归母净利润"),
                        "operating_profit": field("营业利润"),
                        "total_profit": field("利润总额"),
                        "operating_cost": field("营业成本"),
                        "total_cost": field("营业总成本"),
                        "sales_expense": field("销售费用"),
                        "admin_expense": field("管理费用"),
                        "rd_expense": field("研发费用"),
                        "financial_expense": field("财务费用"),
                        "eps_basic": field("基本每股收益"),
                        "eps_diluted": field("稀释每股收益"),
                        "total_assets": field("总资产", "资产总计"),
                        "total_liab": field("总负债", "负债合计"),
                        "total_equity": field("股东权益合计", "所有者权益"),
                        "parent_equity": field("归母所有者权益"),
                        "monetary_cap": field("货币资金"),
                        "accounts_receivable": field("应收账款"),
                        "inventory": field("存货"),
                        "fixed_assets": field("固定资产"),
                        "goodwill": field("商誉"),
                        "short_term_borrowing": field("短期借款"),
                        "long_term_borrowing": field("长期借款"),
                        "cashflow_operating": field("经营活动现金流量净额"),
                        "operating_cashflow": field("经营现金流", "经营活动现金流量净额"),
                        "cashflow_investing": field("投资活动现金流量净额"),
                        "investing_cashflow": field("投资现金流"),
                        "cashflow_financing": field("筹资活动现金流量净额"),
                        "financing_cashflow": field("筹资现金流"),
                        "net_cashflow": field("现金及现金等价物净增加额", "现金流净额"),
                        "cash_end": field("期末现金及现金等价物余额"),
                        "roe": field("ROE", "净资产收益率"),
                        "roe_deducted": field("扣非ROE"),
                        "roa": field("ROA", "总资产报酬率"),
                        "gross_margin": field("毛利率"),
                        "net_margin": field("净利率"),
                        "debt_ratio": field("资产负债率", "负债率"),
                        "current_ratio": field("流动比率"),
                        "quick_ratio": field("速动比率"),
                        "asset_turnover": field("总资产周转率"),
                    },
                    "computed": {
                        "suffixes": ["yoy", "qoq"],
                        "base_fields": [
                            "revenue",
                            "total_revenue",
                            "operating_revenue",
                            "operating_cost",
                            "total_cost",
                            "sales_expense",
                            "admin_expense",
                            "rd_expense",
                            "financial_expense",
                            "operating_profit",
                            "total_profit",
                            "profit",
                            "net_profit",
                            "parent_net_profit",
                            "deducted_parent_net_profit",
                            "eps_basic",
                            "eps_diluted",
                            "total_assets",
                            "total_liab",
                            "total_equity",
                            "parent_equity",
                            "monetary_cap",
                            "accounts_receivable",
                            "inventory",
                            "fixed_assets",
                            "goodwill",
                            "short_term_borrowing",
                            "long_term_borrowing",
                        ],
                        "desc": "Computed output/filter/order fields use <base_field>_yoy or <base_field>_qoq.",
                    },
                },
                "report": {
                    "api": "stock.report",
                    "desc": "个股研报结构化数据：报告信息、评级、核心观点、风险、目标价和研报预测指标。报告日期按研报发布日期查询；预测值使用 metric_* 字段。",
                    "fields": {
                        "report_id": field("研报ID", "报告ID", "report_id"),
                        "code": field("股票代码", "证券代码", "code"),
                        "name": field("股票名称", "证券名称", "name"),
                        "institution": field("研究机构", "机构", "institution"),
                        "analyst": field("研究员", "分析师", "analyst"),
                        "report_date": field("报告发布日期", "发布日期", "报告期", "report_date", "report_period"),
                        "rating": field("评级", "投资评级", "rating"),
                        "rating_change": field("评级变动", "变动", "rating_change"),
                        "change_reason": field("变动原因", "change_reason"),
                        "investment_highlights": field("投资要点", "核心观点", "investment_highlights"),
                        "risk_warnings": field("风险提示", "风险", "risk_warnings"),
                        "target_price_lower": field("目标价下限", "预测最低价", "target_price_lower", "target_low"),
                        "target_price_upper": field("目标价上限", "预测最高价", "target_price_upper", "target_high"),
                        "metric_code": field("指标编码", "metric_code"),
                        "metric_name": field("指标名称", "metric_name"),
                        "forecast_year": field("预测年份", "预测年度", "forecast_year"),
                        "value_type": field("数值类型", "实际/预测", "value_type"),
                        "metric_value": field("指标数值", "预测值", "value", "metric_value"),
                        "unit": field("单位", "单位名称", "unit"),
                        "source_locator": field("来源定位", "source_locator"),
                        "created_at": field("抽取时间", "created_at"),
                    },
                    "value_domains": {
                        "metric_code": {
                            "eps": "每股收益",
                            "np_parent": "归母净利润",
                            "np_parent_growth": "归母净利润增长率",
                            "pb": "市净率",
                            "pe": "市盈率",
                            "revenue": "营业收入",
                            "revenue_growth": "营业收入增长率",
                            "roe": "净资产收益率",
                        },
                        "value_type": {
                            "forecast": "预测值",
                            "actual": "实际值",
                        },
                    },
                    "api": [
                        {
                            "api_name": "stock.report",
                            "api_function": "查询个股在报告日期范围内的研报结构化信息和预测指标。",
                            "api_class": "report_query",
                        }
                    ],
                    "rules": [
                        "只查询 REPORT_DB_URL 指向数据库中的 chatbi_report_v 和 chatbi_metric_v 两个视图。",
                        "报告字段查询返回研报级记录；包含 metric_code、metric_name、forecast_year 或 metric_value 时返回指标级记录。",
                        "同一请求同时使用报告字段和指标字段时，按研报ID关联两个视图。",
                        "code 同时接受 6 位代码和 CODE.EXCHANGE；provider 会按研报视图的 6 位代码格式归一化。",
                        "metric_code 只能使用 value_domains 中的 8 个标准编码；不要臆造指标编码。metric_name 使用对应的标准中文名。",
                        "用户问预测值时必须加入 value_type = forecast；用户问历史实际值时使用 value_type = actual。",
                        "用户指定预测年份时，必须在 filter 中加入 forecast_year 条件，不能只返回全部年度指标后再自行挑选。",
                        "report_date/report_period 是研报发布日期，不是财务报表报告期；按日期范围使用 >= 和 <= 两个边界条件。",
                        "目标价字段按语义返回 target_price_lower 和 target_price_upper；不要直接使用底层视图的中文别名判断上下限。",
                        "需要完整返回符合条件的研报或指标记录时使用 limit = -1；若返回行数达到 limit，不得表述为全部数据。",
                    ],
                },
            },
        },
        "index": {
            "dataviews": {
                "base_info": {
                    "api": "index.base_info",
                    "desc": "指数基础信息",
                    "fields": {
                        "code": field("指数代码", "code"),
                        "name": field("指数名称", "name"),
                    },
                },
                "quote": {"api": "index.quote", "desc": "指数行情", "fields": quote_fields(), "kd": quote_kd_methods()},
                "pricevalue": {
                    "api": "index.pricevalue",
                    "desc": "指数估值",
                    "fields": {
                        "code": field("指数代码"),
                        "name": field("指数名称"),
                        "tradedate": field("交易日期", "日期"),
                        "pe": field("PE"),
                        "pe_lyr": field("静态市盈率", "PE LYR"),
                        "pe_ttm": field("滚动市盈率", "PE TTM"),
                        "pb": field("PB"),
                        "pb_mrq": field("市净率MRQ"),
                        "ps": field("PS"),
                        "ps_lyr": field("静态市销率"),
                        "ps_ttm": field("滚动市销率"),
                        "market_value": field("市值", "总市值"),
                        "float_mv": field("流通市值"),
                    },
                },
                "constitution": {"api": "index.constitution", "desc": "指数成分股", "fields": constitution_fields("index")},
            },
        },
        "industry": {
            "dataviews": {
                "base_info": {
                    "api": "industry.base_info",
                    "desc": "行业基础信息",
                    "fields": {
                        "industry_code": field("行业代码"),
                        "industry_name": field("行业名称"),
                        "level": field("级别"),
                        "level1_industry_name": field("一级行业名称"),
                        "level2_industry_name": field("二级行业名称"),
                        "level3_industry_name": field("三级行业名称"),
                    },
                },
                "constitution": {"api": "industry.constitution", "desc": "行业成分股和成分股维度聚合", "fields": industry_constitution_fields()},
            },
        },
        "plate": {
            "dataviews": {
                "base_info": {"api": "plate.base_info", "desc": "板块基础信息", "fields": {"plate_code": field("板块代码"), "plate_name": field("板块名称")}},
                "quote": {"api": "plate.quote", "desc": "板块行情", "fields": quote_fields(), "kd": quote_kd_methods()},
                "moneyflow": {"api": "plate.moneyflow", "desc": "板块资金流向", "fields": moneyflow_fields(), "kd": moneyflow_kd_methods()},
                "pricevalue": {
                    "api": "plate.pricevalue",
                    "desc": "板块估值",
                    "fields": {
                        "code": field("板块代码"),
                        "name": field("板块名称"),
                        "plate_code": field("板块代码"),
                        "plate_name": field("板块名称"),
                        "tradedate": field("交易日期", "日期"),
                        "pe": field("PE"),
                        "pe_lyr": field("静态市盈率", "PE LYR"),
                        "pe_ttm": field("滚动市盈率", "PE TTM"),
                        "pb": field("PB"),
                        "ps": field("PS"),
                        "ps_lyr": field("静态市销率"),
                    },
                },
                "constitution": {"api": "plate.constitution", "desc": "板块成分股和成分股维度聚合", "fields": constitution_fields("plate")},
            },
        },
        "fund": {
            "dataviews": {
                "base_info": {"api": "fund.base_info", "desc": "基金基础信息", "fields": {"code": field("基金代码"), "name": field("基金简称"), "full_name": field("基金全称")}},
                "quote": {"api": "fund.quote", "desc": "基金行情", "fields": fund_quote_fields(), "kd": quote_kd_methods()},
            },
        },
        "bond": {
            "dataviews": {
                "base_info": {"api": "bond.base_info", "desc": "债券基础信息", "fields": {"code": field("债券代码"), "name": field("债券简称"), "issuer": field("发行主体")}},
                "quote": {"api": "bond.quote", "desc": "债券行情", "fields": bond_quote_fields(), "kd": quote_kd_methods()},
            },
        },
        "hot_event": {
            "dataviews": {
                "base_info": {
                    "api": "hot_event.base_info",
                    "desc": "热点事件/概念基础信息",
                    "fields": {
                        "event_id": field("热点ID", "事件ID", "热点代码", "event_id", "event_code"),
                        "event": field("热点名称", "事件名称", "概念名称", "event", "event_name"),
                        "first_trigger_date": field("首次触发日期", "开始日期"),
                        "latest_trigger_date": field("最近触发日期", "最新日期"),
                        "latest_active_id": field("最近活跃周期ID", "active_id"),
                        "is_active": field("是否活跃"),
                        "loop_times": field("激活次数", "轮动次数"),
                        "max_exist_days": field("最长持续天数"),
                        "slience_days": field("沉寂天数"),
                        "company_num": field("历史关联公司数", "公司数"),
                        "current_company_num": field("当前公司数", "当前活跃公司数"),
                        "core_event_desc": field("核心事件描述"),
                        "latest_event_desc": field("最新事件描述"),
                    },
                },
                "state": {
                    "api": "hot_event.state",
                    "desc": "热点事件/概念状态和热度快照",
                    "fields": {
                        "event_id": field("热点ID", "事件ID", "热点代码", "event_id", "event_code"),
                        "active_id": field("活跃周期ID"),
                        "event": field("热点名称", "事件名称", "概念名称", "event", "event_name"),
                        "tradedate": field("交易日期", "日期", "trade_date"),
                        "state_time": field("状态时间", "采样时间"),
                        "heat_score": field("热度", "冷热分", "heat_score", "hotness"),
                        "change_label": field("变化标签", "状态标签"),
                        "company_num": field("公司数", "活跃公司数"),
                        "avg_lift": field("平均涨幅"),
                        "limited_count": field("涨停数量"),
                        "board_strength": field("大盘强弱", "大盘涨跌幅"),
                    },
                },
                "member": {
                    "api": "hot_event.member",
                    "desc": "热点事件/概念成分股",
                    "fields": {
                        "event_id": field("热点ID", "事件ID", "热点代码", "event_id", "event_code"),
                        "active_id": field("活跃周期ID"),
                        "event": field("热点名称", "事件名称", "概念名称", "event", "event_name"),
                        "tradedate": field("交易日期", "日期", "trade_date"),
                        "stock_code": field("股票代码", "证券代码", "成分股代码"),
                        "stock_name": field("股票名称", "证券名称", "成分股名称"),
                        "company_event": field("公司事件", "关联原因"),
                        "relation_type": field("关系类型", "triggered", "extended"),
                        "is_leader": field("是否龙头"),
                    },
                },
            },
        },
    },
    "constitution_agg_metrics": {
        "stock.quote.amount": ["sum", "avg", "max", "min", "median"],
        "stock.quote.volumn": ["sum", "avg", "max", "min", "median"],
        "stock.quote.pct": ["sum", "avg", "max", "min", "median"],
        "stock.quote.close": ["avg", "max", "min", "median"],
        "stock.moneyflow.main_net": ["sum", "avg", "max", "min", "median"],
        "stock.financial_3_table.roe": ["avg", "max", "min", "median"],
    },
}


def normalize_dataview(dataview: str) -> str:
    value = str(dataview or "").strip()
    return DATAVIEW_ALIASES.get(value, value)


def normalize_dataview_for_subject(subject: str, dataview: str) -> str:
    value = normalize_dataview(dataview)
    if str(subject or "").strip() == "stock":
        return STOCK_DATAVIEW_ALIASES.get(value, value)
    return value


def default_realtime_for_stock_dataview(dataview: str) -> int:
    value = str(dataview or "").strip()
    normalized = DATAVIEW_ALIASES.get(value, value)
    if value in HISTORY_STOCK_DATAVIEW_NAMES or normalized in HISTORY_STOCK_DATAVIEW_NAMES:
        return 0
    return 1


def get_dataview(subject: str, dataview: str) -> Dict[str, Any] | None:
    subject_name = str(subject or "").strip()
    normalized = normalize_dataview_for_subject(subject_name, dataview)
    subject_cfg = FINANCE_CATALOG["subjects"].get(subject_name)
    if not subject_cfg:
        return None
    view = subject_cfg.get("dataviews", {}).get(normalized)
    return deepcopy(view) if view else None


def has_api(api: str) -> bool:
    return resolve_api(api) is not None


def resolve_api(api: str) -> Dict[str, Any] | None:
    parts = str(api or "").strip().split(".")
    if len(parts) < 2:
        return None
    subject = parts[0]
    raw_dataview = parts[1]
    dataview = normalize_dataview_for_subject(subject, raw_dataview)
    default_realtime = default_realtime_for_stock_dataview(raw_dataview) if subject == "stock" and dataview == "quote" else 1
    view = get_dataview(subject, dataview)
    if not view:
        return None
    if len(parts) == 2:
        return {"type": "base", "subject": subject, "dataview": dataview, "view": view, "default_realtime": default_realtime}
    if len(parts) == 3 and parts[2] == "agg" and (dataview == "constitution" or (subject == "stock" and dataview == "quote")):
        return {"type": "agg", "subject": subject, "dataview": dataview, "view": view, "default_realtime": default_realtime}
    if len(parts) == 3 and parts[2] == "dynamic_cal" and dataview == "quote":
        return {"type": "dynamic_cal", "subject": subject, "dataview": dataview, "view": view, "default_realtime": default_realtime}
    if len(parts) == 3 and parts[2].startswith("kd_"):
        metric = parts[2][3:]
        if metric.endswith("_pct_change"):
            field_name, method = metric[: -len("_pct_change")], "pct_change"
        else:
            field_name, method = metric.rsplit("_", 1) if "_" in metric else ("", "")
        return {
            "type": "kd",
            "subject": subject,
            "dataview": dataview,
            "field": field_name,
            "method": method,
            "view": view,
            "default_realtime": default_realtime,
        }
    return None


def catalog_summary() -> Dict[str, Any]:
    return deepcopy(FINANCE_CATALOG)
