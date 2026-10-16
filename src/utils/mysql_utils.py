# src/utils/mysql_utils.py
import pymysql
import datetime
from urllib.parse import unquote, urlparse
import pandas as pd
from pymysql.constants import FIELD_TYPE
from pymysql.converters import conversions
from collections import defaultdict
from typing import List, Dict, Any, Tuple ,Sequence, Optional
import time
import json
import json as _jsonlib
import os
import re
from bs4 import BeautifulSoup, Comment

PAGE_TYPE_NEWS = 0
PAGE_TYPE_REPORT = 1
PAGE_TYPE_ANNOUNCEMENT = 2
PAGE_TYPE_FINANCIAL_REPORT = 3


def _mysql_utf8mb4_kwargs() -> Dict[str, Any]:
    return {
        "charset": "utf8mb4",
        "use_unicode": True,
        "init_command": "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci",
    }


def _truncate_utf8_bytes(value: str, max_bytes: int) -> str:
    if value is None:
        return ""
    data = value.encode("utf-8", errors="ignore")
    if len(data) <= max_bytes:
        return value
    clipped = data[:max_bytes]
    while True:
        try:
            return clipped.decode("utf-8")
        except UnicodeDecodeError:
            clipped = clipped[:-1]
            if not clipped:
                return ""


def _looks_like_html(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.search(r"<[a-zA-Z!/][^>]*>", text))


def _clean_page_history_content(value: str) -> str:
    raw = str(value or "").replace("\x00", "").strip()
    if not raw:
        return ""
    if not _looks_like_html(raw):
        return raw

    soup = BeautifulSoup(raw, "html.parser")

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag in soup.find_all(["script", "style", "noscript", "svg", "iframe", "canvas"]):
        tag.decompose()

    for tag in soup.find_all(["br", "hr"]):
        tag.replace_with("\n")

    for tag in soup.find_all(["th", "td"]):
        tag.append("\t")

    for tag in soup.find_all(
        ["p", "div", "section", "article", "header", "footer", "aside", "li", "ul", "ol", "table", "tr"]
    ):
        tag.insert_before("\n")
        tag.append("\n")

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.insert_before("\n")
        tag.append("\n")

    text = soup.get_text(separator="", strip=False)
    text = text.replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\t{2,}", "\t", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()



class MySQLUtils:
    """
    用于管理 MySQL 数据库连接，并执行对 realtime_stock_concept_info 表的插入和更新等操作。
    新表结构:
      - realtime_stock_concept_info 中 (company, trigger_date, concept) => unique key
      - code 不参与唯一
      - sotck_info (text) 用于存储json
    """

    def __init__(self,
                 host="47.94.1.2",
                 user="cubeyz", 
                 password=None,
                 database="stock_agent", 
                 connect_timeout=30,  # 延长连接超时时间
                 read_timeout=60 ,     # 延长查询超时时间
                 port=3312):
        resolved_host = host
        resolved_user = user
        resolved_password = password if password is not None else os.getenv("STOCK_AGENT_DB_PASSWORD", "")
        resolved_database = database
        resolved_port = port
        platform_url = str(os.getenv("PLATFORM_DB_URL") or "").strip()
        if password is None and not resolved_password and platform_url:
            parsed = urlparse(platform_url.replace("mysql+pymysql://", "mysql://", 1))
            url_database = (parsed.path or "/").lstrip("/")
            if parsed.scheme == "mysql" and parsed.hostname and url_database == database:
                resolved_host = parsed.hostname
                resolved_user = unquote(parsed.username or resolved_user)
                resolved_password = unquote(parsed.password or "")
                resolved_database = url_database
                resolved_port = parsed.port or port
        self.host = resolved_host
        self.user = resolved_user
        self.password = resolved_password
        self.database = resolved_database
        self.port = resolved_port
        self.conn = None
        self.connect_db()



    def connect_db(self):
        """
        建立数据库连接
        """
        self.conn = pymysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database,
            port=self.port,
            **_mysql_utf8mb4_kwargs(),
        )
    
    def query(self,sql,filters=None):
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if filters:
                cursor.execute(sql,filters)
            else:
                cursor.execute(sql)
            rows = cursor.fetchall()
            return rows


    def get_concept_companies(self, concept_name, date_str):
        """
        从相关快照表中，找出指定概念和日期下最新快照（snap_index最大）的公司信息

        :param concept_name: 概念名称
        :param date_str: 字符串形式的日期，如 "2025-03-04"
        :return: list[dict], 每个 dict 形如:
          {
            "company": "公司名称",
            "stock_code": "股票代码",
            "concept": "概念名称",
            "event": "事件描述",
            "trigger_time": "触发时间",  # 实际类型需根据数据库字段类型确定
            "current_lift": 3.25       # 当前涨幅
          }
        """
        # 1) 找出指定概念当天的最新 snap_index
        snap_index_sql = """
            SELECT snap_index
            FROM stock_concept_snapshot  # 假设表名为这个
            WHERE concept_t1 = %s
              AND trigger_date = %s
            ORDER BY snap_index DESC
            LIMIT 1
        """

        # 2) 用找到的三元组获取公司数据
        company_sql = """
            SELECT 
                company, 
                max(code), 
                max(concept_t1), 
                max(events), 
                min(first_trigger_time)
                
            FROM stock_concept_snapshot
            WHERE concept_t1 = %s
              AND trigger_date = %s
              AND snap_index = %s group by company
        """

        with self.conn.cursor() as cursor:
            # 第一步：查找当天该概念的最大 snap_index
            cursor.execute(snap_index_sql, (concept_name, date_str))
            row = cursor.fetchone()
            if not row:
                return []
            latest_snap_index = row[0]

            # 第二步：获取公司数据
            cursor.execute(company_sql, (concept_name, date_str, latest_snap_index))
            rows = cursor.fetchall()

        # 整理返回结果
        result = []
        for r in rows:
            result.append({
                "company": r[0] or "",           # 处理可能的NULL值
                "code": r[1] or "",
                "concept": r[2] or "",
                "events": r[3] or "",
                "first_trigger_time": r[4].strftime("%Y-%m-%d %H:%M:%S") if r[4] else ""  # 假设是datetime类型
            })

        return result

    # set activate for daily review
    def set_activate(self,date=""):

        sql = """
        update stock_agent.stock_daily_review set
        is_active = 2
        where market_cap_pick > 100 
        and pct_change_pick > 5
        and pe_percentile_pick <= 20
        and announcements_pick <>'' 
        and announcements_pick is not null
        and is_active = 0
        """
        if date:
            sql = sql + " and pick_date ='"+date+"'"

        print(sql)
        with self.conn.cursor() as cursor:
            ret = cursor.execute(sql)
            self.conn.commit()
            return ret


    def get_latest_concepts_of_date(self, date_str):
        """
        从 concept_snapshot 表中，找出指定 trigger_date=date_str 的最新快照（snap_index 最大的记录），
        并返回 concept_t1, avg_lift, company_count。

        :param date_str: 字符串形式的日期，如 "2025-03-04"
        :return: list[dict], 每个 dict 形如:
          {
            "concept": "...",
            "avg_lift": 3.25,
            "company_count": 10
          }
        """
        # 1) 找出当天的最新 snap_index
        concept_sql = """
            SELECT concept_t1, avg_lift, company_count , hotness , snap_index , events
            FROM concept_snapshot
            WHERE trigger_date = %s
              AND company_count > 3
              ORDER by snap_index 
        """
        with self.conn.cursor() as cursor:
            # 第一步：查找当日 snap_index 最大值
            cursor.execute(concept_sql, (date_str,))
            rows = cursor.fetchall()
            if not rows:
                # 当天没有任何记录
                return []
            hotness_dic = {}
            result = []
            current_snap = ''
            for concept_t1, avg_lift, company_count , hotness , snap_index ,event in rows:
                if concept_t1 not in hotness_dic:
                    hotness_dic[concept_t1] = []
                hotness_dic[concept_t1].append(float(hotness))
                if snap_index > current_snap:
                    current_snap = snap_index
                    result = []
                print(hotness_dic[concept_t1])
                max_hotness = max(hotness_dic[concept_t1])
                min_hotness = min(hotness_dic[concept_t1])
                hotness_list = [str(round((i-min_hotness+0.001)/(max_hotness-min_hotness+0.001),2)) for i in hotness_dic[concept_t1]]
                hotness_str = ",".join(hotness_list)
                result.append({
                "concept": concept_t1,
                "avg_lift": float(avg_lift),
                "hotness": hotness_str,
                "company_count": int(company_count),
                "event": event
                })

        result = sorted(result, key=lambda d: d["avg_lift"], reverse=True)
            # 第二步：用该 snap_index 查出概念列表

        # 整理返回

        return result
    

    def close_db(self):
        """
        关闭数据库连接
        """
        if self.conn:
            self.conn.close()
            self.conn = None

    def reconnect(self):
        """
        主动断开并重新连接数据库，防止长连接超时
        """
        try:
            print("[MySQLUtils] Reconnecting...")
            self.close_db()
            time.sleep(1) # 稍微等待一下，释放资源
            self.connect_db()
            print("[MySQLUtils] Reconnected successfully.")
        except Exception as e:
            print(f"[MySQLUtils] Reconnect failed: {e}")
            # 如果重连失败，尝试再次连接或抛出异常供上层处理
            raise e
    
    ## check review ready ##
    def is_review_ready(self):

        current_time = datetime.datetime.now().strftime("%Y%m%d")
        sql = f"""
    SELECT
    /* 今日任务完成标志 */
    CASE 
    WHEN SUM(CASE WHEN pick_date = '{current_time}' THEN 1 ELSE 0 END) > 0
    THEN 1 ELSE 0
    END                                                    AS is_ready,

    /* 今日激活公司数 */
    SUM(CASE WHEN pick_date = '{current_time}' AND is_active = 1
    THEN 1 ELSE 0 END)                           AS today_activate,

    /* 今日 review 总数（激活 + 未激活） */
    SUM(CASE WHEN pick_date = '{current_time}'
    THEN 1 ELSE 0 END)  AS today_reviewed,
    /* 历史累计激活公司数（去重） */
    COUNT(DISTINCT CASE WHEN is_active = 1
    THEN code END)                    AS total_activate,

    /* 今日激活记录的最大更新时间 */
    COALESCE(
    MAX(CASE WHEN pick_date = '{current_time}'
    THEN update_time END),
    ''
    )                             AS today_update_time
    FROM stock_daily_review;
    """
        with self.conn.cursor() as cursor:
            # 第一步：查找当日 snap_index 最大值
            cursor.execute(sql)
            rows = cursor.fetchall()
            #if not rows:
                # 当天没有任何记录
            #    return 0
            dic = {}
            for r in rows:
                dic['is_ready'] = r[0]
                dic['today_active'] = r[1]
                dic['today_reviewed'] = r[2]
                dic['update_time'] = r[4]
                dic['total_active'] = r[3]
            return dic

 

    def bulk_insert_snapshot(self, rows):
        """
        rows: list of dict, each must have keys:
          [company, code, concept_t1, concept_t2, first_trigger_time,
           events, first_trigger_price, trigger_date, source,
           company_base_info, history_index, intraday_index, snap_index, std_concept]
    
        Insert them into stock_concept_snapshot
        """
        if not rows:
            return
        sql = """
        INSERT INTO stock_concept_snapshot (
          company, code, concept_t1, concept_t2, first_trigger_time,
          events, first_trigger_price, trigger_date, source,
          company_base_info, history_index, intraday_index,
          snap_index, std_concept
        )
        VALUES (
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s,
          %s, %s
        )
        """
        data_list = []
        for r in rows:
            data_list.append((
              r.get("company",""),
              r.get("code",""),
              r.get("concept_t1",""),
              r.get("concept_t2",""),
              r.get("first_trigger_time",""),
              r.get("events",""),
              r.get("first_trigger_price",0),
              r.get("trigger_date",""),
              r.get("source",""),
              r.get("company_base_info",""),
              r.get("history_index",""),
              r.get("intraday_index",""),
              r.get("snap_index","0"),
              r.get("std_concept",""),
            ))
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, data_list)
        self.conn.commit()
        print(f"bulk_insert_snapshot done => {len(rows)} rows.")

    def bulk_insert_concept_snapshot(self, rows):
        """
        批量插入概念快照数据到 concept_snapshot 表。
        :param rows: list[dict], 每条 dict 至少包含:
            {
                "concept_t1": str,
                "concept_t2": str,
                "events": str,
                "hotness": float,
                "trigger_date": str,
                "company_count": int,
                "avg_lift": float,
                "zt_count": int,
                "company_count_diff": int,
                "lift_diff": float,
                "snap_index": str,
                "std_concept": str
            }
        """
        if not rows:
            print("[Info] bulk_insert_concept_snapshot called with empty rows.")
            return
    
        # 构造 SQL
        sql = """
        INSERT INTO concept_snapshot
        (
          concept_t1,
          concept_t2,
          events,
          hotness,
          trigger_date,
          company_count,
          avg_lift,
          zt_count,
          company_count_diff,
          lift_diff,
          snap_index,
          std_concept
        )
        VALUES
        (
          %s, %s, %s,
          %s, %s, %s,
          %s, %s, %s,
          %s, %s, %s
        )
        """
    
        # 准备 insert_data
        insert_data = []
        for row in rows:
            insert_data.append((
                row.get("concept_t1",""),
                row.get("concept_t2",""),
                row.get("events",""),
                row.get("hotness",0.0),
                row.get("trigger_date",""),
                row.get("company_count",0),
                row.get("avg_lift",0.0),
                row.get("zt_count",0),
                row.get("company_count_diff",0),
                row.get("lift_diff",0.0),
                row.get("snap_index","0"),
                row.get("std_concept","")
            ))
            
        current_time = datetime.datetime.now().strftime("%Y%m%d_%H")
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, insert_data)
            self.conn.commit()
    
        print(f"[Info] bulk_insert_concept_snapshot inserted {len(rows)} rows.")



    def filter_makeup_company(self):
        sql = 'select code ,company from company_makeup where makeup_class = 0'
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            print(rows)
        return rows




    def store_and_get_new_urls(self, url_list, page_type: int = PAGE_TYPE_NEWS):
        """
        给定一批 url_list:
         1) 从表 page_crawled_history 中查询已存在的url
         2) 过滤掉已存在的
         3) 对剩余新url插入 (url, crawled_time=now, host=xxx, page_type=xxx)
         4) 返回本次新增的url列表
        """
        if not url_list:
            return []

        # 1) 将url_list转为set去重,避免重复对比
        url_set = set(url_list)

        # 2) 分批次查询，考虑MySQL对in(...)限制(一般1000). 
        #    也可一次性,若确定url_list不大. 这里示例使用一次性做法:
        placeholders = ",".join(["%s"] * len(url_set))
        select_sql = f"SELECT url FROM page_crawled_history WHERE url IN ({placeholders})"

        with self.conn.cursor() as cursor:
            cursor.execute(select_sql, list(url_set))
            rows = cursor.fetchall()
            existing_urls = set(r[0] for r in rows)

        # 3) 得到 new_urls
        new_urls = list(url_set - existing_urls)
        if not new_urls:
            return []

        # 4) 插入新url
        insert_sql = """
        INSERT INTO page_crawled_history (url, crawled_time, host, page_type)
        VALUES (%s, %s, %s, %s)
        """
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        insert_data = []
        for u in new_urls:
            host_val = urlparse(u).netloc  # 解析host
            insert_data.append((u, now_str, host_val, int(page_type)))

        with self.conn.cursor() as cursor:
            cursor.executemany(insert_sql, insert_data)
        self.conn.commit()

        return new_urls

    def filter_new_urls(self, url_list: Sequence[str]) -> List[str]:
        """
        仅查询 page_crawled_history 中已存在的 url，返回本次真正未入库的新 url。
        与 store_and_get_new_urls 不同，这个方法不会预插入占位记录，适合“先抓成功再入库”的链路。
        """
        if not url_list:
            return []

        url_set = {str(url).strip() for url in url_list if str(url).strip()}
        if not url_set:
            return []

        placeholders = ",".join(["%s"] * len(url_set))
        select_sql = f"SELECT url FROM page_crawled_history WHERE url IN ({placeholders})"

        with self.conn.cursor() as cursor:
            cursor.execute(select_sql, list(url_set))
            rows = cursor.fetchall()
            existing_urls = {r[0] for r in rows}

        return [url for url in url_list if str(url).strip() and str(url).strip() not in existing_urls]

    def insert_url_crawled_history_records(self, records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        """
        批量写入 page_crawled_history，支持新字段:
          - search_key
          - content
          - title
          - page_type
        records item 示例:
          {
            "url": "...",
            "host": "stock.10jqka.com.cn",  # 可选，不传则自动解析
            "search_key": "贵州茅台",
            "title": "网页标题",
            "content": "<html...> 或正文文本",
            "page_type": 1,
            "crawled_time": "2026-02-27 12:00:00"  # 可选
          }
        """
        if not records:
            return {"submitted": 0, "inserted": 0, "updated": 0, "affected": 0}

        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 仅做批次内去重，跨批次/并发去重交由DB唯一约束保证
        dedup_map: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            u = rec.get("url", "")
            if not u:
                continue
            dedup_map[u] = rec
        if not dedup_map:
            return {"submitted": 0, "inserted": 0, "updated": 0, "affected": 0}

        # 统计本批次里哪些URL已存在，用于准确输出 inserted/updated
        url_list = list(dedup_map.keys())
        placeholders = ",".join(["%s"] * len(url_list))
        select_sql = f"SELECT url FROM page_crawled_history WHERE url IN ({placeholders})"
        with self.conn.cursor() as cursor:
            cursor.execute(select_sql, url_list)
            existing_urls = {row[0] for row in cursor.fetchall()}

        sql = """
        INSERT INTO page_crawled_history
        (url, crawled_time, host, search_key, title, content, page_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          crawled_time=VALUES(crawled_time),
          host=VALUES(host),
          search_key=VALUES(search_key),
          title=VALUES(title),
          content=VALUES(content),
          page_type=VALUES(page_type)
        """
        data_list = []
        for url, rec in dedup_map.items():
            host = rec.get("host") or urlparse(url).netloc
            crawled_time = rec.get("crawled_time", now_str)
            search_key = rec.get("search_key")
            title = (rec.get("title") or "").replace("\x00", "")[:256]
            content = _clean_page_history_content(rec.get("content") or "")
            page_type = int(rec.get("page_type", PAGE_TYPE_NEWS))
            # MySQL TEXT 最大约 65535 bytes，预留少量冗余避免边界失败
            content = _truncate_utf8_bytes(content, 60000)
            data_list.append((url, crawled_time, host, search_key, title, content, page_type))

        if not data_list:
            return {"submitted": 0, "inserted": 0, "updated": 0, "affected": 0}

        with self.conn.cursor() as cursor:
            cursor.executemany(sql, data_list)
            affected = cursor.rowcount
        self.conn.commit()
        submitted = len(data_list)
        updated = len(existing_urls)
        inserted = submitted - updated
        return {
            "submitted": int(submitted),
            "inserted": int(max(inserted, 0)),
            "updated": int(max(updated, 0)),
            "affected": int(affected),
        }



    def get_all_concepts_info(self,date=""):
        """
        获取 realtime_concepts 表中所有记录的详细信息
        """
        sql = """
        SELECT
          id,
          concept,
          concept_event,
          hotness,
          trigger_date,
          first_trigger_time,
          create_time,
          update_time
        FROM realtime_concepts
        """

        if date:
            sql += f" WHERE trigger_date >= {date}"

        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return rows

    def update_company_info(self, company_name, update_dic):
        """
        根据 company_name 更新 realtime_stock_concept_info 表中所有匹配该公司的记录。
        不需要 trigger_date 或 concepts, 若有多条则全部更新。
        
        update_dic 形如: { "trend_weekly": "...", "first_trigger_price": 123, "sotck_info": "{}", ... }
        只会更新其中出现的字段，其余字段不变。
        """
        if not company_name:
            print("[Warn] update_company_info called with empty company_name.")
            return
    
        # 可更新字段
        ALLOWED_COLUMNS = {
            "code","concepts","events","trend_weekly","trend_realtime","company",
            "first_trigger_price","sotck_info","create_time","update_time",
            "first_trigger_time" , "history_index" , "intraday_index" ,"company_base_info","is_ready"
        }
    
        # 动态拼接
        update_sets = []
        values = []
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 强制更新 update_time => 你可选, 如果不需要自动更新, 可省略
        if "update_time" not in update_dic:
            update_dic["update_time"] = now_str
    
        for col, val in update_dic.items():
            if col in ALLOWED_COLUMNS:
                update_sets.append(f"{col}=%s")
                values.append(val)
    
        if not update_sets:
            print("[Info] update_company_info: No valid columns to update.")
            return
        set_clause = ", ".join(update_sets)
        sql = f"""
        UPDATE realtime_stock_concept_info
        SET {set_clause}
        WHERE ( company=%s or code=%s)
        """
        
        with self.conn.cursor() as cursor:
            cursor.execute(sql, [ *values, company_name, company_name])
        self.conn.commit()
 
    # 按字段更新复盘表格
    def update_stock_daily_review(
        self,
        rows: List[Dict[str, Any]],
        commit: bool = True
    ) -> int:
        """
        更智能的批量 UPDATE：
        - 每条字典至少要有 code / pick_date + ≥1 个可更新字段
        - 自动按“字段组合”分组，确保只更新给定字段
        - 返回总共影响的行数
        """
        if not rows:
            return 0

        # 允许更新的列白名单
        updatable_cols = {
            "news_event_pick", "concept_pick", "pct_change_pick", "price_pick",
            "pe_pick", "pick_datetime", "price_today", "pct_change_today",
            "pct_change_total", "announcements_pick", "recent_pe_series",
            "pe_percentile_pick", "market_cap_pick", "dragon_tiger_pick",
            "fundamentals_pick", "update_time", "is_active",
            "announcements_url_today","pct_5","pct_20","pct_60",
            "company_desc", "peg_pick", "announcements_list",'adj_price_pick',
            "pct_change_max", "pick_reason",
        }

        # --- 1. 按字段组合分组 --------------------------------------------------
        group_map: defaultdict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)

        for row in rows:
            if "code" not in row or "pick_date" not in row:
                raise ValueError("每条记录必须包含 code 和 pick_date 作为主键")
            cols_present = tuple(sorted( (set(row.keys()) - {"code", "pick_date"}) & updatable_cols))
            if not cols_present:
                raise ValueError("更新字段不能为空")
            # 检查非法列
            illegal = set(row.keys()) - {"code", "pick_date"} - updatable_cols
            if illegal:
                raise ValueError(f"出现非法列名: {illegal}")
            if 'adj_price_pick' not in row:
                print(f"adj_price_pick miss : {row}")
            group_map[cols_present].append(row)

        total_affected = 0

        # --- 2. 针对每个字段组合生成 SQL 并批量执行 --------------------------
        with self.conn.cursor() as cursor:
            for cols in group_map:
                set_clause = ", ".join(f"`{c}`=%s" for c in cols)
                sql = f"""
                    UPDATE stock_daily_review
                    SET {set_clause}
                    WHERE code=%s AND pick_date=%s
                """
                # params: [列值..., code, pick_date]
                param_batch = [
                    [row[c] for c in cols] + [row["code"], row["pick_date"]]
                    for row in group_map[cols]
                ]
                cursor.executemany(sql, param_batch)
                total_affected += cursor.rowcount

        if commit:
            self.conn.commit()
        return total_affected



    def record_exists(self, company, trigger_date, concept):
        """
        检查realtime_stock_concept_info表中是否已存在 (company, trigger_date, concept)
        返回 True/False
        """
        sql = """SELECT id FROM realtime_stock_concept_info 
                 WHERE company=%s AND trigger_date=%s AND concepts=%s
                 LIMIT 1"""
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (company, trigger_date, concept))
            row = cursor.fetchone()
            return (row is not None)

    def insert_record(self, data):
        """
        插入一条记录 data 到 realtime_stock_concept_info 表。
        data 必须至少包含:
          company, trigger_date, concepts
        其余字段可选:
          code, first_trigger_time, events,
          trend_weekly, trend_realtime, first_trigger_price, sotck_info, ...
        """
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        create_time = data.get("create_time", now_str)
        update_time = data.get("update_time", now_str)

        sql = """
        INSERT INTO realtime_stock_concept_info
        (
          company, code, concepts, first_trigger_time,
          events, create_time, update_time, trend_weekly,
          trend_realtime, first_trigger_price, trigger_date,  source
        )
        VALUES
        (
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s
        )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                data["company"],
                data.get("code",""),
                data["concepts"],   # <-- 注意改为 concept
                data.get("first_trigger_time",""),
                data.get("events",""),
                create_time,
                update_time,
                data.get("trend_weekly",""),
                data.get("trend_realtime",""),
                data.get("first_trigger_price",0),
                data["trigger_date"],
                data.get("source","异动")
            ))
        self.conn.commit()

    def partial_update_record(self, data):
        """
        对已有 (company, trigger_date, concept) 记录做"部分字段更新"。
        只更新 data 中出现的字段(见 ALLOWED_COLUMNS)，其余保持不变。
        同时 update_time 强制更新为当前时间。
        """
        if not all(k in data for k in ("company", "trigger_date", "concepts")):
            print("[Warn] partial_update_record missing (company, trigger_date, concept). skip")
            return

        ALLOWED_COLUMNS = {
            "code","concepts","events","trend_weekly","trend_realtime",
            "first_trigger_price","sotck_info"
        }

        update_sets = []
        values = []
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 强制 update_time
        update_sets.append("update_time=%s")
        values.append(now_str)

        for col in ALLOWED_COLUMNS:
            if col in data:
                update_sets.append(f"{col}=%s")
                values.append(data[col])

        if len(update_sets) == 1:
            # 仅 update_time => 依然更新
            pass

        set_clause = ", ".join(update_sets)
        sql = f"""
        UPDATE realtime_stock_concept_info
        SET {set_clause}
        WHERE company=%s AND trigger_date=%s AND concepts=%s
        """

        values.append(data["company"])
        values.append(data["trigger_date"])
        values.append(data["concepts"])

        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(values))
        self.conn.commit()

    def upsert_record(self, data):
        """
        如果 (company, trigger_date, concept) 存在 => partial_update_record
        否则 => insert_record
        """
        if not all(k in data for k in ("company","trigger_date","concepts")):
            print("[Warn] upsert_record missing essential keys =>", data)
            return

        if self.record_exists(data["company"], data["trigger_date"], data["concepts"]):
            self.partial_update_record(data)
        else:
            self.insert_record(data)

    def bulk_upsert_realtime_info(self, records):
        """
        批量 upsert(简化版本):
          - 先找出已有的 (company, trigger_date, concepts) 集合
          - 对存在的记录不作任何更新
          - 对不存在的, 一次性 executemany 批量插入
        :param records: list[dict], 每个dict至少包含:
             {
               "company": ...,
               "concepts": ...,
               "trigger_date": ...,
               --其他字段可选--
             }
        """
        if not records:
            return
    
        # 1) 基本检查 + 提取必要三元组
        needed = []
        for rec in records:
            if not all(k in rec for k in ("company","trigger_date","concepts")):
                print("[Warn] bulk_upsert => skip one record, missing keys =>", rec)
                continue
            needed.append((
                rec["company"].strip(),
                rec["trigger_date"].strip(),
                rec["concepts"].strip()
            ))
        if not needed:
            return
    
        # 2) 在数据库中查已有记录
        #   needed => [ (company,trigger_date,concepts), ... ]
        #   因 MySQL一次in(...)长度限制, 若 needed 很长, 可分批查. 这里简单一次查.
        #   先去重
        needed_set = set(needed)
        placeholders = ",".join(["(%s,%s,%s)"] * len(needed_set))  
        # 在 MySQL 里无法直接对三列写 in(...) => 可使用 or / union / or 采用临时表
        # 这里用一个技巧: 在 python 里先把(三元组)映射到 single string, 再in(...) 
        # 不过最简洁做法：分次查询(或建临时表). 这里示例进行一次临时方案:
        # 直接拼接  (company,trigger_date,concepts) => concat
        # 并在表里也用 CONCAT_WS("#",company,trigger_date,concepts) = ?
    
        # 先构造  "company#trigger_date#concept" => for searching
        def triple_key(c,t,p):
            return f"{c}#{t}#{p}"
    
        str_needed = [ triple_key(*tp) for tp in needed_set ]
        placeholders_str = ",".join(["%s"]*len(str_needed))
    
        select_sql = f"""
        SELECT
          CONCAT_WS('#', company, trigger_date, concepts) as ctp_key
        FROM realtime_stock_concept_info
        WHERE CONCAT_WS('#', company, trigger_date, concepts) IN ({placeholders_str})
        """
    
        with self.conn.cursor() as cursor:
            cursor.execute(select_sql, str_needed)
            rows = cursor.fetchall()
        existing_keys = set(r[0] for r in rows)
    
        # 3) 过滤 => 只保留不在 existing_keys 里的 => 待插入 new_records
        to_insert = []
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
        for rec in records:
            ckey = triple_key(
                rec["company"].strip(),
                rec["trigger_date"].strip(),
                rec["concepts"].strip()
            )
            if ckey in existing_keys:
                # 已存在 => 不做任何更新/插入
                continue
    
            # 不存在 => 批量插入
            # 构造 insert 所需字段
            company = rec["company"]
            code    = rec.get("code","")
            concepts= rec["concepts"]
            first_trigger_time = rec.get("first_trigger_time","")
            events  = rec.get("events","")
            create_time = rec.get("create_time", now_str)
            update_time = rec.get("update_time", now_str)
            trend_weekly= rec.get("trend_weekly","")
            trend_realtime= rec.get("trend_realtime","")
            first_trigger_price = rec.get("first_trigger_price",0)
            trigger_date = rec["trigger_date"]
            source = rec.get("source","异动")
    
            to_insert.append((
                company, code, concepts, first_trigger_time,
                events, create_time, update_time, trend_weekly,
                trend_realtime, first_trigger_price, trigger_date, source
            ))
    
        if not to_insert:
            print("[Info] bulk_upsert => all records exist, no new insertion.")
            return
    
        # 4) 一次性 executemany 插入
        insert_sql = """
        INSERT IGNORE INTO realtime_stock_concept_info
        (
          company, code, concepts, first_trigger_time,
          events, create_time, update_time, trend_weekly,
          trend_realtime, first_trigger_price, trigger_date, source
        )
        VALUES
        (
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s
        )
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(insert_sql, to_insert)
        self.conn.commit()
    
        print(f"[Info] bulk_upsert => Inserted {len(to_insert)} new records.")
    def get_companies_filtered(self, codes=None, days=0):
        """
        从 realtime_stock_concept_info 表查询:
        - 若 codes 不为空 => code in [...]
        - 若 days>0 => trigger_date >= boundary
        排序 => company, trigger_date
        """
        sql_base = """ SELECT
            id,company,code,concepts,first_trigger_time,events,trend_weekly,
            first_trigger_price,trigger_date,source,sotck_info,history_index,intraday_index,is_ready,company_base_info
        FROM realtime_stock_concept_info
        WHERE 1=1
        """
        params = []

        if codes and len(codes)>0:
            placeholders = ",".join(["%s"]*len(codes))
            sql_base += f" AND code IN ({placeholders}) "
            params.extend(codes)

        if days>=0:
            now = datetime.datetime.now()
            delta = datetime.timedelta(days=days)
            boundary_dt = now - delta
            boundary_str = boundary_dt.strftime("%Y%m%d")
            sql_base += " AND trigger_date >= %s "
            params.append(boundary_str)

        # 按 'company','trigger_date' 排序
        sql_base += " ORDER BY company ASC, trigger_date ASC"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql_base, tuple(params))
            rows = cursor.fetchall()
        return rows   

    def get_all_companies(self,date=''):
        """
        获取 realtime_stock_concept_info 表中所有记录 => list[dict].
        """
        sql = """
        SELECT 
          id,
          company,
          code,
          concepts,
          first_trigger_time,
          events,
          create_time,
          update_time,
          trend_weekly,
          trend_realtime,
          first_trigger_price,
          trigger_date,
          sotck_info
        FROM realtime_stock_concept_info
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if date:
                sql = sql + " where trigger_date = %s"
                cursor.execute(sql ,(date,))
            else:
                cursor.execute(sql)
            rows = cursor.fetchall()
            return rows

    ############################################################################
    # 新增: 异动表操作
    ############################################################################
    def get_active_abnormal_events(self) -> List[Dict[str, Any]]:
        """
        获取当前仍在异动中的股票记录 (is_active=1)
        """
        sql = """
        SELECT id, code, company, first_trigger_time, abnormal_pct, trigger_date
        FROM realtime_stock_abnormal
        WHERE is_active = 1
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    def insert_abnormal_event(self, data: Dict[str, Any]):
        """
        插入一条异动记录.
        必填:
          code, company, first_trigger_time, abnormal_pct, trigger_date
        可选:
          end_time, is_active, create_time, update_time
        """
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sql = """
        INSERT INTO realtime_stock_abnormal
        (
          code, company, first_trigger_time, abnormal_pct,
          end_time, is_active, trigger_date, create_time, update_time
        )
        VALUES
        (
          %s, %s, %s, %s,
          %s, %s, %s, %s, %s
        )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                data["code"],
                data["company"],
                data.get("first_trigger_time", now_str),
                data.get("abnormal_pct", 0),
                data.get("end_time", None),
                data.get("is_active", 1),
                data.get("trigger_date", None),
                data.get("create_time", now_str),
                data.get("update_time", now_str),
            ))
        self.conn.commit()

    def update_abnormal_active(self, code: str, update_dic: Dict[str, Any]):
        """
        更新指定 code 的当前 active 异动记录 (is_active=1)
        """
        if not code:
            return
        if not update_dic:
            return
        allowed = {"abnormal_pct", "end_time", "is_active", "update_time"}
        sets = []
        vals = []
        if "update_time" not in update_dic:
            update_dic["update_time"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for k, v in update_dic.items():
            if k in allowed:
                sets.append(f"{k}=%s")
                vals.append(v)
        if not sets:
            return
        set_clause = ", ".join(sets)
        sql = f"""
        UPDATE realtime_stock_abnormal
        SET {set_clause}
        WHERE code=%s AND is_active=1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, [*vals, code])
        self.conn.commit()

    def close_abnormal_events(self, codes: Sequence[str], end_time: str = None):
        """
        批量关闭异动事件 (is_active=1 -> 0)
        """
        if not codes:
            return 0
        if end_time is None:
            end_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        placeholders = ",".join(["%s"] * len(codes))
        sql = f"""
        UPDATE realtime_stock_abnormal
        SET end_time=%s, is_active=0, update_time=%s
        WHERE code IN ({placeholders}) AND is_active=1
        """
        params = [end_time, end_time, *codes]
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            affected = cursor.rowcount
        self.conn.commit()
        return affected

    ############################################################################
    # 新增: 热点追踪表 hotspot_trace 操作
    ############################################################################
    def upsert_hotspot_trace(self, data: Dict[str, Any]) -> int:
        """
        单条写入/更新 hotspot_trace。
        唯一键: (trace_type, trace_key, trigger_date)
        """
        required = {"trace_type", "trace_key", "trigger_date"}
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise ValueError(f"upsert_hotspot_trace missing fields: {missing}")

        sql = """
        INSERT INTO hotspot_trace
        (
          trace_type, trace_key, trigger_date,
          hotness, title, summary, content_json, sources_json,
          latest_news_time, last_read_news_time, new_url_count, new_content_chars,
          has_update, update_notify_time,
          create_time, update_time
        )
        VALUES
        (
          %s, %s, %s,
          %s, %s, %s, %s, %s,
          NULLIF(%s, ''), NULLIF(%s, ''), %s, %s,
          %s, NULLIF(%s, ''),
          COALESCE(%s, NOW()), COALESCE(%s, NOW())
        )
        ON DUPLICATE KEY UPDATE
          hotness = VALUES(hotness),
          title = VALUES(title),
          summary = VALUES(summary),
          content_json = VALUES(content_json),
          sources_json = VALUES(sources_json),
          latest_news_time = VALUES(latest_news_time),
          last_read_news_time = VALUES(last_read_news_time),
          new_url_count = VALUES(new_url_count),
          new_content_chars = VALUES(new_content_chars),
          has_update = VALUES(has_update),
          update_notify_time = VALUES(update_notify_time),
          update_time = NOW()
        """
        params = (
            data.get("trace_type"),
            data.get("trace_key"),
            data.get("trigger_date"),
            float(data.get("hotness", 0) or 0),
            data.get("title", ""),
            data.get("summary", ""),
            data.get("content_json", ""),
            data.get("sources_json", ""),
            data.get("latest_news_time", ""),
            data.get("last_read_news_time", ""),
            int(data.get("new_url_count", 0) or 0),
            int(data.get("new_content_chars", 0) or 0),
            1 if int(data.get("has_update", 0) or 0) > 0 else 0,
            data.get("update_notify_time", ""),
            data.get("create_time"),
            data.get("update_time"),
        )
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            affected = cursor.rowcount
        self.conn.commit()
        return affected

    def batch_upsert_hotspot_trace(self, rows: List[Dict[str, Any]]) -> int:
        """
        批量写入/更新 hotspot_trace。
        """
        if not rows:
            return 0
        sql = """
        INSERT INTO hotspot_trace
        (
          trace_type, trace_key, trigger_date,
          hotness, title, summary, content_json, sources_json,
          latest_news_time, last_read_news_time, new_url_count, new_content_chars,
          has_update, update_notify_time,
          create_time, update_time
        )
        VALUES
        (
          %s, %s, %s,
          %s, %s, %s, %s, %s,
          NULLIF(%s, ''), NULLIF(%s, ''), %s, %s,
          %s, NULLIF(%s, ''),
          COALESCE(%s, NOW()), COALESCE(%s, NOW())
        )
        ON DUPLICATE KEY UPDATE
          hotness = VALUES(hotness),
          title = VALUES(title),
          summary = VALUES(summary),
          content_json = VALUES(content_json),
          sources_json = VALUES(sources_json),
          latest_news_time = VALUES(latest_news_time),
          last_read_news_time = VALUES(last_read_news_time),
          new_url_count = VALUES(new_url_count),
          new_content_chars = VALUES(new_content_chars),
          has_update = VALUES(has_update),
          update_notify_time = VALUES(update_notify_time),
          update_time = NOW()
        """
        params_list = []
        for item in rows:
            required = {"trace_type", "trace_key", "trigger_date"}
            missing = [k for k in required if not item.get(k)]
            if missing:
                raise ValueError(f"batch_upsert_hotspot_trace missing fields: {missing}")
            params_list.append(
                (
                    item.get("trace_type"),
                    item.get("trace_key"),
                    item.get("trigger_date"),
                    float(item.get("hotness", 0) or 0),
                    item.get("title", ""),
                    item.get("summary", ""),
                    item.get("content_json", ""),
                    item.get("sources_json", ""),
                    item.get("latest_news_time", ""),
                    item.get("last_read_news_time", ""),
                    int(item.get("new_url_count", 0) or 0),
                    int(item.get("new_content_chars", 0) or 0),
                    1 if int(item.get("has_update", 0) or 0) > 0 else 0,
                    item.get("update_notify_time", ""),
                    item.get("create_time"),
                    item.get("update_time"),
                )
            )
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, params_list)
            affected = cursor.rowcount
        self.conn.commit()
        return affected

    def query_hotspot_trace(
        self,
        trigger_date: str,
        trace_type: str = "",
        trace_key: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        按日期查询热点追踪记录，可按类型/键过滤。
        """
        sql = """
        SELECT id, trace_type, trace_key, trigger_date, hotness, title,
               summary, content_json, sources_json,
               latest_news_time, last_read_news_time, new_url_count, new_content_chars,
               has_update, update_notify_time, create_time, update_time
        FROM hotspot_trace
        WHERE trigger_date = %s
        """
        params: List[Any] = [trigger_date]
        if trace_type:
            sql += " AND trace_type = %s"
            params.append(trace_type)
        if trace_key:
            sql += " AND trace_key = %s"
            params.append(trace_key)
        sql += " ORDER BY hotness DESC, update_time DESC LIMIT %s"
        params.append(int(limit))
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            return cursor.fetchall()

    def query_hotspot_trace_overview(
        self,
        trigger_date: str = "",
        keyword: str = "",
        trace_type: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        查询热点追踪列表，支持按日期/关键词/类型过滤。
        """
        pick_date = (trigger_date or "").strip() or datetime.datetime.now().strftime("%Y%m%d")
        sql = """
        SELECT id, trace_type, trace_key, trigger_date, hotness, title,
               summary, content_json, sources_json,
               latest_news_time, last_read_news_time, new_url_count, new_content_chars,
               has_update, update_notify_time, create_time, update_time
        FROM hotspot_trace
        WHERE trigger_date = %s
        """
        params: List[Any] = [pick_date]
        if trace_type:
            sql += " AND trace_type = %s"
            params.append(trace_type)
        if keyword:
            sql += " AND (trace_key LIKE %s OR title LIKE %s OR summary LIKE %s)"
            like_pattern = f"%{keyword}%"
            params.extend([like_pattern, like_pattern, like_pattern])
        sql += " ORDER BY update_time DESC, id DESC LIMIT %s"
        params.append(int(limit))
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            return cursor.fetchall()

    def get_hotspot_trace_by_id(self, trace_id: int) -> Optional[Dict[str, Any]]:
        """
        按主键读取单条热点追踪记录。
        """
        sql = """
        SELECT id, trace_type, trace_key, trigger_date, hotness, title,
               summary, content_json, sources_json,
               latest_news_time, last_read_news_time, new_url_count, new_content_chars,
               has_update, update_notify_time, create_time, update_time
        FROM hotspot_trace
        WHERE id = %s
        LIMIT 1
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (int(trace_id),))
            return cursor.fetchone()

    def get_latest_hotspot_trace(
        self,
        trace_key: str,
        trace_type: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        按 trace_key 读取最近一条热点记录，可选限定 trace_type。
        """
        sql = """
        SELECT id, trace_type, trace_key, trigger_date, hotness, title,
               summary, content_json, sources_json,
               latest_news_time, last_read_news_time, new_url_count, new_content_chars,
               has_update, update_notify_time, create_time, update_time
        FROM hotspot_trace
        WHERE trace_key = %s
        """
        params: List[Any] = [trace_key]
        if trace_type:
            sql += " AND trace_type = %s"
            params.append(trace_type)
        sql += " ORDER BY update_time DESC, id DESC LIMIT 1"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            return cursor.fetchone()

    def get_latest_hotspot_trace_by_keys(
        self,
        trace_type: str,
        trace_keys: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        """
        获取指定 trace_type + 多个 trace_key 的最近一条追踪记录。
        返回: {trace_key: row_dict}
        """
        keys = [k for k in trace_keys if k]
        if not keys:
            return {}
        placeholders = ",".join(["%s"] * len(keys))
        sql = f"""
        SELECT t.id, t.trace_type, t.trace_key, t.trigger_date, t.hotness, t.title,
               t.summary, t.content_json, t.sources_json,
               t.latest_news_time, t.last_read_news_time, t.new_url_count, t.new_content_chars,
               t.has_update, t.update_notify_time, t.create_time, t.update_time
        FROM hotspot_trace t
        INNER JOIN (
            SELECT trace_key, MAX(update_time) AS max_update_time
            FROM hotspot_trace
            WHERE trace_type = %s
              AND trace_key IN ({placeholders})
            GROUP BY trace_key
        ) x
          ON t.trace_key = x.trace_key
         AND t.update_time = x.max_update_time
         AND t.trace_type = %s
        """
        params = [trace_type, *keys, trace_type]
        result: Dict[str, Dict[str, Any]] = {}
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
            for row in rows:
                result[row["trace_key"]] = row
        return result

    def mark_hotspot_trace_read(
        self,
        trace_type: str,
        trace_key: str,
        trigger_date: str,
        read_time: str = "",
    ) -> int:
        read_time = (read_time or "").strip() or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
        UPDATE hotspot_trace
        SET has_update = 0,
            last_read_news_time = %s,
            update_time = NOW()
        WHERE trace_type = %s
          AND trace_key = %s
          AND trigger_date = %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (read_time, trace_type, trace_key, trigger_date))
            affected = cursor.rowcount
        self.conn.commit()
        return affected

    def list_active_abnormal_signals(self, limit: int = 200) -> List[Dict[str, Any]]:
        """
        拉取当前活跃异动信号，供独立热点调度器做分析决策。
        """
        sql = """
        SELECT id, code, company, first_trigger_time, abnormal_pct, is_active,
               DATE_FORMAT(trigger_date, '%%Y%%m%%d') AS trigger_date,
               create_time, update_time
        FROM realtime_stock_abnormal
        WHERE is_active = 1
        ORDER BY update_time DESC, id DESC
        LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (int(limit),))
            return cursor.fetchall()

    def list_recent_concept_signals(self, days: int = 1, limit: int = 200) -> List[Dict[str, Any]]:
        """
        拉取近期概念信号，供独立热点调度器做分析决策。
        """
        sql = """
        SELECT id, concept, concept_event, hotness, first_trigger_time,
               DATE_FORMAT(trigger_date, '%%Y%%m%%d') AS trigger_date,
               source_url, create_time, update_time
        FROM realtime_concepts
        WHERE trigger_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
        ORDER BY update_time DESC, id DESC
        LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (max(0, int(days)), int(limit)))
            return cursor.fetchall()

    def list_company_news_since(
        self,
        company: str,
        since_time: str,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        从 realtime_stock_concept_info 拉取公司维度增量事件。
        """
        sql = """
        SELECT id, company, code, concepts, events, source, trigger_date, update_time
        FROM realtime_stock_concept_info
        WHERE company = %s
          AND update_time >= %s
        ORDER BY update_time DESC, id DESC
        LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (company, since_time, int(limit)))
            return cursor.fetchall()

    def list_concept_news_since(
        self,
        concept: str,
        since_time: str,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        从 realtime_stock_concept_info 拉取概念维度增量事件。
        """
        sql = """
        SELECT id, company, code, concepts, events, source, trigger_date, update_time
        FROM realtime_stock_concept_info
        WHERE concepts LIKE %s
          AND update_time >= %s
        ORDER BY update_time DESC, id DESC
        LIMIT %s
        """
        like_pattern = f"%{concept}%"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (like_pattern, since_time, int(limit)))
            return cursor.fetchall()

    def list_url_history_since(
        self,
        search_key: str,
        since_time: str,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        从 page_crawled_history 读取指定热点关键字的新增网页。
        """
        sql = """
        SELECT id, url, host, search_key, title, content, crawled_time
        FROM page_crawled_history
        WHERE search_key = %s
          AND crawled_time >= %s
        ORDER BY crawled_time DESC, id DESC
        LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (search_key, since_time, int(limit)))
            return cursor.fetchall()

    def list_page_history(
        self,
        page_type: int,
        search_key: Optional[str] = None,
        since_time: Optional[str] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        从 page_crawled_history 按 page_type 读取页面记录，可选按 search_key 和 since_time 过滤。
        """
        sql = """
        SELECT id, url, host, search_key, title, content, crawled_time, page_type
        FROM page_crawled_history
        WHERE page_type = %s
        """
        params: List[Any] = [int(page_type)]
        if search_key:
            sql += " AND search_key = %s"
            params.append(search_key)
        if since_time:
            sql += " AND crawled_time >= %s"
            params.append(since_time)
        sql += " ORDER BY crawled_time DESC, id DESC LIMIT %s"
        params.append(int(limit))
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def list_page_history_by_urls(
        self,
        page_type: int,
        urls: Sequence[str],
    ) -> List[Dict[str, Any]]:
        """
        按指定 URL 列表读取 page_crawled_history 记录。
        """
        normalized_urls = [str(url).strip() for url in urls if str(url).strip()]
        if not normalized_urls:
            return []
        placeholders = ",".join(["%s"] * len(normalized_urls))
        sql = f"""
        SELECT id, url, host, search_key, title, content, crawled_time, page_type
        FROM page_crawled_history
        WHERE page_type = %s
          AND url IN ({placeholders})
        """
        params: List[Any] = [int(page_type), *normalized_urls]
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        row_map = {str(row["url"]): row for row in rows}
        return [row_map[url] for url in normalized_urls if url in row_map]

    ############################################################################
    # 新增:  realtime_concepts 操作 (用于管理概念表).
    ############################################################################
    def get_all_concepts(self , date = '20250101'):
        """
        获取 realtime_concepts 表中所有 concept
        返回 list[str]
        """
        sql = "SELECT distinct concept FROM realtime_concepts where trigger_date >= %s" % date
        concepts = []
        with self.conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            for r in rows:
                concepts.append(r[0])
        return concepts

    def concept_exists(self, concept, trigger_date):
        """
        判断 realtime_concepts 是否已存在 (concept, trigger_date)
        """
        sql = """SELECT id FROM realtime_concepts
                 WHERE concept=%s AND trigger_date=%s LIMIT 1"""
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (concept, trigger_date))
            row = cursor.fetchone()
            return (row is not None)

    def insert_concept(self, data):
        """
        data: {
          concept, concept_event, hotness, trigger_date, first_trigger_time, ...
        }
        """
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        create_time = data.get("create_time", now_str)
        update_time = now_str

        sql = """
        INSERT INTO realtime_concepts
        (concept, concept_event, hotness, trigger_date, first_trigger_time, create_time, update_time)
        VALUES
        (%s, %s, %s, %s, %s, %s, %s)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                data["concept"],
                data.get("concept_event",""),
                data.get("hotness",0),
                data["trigger_date"],
                data.get("first_trigger_time",""),
                create_time,
                update_time
            ))
        self.conn.commit()

    def update_concept(self, data):
        """
        更新概念(若需)
        """
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        sql = """
        UPDATE realtime_concepts
        SET concept_event=%s, hotness=%s, update_time=%s
        WHERE concept=%s AND trigger_date=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                data.get("concept_event",""),
                data.get("hotness",0),
                now_str,
                data["concept"],
                data["trigger_date"]
            ))
        self.conn.commit()

    def upsert_concept(self, data):
        """
        如果(concept, trigger_date)存在 => update_concept, 否则 => insert_concept
        """
        if not all(k in data for k in ("concept","trigger_date")):
            print("[Warn] upsert_concept missing keys =>", data)
            return
        if self.concept_exists(data["concept"], data["trigger_date"]):
            self.update_concept(data)
        else:
            self.insert_concept(data)
    
    def get_history_value_stock_list(self,date:str , sort_by:str="lift"):
        sql = """
            SELECT code,
                   company,
                   DATE_FORMAT(pick_date, '%%Y-%%m-%%d') AS pick_date,
                   pct_change_total,
                   price_today,
                   pct_change_today,
                   pe_percentile_pick,
                   company_desc,
                   announcements_url_today,
                   announcements_list,
                   peg_pick,
                   announcements_pick,
                   pct_change_max,
                   deactive_date,
                   pick_reason
            FROM   stock_daily_review
            WHERE  is_active = 1
              AND  pick_date <= %s
        """
        order_by_clause = "ORDER BY pct_change_total DESC"
        if sort_by == 'date':
            order_by_clause = "ORDER BY pick_date DESC"
        sql = sql +"\n"+order_by_clause    
        with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql,(date,))
            rows = cursor.fetchall()
            return rows
        return []
    
    def get_new_value_stock_list(self, date_str: str, limit=20):
        """
        获取当日复盘数据，返回格式：
        {
            "new_stocks": [ ... ],      # stock_daily_review 中当日激活的数据
            "callout_stocks": [ ... ]   # gll_fupanla_callout 中当日调出的数据
        }
        """
        result = {
            "new_stocks": [],
            "callout_stocks": []
        }

        # 1. 转换日期格式
        # stock_daily_review 可能接受 datetime 对象或 YYYY-MM-DD 字符串
        # gll_fupanla_callout 明确需要 YYYY-MM-DD 字符串
        try:
            # 假设传入的是 "20260112"
            if isinstance(date_str, str) and len(date_str) == 8:
                dt_obj = datetime.datetime.strptime(date_str, "%Y%m%d")
                fmt_date = dt_obj.strftime("%Y-%m-%d")
            elif isinstance(date_str, (datetime.date, datetime.datetime)):
                # 如果传入的是对象，转为字符串
                fmt_date = date_str.strftime("%Y-%m-%d")
            else:
                fmt_date = date_str # 兜底
        except Exception as e:
            print(f"日期转换错误: {e}")
            return result

        with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cursor:
            # === 第一部分：新增记录 (原有逻辑) ===
            sql_new = """
            SELECT code,
                   company,
                   price_today,
                   pct_change_today,
                   price_pick,
                   peg_pick,
                   pct_change_pick,
                   deactive_date
            FROM   stock_daily_review
            WHERE  pick_date = %s
              AND  is_active = 1
            ORDER  BY pct_change_today DESC
            LIMIT  %s
            """
            cursor.execute(sql_new, (fmt_date, limit))
            result["new_stocks"] = cursor.fetchall()

            # === 第二部分：调出记录 (CalloutService 逻辑) ===
            sql_callout = """
            SELECT code, 
                   company, 
                   pick_time, 
                   out_time, 
                   rise_rate, 
                   max_rise_rate,
                   price_pick,
                   pct_change_pick
            FROM   gll_fupanla_callout
            WHERE  out_time = %s
            ORDER  BY out_time DESC, code ASC
            """
            cursor.execute(sql_callout, (fmt_date,))
            result["callout_stocks"] = cursor.fetchall()

        return result

    def get_new_value_stock_detail(self,code:str,date:str ):
        sql = """
        SELECT *
        FROM   stock_daily_review
        WHERE  code = %s
        AND  pick_date = %s 
        """
        with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql,(code ,date))
            row = cursor.fetchone()
            return row
        return {}

    # ---------------- 新增 ----------------
    def insert_value_stocks_today(self, rows: List[Dict[str, Any]]) -> int:
        """
        批量插入今日入选股票；若 (code, pick_date) 已存在则更新。
        
        rows 中每条 dict **必须包含**
          - code, company, news_event_pick, concept_pick, pick_datetime
        可选携带
          - pct_change_pick, price_pick, pe_pick, pick_date, 以及表里任何可写列
        
        返回受影响的行数（insert + update）。
        """
        REQUIRED = {"code", "company", "news_event_pick", "concept_pick", "pick_datetime"}
        if not rows:
            return 0

        # 白名单：可写列（主键、自增列除外）
        writable_cols = {
            "code", "company", "news_event_pick", "concept_pick",
            "pct_change_pick", "price_pick", "pe_pick",
            "pick_datetime", "pick_date",   # 时间列
            "price_today", "pct_change_today", "pct_change_total",
            "announcements_pick", "recent_pe_series", "pe_percentile_pick",
            "market_cap_pick", "dragon_tiger_pick", "fundamentals_pick",
            "is_active", "announcements_url_today", "pick_reason"
        }

        params_batch = []
        for i, row in enumerate(rows, 1):
            missing = REQUIRED - row.keys()
            if missing:
                raise ValueError(f"第 {i} 条记录缺少必填字段: {missing}")

            # --- 默认值填充 ---
            defaults = {
                "pct_change_pick": 0,
                "price_pick": 0,
                "pe_pick": 0,
                "pick_date": row.get("pick_date") or datetime.datetime.today().isoformat()
            }

            # 过滤非法键 & 合并默认
            clean_row = {k: (row[k] if k in row else defaults.get(k))
                         for k in writable_cols
                         if (k in row or k in defaults)}

            params_batch.append(clean_row)

        # 动态列顺序保持稳定
        cols = sorted({k for r in params_batch for k in r.keys()})
        col_names = ", ".join(f"`{c}`" for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))

        # ON DUPLICATE：只更新传入/默认的列（排除 code, pick_date）
        upd_cols = [c for c in cols if c not in ("code", "pick_date")]
        upd_clause = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in upd_cols)

        sql = f"""
        INSERT ignore INTO stock_daily_review ({col_names})
        VALUES ({placeholders})
        """

        values_list = [tuple(r[c] for c in cols) for r in params_batch]

        with self.conn.cursor() as cursor:
            cursor.executemany(sql, values_list)
            affected = cursor.rowcount  # insert+update 行数

        self.conn.commit()
        return affected

    #牛股的人工检验

    def get_stocks_by_status(self, pick_date: str | datetime.date, is_active: int) -> list[dict]:
        if pick_date is None:
            pick_date = datetime.date.today()
        sql = """
            SELECT
                id, code, company,
                news_event_pick, concept_pick,
                pct_change_pick, price_pick, pe_pick,
                pe_percentile_pick, market_cap_pick,
                announcements_pick,
                pick_datetime, pick_date, is_active
            FROM stock_daily_review
            WHERE pick_date = %s AND is_active = %s
            ORDER BY market_cap_pick DESC, pct_change_pick DESC, id ASC
        """
        with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (pick_date, is_active))
            return cur.fetchall()

    # 如果你还保留旧代码用到它，也可以把原来的 get_candidate_stocks 改成调用上面这个：
    def get_candidate_stocks(self, pick_date):
        return self.get_stocks_by_status(pick_date, 2)



    def confirm_bull_stocks(self, ids: Sequence[int], pick_date: str | datetime.date | None = None) -> int:
        """
        批量把给定 ids 对应记录置为 is_active=1（仅限当前仍为2的记录）
        可选按 pick_date 进一步限定；返回受影响行数
        """
        ids = [int(x) for x in ids if str(x).isdigit()]
        if not ids:
            return 0

        placeholders = ",".join(["%s"] * len(ids))
        params: list = list(ids)
        sql = f"""
            UPDATE stock_daily_review
            SET is_active = 1, update_time = NOW()
            WHERE id IN ({placeholders}) AND is_active = 2
        """
        if pick_date is not None:
            sql += " AND pick_date = %s"
            params.append(pick_date)

        try:
            with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cur:
                cur.execute(sql, params)
                affected = cur.rowcount
            self.conn.commit()
            return affected
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print("[Error] confirm_bull_stocks failed:", e)
            return 0
######人工 新增牛股相关接口
    def insert_bull_stock(self, row: dict, default_status: int = 2) -> dict:
        """
        新增/更新一条牛股（仅必要字段）。
        必填: code, company, pick_date
        可选: news_event_pick, concept_pick, pick_datetime
        default_status: 默认写入 is_active=2（候选），也可传1直接确认
        返回: {"affected": 1|2, "action": "inserted"|"updated"}
        """
        code = (row.get("code") or "").strip()
        company = (row.get("company") or "").strip()
        pick_date = row.get("pick_date")
        concept = (row.get("concept_pick") or "").strip()
        event = (row.get("news_event_pick") or "").strip()
        pick_dt = row.get("pick_datetime")

        if not code or not company or not pick_date:
            raise ValueError("code/company/pick_date 为必填")

        # pick_datetime 可选：若未提供则落地为 pick_date 00:00:00
        if not pick_dt:
            pick_dt = f"{pick_date} 00:00:00"

        sql = """
        INSERT INTO stock_daily_review
        (`code`,`company`,`news_event_pick`,`concept_pick`,`pick_datetime`,`pick_date`,`is_active`)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          `company`=VALUES(`company`),
          `news_event_pick`=VALUES(`news_event_pick`),
          `concept_pick`=VALUES(`concept_pick`),
          `pick_datetime`=VALUES(`pick_datetime`),
          `is_active`=VALUES(`is_active`),
          `update_time`=NOW()
        """
        params = (code, company, event, concept, pick_dt, pick_date, int(default_status))
        with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, params)
            affected = cur.rowcount  # 1=insert, 2=update
        self.conn.commit()
        return {"affected": affected, "action": "updated" if affected == 2 else "inserted"}

    def exists_code_on_date(self, code: str, pick_date: str | datetime.date) -> bool:
        sql = """SELECT 1 FROM stock_daily_review
                 WHERE `code`=%s AND `pick_date`=%s LIMIT 1"""
        with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (code, pick_date))
            return cur.fetchone() is not None

    def get_distinct_options_for_date(self, pick_date: str | datetime.date,
                                      limit: int = 100) -> dict:
        """
        返回某日去重后的概念与事件（按出现频次降序）
        """
        result = {"concepts": [], "events": []}
        # 概念
        sql_concept = """
            SELECT concept_pick AS v, COUNT(*) cnt
            FROM stock_daily_review
            WHERE pick_date=%s AND concept_pick IS NOT NULL AND concept_pick <> ''
            GROUP BY concept_pick
            ORDER BY cnt DESC, v
            LIMIT %s
        """
        # 事件
        sql_event = """
            SELECT news_event_pick AS v, COUNT(*) cnt
            FROM stock_daily_review
            WHERE pick_date=%s AND news_event_pick IS NOT NULL AND news_event_pick <> ''
            GROUP BY news_event_pick
            ORDER BY cnt DESC, v
            LIMIT %s
        """
        with self.conn.cursor(cursor=pymysql.cursors.DictCursor) as cur:
            cur.execute(sql_concept, (pick_date, int(limit)))
            result["concepts"] = [r["v"] for r in cur.fetchall()]
            cur.execute(sql_event, (pick_date, int(limit)))
            result["events"] = [r["v"] for r in cur.fetchall()]
        return result





    def __del__(self):
        self.close_db()

    # ============== 新增个股详情页 ==============================
    # 放在 MySQLUtils 类内部 ===============================

    def _ensure_conn(self):
        if not self.conn or not self.conn.open:
            self.connect_db()

    def _json_dumps(self, obj) -> str:
        return _jsonlib.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    def _json_loads(self, text: Any, default: Any = None) -> Any:
        if text in (None, ""):
            return default
        if isinstance(text, (dict, list)):
            return text
        try:
            return _jsonlib.loads(text)
        except Exception:
            return default

    def check_async_task_tables_ready(self) -> None:
        self._ensure_conn()
        required_tables = {"task_job", "task_step", "task_result"}
        sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name IN (%s, %s, %s)
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.database, "task_job", "task_step", "task_result"))
            rows = cur.fetchall() or []
        existing = {row[0] for row in rows}
        missing = sorted(required_tables - existing)
        if missing:
            raise RuntimeError(
                "缺少异步任务表: "
                + ", ".join(missing)
                + "。请先执行 db_bk/db_sql.sql 中的任务表 DDL。"
            )

    def create_task_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_conn()
        sql = """
            INSERT INTO task_job (
                job_id, task_type, source_type, status, conversation_id, trigger_message_id,
                input_payload, runtime_config, current_stage, progress, result_status,
                error_message, created_at, started_at, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        now = datetime.datetime.now()
        values = (
            payload["job_id"],
            payload["task_type"],
            payload["source_type"],
            payload["status"],
            payload.get("conversation_id"),
            payload.get("trigger_message_id"),
            self._json_dumps(payload.get("input_payload") or {}),
            self._json_dumps(payload.get("runtime_config") or {}),
            payload.get("current_stage") or "queued",
            float(payload.get("progress") or 0.0),
            payload.get("result_status") or "",
            payload.get("error_message") or "",
            payload.get("created_at") or now,
            payload.get("started_at"),
            payload.get("finished_at"),
        )
        with self.conn.cursor() as cur:
            cur.execute(sql, values)
        self.conn.commit()
        return self.get_task_job(payload["job_id"]) or {}

    def update_task_job(self, job_id: str, **fields: Any) -> Dict[str, Any] | None:
        self._ensure_conn()
        allowed = {
            "status",
            "current_stage",
            "progress",
            "result_status",
            "error_message",
            "started_at",
            "finished_at",
            "runtime_config",
        }
        updates = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "runtime_config":
                value = self._json_dumps(value or {})
            updates.append(f"{key} = %s")
            values.append(value)
        if not updates:
            return self.get_task_job(job_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        sql = f"UPDATE task_job SET {', '.join(updates)} WHERE job_id = %s"
        values.append(job_id)
        with self.conn.cursor() as cur:
            cur.execute(sql, values)
        self.conn.commit()
        return self.get_task_job(job_id)

    def get_task_job(self, job_id: str) -> Dict[str, Any] | None:
        self._ensure_conn()
        sql = "SELECT * FROM task_job WHERE job_id = %s LIMIT 1"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (job_id,))
            row = cur.fetchone()
        if not row:
            return None
        row["input_payload"] = self._json_loads(row.get("input_payload"), {})
        row["runtime_config"] = self._json_loads(row.get("runtime_config"), {})
        row["progress"] = float(row.get("progress") or 0.0)
        return row

    def count_task_jobs_by_status(
        self,
        statuses: Sequence[str],
        *,
        task_type: str | None = None,
    ) -> int:
        self._ensure_conn()
        normalized = [str(status or "").strip() for status in statuses if str(status or "").strip()]
        if not normalized:
            return 0
        placeholders = ", ".join(["%s"] * len(normalized))
        sql = f"SELECT COUNT(1) FROM task_job WHERE status IN ({placeholders})"
        params: List[Any] = list(normalized)
        if task_type:
            sql += " AND task_type = %s"
            params.append(str(task_type).strip())
        with self.conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
        if isinstance(row, (list, tuple)):
            return int(row[0] or 0)
        if isinstance(row, dict):
            return int(next(iter(row.values()), 0) or 0)
        return int(row or 0)

    def insert_task_step(self, payload: Dict[str, Any]) -> int:
        self._ensure_conn()
        sql = """
            INSERT INTO task_step (
                job_id, seq, stage, step_type, title, status, tool_name,
                input_summary, output_summary, llm_usage, message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stage = VALUES(stage),
                step_type = VALUES(step_type),
                title = VALUES(title),
                status = VALUES(status),
                tool_name = VALUES(tool_name),
                input_summary = VALUES(input_summary),
                output_summary = VALUES(output_summary),
                llm_usage = VALUES(llm_usage),
                message = VALUES(message)
        """
        values = (
            payload["job_id"],
            int(payload["seq"]),
            payload.get("stage") or "running",
            payload.get("step_type") or "log",
            payload.get("title") or "",
            payload.get("status") or "completed",
            payload.get("tool_name"),
            self._json_dumps(payload.get("input_summary") or {}),
            self._json_dumps(payload.get("output_summary") or {}),
            self._json_dumps(payload.get("llm_usage") or {}),
            payload.get("message") or "",
        )
        with self.conn.cursor() as cur:
            ret = cur.execute(sql, values)
        self.conn.commit()
        return int(ret or 0)

    def list_task_steps(self, job_id: str) -> List[Dict[str, Any]]:
        self._ensure_conn()
        sql = "SELECT * FROM task_step WHERE job_id = %s ORDER BY seq ASC, step_id ASC"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (job_id,))
            rows = cur.fetchall() or []
        for row in rows:
            row["input_summary"] = self._json_loads(row.get("input_summary"), {})
            row["output_summary"] = self._json_loads(row.get("output_summary"), {})
            row["llm_usage"] = self._json_loads(row.get("llm_usage"), {})
        return rows

    def upsert_task_result(self, job_id: str, result_type: str, content: Any, meta: Any = None) -> int:
        self._ensure_conn()
        sql = """
            INSERT INTO task_result (job_id, result_type, content, meta)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                content = VALUES(content),
                meta = VALUES(meta),
                updated_at = CURRENT_TIMESTAMP
        """
        with self.conn.cursor() as cur:
            ret = cur.execute(
                sql,
                (
                    job_id,
                    result_type,
                    self._json_dumps(content),
                    self._json_dumps(meta or {}),
                ),
            )
        self.conn.commit()
        return int(ret or 0)

    def get_task_results(self, job_id: str) -> List[Dict[str, Any]]:
        self._ensure_conn()
        sql = "SELECT * FROM task_result WHERE job_id = %s ORDER BY result_id ASC"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (job_id,))
            rows = cur.fetchall() or []
        for row in rows:
            row["content"] = self._json_loads(row.get("content"), {})
            row["meta"] = self._json_loads(row.get("meta"), {})
        return rows

    def get_task_result_map(self, job_id: str) -> Dict[str, Any]:
        result_map: Dict[str, Any] = {}
        for row in self.get_task_results(job_id):
            result_map[str(row.get("result_type") or "")] = row.get("content")
        return result_map



class StockInfoDbUtils:
    """
    用于管理 MySQL 数据库连接，并执行对 realtime_stock_concept_info 表的插入和更新等操作。
    新表结构:
      - realtime_stock_concept_info 中 (company, trigger_date, concept) => unique key
      - code 不参与唯一
      - sotck_info (text) 用于存储json
    """

    def __init__(self,
                 host=None,
                 user=None,
                 password=None,
                 database="kingdomai",
                 connect_timeout=30,  # 延长连接超时时间
                 read_timeout=60 ,     # 延长查询超时时间
                 port=None):

        self.conv = conversions.copy()
        # 覆盖DECIMAL类型转换规则
        self.conv[FIELD_TYPE.DECIMAL] = float
        self.conv[FIELD_TYPE.NEWDECIMAL] = float
        configured_host = str(os.getenv("KINGDOMAI_DB_HOST") or "").strip()
        configured_user = str(os.getenv("KINGDOMAI_DB_USER") or "").strip()
        configured_password = str(os.getenv("KINGDOMAI_DB_PASSWORD") or "")
        configured_port = str(os.getenv("KINGDOMAI_DB_PORT") or "").strip()
        try:
            environment_port = int(configured_port) if configured_port else None
        except ValueError:
            environment_port = None
        resolved_host = host or configured_host or "127.0.0.1"
        resolved_user = user or configured_user
        resolved_password = password if password is not None else configured_password
        resolved_database = database
        resolved_port = port or environment_port or 3306
        credential_source_name = str(
            os.getenv("KINGDOMAI_DB_CREDENTIAL_SOURCE") or ""
        ).strip()
        credential_source_url = (
            str(os.getenv(credential_source_name) or "").strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", credential_source_name)
            else ""
        )
        configured_database_url = str(os.getenv("KINGDOMAI_DB_URL") or "").strip()
        database_url = (
            configured_database_url
            or credential_source_url
            or str(os.getenv("BUSINESS_DB_URL") or "").strip()
        )
        if database_url:
            parsed = urlparse(database_url.replace("mysql+pymysql://", "mysql://", 1))
            url_database = (parsed.path or "/").lstrip("/")
            supplies_credentials_only = bool(
                credential_source_url
                and not configured_database_url
                and database_url == credential_source_url
            )
            if (
                parsed.scheme == "mysql"
                and parsed.hostname
                and (url_database == database or supplies_credentials_only)
            ):
                if host is None and not configured_host:
                    resolved_host = parsed.hostname
                if user is None and not configured_user:
                    resolved_user = unquote(parsed.username or resolved_user)
                if password is None and not configured_password:
                    resolved_password = unquote(parsed.password or "")
                resolved_database = database
                if port is None and environment_port is None:
                    resolved_port = parsed.port or 3306
        self.host = resolved_host
        self.user = resolved_user
        self.password = resolved_password
        self.database = resolved_database
        self.port = resolved_port
        self.conn = None
        self.connect_db()



    def connect_db(self):
        """
        建立数据库连接
        """
        self.conn = pymysql.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database,
            port=self.port,
            conv=self.conv,
            **_mysql_utf8mb4_kwargs(),
        )

    def close_db(self):
        """
        关闭数据库连接
        """
        if self.conn:
            self.conn.close()
            self.conn = None

    def reconnect(self):
        """
        主动断开并重新连接数据库
        """
        try:
            print("[StockInfoDbUtils] Reconnecting...")
            self.close_db()
            time.sleep(1)
            self.connect_db()
            print("[StockInfoDbUtils] Reconnected successfully.")
        except Exception as e:
            print(f"[StockInfoDbUtils] Reconnect failed: {e}")
            raise e

    def __del__(self):
        self.close_db()

    def get_stock_history_hq(self, stk_code, start_date=None, end_date=None ,ret_format="dataframe"):
        """
        获取股票历史行情数据
        :param stk_code: 证券代码
        :param start_date: 起始日期 (格式: 'YYYY-MM-DD')
        :param end_date: 结束日期 (格式: 'YYYY-MM-DD')
        :return: list of dict, 每个 dict 包含日期、开盘价、收盘价等信息
        """
        if not self.conn.open:
            self.connect_db()

        sql = f"""
        SELECT trade_date, open, close, high, low, volume,amount, turn_ratio as turnover
        FROM kcrp_stock_price
        WHERE stk_code = '{stk_code}'
        """

        if start_date:
            sql += f" AND trade_date >= '{start_date}'"
        if end_date:
            sql += f" AND trade_date <= '{end_date}'"

        sql += " ORDER BY trade_date desc"

        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        if ret_format == "dataframe":
            import pandas as pd
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame(rows)
        return rows

    def get_all_company_code_mkt(self):
        if not self.conn.open:
            self.connect_db()
        sql = f"""
                SELECT 
                    stk_code ,
                    stk_name 
                FROM kcrp_stock_baseinfo
                """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return rows

    def get_active_company_code_mkt(self):
        """
        返回所有未退市的股票列表
        """
        if not self.conn.open:
            self.connect_db()
        sql = f"""
                SELECT 
                    stk_code ,
                    stk_name 
                FROM kcrp_stock_baseinfo
                WHERE delist_date = '2999-12-31'
                AND list_date IS NOT NULL;
                """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return rows

    def resolve_stock_identity(self, value: str) -> Optional[Dict[str, Any]]:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if not self.conn.open:
            self.connect_db()

        code6_match = re.fullmatch(r"(\d{6})", normalized)
        full_code_match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", normalized.upper())

        sql = """
            SELECT stk_code, stk_name, list_date, delist_date
            FROM kcrp_stock_baseinfo
            WHERE delist_date = '2999-12-31'
              AND (
                    stk_name = %s
                 OR stk_code = %s
                 OR LEFT(stk_code, 6) = %s
              )
            ORDER BY
              CASE
                WHEN stk_code = %s THEN 0
                WHEN LEFT(stk_code, 6) = %s THEN 1
                WHEN stk_name = %s THEN 2
                ELSE 9
              END,
              list_date DESC
            LIMIT 1
        """
        full_code = full_code_match.group(0) if full_code_match else normalized.upper()
        code6 = code6_match.group(1) if code6_match else (full_code_match.group(1) if full_code_match else normalized)
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (normalized, full_code, code6, full_code, code6, normalized))
            return cursor.fetchone()

    def get_latest_plate_trade_date(self) -> Optional[str]:
        if not self.conn.open:
            self.connect_db()
        sql = "SELECT MAX(trade_date) AS trade_date FROM kcrp_yp_plate_price"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            row = cursor.fetchone() or {}
        trade_date = row.get("trade_date")
        if not trade_date:
            return None
        return trade_date.strftime("%Y-%m-%d") if hasattr(trade_date, "strftime") else str(trade_date)

    def get_company_concept_profile(
        self,
        company_or_code: str,
        query_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.conn.open:
            self.connect_db()

        identity = self.resolve_stock_identity(company_or_code)
        if not identity:
            return {
                "company": str(company_or_code or "").strip(),
                "stk_code": "",
                "query_date": str(query_date or ""),
                "industries": [],
                "sectors": [],
                "concepts": [],
                "events": [],
                "concept_rows": [],
            }

        effective_date = str(query_date or "").strip() or (self.get_latest_plate_trade_date() or "")
        if not effective_date:
            return {
                "company": str(identity.get("stk_name") or company_or_code or "").strip(),
                "stk_code": str(identity.get("stk_code") or "").strip(),
                "query_date": "",
                "industries": [],
                "sectors": [],
                "concepts": [],
                "events": [],
                "concept_rows": [],
            }

        sql = """
            SELECT
                p.plate_code,
                p.plate_name,
                pp.trade_date,
                COALESCE(pp.amount, 0) AS amount
            FROM kcrp_yp_plate_member pm
            JOIN kcrp_yp_plate p
              ON p.plate_code = pm.plate_code
            LEFT JOIN kcrp_yp_plate_price pp
              ON pp.plate_code = p.plate_code
             AND pp.trade_date = %s
            WHERE pm.stk_code = %s
              AND (p.begin_date IS NULL OR p.begin_date <= %s)
              AND (p.end_date IS NULL OR p.end_date >= %s)
            ORDER BY COALESCE(pp.amount, 0) DESC, p.plate_code ASC
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, (effective_date, identity["stk_code"], effective_date, effective_date))
            rows = cursor.fetchall() or []

        normalized_rows = []
        for row in rows:
            trade_date = row.get("trade_date")
            normalized_rows.append(
                {
                    "plate_code": str(row.get("plate_code") or "").strip(),
                    "plate_name": str(row.get("plate_name") or "").strip(),
                    "trade_date": trade_date.strftime("%Y-%m-%d") if hasattr(trade_date, "strftime") else str(trade_date or ""),
                    "amount": float(row.get("amount") or 0),
                }
            )

        top_concept = normalized_rows[0]["plate_name"] if normalized_rows else ""
        return {
            "company": str(identity.get("stk_name") or company_or_code or "").strip(),
            "stk_code": str(identity.get("stk_code") or "").strip(),
            "query_date": effective_date,
            "industries": [],
            "sectors": [],
            "concepts": [top_concept] if top_concept else [],
            "events": [],
            "concept_rows": normalized_rows,
        }



    def get_company_basic_info(self, stk_code):
        """
        获取公司基本面核心指标
        :param stk_code: 证券代码
        :return: 结构化字典，包含分类指标
        """
        if not self.conn.open:
            self.connect_db()

        result_dict = {
            "估值指标": {},
            "盈利能力": {},
            "偿债能力": {},
            "运营效率": {},
            "股东结构": {},
            "市场表现": {}
        }

        try:
            with self.conn.cursor() as cursor:
                # 获取最新财务指标
                fin_sql = f"""
                SELECT 
                    roe_avg AS '净资产收益率(%)',
                    gp_margin AS '销售毛利率(%)',
                    np_margin AS '销售净利率(%)',
                    current_ratio AS '流动比率',
                    quick_ratio AS '速动比率',
                    libility_to_asset AS '资产负债率(%)',
                    asset_turn_ratio AS '总资产周转率(次)',
                    arturn_ratio AS '应收账款周转率(次)'
                FROM kcrp_stock_financial_indicator 
                WHERE stk_code = '{stk_code}'
                AND report_period = (
                    SELECT MAX(report_period) 
                    FROM kcrp_stock_financial_indicator 
                    WHERE stk_code = '{stk_code}'
                )
                """
                cursor.execute(fin_sql)
                fin_data = cursor.fetchone()
                if fin_data:
                    result_dict["盈利能力"].update({
                        "净资产收益率(%)": fin_data[0],
                        "销售毛利率(%)": fin_data[1],
                        "销售净利率(%)": fin_data[2]
                    })
                    result_dict["偿债能力"].update({
                        "流动比率": fin_data[3],
                        "速动比率": fin_data[4],
                        "资产负债率(%)": fin_data[5]
                    })
                    result_dict["运营效率"].update({
                        "总资产周转率(次)": fin_data[6],
                        "应收账款周转率(次)": fin_data[7]
                    })

                # 获取最新估值数据
                val_sql = f"""
                SELECT 
                    pe_ttm AS '市盈率(TTM)',
                    pb_lf AS '市净率(LF)',
                    ps_ttm AS '市销率(TTM)',
                    free_float_mv AS '自由流通市值(元)',
                    turn_ratio AS '换手率(%)'
                FROM kcrp_stock_pricevaluate 
                WHERE stk_code = '{stk_code}'
                ORDER BY trade_date DESC 
                LIMIT 1
                """
                cursor.execute(val_sql)
                val_data = cursor.fetchone()
                if val_data:
                    result_dict["估值指标"].update({
                        "市盈率(TTM)": val_data[0],
                        "市净率(LF)": val_data[1],
                        "市销率(TTM)": val_data[2]
                    })
                    result_dict["市场表现"].update({
                        "自由流通市值(元)": val_data[3],
                        "换手率(%)": val_data[4]
                    })

                # 获取十大股东信息
                holder_sql = f"""
                SELECT 
                    holder_name AS '股东名称',
                    holder_pct AS '持股比例(%)'
                FROM kcrp_stock_holderfloat
                WHERE stk_code = '{stk_code}'
                AND end_date = (
                    SELECT MAX(end_date) 
                    FROM kcrp_stock_holderfloat 
                    WHERE stk_code = '{stk_code}'
                )
                ORDER BY holder_rank 
                LIMIT 5
                """
                cursor.execute(holder_sql)
                holders = cursor.fetchall()
                if holders:
                    result_dict["股东结构"]["前五大流通股东"] = {
                        f"第{i+1}大股东": {"名称": h[0], "持股比例(%)": h[1]}
                        for i, h in enumerate(holders)
                    }

        except Exception as e:
            raise RuntimeError(f"数据查询失败: {str(e)}")

        # 清理空值
        #decimal_to_float(result_dict)



        return {k:v for k,v in result_dict.items() if v}

    # 历史PE列表
    # 选中当天的PE 总市值
    # PE分位数
    def get_value_stock_company_basic_info(self, stk_code):
        """
        获取公司基本面核心指标
        :param stk_code: 证券代码
        :return: 结构化字典，包含分类指标
        """

        def to_quarter_str(dt_obj):
            """datetime.date → 'YYYY_Qx' 字符串"""
            q = (dt_obj.month - 1) // 3 + 1
            return f"{dt_obj.year}_Q{q}"

        if not self.conn.open:
            self.connect_db()



        # 四个指标字段与对应 SQL（示例）
        queries = {
            'income':f"""
                select report_period , tot_oper_rev from kcrp_stock_income where statement_type='HB'
                """,
            'revenue':f"""
                select report_period , net_profit_after_nrgal_atsolc from kcrp_stock_income where statement_type='HB'
                """,
            'cash':f"""
                select report_period , net_cash_flows_oper_act from kcrp_stock_cashflow where   statement_type =  'HBTZ'
                """,
            'roe':f"""
                SELECT report_period , roe_avg FROM kcrp_stock_financial_indicator  where 1=1
                """
        }

        from collections import defaultdict
        snapshot = defaultdict(dict)     # {period: {metric: value}}

        with self.conn.cursor() as cursor:
            for field, base_sql in queries.items():
                sql = (
                    f"{base_sql} "
                    f"and stk_code = %s "
                    f"ORDER BY report_period DESC "
                    f"LIMIT 12"
                )
                cursor.execute(sql, (stk_code,))
                for period, value in cursor.fetchall():
                    snapshot[period][field] = value

        # ---- 计算 roe_YOY（与前 4 个季度相比） ----
        periods_sorted = sorted(snapshot.keys(), reverse=True)  # 最新在前
        interval = 4                                            # 同比间隔 4 个季度
        #snapshot_new = defaultdict(dict)
        for idx, period in enumerate(periods_sorted):
            prev_idx = idx + interval
            for metric in queries.keys():
                if prev_idx < len(periods_sorted):
                    prev_period = periods_sorted[prev_idx]
                    prev_val = snapshot[prev_period].get(metric)
                    curr_val = snapshot[period].get(metric)
                    if prev_val not in (None, 0):
                        yoy = round((curr_val - prev_val) / abs(prev_val) * 100,2)
                    else:
                        yoy = None
                else:
                    yoy = None
                #snapshot_new[period][metric]['value']  = snapshot[prev_period].get(metric)
                #snapshot_new[period][metric]['yoy']    = yoy
                snapshot[period][f"{metric}_YOY"] = yoy


        formatted_snapshot = {}
        for period_dt in periods_sorted[:8]:                 # 仅保留最新 8 期
            quarter_key = to_quarter_str(period_dt)
            formatted_snapshot[quarter_key] = {}
            for key in snapshot[period_dt]:
                index_name = key.replace("_YOY","")
                if index_name not in formatted_snapshot[quarter_key]:
                    formatted_snapshot[quarter_key][index_name] = {}
                value = snapshot[period_dt][key]
                if 'YOY' in key:
                    formatted_snapshot[quarter_key][index_name]['yoy'] = value
                else:
                    if value > 10e2 or value < 10e-2:
                        value = round(value/10e8,2)
                    value = round(value,2)
                    formatted_snapshot[quarter_key][index_name]['value'] =  value
        return formatted_snapshot

    def get_value_stock_pe(self, stk_code, date):
        """
        获取报告期PE列表
        由于四个报告期当天是否开盘判断太过复杂，包括假期等的影响，因此这里统一取每个月的十五号的pe值，并取到列表满24个值为止
        """

        if not self.conn.open:
            self.connect_db()

        sql = f"""
              SELECT 
              pe_ttm
              FROM 
              kcrp_stock_pricevaluate
              WHERE 
              stk_code = %s
              AND trade_date = %s
              limit 1
              """
        ret = []
        with self.conn.cursor() as cursor:
             cursor.execute(sql, (stk_code,date))
             value = cursor.fetchone()
             if value:
                 return value[0]
             else:
                 return None

    def get_stock_price_pct(self,code_list , date_str=""):

        if not self.conn.open:
            self.connect_db()
        if not code_list :
            return None
        code_str = '","'.join(code_list)
        code_str = f'"{code_str}"'
        placeholders = ', '.join(['%s'] * len(code_list))
        sql = f"""
              SELECT 
              stk_code, close, rise_fall_rate , adjclose
              FROM 
              kcrp_stock_price
              WHERE 
              stk_code in ( {placeholders} )
              AND trade_date = %s
              """
        params = tuple(code_list) + (date_str,)
        ret = {}

        with self.conn.cursor() as cursor:
             cursor.execute(sql, params)
             for c,v,p,adjv in cursor.fetchall():
                 ret[c] = (v,p,adjv)
        print(f"len(ret) : {len(ret)}")
        return ret


    def get_value_stock_pe_series(self, stk_code):
        """
        获取报告期PE列表
        """

        if not self.conn.open:
            self.connect_db()

        sql = f"""
              SELECT 
              pe_ttm
              FROM 
              kcrp_stock_pricevaluate
              WHERE 
              stk_code = %s
              AND trade_date > DATE_SUB(CURDATE(), INTERVAL 9 YEAR)
              and pe_ttm >0
              ORDER BY 
              pe_ttm ASC
              """
        ret = []
        with self.conn.cursor() as cursor:
             cursor.execute(sql, (stk_code,))
             for value in cursor.fetchall():
                 value = value[0]
                 value = round(value,2)
                 ret.append(value)
        return ret

    def condition_query(
        self,
        target_table: str,
        filter_condition_dic: Dict[str, Any],
        query_index_list: Sequence[str],
    ) -> List[Tuple[Any, ...]]:
        """
        通用条件查询

        :param target_table: 要查询的表名
        :param filter_condition_dic: 过滤条件字典，**必须**含 stk_code 与 report_period
        :param query_index_list: 要返回的列名序列
        :return: cursor.fetchall() 的结果，默认是元组列表
        """
        # 1. 检查必备条件
        required = {"stk_code"}
        missing = required - filter_condition_dic.keys()
        if missing:
            raise ValueError(f"filter_condition_dic 缺少必须字段: {', '.join(missing)}")

        if not query_index_list:
            raise ValueError("query_index_list 不能为空")


        # 3. 拼接 SELECT 与 WHERE
        select_clause = ", ".join(f"`{col}`" for col in query_index_list)
        where_clause = " AND ".join(f"`{k}` = %s" for k in filter_condition_dic.keys())
        sql = f"SELECT {select_clause} FROM `{target_table}` WHERE {where_clause}"

        # 4. 执行查询
        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(filter_condition_dic.values()))
            rows = cursor.fetchall()  # 默认返回元组，可按需转 dict
        return rows


    def get_stock_price_series(self, stk_code, start_date=None, end_date=None):
        """
        获取单支股票的历史复权价序列（来自 kcrp_stock_price）
        :param stk_code: 股票代码，如 'sh600519'
        :param start_date: 起始日期，格式 'YYYYMMDD'
        :param end_date: 终止日期，格式 'YYYYMMDD'
        :return: List[Tuple[trade_date(str), adjclose(float)]]
        """
        if not self.conn.open:
            self.connect_db()

        sql = f"""
        SELECT trade_date, adjclose
        FROM kcrp_stock_price
        WHERE stk_code = %s
        """
        params = [stk_code]
        if start_date:
            sql += " AND trade_date >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND trade_date <= %s"
            params.append(end_date)
        sql += " ORDER BY trade_date ASC"

        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
            # rows: list of tuples [(trade_date, adjclose), ...]
        return rows

    # ===================== 新增：AI综合诊断每日更新 & 查询 ===================== #



    def _json_dumps(self, obj) -> str:
        # 统一用 _jsonlib，避免被其它同名导入覆盖
        return _jsonlib.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    def _json_loads(self, text: Any, default: Any = None) -> Any:
        if text in (None, ""):
            return default
        if isinstance(text, (dict, list)):
            return text
        try:
            return _jsonlib.loads(text)
        except Exception:
            return default

    def ensure_async_task_tables(self) -> None:
        self._ensure_conn()
        statements = [
            """
            CREATE TABLE IF NOT EXISTS task_job (
                job_id VARCHAR(64) PRIMARY KEY,
                task_type VARCHAR(64) NOT NULL,
                source_type VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL,
                conversation_id VARCHAR(64) NULL,
                trigger_message_id VARCHAR(64) NULL,
                input_payload LONGTEXT NULL,
                runtime_config LONGTEXT NULL,
                current_stage VARCHAR(64) NOT NULL,
                progress DECIMAL(5,2) NOT NULL DEFAULT 0.00,
                result_status VARCHAR(32) NOT NULL DEFAULT '',
                error_message TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME NULL,
                finished_at DATETIME NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_task_job_status_created (status, created_at),
                KEY idx_task_job_conversation (conversation_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS task_step (
                step_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                job_id VARCHAR(64) NOT NULL,
                seq INT NOT NULL,
                stage VARCHAR(64) NOT NULL,
                step_type VARCHAR(64) NOT NULL,
                title VARCHAR(255) NOT NULL,
                status VARCHAR(32) NOT NULL,
                tool_name VARCHAR(128) NULL,
                input_summary LONGTEXT NULL,
                output_summary LONGTEXT NULL,
                llm_usage LONGTEXT NULL,
                message TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_task_step_job_seq (job_id, seq),
                KEY idx_task_step_job_created (job_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS task_result (
                result_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                job_id VARCHAR(64) NOT NULL,
                result_type VARCHAR(64) NOT NULL,
                content LONGTEXT NULL,
                meta LONGTEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_task_result_job_type (job_id, result_type),
                KEY idx_task_result_job (job_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
        ]
        with self.conn.cursor() as cur:
            for sql in statements:
                cur.execute(sql)
        self.conn.commit()

    def check_async_task_tables_ready(self) -> None:
        self._ensure_conn()
        required_tables = {"task_job", "task_step", "task_result"}
        sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name IN (%s, %s, %s)
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (self.database, "task_job", "task_step", "task_result"))
            rows = cur.fetchall() or []
        existing = {row[0] for row in rows}
        missing = sorted(required_tables - existing)
        if missing:
            raise RuntimeError(
                "缺少异步任务表: "
                + ", ".join(missing)
                + "。请先执行 db_bk/db_sql.sql 中的任务表 DDL。"
            )

    def create_task_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_conn()
        sql = """
            INSERT INTO task_job (
                job_id, task_type, source_type, status, conversation_id, trigger_message_id,
                input_payload, runtime_config, current_stage, progress, result_status,
                error_message, created_at, started_at, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        now = datetime.datetime.now()
        values = (
            payload["job_id"],
            payload["task_type"],
            payload["source_type"],
            payload["status"],
            payload.get("conversation_id"),
            payload.get("trigger_message_id"),
            self._json_dumps(payload.get("input_payload") or {}),
            self._json_dumps(payload.get("runtime_config") or {}),
            payload.get("current_stage") or "queued",
            float(payload.get("progress") or 0.0),
            payload.get("result_status") or "",
            payload.get("error_message") or "",
            payload.get("created_at") or now,
            payload.get("started_at"),
            payload.get("finished_at"),
        )
        with self.conn.cursor() as cur:
            cur.execute(sql, values)
        self.conn.commit()
        return self.get_task_job(payload["job_id"]) or {}

    def update_task_job(self, job_id: str, **fields: Any) -> Dict[str, Any] | None:
        self._ensure_conn()
        allowed = {
            "status",
            "current_stage",
            "progress",
            "result_status",
            "error_message",
            "started_at",
            "finished_at",
            "runtime_config",
        }
        updates = []
        values: List[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "runtime_config":
                value = self._json_dumps(value or {})
            updates.append(f"{key} = %s")
            values.append(value)
        if not updates:
            return self.get_task_job(job_id)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        sql = f"UPDATE task_job SET {', '.join(updates)} WHERE job_id = %s"
        values.append(job_id)
        with self.conn.cursor() as cur:
            cur.execute(sql, values)
        self.conn.commit()
        return self.get_task_job(job_id)

    def get_task_job(self, job_id: str) -> Dict[str, Any] | None:
        self._ensure_conn()
        sql = "SELECT * FROM task_job WHERE job_id = %s LIMIT 1"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (job_id,))
            row = cur.fetchone()
        if not row:
            return None
        row["input_payload"] = self._json_loads(row.get("input_payload"), {})
        row["runtime_config"] = self._json_loads(row.get("runtime_config"), {})
        row["progress"] = float(row.get("progress") or 0.0)
        return row

    def insert_task_step(self, payload: Dict[str, Any]) -> int:
        self._ensure_conn()
        sql = """
            INSERT INTO task_step (
                job_id, seq, stage, step_type, title, status, tool_name,
                input_summary, output_summary, llm_usage, message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stage = VALUES(stage),
                step_type = VALUES(step_type),
                title = VALUES(title),
                status = VALUES(status),
                tool_name = VALUES(tool_name),
                input_summary = VALUES(input_summary),
                output_summary = VALUES(output_summary),
                llm_usage = VALUES(llm_usage),
                message = VALUES(message)
        """
        values = (
            payload["job_id"],
            int(payload["seq"]),
            payload.get("stage") or "running",
            payload.get("step_type") or "log",
            payload.get("title") or "",
            payload.get("status") or "completed",
            payload.get("tool_name"),
            self._json_dumps(payload.get("input_summary") or {}),
            self._json_dumps(payload.get("output_summary") or {}),
            self._json_dumps(payload.get("llm_usage") or {}),
            payload.get("message") or "",
        )
        with self.conn.cursor() as cur:
            ret = cur.execute(sql, values)
        self.conn.commit()
        return int(ret or 0)

    def list_task_steps(self, job_id: str) -> List[Dict[str, Any]]:
        self._ensure_conn()
        sql = "SELECT * FROM task_step WHERE job_id = %s ORDER BY seq ASC, step_id ASC"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (job_id,))
            rows = cur.fetchall() or []
        for row in rows:
            row["input_summary"] = self._json_loads(row.get("input_summary"), {})
            row["output_summary"] = self._json_loads(row.get("output_summary"), {})
            row["llm_usage"] = self._json_loads(row.get("llm_usage"), {})
        return rows

    def upsert_task_result(self, job_id: str, result_type: str, content: Any, meta: Any = None) -> int:
        self._ensure_conn()
        sql = """
            INSERT INTO task_result (job_id, result_type, content, meta)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                content = VALUES(content),
                meta = VALUES(meta),
                updated_at = CURRENT_TIMESTAMP
        """
        with self.conn.cursor() as cur:
            ret = cur.execute(
                sql,
                (
                    job_id,
                    result_type,
                    self._json_dumps(content),
                    self._json_dumps(meta or {}),
                ),
            )
        self.conn.commit()
        return int(ret or 0)

    def get_task_results(self, job_id: str) -> List[Dict[str, Any]]:
        self._ensure_conn()
        sql = "SELECT * FROM task_result WHERE job_id = %s ORDER BY result_id ASC"
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (job_id,))
            rows = cur.fetchall() or []
        for row in rows:
            row["content"] = self._json_loads(row.get("content"), {})
            row["meta"] = self._json_loads(row.get("meta"), {})
        return rows

    def get_task_result_map(self, job_id: str) -> Dict[str, Any]:
        result_map: Dict[str, Any] = {}
        for row in self.get_task_results(job_id):
            result_map[str(row.get("result_type") or "")] = row.get("content")
        return result_map



    # --------- 新增：AI综合诊断所需的 SELECT 封装（仅新增代码） --------- #
    def _ensure_conn(self):
        if not self.conn or not self.conn.open:
            self.connect_db()

    def get_latest_fin_period(self, stk_code: str) -> str | None:
        """最新财报期（如 20250331）"""
        self._ensure_conn()
        sql = """
            SELECT MAX(report_period)
            FROM kcrp_stock_income
            WHERE stk_code = %s
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (stk_code,))
            row = cur.fetchone()
            return row[0] if row and row[0] else None

    def get_growth_numbers(self, stk_code: str, limit_quarters: int = 40) -> dict:
        """
        成长板块：营业收入/净利润序列数据
        返回: {"revenue_series": [{"period": "...", "value": ...}, ...], 
              "net_income_series": [{"period": "...", "value": ...}, ...]}
        """
        self._ensure_conn()
        sql = """
            SELECT report_period,
                   tot_oper_rev AS revenue,
                   net_profit_after_nrgal_atsolc AS net_income
            FROM kcrp_stock_income
            WHERE stk_code = %s AND statement_type = 'HB'
            ORDER BY report_period DESC
            LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (stk_code, int(limit_quarters)))
            rows = cur.fetchall()
        
        rows = rows or []
        rows_sorted = sorted(rows, key=lambda x: x["report_period"])
        
        # 构建收入序列和净利润序列
        revenue_series = [
            {
                "period": self._fmt_date(r["report_period"]),
                "value": float(r["revenue"]) if r["revenue"] is not None else None,
            }
            for r in rows_sorted
        ]
        
        net_income_series = [
            {
                "period": self._fmt_date(r["report_period"]),
                "value": float(r["net_income"]) if r["net_income"] is not None else None,
            }
            for r in rows_sorted
        ]
        
        return {
            "revenue_series": revenue_series,
            "net_income_series": net_income_series
        }


    def get_profitability_series(self, stk_code: str, limit_quarters: int = 40) -> list[dict]:
        """
        盈利板块：近四个季度 + 本期 的毛利率/净利率/ROE
        返回按时间升序: [{"period": "...", "gross_margin": x, "net_margin": y, "roe": z}, ...]
        """
        self._ensure_conn()
        sql = """
            SELECT report_period,
                   gp_margin AS gross_margin,
                   np_margin AS net_margin,
                   roe_avg    AS roe
            FROM kcrp_stock_financial_indicator
            WHERE stk_code = %s
            ORDER BY report_period DESC
            LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (stk_code, int(limit_quarters)))
            rows = cur.fetchall()
        rows = rows or []
        rows_sorted = sorted(rows, key=lambda x: x["report_period"])
        return [
            {
                "period": self._fmt_date(r["report_period"]),
                "gross_margin": float(r["gross_margin"]) if r["gross_margin"] is not None else None,
                "net_margin": float(r["net_margin"]) if r["net_margin"] is not None else None,
                "roe": float(r["roe"]) if r["roe"] is not None else None,
            }
            for r in rows_sorted
        ]

    def get_inventory_turnover_series(self, stk_code: str, limit_quarters: int = 40) -> list[dict]:
        """
        运营板块：存货周转率（近几年/近若干季）
        优先取 kcrp_stock_financial_indicator.invturn_ratio；若无可按需替换为其它周转指标。
        """
        self._ensure_conn()
        sql = """
            SELECT report_period,
                   invturn_ratio
            FROM kcrp_stock_financial_indicator
            WHERE stk_code = %s
            ORDER BY report_period DESC
            LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (stk_code, int(limit_quarters)))
            rows = cur.fetchall() or []
        rows_sorted = sorted(rows, key=lambda x: x["report_period"])
        return [
            {"period": self._fmt_date(r["report_period"]),
             "value": float(r["invturn_ratio"]) if r["invturn_ratio"] is not None else None}
            for r in rows_sorted
        ]


    def get_solvency_series(self, stk_code: str, limit_quarters: int = 40) -> list[dict]:
        """
        偿债能力板块：近若干季度的偿债能力指标
        返回按时间升序: [{"period": "...", "quick_ratio": x, "current_ratio": y, "cash_ratio": z, "libility_to_asset": a, "ebit_to_interest": b}, ...]
        """
        self._ensure_conn()
        sql = """
            SELECT report_period,
                   quick_ratio,
                   current_ratio,
                   cash_ratio,
                   libility_to_asset,
                   ebit_to_interest
            FROM kcrp_stock_financial_indicator
            WHERE stk_code = %s
            ORDER BY report_period DESC
            LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (stk_code, int(limit_quarters)))
            rows = cur.fetchall()
        rows = rows or []
        rows_sorted = sorted(rows, key=lambda x: x["report_period"])
        return [
            {
                "period": self._fmt_date(r["report_period"]),
                "quick_ratio": float(r["quick_ratio"]) if r["quick_ratio"] is not None else None,
                "current_ratio": float(r["current_ratio"]) if r["current_ratio"] is not None else None,
                "cash_ratio": float(r["cash_ratio"]) if r["cash_ratio"] is not None else None,
                "libility_to_asset": float(r["libility_to_asset"]) if r["libility_to_asset"] is not None else None,
                "ebit_to_interest": float(r["ebit_to_interest"]) if r["ebit_to_interest"] is not None else None,
            }
            for r in rows_sorted
        ]

    def get_cashflow_quarter_series(self, stk_code: str, limit_quarters: int = 40) -> dict:
        """
        现金流板块：经营活动现金流 vs 净利润（近四季+本期）
        返回:
        {
          "series": {
             "ocf": [{"period":..., "value":...}, ...],
             "net_income": [{"period":..., "value":...}, ...],
             "ocf_to_revenue": [{"period":..., "value":...}, ...],
             "ni_to_revenue": [{"period":..., "value":...}, ...]
          },
          "ocf_value": 本期OCF,
          "ocf_yoy": 本期同比(%)
        }
        """
        self._ensure_conn()
        # OCF
        sql_ocf = """
            SELECT report_period, net_cash_flows_oper_act AS ocf
            FROM kcrp_stock_cashflow
            WHERE stk_code = %s AND statement_type = 'HBTZ'
            ORDER BY report_period DESC
            LIMIT %s
        """
        # 净利润和营业收入
        sql_ni_revenue = """
            SELECT report_period, net_profit_after_nrgal_atsolc AS ni, tot_oper_rev AS revenue
            FROM kcrp_stock_income
            WHERE stk_code = %s AND statement_type = 'HB'
            ORDER BY report_period DESC
            LIMIT %s
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql_ocf, (stk_code, int(limit_quarters)))
            ocf_rows = cur.fetchall() or []
            cur.execute(sql_ni_revenue, (stk_code, int(limit_quarters)))
            ni_revenue_rows = cur.fetchall() or []

        # 升序
        ocf_rows = sorted(ocf_rows, key=lambda x: x["report_period"])
        ni_revenue_rows = sorted(ni_revenue_rows, key=lambda x: x["report_period"])
        
        # 创建报告期到营业收入的映射
        revenue_map = {}
        for r in ni_revenue_rows:
            if r["revenue"] is not None and float(r["revenue"]) != 0:
                revenue_map[r["report_period"]] = float(r["revenue"])
            else:
                revenue_map[r["report_period"]] = None
        
        # 计算ocf_to_revenue和ni_to_revenue
        ocf_to_revenue = []
        ni_to_revenue = []
        
        # 计算OCF/营业收入
        for r in ocf_rows:
            period = r["report_period"]
            ocf_value = float(r["ocf"]) if r["ocf"] is not None else None
            revenue = revenue_map.get(period)
            ratio = None
            if ocf_value is not None and revenue is not None:
                ratio = round(ocf_value / revenue * 100, 2)  # 转换为百分比
            ocf_to_revenue.append({
                "period": self._fmt_date(period),
                "value": ratio
            })
        
        # 计算净利润/营业收入
        for r in ni_revenue_rows:
            period = r["report_period"]
            ni_value = float(r["ni"]) if r["ni"] is not None else None
            revenue = revenue_map.get(period)
            ratio = None
            if ni_value is not None and revenue is not None:
                ratio = round(ni_value / revenue * 100, 2)  # 转换为百分比
            ni_to_revenue.append({
                "period": self._fmt_date(period),
                "value": ratio
            })
        
        # 本期与去年同期
        ocf_curr = ocf_rows[-1]["ocf"] if ocf_rows else None
        ocf_last_year = ocf_rows[-5]["ocf"] if len(ocf_rows) >= 5 else None
        ocf_yoy = None
        if ocf_curr is not None and ocf_last_year not in (None, 0):
            ocf_yoy = round((float(ocf_curr) - float(ocf_last_year)) / abs(float(ocf_last_year)) * 100, 2)

        # 提取净利润数据用于net_income系列
        ni_series = []
        for r in ni_revenue_rows:
            ni_series.append({
                "period": self._fmt_date(r["report_period"]),
                "value": float(r["ni"]) if r["ni"] is not None else None
            })

        return {
            "series": {
                "ocf": [{"period": self._fmt_date(r["report_period"]),
                         "value": float(r["ocf"]) if r["ocf"] is not None else None} for r in ocf_rows],
                "net_income": ni_series,
                "ocf_to_revenue": ocf_to_revenue,
                "ni_to_revenue": ni_to_revenue
            },
            "ocf_value": float(ocf_curr) if ocf_curr is not None else None,
            "ocf_yoy": ocf_yoy
        }

    # ---------- 2) 估值/动量：用 change_ratio_30d / change_ratio_3m ----------
    def get_momentum_valuation_snapshot(self, stk_code: str) -> dict:
        """
        估值/动量快照：取最新一日的 PE/PB，以及 30天/3月涨跌幅
        返回: {"pe_ttm":..., "pb":..., "ret_20d":..., "ret_60d":...}
        """
        self._ensure_conn()
        # 估值
        sql_val = """
            SELECT pe_ttm, pb_lf
            FROM kcrp_stock_pricevaluate
            WHERE stk_code = %s
            ORDER BY trade_date DESC
            LIMIT 1
        """
        with self.conn.cursor() as cur:
            cur.execute(sql_val, (stk_code,))
            r1 = cur.fetchone()
        # pe, pb = (float(r1[0]), float(r1[1])) if r1 else (None, None)
        # 处理pe，pb，避免float转换错误
        if r1:
            pe = float(r1[0]) if r1[0] is not None else None
            pb = float(r1[1]) if r1[1] is not None else None
        else:
            pe, pb = None, None

        # 动量：change_ratio_30d ≈ 近20~30个交易日；change_ratio_3m ≈ 近60个交易日
        sql_mom = """
            SELECT change_ratio_30d, change_ratio_3m
            FROM kcrp_dwd_stk_price_period
            WHERE stk_code = %s
            ORDER BY trade_date DESC
            LIMIT 1
        """
        m20 = m60 = None
        with self.conn.cursor() as cur:
            cur.execute(sql_mom, (stk_code,))
            r2 = cur.fetchone()
            if r2:
                m20 = float(r2[0]) if r2[0] is not None else None
                m60 = float(r2[1]) if r2[1] is not None else None

        return {"pe_ttm": pe, "pb": pb, "ret_20d": m20, "ret_60d": m60}

    # ---------- 1) 基础信息：用 market 替代 exchange ----------
    def get_baseinfo(self, stk_code: str) -> dict:
        """
        基础信息（名称/交易所）—— 用 market 字段；若无则按代码前缀推断。
        返回: {"stk_code":..., "stk_name":..., "exchange":...}
        """
        self._ensure_conn()
        sql = """
            SELECT stk_code, stk_name, market
            FROM kcrp_stock_baseinfo
            WHERE stk_code = %s
            LIMIT 1
        """
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, (stk_code,))
            row = cur.fetchone() or {}

        code = row.get("stk_code") or stk_code
        name = row.get("stk_name") or ""
        exchange = row.get("market")  # SH / SZ / BJ（如为空再推断）

        if not exchange:
            if "." in code:
                exchange = code.split(".")[-1].upper()
            else:
                if code.startswith("6"):
                    exchange = "SH"
                elif code.startswith(("0", "3")):
                    exchange = "SZ"
                elif code.startswith(("4", "8", "9")):
                    exchange = "BJ"
                else:
                    exchange = ""

        return {"stk_code": code, "stk_name": name, "exchange": exchange}

    # ✅ 新增一个小工具方法
    def _fmt_date(self, v):
        try:
            # date/datetime → "YYYY-MM-DD"
            import datetime as _dt
            if isinstance(v, (_dt.date, _dt.datetime)):
                return v.strftime("%Y-%m-%d")
        except Exception:
            pass
        return v if v is None or isinstance(v, str) else str(v)

# 测试函数
# def test_stock_detail():

#     print("test stock detail.")
#     # 源库：kingdomai
#     src = StockInfoDbUtils(
#         host="<configured-host>",
#         user="creditrisk",
#         database="kingdomai",
#         port=3306
#     )
#     # 目标库：stock_agent
#     dst = MySQLUtils(
#         host="47.94.1.2",
#         user="cubeyz",
#         database="stock_agent",
#         port=3312
#     )

#     # 构建入库数据
#     code = "600519.SH"  # 以你库中的真实 stk_code 为准
#     payload = src.build_ai_detail_payload(code)  # 生成入库字典
#     assert isinstance(payload, dict) and payload["code"], "payload 生成失败"
#     print("period_label:", payload["period_label"], payload)

#     # 写入目标库
#     n = dst.upsert_stock_ai_diagnosis(payload)
#     print("upsert affected:", n)

#     # 原始行（可检查 JSON 字段）
#     rows = dst.fetch_stock_ai_diagnosis_all(limit=1)
#     print("fetch_all sample:", rows[:1])

#     # API 视图
#     api_obj = dst.fetch_stock_ai_detail_api(code[:6])
#     print("api view:", json.dumps(api_obj))

if __name__ == "__main__":
    import json
    '''
    db = StockInfoDbUtils()
    #data = db.get_value_stock_company_basic_info("000001.SZ")  # 以平安银行为例
    hq_dic=data = db.get_stock_price_pct(["600231.sh","600232.sh",'300255.sz'],'20250429')  # 以平安银行为例
    hq_map={}
    for c in data:
        hq_map[c.split(".")[0]]= {'现价':hq_dic[c][0] ,'涨跌幅':hq_dic[c][1],"adj_price":hq_dic[c][2]}
    print(hq_map)
    '''

    test_stock_detail()

# 600519.SH调用get_profitability_series
# def main():
#     db = StockInfoDbUtils()
#     try:
#         data = db.get_profitability_series("600519.SH")
#         print(json.dumps(data, ensure_ascii=False, indent=2))
#     finally:
#         db.close_db()

# 输出000001.SZ到000010.SZ的所有公司基本面结果（JSON）
# def main():
#     db = StockInfoDbUtils()
#     try:
#         full_result = {}
#         for i in range(1, 11):                  # 000001 -> 000010
#             code = f"{i:06d}.SZ"
#             try:
#                 info = db.get_company_basic_info(code)  # 传入带后缀的代码
#                 full_result[code] = info
#             except Exception as e:
#                 full_result[code] = {"error": str(e)}
#
#         # 1. 直接控制台打印（缩进美观）
#         print(json.dumps(full_result, ensure_ascii=False, indent=2))
#
#     finally:
#         db.close_db()

# if __name__ == "__main__":
#     main()
