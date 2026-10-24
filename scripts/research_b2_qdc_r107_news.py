"""Backfill the frozen R74 official-announcement wording rule for R107.

Only Q75-retained early trades need the extra news decision. Every query and
uniform-scope PDF is cached; a failed retrieval remains unknown, never zero.
"""
from __future__ import annotations

import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from pypdf import PdfReader

from scripts import research_b2_qdc_r104 as r104
from scripts import research_b2_qdc_r107 as run
from scripts.research_b2_daily_only_r100 import read_inputs

SOURCE = r104.ROOT / "docs/stock_automl_runs/20261022_b2_news_factor_r74"
PATTERN = re.compile(r"(?:不涉及|未涉及|未开展|未从事)[^。；]{0,35}(?:业务|经营|概念)|无[^。；]{0,15}经营资质")
UNIFORM = re.compile(r"风险提示|风险的提示|减持.*计划|减持.*预披露|立案|行政处罚|监管警示|业绩预")


def clean(text: str) -> str:
    return re.sub(r"\s+", "", re.sub("<[^>]+>", "", str(text or "")))


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/new/disclosure",
                      "Accept": "application/json, text/javascript, */*; q=0.01"})
    return s


def fetch_window(row: dict, mapping: dict, calendar: list[str]) -> dict:
    code = row["stk_code"][:6]
    d0 = row["d0_date"]
    start = calendar[calendar.index(d0) - 5]
    target = run.OUT / "news_windows" / f"{code}_{d0}.json"
    if target.exists():
        cached = json.loads(target.read_text())
        if cached.get("complete"):
            return cached
    s = session()
    params = dict(pageNum=1, pageSize=30, column="szse", tabName="fulltext",
                  stock=code + "," + mapping[code]["orgId"], seDate=start + "~" + d0,
                  isHLtitle="true", sortName="time", sortType="desc")
    result = dict(stk_code=row["stk_code"], d0_date=d0, d1_date=row["d1_date"],
                  start5=start, complete=False, pages=[], documents=[])
    try:
        docs = []
        for page in range(1, 101):
            params["pageNum"] = page
            for retry in range(3):
                try:
                    response = s.post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                                      data=params, timeout=25)
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or "totalAnnouncement" not in payload:
                        raise ValueError("invalid official query response")
                    break
                except Exception:
                    if retry == 2:
                        raise
                    time.sleep(1 + retry)
            result["pages"].append(payload)
            docs.extend(payload.get("announcements") or [])
            if page * 30 >= int(payload.get("totalAnnouncement") or 0):
                break
        else:
            raise ValueError("official query exceeded 100 pages")
        if any(doc.get("secCode") != code for doc in docs):
            raise ValueError("official query returned another security")
        matches = []
        checked = []
        for doc in docs:
            published = str(doc["adjunctUrl"]).split("/")[1]
            if not start <= published <= d0:
                continue
            title = clean(doc["announcementTitle"])
            if not UNIFORM.search(title):
                continue
            aid = str(doc["announcementId"])
            url = "https://static.cninfo.com.cn/" + doc["adjunctUrl"]
            record = dict(id=aid, published=published, title=title, url=url)
            body_path = run.OUT / "news_bodies" / f"{aid}.txt"
            try:
                if body_path.exists():
                    body = body_path.read_text()
                else:
                    pdf = s.get(url, timeout=35)
                    pdf.raise_for_status()
                    if not pdf.content.startswith(b"%PDF"):
                        raise ValueError("non-PDF official document")
                    reader = PdfReader(io.BytesIO(pdf.content))
                    body = clean("\n".join((reader.pages[i].extract_text() or "")
                                           for i in range(min(len(reader.pages), 40))))
                    body_path.write_text(body)
                found = list(dict.fromkeys(m.group(0) for m in PATTERN.finditer(body)))
                record.update(ok=True, evidence=found[:4])
                matches.extend(found)
            except Exception as exc:
                record.update(ok=False, error=type(exc).__name__ + ": " + str(exc)[:200])
            checked.append(record)
        result.update(complete=all(x.get("ok") for x in checked),
                      documents=checked, total_documents=len(docs),
                      theme_denial_5=int(bool(matches)) if all(x.get("ok") for x in checked) else None)
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:200]
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    (run.OUT / "news_windows").mkdir(parents=True, exist_ok=True)
    (run.OUT / "news_bodies").mkdir(parents=True, exist_ok=True)
    panel = pd.read_pickle(run.OUT / "risk_sell_panel.pkl")
    retained = panel[~panel.q75_veto][["stk_code", "d0_date", "d1_date"]].to_dict("records")
    mapping = {x["code"]: x for x in json.loads((SOURCE / "cninfo_stockmap.json").read_text())["stockList"]}
    _, calendar, _ = read_inputs()
    assert all(row["stk_code"][:6] in mapping for row in retained)
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fetch_window, row, mapping, calendar) for row in retained]
        for future in as_completed(futures):
            results.append(future.result())
            if len(results) % 20 == 0:
                print("official", len(results), "/", len(retained),
                      "complete", sum(x["complete"] for x in results), flush=True)
    summary = pd.DataFrame([{k: x.get(k) for k in ["stk_code", "d0_date", "d1_date",
                                                    "start5", "complete", "total_documents", "theme_denial_5"]}
                            for x in results])
    summary.to_csv(run.DOC / "news_decisions.csv", index=False)
    denials = [{"stk_code": item["stk_code"], "d0_date": item["d0_date"],
                "documents": [doc for doc in item["documents"] if doc.get("evidence")]}
               for item in results if item.get("theme_denial_5") == 1]
    (run.DOC / "news_denial_evidence.json").write_text(
        json.dumps(denials, ensure_ascii=False, indent=2) + "\n")
    cached = []
    for folder in (run.OUT / "news_windows", run.OUT / "news_bodies"):
        for path in sorted(folder.glob("*")):
            if path.is_file() and not path.name.startswith("._"):
                cached.append(dict(path=str(path.relative_to(run.OUT)), bytes=path.stat().st_size,
                                   sha256=run.r106.digest(path)))
    pd.DataFrame(cached).to_csv(run.DOC / "raw_news_hashes.csv", index=False)
    assert summary[["stk_code", "d0_date"]].duplicated().sum() == 0
    panel = panel.merge(summary[["stk_code", "d0_date", "complete", "theme_denial_5"]],
                        on=["stk_code", "d0_date"], how="left", validate="one_to_one")
    panel["news_known"] = panel.complete.map(lambda value: bool(value) if pd.notna(value) else False)
    panel["news_veto"] = panel.theme_denial_5.eq(1)
    panel.to_pickle(run.OUT / "risk_sell_news_panel.pkl")
    panel[["stk_code", "model_month", "d0_date", "d1_date", "d2_date", "entry",
           "qdc_d2_close", "sell7", "sell7_time", "q75_score", "q75_veto",
           "news_known", "news_veto", "total_mv_yi", "mv_source", "d0_turnover_pct",
           "d2_cash_dividend", "close_floor", "close_unverified", "seven_unverified",
           "d0_ohlc_match", "d2_ohlc_match"]].to_csv(run.DOC / "trades.csv", index=False)
    if panel.loc[~panel.q75_veto, "news_known"].all():
        gates = pd.read_csv(r104.ROOT / "outputs/stock_automl/b2_qdc_r105_20261024/gates.csv")
        annual, monthly = run.yearly_and_monthly(panel, gates)
        annual.to_csv(run.DOC / "yearly.csv", index=False)
        monthly.to_csv(run.DOC / "monthly.csv", index=False)
        rows = []
        for month in run.MONTHS:
            z = monthly[monthly.model_month.eq(month)]
            risk = panel[panel.model_month.eq(month)]
            def item(rule: str, mode: str, col: str):
                return z[(z.rule == rule) & (z.sell == mode)][col].iloc[0]
            rows.append(dict(model_month=month, paused=bool(z.paused.iloc[0]),
                             v2_buys=int(item("V2", "close", "buys")),
                             q75_veto=int(risk.q75_veto.sum()),
                             news_veto_after_q75=int((risk.news_veto & ~risk.q75_veto).sum()),
                             combo_buys=int(item("Q75+news", "close", "buys")),
                             v2_close_pct=float(item("V2", "close", "return_pct")),
                             combo_close_pct=float(item("Q75+news", "close", "return_pct")),
                             combo_morning7_pct=float(item("Q75+news", "morning7", "return_pct")),
                             combo_close_floor=int(item("Q75+news", "close", "floor_close")),
                             combo_unverified_morning7=int(item("Q75+news", "morning7", "unverified_exit"))))
        pd.DataFrame(rows).to_csv(run.DOC / "monthly_summary.csv", index=False)
        print(annual.to_string(index=False), flush=True)
    manifest_file = run.DOC / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    manifest.update(announcement_source="CNINFO official historical announcement API",
                    announcement_rule_source=str(SOURCE / "build_factors.py"),
                    news_windows_required=len(retained),
                    news_windows_complete=int(summary.complete.sum()),
                    news_denials=int(summary.theme_denial_5.fillna(0).sum()),
                    news_decisions_sha256=run.r106.digest(run.DOC / "news_decisions.csv"),
                    news_denial_evidence_sha256=run.r106.digest(run.DOC / "news_denial_evidence.json"),
                    raw_news_hashes_sha256=run.r106.digest(run.DOC / "raw_news_hashes.csv"),
                    yearly_sha256=run.r106.digest(run.DOC / "yearly.csv"),
                    monthly_sha256=run.r106.digest(run.DOC / "monthly.csv"),
                    monthly_summary_sha256=run.r106.digest(run.DOC / "monthly_summary.csv"),
                    trades_sha256=run.r106.digest(run.DOC / "trades.csv"))
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(dict(retained=len(retained), complete=int(summary.complete.sum()),
               denied=int(summary.theme_denial_5.fillna(0).sum())), flush=True)


if __name__ == "__main__":
    main()
