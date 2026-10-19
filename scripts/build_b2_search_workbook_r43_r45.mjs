import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "outputs/stock_automl/b2_validation_pause_r45_20261019";
const data = JSON.parse(await fs.readFile(path.join(root, "workbook_data.json"), "utf8"));
const workbook = Workbook.create();
const dark = "#174E5B";
const light = "#E7F1F3";
const font = "Arial";
const percent = "0.00%";
const money = "#,##0";
const decimal = "0.0000";
const dateValue = (value, day = false) => new Date(`${value}${day ? "" : "-01"}T12:00:00`);

function baseSheet(name, title, rows, headers, dataStart = 4) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = { name: font, size: 14, bold: true, color: dark };
  const count = headers.length;
  sheet.getRangeByIndexes(dataStart - 2, 0, 1, count).values = [headers];
  const header = sheet.getRangeByIndexes(dataStart - 2, 0, 1, count);
  header.format = {
    fill: dark,
    font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
  };
  header.format.rowHeight = 26;
  header.format.horizontalAlignment = "center";
  if (rows.length) {
    const body = sheet.getRangeByIndexes(dataStart - 1, 0, rows.length, count);
    body.values = rows;
    body.format.font = { name: font, size: 10, color: "#22313A" };
    body.format.rowHeight = 21;
    body.format.verticalAlignment = "center";
  }
  sheet.freezePanes.freezeRows(dataStart - 1);
  return sheet;
}

const summary = workbook.worksheets.add("总览");
summary.showGridLines = false;
summary.getRange("A2").values = [["B2 日线选模与早期回撤"]];
summary.getRange("A2").format.font = { name: font, size: 14, bold: true, color: dark };
summary.getRange("A3").values = [[data.scope]];
summary.getRange("A3").format.font = { name: font, size: 10, italic: true, color: "#52636B" };
summary.getRange("A5:G5").values = [["方案", "买入笔数", "期末资产", "总收益", "最大回撤", "年化夏普", "交易费用"]];
summary.getRange("A5:G5").format = { fill: dark, font: { name: font, size: 10, bold: true, color: "#FFFFFF" } };
summary.getRange("A5:G5").format.rowHeight = 26;
summary.getRange("A6:G10").values = data.summary;
summary.getRange("A6:G10").format.font = { name: font, size: 10, color: "#22313A" };
summary.getRange("C6:C10").format.numberFormat = money;
summary.getRange("D6:E10").format.numberFormat = percent;
summary.getRange("F6:F10").format.numberFormat = "0.00";
summary.getRange("G6:G10").format.numberFormat = money;
summary.getRange("A12").values = [["逐年账户结果"]];
summary.getRange("A12").format.font = { name: font, size: 12, bold: true, color: dark };
summary.getRange("A14:H14").values = [["年份", "方案", "买入笔数", "年初资产", "年末资产", "年收益", "年内回撤", "年化夏普"]];
summary.getRange("A14:H14").format = { fill: dark, font: { name: font, size: 10, bold: true, color: "#FFFFFF" } };
summary.getRange("A14:H14").format.rowHeight = 26;
summary.getRangeByIndexes(14, 0, data.annual.length, 8).values = data.annual;
summary.getRangeByIndexes(14, 0, data.annual.length, 8).format.font = { name: font, size: 10, color: "#22313A" };
summary.getRangeByIndexes(14, 3, data.annual.length, 2).format.numberFormat = money;
summary.getRangeByIndexes(14, 5, data.annual.length, 2).format.numberFormat = percent;
summary.getRangeByIndexes(14, 7, data.annual.length, 1).format.numberFormat = "0.00";
summary.getRange("A36").values = [["2023 年仅含 5—12 月；2026 年仅含 1—9 月。月收益按持仓日历月统计，月度选模按 D0 所属月。 "]];
summary.getRange("A36").format.font = { name: font, size: 10, italic: true, color: "#52636B" };
summary.getRange("A:A").format.columnWidth = 17;
summary.getRange("B:B").format.columnWidth = 18;
summary.getRange("C:E").format.columnWidth = 15;
summary.getRange("F:H").format.columnWidth = 15;

const monthlyRows = data.monthly.map(row => [dateValue(row[0]), ...row.slice(1)]);
const monthly = baseSheet("逐月", "逐月收益与回撤", monthlyRows,
  ["月份", "方案", "买入笔数", "月初资产", "月末资产", "月收益", "月内回撤", "交易费用"]);
monthly.getRange(`A4:A${monthlyRows.length + 3}`).format.numberFormat = "yyyy-mm";
monthly.getRange(`D4:E${monthlyRows.length + 3}`).format.numberFormat = money;
monthly.getRange(`F4:G${monthlyRows.length + 3}`).format.numberFormat = percent;
monthly.getRange(`H4:H${monthlyRows.length + 3}`).format.numberFormat = money;
monthly.getRange("A:A").format.columnWidth = 14;
monthly.getRange("B:B").format.columnWidth = 18;
monthly.getRange("C:H").format.columnWidth = 15;
monthly.tables.add(`A3:H${monthlyRows.length + 3}`, true, "MonthlyResults");

const metricRows = data.model_metrics.map(row => [dateValue(row[0]), ...row.slice(1)]);
const models = baseSheet("选模", "每月训练、验证与测试", metricRows,
  ["月份", "方案", "所选模型", "训练内Top1", "折外Top1", "折外Top3", "验证Top1", "验证Top3", "测试Top1", "测试Top3", "验证Top1笔数", "验证D2代理", "月停手通过"]);
models.getRange(`A4:A${metricRows.length + 3}`).format.numberFormat = "yyyy-mm";
models.getRange(`D4:J${metricRows.length + 3}`).format.numberFormat = percent;
models.getRange(`L4:L${metricRows.length + 3}`).format.numberFormat = percent;
models.getRange("A:A").format.columnWidth = 14;
models.getRange("B:B").format.columnWidth = 15;
models.getRange("C:C").format.columnWidth = 24;
models.getRange("D:M").format.columnWidth = 16;
models.tables.add(`A3:M${metricRows.length + 3}`, true, "MonthlyModelChoice");

const tradeRows = data.trades.map(row => [dateValue(row[0], true), ...row.slice(1)]);
const trades = baseSheet("逐笔", "四 K 模拟交易明细", tradeRows,
  ["D1日期", "方案", "D0排名", "D0评分", "四K规则", "四K实体", "买入K结束", "买入价", "D2收盘", "D2费后收益", "月停手通过"]);
trades.getRange(`A4:A${tradeRows.length + 3}`).format.numberFormat = "yyyy-mm-dd";
trades.getRange(`D4:D${tradeRows.length + 3}`).format.numberFormat = decimal;
trades.getRange(`F4:F${tradeRows.length + 3}`).format.numberFormat = percent;
trades.getRange(`H4:I${tradeRows.length + 3}`).format.numberFormat = "0.00";
trades.getRange(`J4:J${tradeRows.length + 3}`).format.numberFormat = percent;
trades.getRange("A:A").format.columnWidth = 17;
trades.getRange("B:B").format.columnWidth = 16;
trades.getRange("C:K").format.columnWidth = 15;
trades.tables.add(`A3:K${tradeRows.length + 3}`, true, "TradeDetails");

workbook.recalculate();
const inspection = await workbook.inspect({ kind: "table", range: "总览!A5:H19",
  include: "values,formulas", tableMaxRows: 20, tableMaxCols: 8 });
console.log(inspection.ndjson);
const errors = await workbook.inspect({ kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" });
console.log(errors.ndjson);
const preview = await workbook.render({ sheetName: "总览", range: "A1:H35", scale: 1.4, format: "png" });
await fs.writeFile(path.join(root, "workbook_preview.png"),
  new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
const filename = path.join(root, "B2_日线多模型_早期回撤_R43-R45_20261019.xlsx");
await output.save(filename);
console.log(filename);
