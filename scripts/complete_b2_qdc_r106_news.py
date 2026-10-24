"""Fill seven QDC-only R86 announcement windows using R74's frozen wording rule."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import requests
from pypdf import PdfReader

from scripts.research_b2_daily_only_r100 import read_inputs
from scripts.research_b2_qdc_r106 import OUT, ROOT

SOURCE = ROOT / "docs/stock_automl_runs/20261022_b2_news_factor_r74"
PATTERN = re.compile(r"(?:不涉及|未涉及|未开展|未从事)[^。；]{0,35}(?:业务|经营|概念)|无[^。；]{0,15}经营资质")
UNIFORM = re.compile(r"风险提示|风险的提示|减持.*计划|减持.*预披露|立案|行政处罚|监管警示|业绩预")


def clean(text: str) -> str:
    return re.sub(r"\s+", "", re.sub("<[^>]+>", "", str(text or "")))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    panel = pd.read_pickle(OUT / "risk_panel.pkl")
    missing = panel[~panel.news_known][["stk_code", "d0_date", "d1_date"]].drop_duplicates()
    mapping = {x["code"]: x for x in json.loads((SOURCE / "cninfo_stockmap.json").read_text())["stockList"]}
    _, calendar, _ = read_inputs()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"})
    rows = []
    for row in missing.itertuples(index=False):
        code = row.stk_code[:6]
        start = calendar[calendar.index(row.d0_date) - 5]
        params = dict(pageNum=1, pageSize=30, column="szse", tabName="fulltext",
                      stock=code + "," + mapping[code]["orgId"], seDate=start + "~" + row.d0_date,
                      isHLtitle="true", sortName="time", sortType="desc")
        docs = []
        complete = True
        for page in range(1, 101):
            params["pageNum"] = page
            response = session.post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                                    data=params, timeout=25)
            response.raise_for_status()
            payload = response.json()
            (OUT / f"cninfo_{code}_{row.d0_date}_{page}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            if "announcements" not in payload:
                complete = False
                break
            docs.extend(payload.get("announcements") or [])
            if not payload.get("hasMore"):
                break
        else:
            complete = False
        hits = []
        checked = []
        for doc in docs:
            date = doc["adjunctUrl"].split("/")[1]
            if not start <= date <= row.d0_date:
                continue
            title = clean(doc["announcementTitle"])
            if not UNIFORM.search(title):
                continue
            aid = doc["announcementId"]
            url = "https://static.cninfo.com.cn/" + doc["adjunctUrl"]
            record = dict(id=aid, published=date, title=title, url=url, ok=False, theme_denial=False)
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"):
                    raise ValueError("non-PDF response")
                reader = PdfReader(io.BytesIO(response.content))
                body = clean("\n".join((reader.pages[i].extract_text() or "") for i in range(min(len(reader.pages), 40))))
                matches = list(dict.fromkeys(x.group(0) for x in PATTERN.finditer(body)))
                record.update(ok=True, theme_denial=bool(matches), evidence=matches[:4], pages=len(reader.pages))
                (OUT / f"cninfo_body_{aid}.txt").write_text(body)
            except Exception as exc:
                record["error"] = type(exc).__name__ + ": " + str(exc)[:160]
                complete = False
            checked.append(record)
            hits.extend(record.get("evidence", []))
        rows.append(dict(stk_code=row.stk_code, d0_date=row.d0_date, d1_date=row.d1_date,
                         start5=start, official_complete=complete, documents=len(docs),
                         uniform_documents=len(checked), theme_denial_5=int(bool(hits)) if complete else None,
                         evidence=hits[:5], checked=checked))
        print(code, row.d0_date, "docs", len(docs), "checked", len(checked),
              "complete", complete, "denial", bool(hits), flush=True)
    (OUT / "missing_news_evidence.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    simple = pd.DataFrame([{k: row[k] for k in ["stk_code", "d0_date", "d1_date", "start5",
                                                   "official_complete", "documents", "uniform_documents",
                                                   "theme_denial_5"]} for row in rows])
    simple.to_csv(OUT / "missing_news_decisions.csv", index=False)


if __name__ == "__main__":
    main()
