import fs from 'node:fs/promises';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const root=process.argv[2];
const file=process.argv[3];
const data=JSON.parse(await fs.readFile(`${root}/workbook_data.json`,'utf8'));
const wb=Workbook.create();
const pct='0.00%;[Red](0.00%);0.00%';
const notes={
  '总体':'2026年4—9月；100万空仓起步；D2收盘卖；买卖各0.1%；两层训练均6个月。',
  '月度':'连续账户，按持仓日历月统计；月回撤从上月末资产开始计算。',
  '日线评估':'三日再板=D1—D3至少一天收盘封板。原D0排名固定，开盘过滤后不补位。',
  '每月规则':'训练为历史因果预测Top1；验证至少6笔；规则迁移至原Top3。',
  '每日':'空仓买一半现金；有昨日持仓则买剩余现金；收盘卖款次晨可用。',
  '逐笔':'分钟为K线结束标签，买价取该K开价代理；未卖收益留空；流通估值为模型代理值。',
};
let tableNumber=0;
const column=(i)=>{let s='';for(i++;i;i=Math.floor((i-1)/26))s=String.fromCharCode(65+(i-1)%26)+s;return s;};
for(const [name,source] of Object.entries(data)){
  const sh=wb.worksheets.add(name);
  sh.showGridLines=false;
  sh.tabColor=name==='总体'?'#233C58':'#647B91';
  const rows=source.rows.map(row=>row.map((v,i)=>source.dates.includes(i)&&typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)?new Date(`${v}T00:00:00Z`):v));
  const last=5+rows.length, end=column(source.headers.length-1);
  sh.getRange(`A1:${end}${last}`).format.font.name='Arial';
  sh.getRange(`A1:${end}${last}`).format.font.size=11;
  sh.getRange('A2').values=[[`${source.title || 'B2 验证窗口对照'} · ${name}`]];
  sh.getRange('A2').format.font.size=15;
  sh.getRange('A2').format.font.bold=true;
  sh.getRange('A3').values=[[source.note || notes[name] || '']];
  sh.getRange('A3').format.font.color='#586477';
  sh.getRange(`A5:${end}5`).values=[source.headers];
  if(rows.length)sh.getRange(`A6:${end}${last}`).values=rows;
  sh.getRange(`A5:${end}${last}`).format.rowHeight=24;
  sh.getRange(`A5:${end}${last}`).format.columnWidth=14;
  sh.getRange(`A5:${end}5`).format.rowHeight=32;
  sh.getRange(`A5:${end}5`).format.fill='#233C58';
  sh.getRange(`A5:${end}5`).format.font.color='#FFFFFF';
  sh.getRange(`A5:${end}5`).format.font.bold=true;
  sh.getRange(`A5:${end}5`).format.horizontalAlignment='center';
  sh.getRange(`A6:${end}${last}`).setNumberFormat('#,##0;[Red](#,##0);0');
  for(const i of source.percent)sh.getRange(`${column(i)}6:${column(i)}${last}`).setNumberFormat(pct);
  for(const i of source.decimal)sh.getRange(`${column(i)}6:${column(i)}${last}`).setNumberFormat('0.00;[Red](0.00);0.00');
  for(const i of source.dates){
    sh.getRange(`${column(i)}6:${column(i)}${last}`).setNumberFormat(['月度','日线评估','每月规则'].includes(name)?'yyyy-mm':'yyyy-mm-dd');
    sh.getRange(`${column(i)}5:${column(i)}${last}`).format.columnWidth=15;
  }
  if(name==='日线评估')sh.getRange(`D5:D${last}`).format.columnWidth=20;
  if(name==='总体')sh.getRange(`A5:A${last}`).format.columnWidth=30;
  if(name==='每日')sh.getRange(`B5:B${last}`).format.columnWidth=30;
  if(name==='D2评估')sh.getRange(`C5:C${last}`).format.columnWidth=17;
  if(name==='前三候选'){
    sh.getRange(`G5:G${last}`).format.columnWidth=24;
    sh.getRange(`H5:H${last}`).format.columnWidth=58;
    sh.getRange(`D6:D${last}`).setNumberFormat('0.0000');
  }
  if(name==='每月规则'){
    sh.getRange(`C5:F${last}`).format.columnWidth=24;
    sh.getRange(`G5:G${last}`).format.columnWidth=58;
  }
  if(name==='逐笔'){
    sh.getRange(`E5:E${last}`).format.columnWidth=58;
    sh.getRange(`U5:U${last}`).format.columnWidth=19;
    sh.getRange(`D6:D${last}`).setNumberFormat('0.0000');
    sh.getRange(`P6:P${last}`).formulas=rows.map((r,i)=>[r[14]==null?'=IF(O'+(i+6)+'="","",O'+(i+6)+'/L'+(i+6)+'*0.999/1.001-1)':`=O${i+6}/L${i+6}*0.999/1.001-1`]);
  }
  if(name==='总体')sh.getRange(`G6:G${last}`).formulas=rows.map((_,i)=>[`=E${i+6}/F${i+6}`]);
  if(name==='月度' && source.headers[3]==='收益差'){
    sh.getRange(`D6:D${last}`).formulas=rows.map((r,i)=>[r[1]==null?'=""':`=C${i+6}-B${i+6}`]);
    sh.getRange(`G6:G${last}`).formulas=rows.map((r,i)=>[r[4]==null?'=""':`=F${i+6}-E${i+6}`]);
    sh.getRange(`J5:L${last}`).format.columnWidth=18;
  }
  const table=sh.tables.add(`A5:${end}${last}`,true,`R31Table${++tableNumber}`);
  table.showFilterButton=true;
  if(rows.length>20)sh.freezePanes.freezeRows(5);
}
wb.recalculate();
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},maxChars:1500});
await fs.writeFile(`${root}/workbook_errors.ndjson`,errors.ndjson);
console.log(errors.ndjson);
const check=await wb.inspect({kind:'table',range:'总体!A5:N9',include:'values,formulas',tableMaxRows:5,tableMaxCols:14,maxChars:5000});
await fs.writeFile(`${root}/workbook_inspect.ndjson`,check.ndjson);
for(const name of Object.keys(data)){
  if(data[name].render===false)continue;
  const preview=await wb.render({sheetName:name,range:name==='总体'?'A1:N10':'A1:J12',scale:1.3,format:'png'});
  await fs.writeFile(`${root}/preview_${name}.png`,new Uint8Array(await preview.arrayBuffer()));
  if(name==='逐笔' || name==='前三候选'){
    const tail=await wb.render({sheetName:name,range:`K5:${column(data[name].headers.length-1)}12`,scale:1.3,format:'png'});
    await fs.writeFile(`${root}/preview_${name}_后列.png`,new Uint8Array(await tail.arrayBuffer()));
  }
}
const out=await SpreadsheetFile.exportXlsx(wb);await out.save(file);
console.log(file);
