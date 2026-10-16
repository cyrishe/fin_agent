"""Read-only, bounded database observations; not a replacement for ingestion job logs."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pymysql

from src.utils.mysql_utils import StockInfoDbUtils, MySQLUtils

LOG = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")


class ObservationUnavailable(ValueError):
    """Safe operational explanation suitable for the public status page."""


class MonitorDatabase(StockInfoDbUtils):
    def connect_db(self):
        self.conn = pymysql.connect(host=self.host, port=self.port, user=self.user,
            password=self.password, database=self.database, charset="utf8mb4",
            connect_timeout=5, read_timeout=8, write_timeout=5, autocommit=True)


class MonitorHotDatabase(MySQLUtils):
    connect_db = MonitorDatabase.connect_db


@dataclass(frozen=True)
class Dataset:
    id: str
    group: str
    label: str
    table: str
    date_field: str = "trade_date"
    daily: bool = True
    trading: bool = True
    lag: int = 1
    database: str = "kingdomai"
    realtime: bool = False


def datasets() -> list[Dataset]:
    from src.experiments.staged_data_protocol.phase2.quote_provider import QUOTE_SOURCES
    from src.experiments.staged_data_protocol.phase2.moneyflow_provider import MONEYFLOW_SOURCES
    from src.experiments.staged_data_protocol.phase2.pricevalue_provider import PRICEVALUE_SOURCES
    from src.experiments.staged_data_protocol.phase2.base_info_provider import BASE_INFO_SOURCES
    from src.experiments.staged_data_protocol.phase2.stock_corporate_provider import STOCK_CORPORATE_VIEWS
    from src.experiments.staged_data_protocol.phase2.report_provider import REPORT_TABLE, METRIC_FACT_TABLE, METRIC_DEF_TABLE

    names = {"stock": "股票", "fund": "基金", "bond": "债券", "index": "指数", "plate": "板块", "industry": "行业"}
    result = []
    for view, label, sources in [("quote", "行情", QUOTE_SOURCES), ("moneyflow", "资金流", MONEYFLOW_SOURCES), ("pricevalue", "估值", PRICEVALUE_SOURCES)]:
        for subject, source in sources.items():
            result.append(Dataset(f"{subject}.{view}", names[subject], label, source.table))
    result.append(Dataset("stock.margin", "股票", "融资融券", "kcrp_stock_margintrade"))
    result.append(Dataset("stock.intraday_quote", "股票", "分钟K线", "aiia_stock_realtime_minute_snapshot", lag=0, realtime=True))
    for subject, source in BASE_INFO_SOURCES.items():
        result.append(Dataset(f"{subject}.basic_info", names[subject], "基础资料", source.source_tables[0], "update_time", False, False))
    for subject, table in [("index", "kcrp_index_member"), ("plate", "kcrp_yp_plate_member"), ("industry", "kcrp_industry_member")]:
        result.append(Dataset(f"{subject}.constitution", names[subject], "成分关系", table, "update_time", False, False))
    result.append(Dataset("industry.basic_info", "行业", "行业分类", "kcrp_industry_base", "update_time", False, False))
    for label, table in [("利润表", "income"), ("资产负债表", "balancesheet"), ("现金流量表", "cashflow"), ("财务指标", "financial_indicator")]:
        result.append(Dataset(f"stock.financial.{table}", "财务三表", label, f"kcrp_stock_{table}", "ann_date", False, False))
    labels = {"shareholder": "股东", "pledge": "质押", "corporate_action": "公司行动", "performance_notice": "业绩预告", "business_segment": "业务分部"}
    for view, definition in STOCK_CORPORATE_VIEWS.items():
        for source in definition.sources:
            result.append(Dataset(f"stock.{view}.{source.source}", "公司披露", f"{labels.get(view, view)} · {source.source}", source.table, "update_time", False, False))
    result.extend([
        Dataset("stock.report", "研报", "研报原文与观点", REPORT_TABLE, "publish_at", True, False),
        Dataset("stock.report_metric", "研报", "研报预测指标（按研报发布日期）", METRIC_FACT_TABLE, "publish_at", True, False),
        Dataset("report.metric_def", "研报", "指标定义", METRIC_DEF_TABLE, "updated_at", False, False),
        Dataset("hot_event.base_info", "热点", "事件资料", "hot_event_base_info", "latest_trigger_date", False, False, database="stock_agent"),
        Dataset("hot_event.state", "热点", "热度状态", "hot_event_state", "trade_date", database="stock_agent"),
        Dataset("hot_event.member", "热点", "关联股票", "hot_event_member", "trade_date", database="stock_agent"),
    ])
    overrides = json.loads(os.environ.get("FINANCE_STATUS_LAGS_JSON") or "{}")
    from dataclasses import replace
    return [replace(item, lag=max(0, min(5, int(overrides.get(item.id, item.lag))))) for item in result]


def target_dates(spec: Dataset, now: datetime, calendar: dict[date, bool]) -> list[date]:
    if spec.trading:
        days = sorted((d for d, opened in calendar.items() if opened and d <= now.date()), reverse=True)
        # T-1 means the previous trading date, including when today is closed.
        if now.date() not in days:
            days.insert(0, now.date())
        selected = days[spec.lag:spec.lag + 6]
        if len(selected) != 6:
            raise ValueError("交易日历历史不足")
        return selected
    return [now.date() - timedelta(days=spec.lag + n) for n in range(6)]


def describe(spec: Dataset, counts: list[int], *, opened: bool, due: bool) -> tuple[str, bool]:
    if not spec.daily:
        return ("按披露/变更更新" if counts[0] else "无新增披露/变更"), False
    if opened is None:
        return "日期状态待核实 · 交易日历不可用", True
    if not opened:
        return "正常 · 非交易日免更新告警", False
    if not due:
        return "已到达" if counts[0] else "待检查 · 未到检查时间", False
    if not counts[0]:
        return "未更新 · 目标日暂无数据", True
    return "已到达", False


def minute_cutoff(now: datetime) -> time | None:
    """Use completed minute windows; lunch and close stop the freshness clock."""
    clock = now.time().replace(second=0, microsecond=0)
    if clock < time(9, 31):
        return None
    if time(11, 30) <= clock < time(13, 1):
        return time(11, 30)
    return min(clock, time(15))


def minute_health(now, opened, latest, tolerance):
    if opened is None:
        return "交易日历缺失 · 无法判定实时延迟", True
    if not opened:
        return "正常 · 非交易日免更新告警", False
    cutoff = minute_cutoff(now)
    if cutoff is None:
        return "待开盘", False
    expected = datetime.combine(now.date(), cutoff)
    if latest is None or latest < expected - timedelta(seconds=tolerance):
        return "分钟数据延迟 · 请核对实时采集", True
    return "实时数据已到达", False


class DataStatusMonitor:
    def __init__(self, *, db_factory=MonitorDatabase, specs=None, snapshot_path=None):
        self.db_factory = db_factory
        self.specs = specs if specs is not None else datasets()
        self.path = Path(snapshot_path or os.environ.get("FINANCE_STATUS_SNAPSHOT_PATH") or "data/finance_status/latest.json")
        self.interval = max(60, int(os.environ.get("FINANCE_STATUS_INTERVAL_SECONDS") or 900))
        self.realtime_interval = max(30, int(os.environ.get("FINANCE_STATUS_REALTIME_INTERVAL_SECONDS") or 60))
        self.realtime_tolerance = max(60, int(os.environ.get("FINANCE_STATUS_REALTIME_TOLERANCE_SECONDS") or 300))
        self.check_time = time.fromisoformat(os.environ.get("FINANCE_STATUS_CHECK_TIME") or "15:30")
        self.lock = threading.Lock()
        self.snapshot = None
        self.disk_mtime = None
        try:
            self.snapshot = json.loads(self.path.read_text())
            self.disk_mtime = self.path.stat().st_mtime_ns
        except (OSError, ValueError):
            pass

    def current(self):
        try:
            mtime = self.path.stat().st_mtime_ns
            if self.disk_mtime != mtime:
                self.snapshot = json.loads(self.path.read_text())
                self.disk_mtime = mtime
        except (OSError, ValueError):
            pass
        now = datetime.now(TZ)
        value = dict(self.snapshot or {"checked_at": None, "items": [], "message": "等待首次扫描"})
        value["server_time"] = now.isoformat()
        value["check_time"] = self.check_time.isoformat(timespec="minutes")
        value["interval_seconds"] = self.interval
        checked = value.get("checked_at")
        value["stale"] = not checked or (now - datetime.fromisoformat(checked)).total_seconds() > self.interval * 2
        if checked and checked[:10] == now.date().isoformat() and now.time() < self.check_time and not value.get("scan_failed"):
            value["stale"] = False
        realtime_checked = value.get("realtime_checked_at")
        value["realtime_stale"] = any(s.realtime for s in self.specs) and (
            not realtime_checked or (now-datetime.fromisoformat(realtime_checked)).total_seconds() > self.realtime_interval*2)
        value["stale"] = value["stale"] or value["realtime_stale"]
        return value

    async def run(self):
        last_daily = None
        while True:
            now = datetime.now(TZ)
            # Startup observation also establishes the authoritative holiday calendar.
            if last_daily is None or last_daily.date() != now.date() or (now.time() >= self.check_time and (now-last_daily).total_seconds() >= self.interval):
                await asyncio.to_thread(self.scan, now)
                last_daily = now
            else:
                await asyncio.to_thread(self.scan, now, realtime_only=True)
            await asyncio.sleep(min(self.interval, self.realtime_interval))

    def scan(self, now=None, *, realtime_only=False):
        now = now or datetime.now(TZ)
        selected = [s for s in self.specs if s.realtime] if realtime_only else self.specs
        if not self.lock.acquire(blocking=False):
            return self.current()
        db = None
        hot_db = None
        try:
            db = self.db_factory()
            db.conn._read_timeout = 8
            with db.conn.cursor(pymysql.cursors.DictCursor) as c:
                c.execute("SET SESSION MAX_EXECUTION_TIME=3000")
                calendar = {}
                try:
                    c.execute("SELECT calendar_date,is_trade_day FROM aiia_trade_calendar WHERE market_code=%s AND calendar_date BETWEEN %s AND %s", ("CN_A", now.date()-timedelta(days=90), now.date()))
                    calendar = {r["calendar_date"]: bool(r["is_trade_day"]) for r in c.fetchall()}
                except pymysql.Error:
                    LOG.warning("Data monitor trading calendar unavailable")
            items = []
            for spec in selected:
                try:
                    if spec.trading and not spec.realtime and now.date() not in calendar:
                        items.append({"id": spec.id, "group": spec.group, "label": spec.label, "message": "交易日历缺失 · 无法判定目标日期", "attention": True})
                    else:
                        source_db = db
                        if spec.database == "stock_agent":
                            if hot_db is None:
                                hot_db = MonitorHotDatabase()
                                with hot_db.conn.cursor() as c:
                                    c.execute("SET SESSION MAX_EXECUTION_TIME=3000")
                            source_db = hot_db
                        items.append(self.observe(source_db, spec, now, calendar))
                except Exception as exc:
                    LOG.warning("Data status observation failed: %s", spec.id, exc_info=True)
                    message = str(exc) if isinstance(exc, ObservationUnavailable) else "检查失败 · 请查看服务日志"
                    items.append({"id": spec.id, "group": spec.group, "label": spec.label, "message": message, "attention": True})
                    # A timed-out socket cannot be reused for the next source.
                    try:
                        db.conn.ping(reconnect=True)
                        with db.conn.cursor() as c:
                            c.execute("SET SESSION MAX_EXECUTION_TIME=3000")
                    except Exception:
                        break
            if len(items) < len(selected):
                for spec in selected[len(items):]:
                    items.append({"id": spec.id, "group": spec.group, "label": spec.label, "message": "数据库连接中断 · 未检查", "attention": True})
            for item in items:
                item["checked_at"] = now.isoformat()
            if realtime_only:
                merged = {r["id"]: r for r in (self.snapshot or {}).get("items", [])}
                merged.update({r["id"]: r for r in items})
                items = list(merged.values())
            snapshot = {"checked_at": (self.snapshot or {}).get("checked_at") if realtime_only else now.isoformat(), "realtime_checked_at": now.isoformat(), "trading_day": calendar.get(now.date()), "items": items, "message": ("交易日历未覆盖今天；部分时效无法判定。" if now.date() not in calendar else "") + "扫描完成；到达不等于全量完整，数量差异仅供核对。"}
            if realtime_only and (self.snapshot or {}).get("scan_failed"):
                snapshot["scan_failed"] = True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
            self.disk_mtime = self.path.stat().st_mtime_ns
            self.snapshot = snapshot
        except Exception:
            LOG.warning("Data status scan failed", exc_info=True)
            inventory = [{"id": s.id, "group": s.group, "label": s.label, "message": "未检查 · 数据库连接失败", "attention": True} for s in self.specs]
            self.snapshot = {**(self.snapshot or {"items": inventory}), "message": "扫描失败 · 数据库或交易日历不可用", "scan_failed": True}
        finally:
            try:
                if db:
                    db.close_db()
                if hot_db:
                    hot_db.close_db()
            finally:
                self.lock.release()
        return self.current()

    def observe(self, db, spec, now, calendar):
        if spec.realtime:
            return self.observe_minutes(db, spec, now, calendar)
        days = target_dates(spec, now, calendar)
        table = f"`{spec.table}`"
        field = f"`{spec.date_field}`"
        metric = spec.id == "stock.report_metric"
        with db.conn.cursor(pymysql.cursors.DictCursor) as c:
            c.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (spec.table,))
            columns = {r["COLUMN_NAME"] for r in c.fetchall()}
            if not columns or (not metric and spec.date_field not in columns):
                raise ObservationUnavailable("监控源表或日期字段不存在")
            if metric:
                from src.experiments.staged_data_protocol.phase2.report_provider import REPORT_TABLE
                table = f"`{REPORT_TABLE}` r STRAIGHT_JOIN `{spec.table}` m ON r.id=m.report_id"
                field = "r.publish_at"
                c.execute(f"SELECT r.publish_at AS latest FROM `{REPORT_TABLE}` r WHERE EXISTS (SELECT 1 FROM `{spec.table}` m WHERE m.report_id=r.id) ORDER BY r.publish_at DESC LIMIT 1")
                last = c.fetchone()
                latest = last["latest"] if last else None
            else:
                c.execute("SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND SEQ_IN_INDEX=1", (spec.table,))
                indexed = {r["COLUMN_NAME"] for r in c.fetchall()}
                if spec.date_field not in indexed:
                    c.execute("SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (spec.table,))
                    size = c.fetchone()
                    if size and (size["TABLE_ROWS"] or 0) > 100000:
                        raise ObservationUnavailable("待优化日期索引 · 已跳过大表全扫描")
                c.execute(f"SELECT MAX({field}) AS latest FROM {table}")
                latest = c.fetchone()["latest"]
            # Bounded date range, with no function on the WHERE column.
            c.execute(f"SELECT DATE({field}) AS day,COUNT(*) AS n FROM {table} WHERE {field}>=%s AND {field}<%s GROUP BY DATE({field})", (min(days), max(days)+timedelta(days=1)))
            by_day = {r["day"]: int(r["n"]) for r in c.fetchall()}
            counts = [by_day.get(day, 0) for day in days]
            # Latest arrival time in the target slice, not MAX over all history.
            arrival = next((f for f in ("update_time", "updated_at", "created_at", "create_time") if f in columns), None)
            arrived_at = None
            if arrival:
                prefix = "m." if metric else ""
                c.execute(f"SELECT MAX({prefix}`{arrival}`) AS t FROM {table} WHERE {field}>=%s AND {field}<%s", (days[0], days[0]+timedelta(days=1)))
                arrived_at = c.fetchone()["t"]
        average = sum(counts[1:]) / 5
        ratio = counts[0]/average if average else None
        due = now.time() >= self.check_time
        message, attention = describe(spec, counts, opened=calendar.get(now.date()), due=due)
        if latest is None:
            message, attention = "数据源为空或日期均缺失", True
        volume_note = ""
        if spec.daily and due and calendar.get(now.date()) and counts[0] and ratio is not None and ratio < 0.8:
            volume_note = "低于前五日均值的80%，请核对完整性"
            attention = True
        return {"id": spec.id, "group": spec.group, "label": spec.label,
                "message": message, "attention": attention, "target_date": days[0].isoformat(),
                "latest_date": str(latest) if latest else None, "arrived_at": str(arrived_at) if arrived_at else None,
                "count": counts[0], "baseline_mean": average, "ratio": ratio,
                "history": [{"date": d.isoformat(), "count": n} for d,n in zip(days, counts)],
                "volume_note": volume_note, "date_basis": spec.date_field,
                "cadence": ("交易日" if spec.trading else "自然日") + f" T-{spec.lag}" if spec.daily else "按披露/变更",
                "sync_note": "仅观测库内数据；无上游批次完成标记，不能据此确认同步任务成功。"}

    def observe_minutes(self, db, spec, now, calendar):
        # Monitor source 1m bars, not the sum of overlapping derived periods.
        previous = sorted((d for d, opened in calendar.items() if opened and d < now.date()), reverse=True)[:5]
        days = [now.date(), *previous]
        cutoff = minute_cutoff(now) or time(9, 30)
        counts = []
        latest = arrival = None
        with db.conn.cursor(pymysql.cursors.DictCursor) as c:
            for day in days:
                c.execute(f"SELECT COUNT(*) AS n FROM `{spec.table}` WHERE trade_date=%s AND kline_type='1m' AND bar_end_time<=%s", (day, datetime.combine(day, cutoff)))
                counts.append(int(c.fetchone()["n"]))
            c.execute(f"SELECT snapshot_time,fetch_time FROM `{spec.table}` WHERE trade_date=%s AND kline_type='1m' ORDER BY bar_end_time DESC LIMIT 1", (now.date(),))
            row = c.fetchone()
            if row:
                latest, arrival = row["snapshot_time"], row["fetch_time"]
        message, attention = minute_health(now, calendar.get(now.date()), latest, self.realtime_tolerance)
        mean = sum(counts[1:])/5 if len(previous) == 5 else None
        return {"id": spec.id, "group": spec.group, "label": spec.label,
                "message": message, "attention": attention, "target_date": now.date().isoformat(),
                "latest_date": str(latest) if latest else None, "arrived_at": str(arrival) if arrival else None,
                "count": counts[0], "baseline_mean": mean, "ratio": counts[0]/mean if mean else None,
                "history": [{"date": d.isoformat(), "count": n} for d,n in zip(days,counts)],
                "cadence": f"实时 · 同时点 {cutoff:%H:%M} · 1分钟源K线",
                "sync_note": "盘中检查源分钟时效；午休及收盘冻结预期时点。数量对比为前五交易日同时点累计量，不等于全部证券完整。"}
