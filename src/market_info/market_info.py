import akshare as ak
import pandas as pd
import datetime
import requests
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import islice
from typing import Dict, List, Tuple
from src.utils.mysql_utils import MySQLUtils , StockInfoDbUtils
import re

#all stock code and name dic from a tsv
ALL_STOCK_CODE_NAME_TSV = "stock_name.tsv"
ALL_STOCK_CODE_DIC = {}
with open(ALL_STOCK_CODE_NAME_TSV, 'r', encoding='utf-8') as f:
    for line in f:
        code, name = line.strip().split('\t')
        ALL_STOCK_CODE_DIC[code] = name


class StockInfo:
    """
    基于 akshare, 提供对股票行情、指标及扩展需求的功能:
      1) 获取并计算近60天日线(只显示30天), 并在tooltip显示涨跌幅
      2) 获取当天分时(1分钟线), 供 "today" 分时图使用
      3) 扩展: 计算十日/三十日/六十日涨幅, 连板数, N天M板, 触发涨幅等
      4) 预留财务/基本面占位, 暂不实现实际抓取
    """
    #static variable: 所有股票代码和名称
    #load from a tsv file and load automatically
    stock_code_name: Dict[str, str] = ALL_STOCK_CODE_DIC
    stock_stk_code_list: List[str] = list(ALL_STOCK_CODE_DIC.keys())
    stock_norm_code_list = [f"{code.split('.')[0]}"  for code in stock_stk_code_list]
               

    def __init__(self):
        self._PREFIX2MKT: List[Tuple[str, str]] = [
        # 北京证券交易所（BJ）
        ("920", "BJ"), ("43", "BJ"), ("83", "BJ"),
        ("87", "BJ"), ("88", "BJ"), ("82", "BJ"),
        # 上海证券交易所（SH）
        ("900", "SH"),               # 沪市 B 股
        ("688", "SH"), ("689", "SH"),# 科创板
        ("60", "SH"), ("61", "SH"), ("603", "SH"),
        ("605", "SH"),
        # 深圳证券交易所（SZ）
        ("000", "SZ"), ("001", "SZ"), ("002", "SZ"),
        ("003", "SZ"), ("200", "SZ"), ("300", "SZ"),
        ("301", "SZ"), ("302", "SZ"),
        ]

    def get_stock_name(self, code: str) -> str:    
        """
        根据股票代码获取股票名称。
        如果代码不在 stock_code_name 中，返回 "未知股票"。
        """
        code= code.lower().strip()
        code = self.normalize_cn_a_code(code)
        #if not re.fullmatch(r"\d{6}", code):
            # 如果不是6位数字代码，返回 None
        #    return None
        return self.stock_code_name.get(code, None)
    #输入6位股票代码
    #输出 xxxxxx.mkt (600111.SH)
    def normalize_cn_a_code(self,code: str) -> str:
        if 'sh' in code.lower() or 'sz' in code.lower() or 'bj' in code.lower():
            code = code.lower().replace('sh', '').replace('sz', '').replace('bj', '')
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("证券代码必须是 6 位数字")# ---------------------------

        for prefix, mkt in self._PREFIX2MKT:
            if code.startswith(prefix):
                return f"{code}.{mkt}"
                                
        raise ValueError(f"无法识别的证券代码前缀: {code}")


    #  1) 获取"近60天"日级别 K线 + 仅返回后30天可视
    # ---------------------------

    def get_60days_daily_data(self, code="600519"):
        """
        获取近 60 天的日 K 线数据, 主要用于更准确的技术指标计算 (如RSI需要更多历史).
        最终只返回后 30 天的可视区.
        """
        db = StockInfoDbUtils()
        stk_code = self.normalize_cn_a_code(code)
        #4个月 60天 每月20个工作日 等于3个月，去除一些法定假日,4个月肯定够取60
        start_date = datetime.datetime.now() - datetime.timedelta(days=120)
        end_date = datetime.datetime.now()


        df = db.get_stock_history_hq(stk_code, start_date, end_date)

        # 常规处理
        df.reset_index(inplace=True)
        df.rename(columns={'trade_date': 'Datetime'}, inplace=True)
        df.sort_values('Datetime', inplace=True)
        df.reset_index(drop=True, inplace=True)
        df.fillna(0, inplace=True)
        df.replace([float('inf'), -float('inf')], 0, inplace=True)

        # 若 length>60 => 只保留 tail(60) 用于计算
        if len(df) > 60:
            df = df.tail(60)

        return df

    def compute_tech_indicators(self, df):
        """
        计算常用指标 (MACD, MA5/10/20, RSI(14)) 并新增一列 pct_chg 表示当日相对前一天收盘涨跌幅(%).
        其中:
          MACD(DIF), DEA, MACD_HIST
          MA5, MA10, MA20
          RSI(14)
          pct_chg (相对上一日close)
        """
        df.sort_values("Datetime", ascending=True, inplace=True)

        # 计算涨跌幅(相对前一天收盘)
        df['pct_chg'] = df['close'].pct_change(fill_method=None) * 100
        df["pct_chg"] = df["pct_chg"].fillna(0)

        # MACD
        df["EMA12"] = df["close"].ewm(span=12).mean()
        df["EMA26"] = df["close"].ewm(span=26).mean()
        df["MACD"]  = df["EMA12"] - df["EMA26"]   # DIF
        df["DEA"]   = df["MACD"].ewm(span=9).mean()
        df["MACD_HIST"] = df["MACD"] - df["DEA"]

        # MA(5,10,20)
        df["MA5"]  = df["close"].rolling(5).mean()
        df["MA10"] = df["close"].rolling(10).mean()
        df["MA20"] = df["close"].rolling(20).mean()

        # RSI(14)
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df["RSI"] = 100 - 100/(1 + (gain/(loss + 1e-9)))

        df.fillna(0, inplace=True)
        df.replace([float('inf'), -float('inf')], 0, inplace=True)
        return df

    def get_daily_data_xdays(self, code="sh600519", days=60) -> pd.DataFrame:
        """
        从上市以来的日K中, 只保留末尾 tail(days) 行.
        注意: days 指 "交易日数", 并不是自然日数, 
        如果想做得更严格, 可能需要取更长, 以防还不够. 
        这里做简化, 直接取全部后 tail(days).
        """
        try:
            db = StockInfoDbUtils()
            start_date = datetime.datetime.now() - datetime.timedelta(days=days)
            end_date = datetime.datetime.now()
            stk_code = self.normalize_cn_a_code(code)
            df = db.get_stock_history_hq(stk_code, start_date=start_date, end_date=end_date)
            # 转为 DataFrame 并处理
            df.reset_index(inplace=True)
            df.rename(columns={'trade_date':'Datetime'}, inplace=True)
            df.sort_values('Datetime', inplace=True)
            df.reset_index(drop=True, inplace=True)
            # 如果行数超过 days, 只留后面 tail(days)
            if len(df) > days:
                df = df.tail(days)
            # 处理缺失/inf
            df.fillna(0, inplace=True)
            df.replace([float('inf'), -float('inf')], 0, inplace=True)
            # 保留三位小数
            df = df.round(3)
            return df
        except Exception as e:
            print(f"api error for code {code} {e}")
            return pd.DataFrame()


    def get_xd_daily_echarts(self, code="sh600519", total_days=60, show_days=60):
        """
        获取近 total_days 日K, 用更长周期去计算 RSI / MACD 等指标，
        但最后只截取 show_days 天返回给前端可视化。
        """
        # 1) 取 total_days 日K
        df = self.get_daily_data_xdays(code=code, days=total_days)
        if df.empty:
            return None
        # => 你可能自己写个 get_daily_data_xdays(...)：先获取至少 60~90天数据
    
        # 2) 在 df 上做 RSI / MACD / MA 等 计算
        df = self.compute_tech_indicators(df)
        
        # 3) 如果想只显示后 30 天
        #if len(df) > show_days:
        #    df = df.tail(show_days)
    
        # 4) 转成 ECharts JSON
        df.fillna(0, inplace=True)
        df.replace([float("inf"), -float("inf")], 0, inplace=True)
        df = df.round(4)
        return self.df_to_echarts_json(df)


    def df_to_echarts_json(self, df):
        """
        将 df 的最后30天(可视区) 转成 ECharts JSON:
         {
           "kline": [
             [time, open, close, low, high, volume, ...? pct_chg],
             ...
           ],
           "indicators": { "MACD":[ [time, val], ...], "DEA":..., "pct_chg": ... }
         }
        """
        # 仅截取后30天
        #if len(df) > 30:
        #    df = df.tail(30)

        # ECharts: [Datetime, open, high, low, close, volume]
        # 但在JS里常见写法: [time, open, high, low, close] => 你可以对接自己JS逻辑
        # 这里示例: [time, open, close, low, high, volume, pct_chg]
        # (可以根据自己JS中 tooltip / series 结构自行决定)
        required_cols = ["Datetime","open","close","low","high","volume","pct_chg",
                         "MACD","DEA","MACD_HIST","MA5","MA10","MA20","RSI"]
        exist_cols = [c for c in required_cols if c in df.columns]

        # 先分离: kline vs indicators
        # kline: [Datetime, open, close, low, high, volume, pct_chg?]
        kline_list = []
        # indicators dict
        indicators_dict = {}

        # 约定: 作为k线主数据: open, close, low, high, volume, pct_chg
        # 其余如 MACD, DEA, RSI => indicators
        main_cols = ["open","close","low","high","volume","pct_chg"]
        tech_cols = set(["MACD","DEA","MACD_HIST","MA5","MA10","MA20","RSI"])

        for col in tech_cols:
            indicators_dict[col] = []

        for _, row in df.iterrows():
            tstr = str(row["Datetime"])
            o = row["open"]
            c = row["close"]
            l = row["low"]
            h = row["high"]
            vol = row["volume"]
            chg= row["pct_chg"] if "pct_chg" in df.columns else 0

            # kline row
            kline_list.append([tstr, o, c, l, h, vol, chg])

            # 其余(tech_cols)
            for tc in tech_cols:
                val = row.get(tc, 0)
                indicators_dict[tc].append([tstr, val])

        return {
            "kline": kline_list,
            "indicators": indicators_dict
        }

    def get_daily_echarts_60days(self, code="sh600519"):
        """
        获取 60天日线, 计算指标, 最后仅返回后30天 kline (但指标更准确)
        """
        df = self.get_60days_daily_data(code=code)
        df = self.compute_tech_indicators(df)
        return self.df_to_echarts_json(df)


    # ========== 当日分时(tick/minute) ==============

    def get_today_fenshi(self, code="sh600519"):
        """
        获取当天分时(1分钟线), 仅限当天数据:
          - ak.stock_zh_a_minute(...) 返回可能包含最近几天 minute?
          - 需要过滤 "today"
        """
        df = ak.stock_zh_a_minute(symbol=code, period="1", adjust="")
        df.rename(columns={'day':'Datetime'}, inplace=True)
        df.sort_values('Datetime', inplace=True)
        df.reset_index(drop=True, inplace=True)
        
        # 1) 强制把关键列转换为float
        #    一般包含 open, close, high, low, volume, amount
        #    如果遇到空字符串则变成NaN，再 fillna(0)
        for col in ["open","close","high","low","volume","amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    
        # 2) 只取今日
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        df = df[df["Datetime"].str.contains(today_str)]
        df.reset_index(drop=True, inplace=True)
    
        # 3) 计算当日分时涨跌幅
        df["pct_chg"] = df["close"].pct_change(fill_method=None) * 100
        df["pct_chg"] = df["pct_chg"].fillna(0)
    
        # 4) 返回 echarts json
        return self.df_to_echarts_json(df)


    # ========== 额外: 盘内统计(10日/30日/60日涨幅, 连板等) ===============
    def calc_n_day_gains(self, code="sh600519"):
        """
        示例: 返回 10日/30日/60日涨幅(%)  => placeholder
        """
        # 用 stock_zh_a_daily, 取最近60~70天
        date_70_days_ago = datetime.datetime.now() - datetime.timedelta(days=100)
        db = StockInfoDbUtils()
        stk_code = self.normalize_cn_a_code(code)
        df = db.get_stock_history_hq(stk_code ,start_date=date_70_days_ago)
        df.reset_index(inplace=True)
        df.rename(columns={'trade_date':'Datetime'}, inplace=True)
        df.sort_values('Datetime', inplace=True)
        df.reset_index(drop=True, inplace=True)

        # tail(70) 保险
        if len(df)>70:
            df = df.tail(70)

        # 末行(最新价)
        if df.empty:
            return 0,0,0

        last_close = df.iloc[-1]["close"]

        # 10日前
        if len(df)>=10:
            close_10 = df.iloc[-10]["close"]
            gain_10 = (last_close - close_10)/close_10 *100
        else:
            gain_10=0

        # 30日前
        if len(df)>=30:
            close_30 = df.iloc[-30]["close"]
            gain_30 = (last_close - close_30)/close_30 *100
        else:
            gain_30=0

        # 60日前
        if len(df)>=60:
            close_60 = df.iloc[-60]["close"]
            gain_60 = (last_close - close_60)/close_60 *100
        else:
            gain_60=0

        return (round(gain_10,2), round(gain_30,2), round(gain_60,2))

    def calc_2weeks_lianban(self, code="sh600519"):
        """
        计算: 
          - 两周(14天)内 N天M板 (最小范围, 示例)
          - 连板次数
          - 连涨天数

        全是示例, 具体逻辑看你如何认定"涨停".
        """
        db = StockInfoDbUtils()
        stk_code = self.normalize_cn_a_code(code)
        # 获取近14天的日K线数据
        data = datetime.datetime.now() - datetime.timedelta(days=20)
        df = db.get_stock_history_hq(stk_code, start_date=data)
        #df = ak.stock_zh_a_daily(symbol=code, adjust="")
        df.reset_index(inplace=True)
        df.rename(columns={'trade_date':'Datetime'}, inplace=True)
        df.sort_values('Datetime', inplace=True)
        df.reset_index(drop=True, inplace=True)

        # tail(14)
        if len(df)>14:
            df = df.tail(14)
        if df.empty:
            return "14天0板", 0, 0

        # 假设 "涨停"判定: (今日close - 昨日close)/昨日close >= 0.1
        df["pct_chg"] = df["close"].pct_change(fill_method=None)*100
        df.fillna(0, inplace=True)

        # 计算"涨停"=1/0
        df["zhangting"] = df["pct_chg"].apply(lambda x:1 if x>=9.9 else 0)
        # N天M板 => 先找到 第一次涨停与最后一次非涨停之间?
        # demo非常简化:
        zt_idx = df.index[df["zhangting"]==1].tolist()
        if not zt_idx:
            ntm_str = "14天0板"
        else:
            first_zt = zt_idx[0]
            last_zt  = zt_idx[-1]
            count_board = len(zt_idx)
            days_span = (last_zt - first_zt)+1
            ntm_str = f"{days_span}天{count_board}板"

        # 连板: 统计最大连续涨停
        max_lianban=0
        curr=0
        for val in df["zhangting"]:
            if val==1:
                curr+=1
                max_lianban = max(max_lianban, curr)
            else:
                curr=0

        # 连涨: similarly
        df["is_rise"] = df["pct_chg"].apply(lambda x:1 if x>0 else 0)
        max_lianz=0
        curz=0
        for val in df["is_rise"]:
            if val==1:
                curz+=1
                max_lianz = max(max_lianz, curz)
            else:
                curz=0

        return ntm_str, max_lianban, max_lianz

    def calc_trigger_gain(self, realtime_price, trigger_price):
        """
        (实时价格 - 触发价格) / 触发价格 * 100%
        """
        if trigger_price<=1e-9:
            return 0
        return round( (realtime_price - trigger_price)/trigger_price *100, 2 )


    # =============== 一些旧的函数(异动/概念涨幅等)保留 ===============
    def get_top_n_by_5min_change(self, n=10 , threshold=5):
        df = self.get_all_stocks_hq()
        if "涨跌幅" not in df.columns:
            raise ValueError("DataFrame中未找到 '涨跌' 字段")
        df = df[df["涨跌幅"] > threshold]
        df.sort_values(by="涨跌幅", ascending=False, inplace=True)
        top_df = df.head(n).copy()
        top_df.reset_index(drop=True, inplace=True)
        return top_df

    def get_abnormal_stocks_by_pct(
        self,
        threshold: float = 5.0,
        direction: str = "abs",
        min_pct: float = None,
        max_pct: float = None,
    ) -> pd.DataFrame:
        """
        异动识别：从全市场行情中筛选满足涨跌幅条件的股票。

        参数:
            threshold: 默认阈值(%)，当 min_pct/max_pct 未设置时生效
            direction: "abs" | "up" | "down"
                - abs: 绝对值 >= threshold
                - up:  涨幅 >= threshold
                - down:跌幅 <= -threshold
            min_pct/max_pct: 自定义区间过滤(%)，可只传一边

        返回:
            DataFrame: 包含满足条件的股票行情(含代码、名称、涨跌幅等)
        """
        df = self.get_all_stocks_hq()
        if df is None or df.empty:
            return pd.DataFrame()
        if "涨跌幅" not in df.columns:
            raise ValueError("DataFrame中未找到 '涨跌幅' 字段")

        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce").fillna(0)

        if min_pct is None and max_pct is None:
            if direction == "up":
                cond = df["涨跌幅"] >= float(threshold)
            elif direction == "down":
                cond = df["涨跌幅"] <= -abs(float(threshold))
            else:
                cond = df["涨跌幅"].abs() >= abs(float(threshold))
        else:
            cond = pd.Series(True, index=df.index)
            if min_pct is not None:
                cond &= df["涨跌幅"] >= float(min_pct)
            if max_pct is not None:
                cond &= df["涨跌幅"] <= float(max_pct)

        res = df[cond].copy()
        if not res.empty:
            res.sort_values(by="涨跌幅", ascending=False, inplace=True)
            res.reset_index(drop=True, inplace=True)
        return res

    def get_stock_zt_pool_em(self,data=''):
        if not data:
            data = datetime.datetime.now().strftime('%Y%m%d')   
        return ak.stock_zt_pool_em(date=data)

    def get_concept_board_gainers(self, sort_by="涨幅"):
        df = ak.stock_board_concept_name_em()
        if sort_by not in df.columns:
            raise ValueError(f"概念板块信息中不包含 {sort_by} 字段: {df.columns.tolist()}")
        df.sort_values(by=sort_by, ascending=False, inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_realtime_all_bid_ask(self):
        stock_zh_a_spot_df = ak.stock_zh_a_spot_em()
        stock_zh_a_spot_df = stock_zh_a_spot_df[["代码","名称","最新价","60日涨跌幅","涨跌幅"]]
        return stock_zh_a_spot_df

    # =============== 新增方法：一次查询多个股票的行情 ===============
    def get_multi_stocks_hq(self, codes):
        """
        使用内网 API (stockHq) 一次获取多个股票的行情信息。
        
        参数:
            codes (list[str]): 股票代码列表。例如 ["000001", "600519"] 或者 ["sz000001", "sh600519"] 等。
                              如果包含前缀 "sh"/"sz"/"bj"，会自动判断 shtSetcode。
                              如果没有前缀，默认 shtSetcode=0(视为sz)，具体可自行改造。
        
        返回:
            dict: 请求返回的原始 JSON 数据(或可在此处进一步解析).
        """
        url = "http://jzyzwup.upoem1.com/json/hq_basichq/stockHq"

        # 你可以根据实际情况定义交易所映射
        # 例如: sz=0, sh=1, bj=2 （这里仅示例）
        shtMarket = 0  # 市场类型，0 代表深圳(示例) 1代表上海
        #根据股票代码前缀判断市场类型()
        code_dep_dic = {
            6:"sh", 9:"bj", 4:"bj", 0:"sz", 3:"sz",
            1:"sz", 2:"sz", 8:"bj"
            }
        dep_code_dic = {
            "sh":1,
            "sz":0,
            "bj":7
        }


        vStock = []
        for c in codes:
            c_lower = c.lower()
            if c_lower and c_lower[0].isdigit():
                # 如果开头是数字 => 根据第1位判断是 sh/sz/bj
                prefix = code_dep_dic.get(int(c_lower[0]), "sh")
                shtMarket = dep_code_dic.get(prefix, 1)  # e.g. "sh600519"
            else:    
                # 如果原本 code 就有 "sh"/"sz"前缀
                if c_lower.startswith(("sh","sz","bj")):
                    prefix = c_lower[:2]
                    code = c_lower[2:]
                    shtMarket = dep_code_dic.get(prefix, 1)
 
            # 判断是否带前缀
            # 加入请求列表
            vStock.append({"shtSetcode": shtMarket , "sCode": c})

        payload = {
            "stReq": {
                "vStock": vStock,
                "eHqData": 1
            }
        }

        # 发送 POST 请求
        resp = requests.post(url, json=payload)
        data = resp.json()
        v_info = data.get("stRsp", {}).get("vStockHq", [])
        results = []
        for item in v_info:
            s_code = item.get("sCode", "")
            stSimHq = item.get("stSimHq", {})
            # 把需要的字段取出来并翻译成中文/业务字段名
            row_data = {
                "现价":   stSimHq.get("fNowPrice", 0),
                "开盘":   stSimHq.get("fOpen", 0),
                "最高":   stSimHq.get("fHigh", 0),
                "最低":   stSimHq.get("fLow", 0),
                "涨跌额": stSimHq.get("fChgValue", 0),
                "涨跌幅": stSimHq.get("fChgRatio", 0),  # 若需百分比再自行 ×100
                "成交额": stSimHq.get("fAmount", 0),
                "成交量": stSimHq.get("lVolume", 0),
                "昨收":   stSimHq.get("fClose", 0)
            }
            # 每个股票包装成 {股票代码: {字段...}}
            results.append({ s_code: row_data })
        return results
    
    # =============== 获取所有股票行情 ===============
    def get_all_stocks_hq(self,ret_format="dataframe" ,batch_size: int = 500,max_workers: int = 4):
        """
        获取所有股票的行情信息, 包括现价、涨跌幅等。
        默认返回 dataframe 格式, 但也可以转换成其他格式。
        
        返回:
            list: 每个股票的行情数据列表, 形如:
                  [
                    {"000001": {"现价": 10.0, "涨跌幅": 1.2, ...}},
                    {"600519": {"现价": 2000.0, "涨跌幅": -0.5, ...}},
                    ...
                  ]
        """
        def _chunked(iterable, size):
            """把可迭代对象按 size 切片生成子列表"""
            it = iter(iterable)
            while True:
                chunk = list(islice(it, size))
                if not chunk:
                    break
                yield chunk

        idx = 0
        ret = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(self.get_multi_stocks_hq, codes)
                for codes in _chunked(self.stock_norm_code_list, batch_size)
                ]
            for f in as_completed(futures):
                # get() 时如果子线程抛异常会向上冒泡，方便调试
                ret.extend(f.result())
        
        if ret_format == "dataframe":
            # 转换成 DataFrame
            data = []
            for item in ret:
                for code, values in item.items():
                    values["代码"] = code
                    values["名称"] = self.get_stock_name(code)
                    data.append(values)
            df = pd.DataFrame(data)
            df.rename(columns={"现价": "最新价"}, inplace=True)
        else:    
            # 返回原始列表
            df = ret

        return df


    # =============== 新增方法：获取分钟K线数据 ===============
    def get_minute_kline_data(self, code="000001", num_minutes=100, e_line_type=3):
        """
        获取指定股票的分钟K线数据。
    
        参数:
            code (str): 股票代码，默认为 "000001"
            num_minutes (int): 请求返回的 K线条数，默认为 100
            e_line_type (int): 对方接口原生分钟线类型:
                1 = 1分钟
                2 = 5分钟
                3 = 15分钟
        
        返回:
            dict: ECharts 格式的分钟K线, 形如
                  {
                    "kline": [ [time_str, open, close, low, high, volume, 0], ... ],
                    "indicators": {}
                  }
        """
        url = "http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData"
        shtMarket = 0  # 市场类型，0 代表深圳(示例) 1代表上海
        #根据股票代码前缀判断市场类型()
        code_dep_dic = {
            6:"sh", 9:"bj", 4:"bj", 0:"sz", 3:"sz",
            1:"sz", 2:"sz", 8:"bj"
            }
        dep_code_dic = {
            "sh":1,
            "sz":0,
            "bj":7
        }
        if code and code[0].isdigit():
            # 如果开头是数字 => 根据第1位判断是 sh/sz/bj
            prefix = code_dep_dic.get(int(code[0]), "sh")
            shtMarket = dep_code_dic.get(prefix, 1)  # e.g. "sh600519"
        else:    
            # 如果原本 code 就有 "sh"/"sz"前缀
            c_lower = code.lower()
            if c_lower.startswith(("sh","sz","bj")):
                prefix = c_lower[:2]
                code = c_lower[2:]
                shtMarket = dep_code_dic.get(prefix, 1)
        normalized_line_type = int(e_line_type or 3)
        if normalized_line_type not in (1, 2, 3):
            normalized_line_type = 3

        # 请求参数
        payload = {
            "stReq": {
                "stHeader": {
                    "shtMarket": shtMarket  # 市场类型，0 代表深圳(示例)
                },
                "sCode": code,      # 股票代码
                "eLineType": normalized_line_type,
                "shtStartxh": 0,    # 起始序号
                "shtWantNum": num_minutes  # 请求的 K线条数
            }
        }
    
        try:
            # 1) 发送 POST 请求
            response = requests.post(url, json=payload)
            response.raise_for_status()  # 如果不是 2xx，会抛出异常
    
            # 2) 解析 JSON
            data = response.json()
            vAnalyData = data.get("stRsp", {}).get("vAnalyData", [])
            if not vAnalyData:
                return {
                    "kline": [],
                    "indicators": {}
                }
    
            # 3) 格式化结果 => 与日K格式一致:
            #    [ "HH:MM", open, close, low, high, volume, 0(pct_chg占位) ]
            kline_list = []
            pre_close = 0
            for item in vAnalyData:
                # shtTime => 距离0:00的分钟数
                sht_time = item.get("sttDateTime", {}).get("shtTime", 0)
                HH = sht_time // 60
                MM = sht_time % 60
                time_str = f"{HH:02d}:{MM:02d}"  # 格式化成 "HH:MM"
    
                fOpen  = item.get("fOpen", 0.0)
                fClose = item.get("fClose", 0.0)
                fLow   = item.get("fLow", 0.0)
                fHigh  = item.get("fHigh", 0.0)
                volume = item.get("lVolume", 0)
                if pre_close == 0:
                    pre_close = fClose
                pct_chg = (fClose - pre_close) / pre_close * 100
                #2位小数
                pct_chg = round(pct_chg, 2)
                pre_close = fClose
     
                # 注意 amount 也可以取 item["fAmount"]，看你是否需要
                # 这里与日K结构保持一致: [time, open, close, low, high, volume, pct_chg(=0)]
                row = [
                    time_str,
                    fOpen,
                    fClose,
                    fLow,
                    fHigh,
                    volume,
                    pct_chg
                ]
                kline_list.append(row)
            #soort kline_list by timestr
            kline_list.sort(key=lambda x:x[0])

            # 4) 返回 ECharts 格式
            
            return {
                "kline": kline_list,
                "indicators": {}  # 分钟K一般不做MACD等指标,这里先空
            }
    
        except requests.exceptions.RequestException as e:
            return {
                "kline": [],
                "indicators": {},
                "error": f"Request failed: {str(e)}"
            }

    def calc_indicators_with_realtime(self, daily_k_json: dict, realtime_price: float , code:str) -> dict:
        """
        给定一只股票的日K JSON (如 get_xd_daily_echarts 返回),
        和最新股价(从 get_multi_stocks_hq 拿的 "现价")，重新计算
        - 10/30/60日涨幅
        - 连板数, N天M板
        - 连涨天数
        等等。

        返回一个 dict 形如：
        {
          "_10d_gain": "2.31%",
          "_30d_gain": "5.00%",
          "_60d_gain": "12.00%",
          "_ntm_board": "14天3板",
          "_lianban_count": 3,
          "_lianz_count": 2
          ...
        }
        """

        # 从 daily_k_json["kline"] 取出行数据 => 形如 [[date, open, close, low, high, volume, pct_chg], ...]
        # 通常最后30天；若要更多天可以在 get_xd_daily_echarts 里改 show_days=60.
        kline_arr = daily_k_json.get("kline", [])
        if not kline_arr:
            # 返回空指标
            return {
                "_10d_gain":"0.0%",
                "_30d_gain":"0.0%",
                "_60d_gain":"0.0%",
                "_ntm_board":"0天0板",
                "_lianban_count":0,
                "_lianz_count":0
            }

        # 把 kline_arr 转成 DataFrame，列：[date, open, close, low, high, volume, ?pct_chg]
        df = pd.DataFrame(kline_arr, columns=["date","open","close","low","high","volume","pct_chg"])
        # 强制转 float
        for col in ["open","close","low","high","volume","pct_chg"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        df.sort_values("date", inplace=True)  # 以防无序
        df.reset_index(drop=True, inplace=True)

        # 末行 close 当作 "最近交易日收盘价"
        if len(df)==0:
            return {}
        last_close = df.iloc[-1]["close"]

        # 计算 10日前收盘
        # 由于 df 只有30行(默认 show_days=30)，若 10 日前不足 => 取最前一行
        def get_close_x_days_ago(df, x):
            if len(df) < x:
                # 不足 x 行 => 最前
                return df.iloc[0]["close"]
            else:
                return df.iloc[-x]["close"]

        close_10  = get_close_x_days_ago(df, 10)
        close_30  = get_close_x_days_ago(df, 30)
        close_60  = get_close_x_days_ago(df, 60)  # 若30天 => 也只能拿最前

        # 这里要用 "最新股价" (realtime_price) 替代 "last_close" 来算涨幅
        # 如果你想对比“实时价 vs. 10天前收盘”，就:
        gain_10 = 0
        if close_10>1e-9:
            gain_10 = (realtime_price - close_10)/close_10 *100
        gain_30 = 0
        if close_30>1e-9:
            gain_30 = (realtime_price - close_30)/close_30 *100
        gain_60 = 0
        if close_60>1e-9:
            gain_60 = (realtime_price - close_60)/close_60 *100

        # 连板数, N天M板 => 这里是示例, 你可根据 daily df 计算:
        # 连板判断: 前后相邻收盘涨幅>= 9.9% ?
        df["chg"] = df["close"].pct_change(fill_method=None)*100
        df.fillna(0, inplace=True)

        # 涨停和连板的判断只看最近2周
        df = df.tail(14)
        #df["zhangting"] = df["chg"].apply(lambda x:1 if x>=9.9 else 0)
        df["zhangting"] = df["chg"].apply(lambda x: 1 if self.is_zhangting(code, x) else 0)
        # 只看最近的14天


        # 连板
        max_lb=0
        curr=0
        for val in df["zhangting"]:
            if val==1:
                curr+=1
                max_lb = max(max_lb, curr)
            else:
                curr=0
        # N天M板 => 简化, 仅把 df 的首尾 index
        zt_idx = df.index[df["zhangting"]==1].tolist()
        if not zt_idx:
            ntm_str = f"{len(df)}天0板"
        else:
            first_zt = zt_idx[0]
            last_zt  = zt_idx[-1]
            count_board = len(zt_idx)
            days_span = (last_zt - first_zt)+1
            ntm_str = f"{days_span}天{count_board}板"

        # 连涨天数 => 3天连续上涨(>0)
        df["is_rise"] = df["chg"].apply(lambda x:1 if x>0 else 0)
        max_lianz=0
        curz=0
        for val in df["is_rise"]:
            if val==1:
                curz+=1
                max_lianz = max(max_lianz, curz)
            else:
                curz=0

        return {
            "_10d_gain": f"{gain_10:.2f}%",
            "_30d_gain": f"{gain_30:.2f}%",
            "_60d_gain": f"{gain_60:.2f}%",
            "_ntm_board": ntm_str,
            "_lianban_count": max_lb,
            "_lianz_count": max_lianz
        }

    def is_zhangting(self,code,pct):
        #默认涨停幅度为9.9%
        #创业板：以 "300" 或 "301" 开头 => 20%
        #科创板：以 "688" 开头 => 20%
        #北交所：以 "8/9" 开头 => 30%
        if code.startswith("300") or code.startswith("301"):
            return pct>=19.9
        elif code.startswith("688"):
            return pct>=19.9
        elif code.startswith("8") or code.startswith("9"):
            return pct>=29.9
        else:
            return pct>=9.9


if __name__ == "__main__":
    obj = StockInfo()
    #df = ak.stock_zh_a_spot_em()
    #print(df)
    # demo: 
    #df = ak.stock_zh_a_daily(symbol="sh600519", adjust="qfq")
    #print(df)
    #df = obj.calc_2weeks_lianban(code="sh600519")
    #df = obj.get_today_fenshi("sh600519")
    #df= obj.get_all_stocks_hq()
    #print(df)
    code = "sh000001"  # 示例：深证成指的股票代码
    result = obj.get_minute_kline_data(code=code, num_minutes=20)
    print(result)
    #df = obj.get_top_n_by_5min_change()
    #print(df)

    #df = obj.get_top_n_by_5min_change(n=50, threshold=3)
    '''
    code="sh600519"
    df_60 = obj.get_60days_daily_data(code)
    df_60 = obj.compute_tech_indicators(df_60)
    json_60 = obj.df_to_echarts_json(df_60)
    print("Daily(60->30) ECharts JSON:", json_60.keys())

    df_today = obj.get_today_fenshi(code)
    print("Today fenshi keys:", df_today.keys())

    g10, g30, g60 = obj.calc_n_day_gains(code)
    print("十日/三十日/六十日涨幅:", g10, g30, g60)

    ntm_str, max_lb, max_lz = obj.calc_2weeks_lianban(code)
    print("N天M板 =>", ntm_str, "  连板=", max_lb, " 连涨=", max_lz)
    
    trig_g = obj.calc_trigger_gain(realtime_price=1800, trigger_price=1600)
    print("触发涨幅=", trig_g, "%")

    obj = StockInfo()
    # demo: 新增方法调用
    codes_to_query = ["000001", "sh600519", "bj430047"]
    import json

    obj = StockInfo()

    # 测试获取 10 分钟的 K线数据
    code = "000001"  # 示例：深证成指的股票代码
    result = obj.get_minute_kline_data(code=code, num_minutes=20)
    
    # 打印返回的 K线数据
    print("分钟K线数据:", result)
    
    # 测试不同的股票代码
    code2 = "600519"  # 茅台股票
    result2 = obj.get_minute_kline_data(code=code2, num_minutes=20)
    print("茅台分钟K线数据:", result2)
    res = obj.get_multi_stocks_hq(["600519"])
    print(res)
    '''
    
