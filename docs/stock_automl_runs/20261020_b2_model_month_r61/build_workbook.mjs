import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = '/Volumes/ext/fin_agent';
const source = `${root}/docs/stock_automl_runs/20261020_b2_model_month_r61/模型归属.json`;
const target = `${root}/outputs/b2_model_month_r61_20261020/B2_严格月模型归属_R61_20261020.xlsx`;
const data = JSON.parse(await fs.readFile(source, 'utf8'));
const book = Workbook.create();

const asDate = (value) => value ? new Date(`${value}T00:00:00Z`) : null;
const firstSale = new Map();
for (const trade of data.逐笔) {
  const month = trade.卖出日.slice(0,7);
  const old = firstSale.get(month);
  if (!old || trade.卖出日 < old.卖出日 || (trade.卖出日 === old.卖出日 && trade.D0 < old.D0)) firstSale.set(month, trade);
}
const specs = [
  {
    name: '模型月汇总', title: 'B2 按 D0 月模型归属',
    note: '每月模型独立收益：仅交易该月 D0 信号，从100万元起账，所有 D2 结清；不等于连续账户自然月收益。停摆沿用原 R57。',
    headers: ['模型月','停摆','上月末5+10','原候选','原模型胜率','原模型笔均','原模型独立收益','实买','跨月卖','实买胜率','实买笔均','实买收益和','实买独立收益','三连亏','前五三亏'],
    rows: data.月模型.map(r => [r.模型月,r.停摆,r.上月末5加10,r.原候选,r.原模型胜率,r.原模型笔均,r.原模型独立收益,r.实买,r.跨月卖,r.实买胜率,r.实买笔均,r.实买笔收益和,r.实买模型独立收益,r.三连亏,r.前五三亏笔数]),
    percentCols: [3,5,6,7,10,11,12,13],
    intCols: [4,8,9,14,15], widths: [94,66,112,78,104,104,135,70,78,106,104,113,121,78,90],
  },
  {
    name: '月首归属', title: '月首账户收益与首笔卖出的模型归属',
    note: '月首首日非零账户收益来自上月模型持仓或交易；首笔卖出也可能是上月 D0，不能算成本月模型首笔。',
    headers: ['月份','首交易日','原B2首日收益','停摆首日收益','首笔卖出D0','首笔卖出日','首笔属上月','首笔费后收益'],
    rows: data.月首日.map(r => {const t=firstSale.get(r.月份);return [r.月份,asDate(r.首个交易日),r.原B2首日日收益,r.停摆首日日收益,asDate(t?.D0),asDate(t?.卖出日),t?(t.模型月===r.月份?'否':'是'):null,t?.费后收益??null]}),
    dateCols:[2,5,6],percentCols:[3,4,8],widths:[94,112,116,116,112,112,99,118],
  },
  {
    name: '前五笔对照', title: '前五笔亏损：卖出月与 D0 模型月',
    note: '旧统计按 D2 卖出月；新统计只取该模型 D0 选股。触发笔数 4/5 指前四或前五笔累计至少三亏，结果到对应 D2 收盘才知道。',
    headers: ['模型月','实买','卖出月首笔D0','首笔属上月','旧前五盈亏','旧触发笔','模型前五盈亏','模型触发笔','模型三连亏','首次三连亏确认日'],
    rows: data.旧新前五.map(r => [r.模型月,r.实买,asDate(r.卖出月首笔D0),r.卖出月首笔属上月?'是':'否',r.旧前五盈亏,r.旧三亏笔数,r.模型前五盈亏,r.模型三亏笔数,r.模型三连亏,asDate(r.首次三连亏确认日)]),
    dateCols: [3,10], intCols: [2,4,6,8,9], widths: [94,70,122,104,123,90,130,104,103,142],
  },
  {
    name: '跨月卖出', title: 'D0 属本月、D2 在下月的实际交易',
    note: '共31笔，其中包含旧 R60 截止 D1 为9月30日时漏掉的 2026-09-30 D0；其 D2 为 2026-10-09。',
    headers: ['模型月','D0','买入日','卖出日','股票','D0排名','买价','卖价','费后收益','投入元','盈亏元'],
    rows: data.跨月卖.map(r => [r.模型月,asDate(r.D0),asDate(r.买入日),asDate(r.卖出日),Number(r.股票),r.D0排名,r.买价,r.卖价,r.费后收益,r.投入元,r.盈亏元]),
    dateCols: [2,3,4], percentCols: [9], currencyCols: [10,11], intCols: [6], codeCols:[5], priceCols:[7,8], widths: [94,112,112,112,82,82,82,82,108,116,116],
  },
  {
    name: '全部实买', title: '实际买入逐笔：按 D0 月模型排列',
    note: '306笔；买卖各0.1%费用。2026-09-30 D0 已归9月模型，10月9日结清。',
    headers: ['模型月','D0','买入日','卖出日','股票','D0排名','买价','卖价','费后收益','投入元','盈亏元','跨月卖'],
    rows: data.逐笔.map(r => [r.模型月,asDate(r.D0),asDate(r.买入日),asDate(r.卖出日),Number(r.股票),r.D0排名,r.买价,r.卖价,r.费后收益,r.投入元,r.盈亏元,r.跨月卖]),
    dateCols: [2,3,4], percentCols: [9], currencyCols: [10,11], intCols: [6,12], codeCols:[5], priceCols:[7,8], widths: [94,112,112,112,82,82,82,82,108,116,116,82],
  },
];

for (const spec of specs) {
  const sheet = book.worksheets.add(spec.name);
  sheet.showGridLines = false;
  sheet.getRange('A2').values = [[spec.title]];
  sheet.getRange('A3').values = [[spec.note]];
  const count = spec.headers.length;
  sheet.getRangeByIndexes(4,0,1,count).values = [spec.headers];
  sheet.getRangeByIndexes(5,0,spec.rows.length,count).values = spec.rows;
  const all = sheet.getRangeByIndexes(1,0,spec.rows.length+4,count);
  all.format.font = {name:'Arial',size:10,color:'#172335'};
  all.format.verticalAlignment = 'center';
  sheet.getRange('A2').format.font = {name:'Arial',size:14,bold:true,color:'#172335'};
  sheet.getRange('A3').format.font = {name:'Arial',size:10,italic:true,color:'#526476'};
  const header = sheet.getRangeByIndexes(4,0,1,count);
  header.format = {fill:'#174a57',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},verticalAlignment:'center',horizontalAlignment:'center'};
  header.format.rowHeightPx = 30;
  const body = sheet.getRangeByIndexes(5,0,spec.rows.length,count);
  body.format.rowHeightPx = 25;
  for (let j=0;j<count;j++) sheet.getRangeByIndexes(4,j,spec.rows.length+1,1).format.columnWidthPx = spec.widths[j];
  for (const j of spec.percentCols??[]) sheet.getRangeByIndexes(5,j-1,spec.rows.length,1).setNumberFormat('0.00%;[Red]-0.00%;–');
  for (const j of spec.currencyCols??[]) sheet.getRangeByIndexes(5,j-1,spec.rows.length,1).setNumberFormat('#,##0;[Red]-#,##0;–');
  for (const j of spec.intCols??[]) sheet.getRangeByIndexes(5,j-1,spec.rows.length,1).setNumberFormat('0');
  for (const j of spec.dateCols??[]) sheet.getRangeByIndexes(5,j-1,spec.rows.length,1).setNumberFormat('yyyy-mm-dd');
  for (const j of spec.codeCols??[]) sheet.getRangeByIndexes(5,j-1,spec.rows.length,1).setNumberFormat('000000');
  for (const j of spec.priceCols??[]) sheet.getRangeByIndexes(5,j-1,spec.rows.length,1).setNumberFormat('0.00');
  sheet.freezePanes.freezeRows(5);
  const letter = (n) => { let s=''; while(n>0){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26)}return s; };
  const last = letter(count);
  const tableTag = {'模型月汇总':'Model','月首归属':'FirstDay','前五笔对照':'First5','跨月卖出':'Cross','全部实买':'Trades'}[spec.name];
  const tbl = sheet.tables.add(`A5:${last}${spec.rows.length+5}`,true,`B2R61_${tableTag}`);
  tbl.style = 'TableStyleMedium2';
}

book.recalculate();
for (const s of ['模型月汇总','月首归属','前五笔对照','跨月卖出','全部实买']) {
  const check = await book.inspect({kind:'region',sheetId:s,range:'A5:F8',maxChars:900});
  console.log(s,check.ndjson.slice(0,900));
  const png = await book.render({sheetName:s,range:'A2:H10',scale:1.5,format:'png'});
  await fs.writeFile(`${root}/outputs/b2_model_month_r61_20261020/${s}.png`,new Uint8Array(await png.arrayBuffer()));
}
const scan = await book.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!',options:{useRegex:true,maxResults:20},maxChars:500});
console.log('errors',scan.ndjson.slice(0,500));
const output = await SpreadsheetFile.exportXlsx(book);
await output.save(target);
console.log(target);
