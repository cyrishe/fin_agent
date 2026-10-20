/** Build a compact, typed workbook from R55's checked CSV backtest outputs. */
import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const root = process.env.B2_R55_OUTPUT_DIR ??
  "/Volumes/ext/fin_agent/docs/stock_automl_runs/20261020_b2_recent_loss_resume_r55";
const out = path.join(root, "B2_近5_10日亏损停手_R55_20261020.xlsx");
const wb = Workbook.create();

async function rows(file, names, numeric = [], dates = []) {
  const source = await Workbook.fromCSV(await fs.readFile(path.join(root, file), "utf8"),
    { sheetName: "Source" });
  const grid = source.worksheets.getItemAt(0).getUsedRange().values;
  const keys = grid[0].map((x) => String(x).replace(/^\uFEFF/, ""));
  const index = names.map((name) => {
    const i = keys.indexOf(name);
    if (i < 0) throw new Error(`Missing ${name} in ${file}`);
    return i;
  });
  return [names, ...grid.slice(1).filter((r) => r.some((v) => v !== "" && v != null))
    .map((r) => index.map((i, j) => {
      const raw = r[i];
      if (raw === "" || raw == null) return null;
      if (numeric.includes(names[j])) {
        if (raw === "True") return 1;
        if (raw === "False") return 0;
        const value = Number(raw);
        if (!Number.isFinite(value)) throw new Error(`Non-numeric ${names[j]}=${raw} in ${file}`);
        return value;
      }
      if (dates.includes(names[j])) return new Date(`${raw}T00:00:00Z`);
      if (names[j] === "股票") return String(raw).padStart(6, "0");
      return String(raw);
    }))];
}

function letter(i) {
  let s = "";
  for (let k = i + 1; k > 0; k = Math.floor((k - 1) / 26)) s = String.fromCharCode(65 + (k - 1) % 26) + s;
  return s;
}

function table(sheet, startRow, matrix, formats = {}) {
  const { signed = [], unsigned = [], money = [], date = [], decimals3 = [],
    decimals4 = [], text = [] } = formats;
  const bottom = startRow + matrix.length - 1;
  const right = letter(matrix[0].length - 1);
  const area = sheet.getRange(`A${startRow}:${right}${bottom}`);
  area.values = matrix;
  area.format.font = { name: "Arial", size: 10, color: "#1F2937" };
  area.format.rowHeight = 21;
  const head = sheet.getRange(`A${startRow}:${right}${startRow}`);
  head.format = { fill: "#164A54", font: { name: "Arial", size: 10,
    bold: true, color: "#FFFFFF" }, rowHeight: 26,
    horizontalAlignment: "center", verticalAlignment: "center" };
  for (let i = 0; i < matrix[0].length; i++) {
    const col = letter(i);
    const name = matrix[0][i];
    const body = sheet.getRange(`${col}${startRow + 1}:${col}${bottom}`);
    if (signed.includes(name)) body.setNumberFormat("+0.00%;-0.00%;0.00%");
    else if (unsigned.includes(name)) body.setNumberFormat("0.00%");
    else if (money.includes(name)) body.setNumberFormat("#,##0.00;[Red](#,##0.00);0.00");
    else if (date.includes(name)) body.setNumberFormat("yyyy-mm-dd");
    else if (decimals3.includes(name)) body.setNumberFormat("0.000");
    else if (decimals4.includes(name)) body.setNumberFormat("0.0000");
    else if (text.includes(name)) body.setNumberFormat("@");
    else if (typeof matrix[1]?.[i] === "number") body.setNumberFormat("#,##0");
    sheet.getRange(`${col}${startRow}:${col}${bottom}`).format.columnWidth =
      Math.min(22, Math.max(11, String(name).length * 1.7));
  }
}

const totalKeys = ["方案", "信号", "买入", "跳过", "总收益", "最大回撤", "年化夏普",
  "D2费后胜率", "D2费后笔均", "期末资金", "交易费"];
const startKeys = ["起点", "方案", "买入", "总收益", "最大回撤", "年化夏普"];
const total = await rows("总体.csv", totalKeys, totalKeys.filter((x) => x !== "方案"));
const starts = await rows("不同起点.csv", startKeys,
  ["买入", "总收益", "最大回撤", "年化夏普"]);
const years = await rows("逐年.csv", ["方案", "年份", "买入", "收益", "最大回撤"],
  ["年份", "买入", "收益", "最大回撤"]);
const overview = wb.worksheets.add("总览");
overview.showGridLines = false;
overview.getRange("A2").values = [["B2 近 5／10 日亏损停手回测"]];
overview.getRange("A2").format.font = { name: "Arial", size: 14, bold: true, color: "#153D4B" };
overview.getRange("A3").values = [["原 B2 月模型；上月两窗口均亏或零候选则停。恢复方案仅在当月影子 D2 胜率超过 50% 后买下一笔。"]];
overview.getRange("A3").format.font = { name: "Arial", size: 10, italic: true, color: "#536471" };
table(overview, 5, total, { signed: ["总收益", "D2费后笔均"],
  unsigned: ["最大回撤", "D2费后胜率"], money: ["期末资金", "交易费"],
  decimals3: ["年化夏普"] });
overview.getRange("A10").values = [["不同起点：门槛始终用 2023-05 起的完整影子历史"]];
overview.getRange("A10").format.font = { name: "Arial", size: 11, bold: true, color: "#153D4B" };
table(overview, 11, starts, { signed: ["总收益"], unsigned: ["最大回撤"],
  decimals3: ["年化夏普"] });
overview.getRange("A26").values = [["逐年：同一账户连续运行，年初不重置"]];
overview.getRange("A26").format.font = { name: "Arial", size: 11, bold: true, color: "#153D4B" };
table(overview, 27, years, { signed: ["收益"], unsigned: ["最大回撤"] });
overview.tabColor = "#164A54";

const monthKeys = ["月份", "前月候选数", "前月末5日", "前月末10日", "月初暂停",
  "触发卖出日", "原B2候选", "原B2买入", "亏损月全停买入", "亏损月胜率恢复买入",
  "原B2月收益", "亏损月全停月收益", "亏损月胜率恢复月收益",
  "原B2月回撤", "亏损月全停月回撤", "亏损月胜率恢复月回撤"];
const monthly = await rows("逐月.csv", monthKeys,
  monthKeys.filter((x) => !["月份", "触发卖出日"].includes(x)), ["触发卖出日"]);
const monthSheet = wb.worksheets.add("逐月");
monthSheet.showGridLines = false;
table(monthSheet, 1, monthly, { signed: ["前月末5日", "前月末10日", "原B2月收益",
  "亏损月全停月收益", "亏损月胜率恢复月收益"], unsigned: ["原B2月回撤",
  "亏损月全停月回撤", "亏损月胜率恢复月回撤"], date: ["触发卖出日"] });
monthSheet.freezePanes.freezeRows(1);
monthSheet.freezePanes.freezeColumns(1);
monthSheet.tabColor = "#39747A";

const tradeKeys = ["选股月", "D0", "D1", "股票", "排名", "日线分", "四K规则", "买入时刻",
  "买价", "D2收盘", "影子D2费后收益", "前月末5日", "前月末10日", "月初暂停",
  "当月影子已卖", "当月影子胜率", "胜率触发日", "全月暂停买入", "胜率恢复买入",
  "亏损月全停投入", "亏损月全停盈亏额", "亏损月胜率恢复投入", "亏损月胜率恢复盈亏额"];
const tradeNumeric = tradeKeys.filter((x) => !["选股月", "D0", "D1", "股票", "四K规则",
  "买入时刻", "胜率触发日"].includes(x));
const tradeRows = await rows("逐笔候选.csv", tradeKeys, tradeNumeric,
  ["D0", "D1", "胜率触发日"]);
const tradeSheet = wb.worksheets.add("逐笔");
tradeSheet.showGridLines = false;
table(tradeSheet, 1, tradeRows, { signed: ["影子D2费后收益", "前月末5日", "前月末10日"],
  unsigned: ["当月影子胜率"], decimals4: ["日线分"],
  money: ["买价", "D2收盘", "亏损月全停投入", "亏损月全停盈亏额",
          "亏损月胜率恢复投入", "亏损月胜率恢复盈亏额"],
  date: ["D0", "D1", "胜率触发日"], text: ["股票"] });
tradeSheet.freezePanes.freezeRows(1);
tradeSheet.freezePanes.freezeColumns(4);

wb.recalculate();
for (const [sheetName, range] of [["总览", "A2:K8"], ["逐月", "A1:P7"], ["逐笔", "A1:W7"]]) {
  const result = await wb.inspect({ kind: "table", sheetId: sheetName, range,
    include: "values,formulas", tableMaxRows: 7, tableMaxCols: 16, maxChars: 1800 });
  console.log(`${sheetName}: ${result.ndjson.slice(0, 1800)}`);
}
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!",
  options: { useRegex: true, maxResults: 50 }, maxChars: 1200 });
console.log(`Errors: ${errors.ndjson.slice(0, 1200)}`);
for (const [name, range] of [["总览", "A2:K9"], ["逐月", "A1:H8"], ["逐笔", "A1:J8"]]) {
  const preview = await wb.render({ sheetName: name, range, scale: 1.2, format: "png" });
  await fs.writeFile(path.join(root, `preview_${name}.png`),
    new Uint8Array(await preview.arrayBuffer()));
}
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(out);
console.log(out);
