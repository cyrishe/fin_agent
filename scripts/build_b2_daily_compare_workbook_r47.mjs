import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = process.env.B2_R47_OUT ?? "outputs/stock_automl/b2_daily_shape_r47_20261020";
const data = JSON.parse(await fs.readFile(path.join(root, "workbook_data.json"), "utf8"));
const wb = Workbook.create();
const navy = "#174E5B";
const muted = "#52636B";
const pct = "0.00%";
const cash = "#,##0";
const day = value => value ? new Date(`${value}T12:00:00`) : null;
const month = value => value ? day(`${value}-01`) : null;

function sheet(name, title, headers, rows, widths, formats = [], dateCol = null) {
  const ws = wb.worksheets.add(name);
  ws.showGridLines = false;
  ws.tabColor = navy;
  ws.getRange("A2").values = [[title]];
  ws.getRange("A2").format.font = { name: "Arial", size: 14, bold: true, color: navy };
  const n = headers.length;
  const head = ws.getRangeByIndexes(3, 0, 1, n);
  head.values = [headers];
  head.format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
  head.format.rowHeight = 25;
  head.format.horizontalAlignment = "center";
  if (rows.length) {
    const content = dateCol === null ? rows : rows.map(row => {
      const converted = [...row];
      converted[dateCol] = name === "逐月" || name === "模型训练" ? month(row[dateCol]) : day(row[dateCol]);
      return converted;
    });
    const body = ws.getRangeByIndexes(4, 0, rows.length, n);
    body.values = content;
    body.format.font = { name: "Arial", size: 10, color: "#22313A" };
    body.format.rowHeight = 21;
    body.format.verticalAlignment = "center";
    for (const [col, format] of formats) {
      ws.getRangeByIndexes(4, col, rows.length, 1).format.numberFormat = format;
    }
    if (dateCol !== null) {
      ws.getRangeByIndexes(4, dateCol, rows.length, 1).format.numberFormat =
        name === "逐月" || name === "模型训练" ? "yyyy-mm" : "yyyy-mm-dd";
    }
    if (rows.length > 12) {
      ws.tables.add(`A4:${String.fromCharCode(64 + n)}${rows.length + 4}`, true,
        `R47_${name === "逐月" ? "Monthly" : name === "模型训练" ? "Models" : name === "逐笔成交" ? "Trades" : name === "逐日预选" ? "Decisions" : "Labels"}`);
      ws.freezePanes.freezeRows(4);
    }
  }
  widths.forEach((w, col) => ws.getRangeByIndexes(0, col, 1, 1).format.columnWidth = w);
  return ws;
}

const overview = sheet("总览", "B2 日更模型对比",
  ["方案", "停手", "买入笔数", "期末资产", "总收益", "最大回撤", "年化夏普", "三日再板", "D2笔均费后"],
  data.overview, [23, 13, 13, 17, 13, 13, 13, 13, 15],
  [[3, cash], [4, pct], [5, pct], [6, "0.00"], [7, pct], [8, pct]]);
overview.getRange("A3").values = [[data.scope]];
overview.getRange("A3").format.font = { name: "Arial", size: 10, italic: true, color: muted };
overview.getRange("A16").values = [["逐年账户结果"]];
overview.getRange("A16").format.font = { name: "Arial", size: 12, bold: true, color: navy };
const annualHeaders = ["年份", "方案", "停手", "买入笔数", "年初资产", "年末资产", "年收益", "年内回撤", "年化夏普"];
overview.getRange("A18:I18").values = [annualHeaders];
overview.getRange("A18:I18").format = { fill: navy, font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" } };
overview.getRange("A18:I18").format.horizontalAlignment = "center";
overview.getRangeByIndexes(18, 0, data.yearly.length, 9).values = data.yearly;
overview.getRangeByIndexes(18, 0, data.yearly.length, 9).format.font = { name: "Arial", size: 10, color: "#22313A" };
for (const col of [4, 5]) overview.getRangeByIndexes(18, col, data.yearly.length, 1).format.numberFormat = cash;
for (const col of [6, 7]) overview.getRangeByIndexes(18, col, data.yearly.length, 1).format.numberFormat = pct;
overview.getRangeByIndexes(18, 8, data.yearly.length, 1).format.numberFormat = "0.00";

sheet("逐月", "按持仓日历月统计", ["月份", "方案", "停手", "买入笔数", "月初资产", "月末资产", "月收益", "月内回撤", "买卖费用"],
  data.monthly, [15, 23, 13, 13, 17, 17, 13, 13, 15],
  [[4, cash], [5, cash], [6, pct], [7, pct], [8, cash]], 0);
sheet("分类效果", "三日再板与 D2 实际收益", ["方案", "阶段", "样本数", "三日再板率", "D2笔均费后", "D2亏超5%"],
  data.classification, [23, 18, 13, 17, 17, 17], [[3, pct], [4, pct], [5, pct]]);
const training = sheet("模型训练", "日线与四 K 每日重训的月度摘要", ["月份", "方案", "重训日数", "日线训练均数", "日线历史验Top1", "四K训练均数", "四K验证均数", "沿用D0排名", "选择Ridge", "训练机会收益", "验证机会收益", "排名验证收益"],
  data.shape_models, [15, 23, 13, 15, 18, 15, 15, 15, 13, 17, 17, 17], [[4, pct], [9, pct], [10, pct], [11, pct]], 0);
training.getRange("A3").values = [["每日历史窗口互相重叠；这里是模型选择证据，实际收益看总览和逐月。"]];
training.getRange("A3").format.font = { name: "Arial", size: 10, italic: true, color: muted };
sheet("逐笔成交", "账户实际买入明细", ["D1日期", "方案", "停手", "D0名次", "D0评分", "形态模型", "四K最高", "四K最低", "四K实体", "买入时刻", "买入价", "投入金额", "卖出日期", "卖出价", "费后收益", "三日再板"],
  data.ledger, [16, 23, 13, 11, 13, 15, 12, 12, 12, 13, 13, 16, 16, 13, 14, 12],
  [[4, "0.0000"], [6, pct], [7, pct], [8, pct], [10, "0.00"], [11, cash], [13, "0.00"], [14, pct]], 0);
sheet("逐日预选", "四 K 精排预选，含未成交", ["D0日期", "D1日期", "方案", "D0名次", "D0评分", "形态模型", "四K最高", "四K最低", "四K实体", "可成交", "买入时刻", "模拟买价", "D2费后", "三日再板", "影子停手"],
  data.decisions, [16, 16, 23, 11, 13, 15, 12, 12, 12, 11, 13, 13, 14, 12, 12],
  [[4, "0.0000"], [6, pct], [7, pct], [8, pct], [11, "0.00"], [12, pct]], 0);

wb.recalculate();
const scan = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 50 }, maxChars: 2000 });
console.log(scan.ndjson);
const preview = await wb.render({ sheetName: "总览", range: "A1:I44", scale: 1.2, format: "png" });
await fs.writeFile(path.join(root, "workbook_preview.png"), new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(wb);
const filename = path.join(root, "B2_日更模型_三方案对比_R46-R47_20261020.xlsx");
await output.save(filename);
console.log(filename);
