import fs from 'node:fs/promises';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const root=process.argv[2];
const file=process.argv[3];
const data=JSON.parse(await fs.readFile(`${root}/workbook_data.json`,'utf8'));
const wb=Workbook.create();
const letter=(index)=>{let out='';for(let n=index+1;n;n=Math.floor((n-1)/26))out=String.fromCharCode(65+(n-1)%26)+out;return out;};
let counter=0;
for(const [name,source] of Object.entries(data)){
  const sh=wb.worksheets.add(name);
  sh.showGridLines=false;
  sh.tabColor=name==='总览'?'#176A70':'#607E8B';
  const end=letter(source.headers.length-1), last=5+source.rows.length;
  const area=sh.getRange(`A1:${end}${last}`);
  area.format.font.name='Arial';
  area.format.font.size=10;
  sh.getRange('A2').values=[[`B2 补2022分钟 · 历史30/10日回放 · ${name}`]];
  sh.getRange('A2').format.font.size=15;
  sh.getRange('A2').format.font.bold=true;
  sh.getRange('A3').values=[[source.note]];
  sh.getRange('A3').format.font.color='#546678';
  sh.getRange(`A5:${end}5`).values=[source.headers];
  if(source.rows.length)sh.getRange(`A6:${end}${last}`).values=source.rows;
  sh.getRange(`A5:${end}${last}`).format.rowHeight=23;
  sh.getRange(`A5:${end}${last}`).format.columnWidth=14;
  sh.getRange(`A5:${end}5`).format.rowHeight=31;
  sh.getRange(`A5:${end}5`).format.fill='#174A5E';
  sh.getRange(`A5:${end}5`).format.font.color='#FFFFFF';
  sh.getRange(`A5:${end}5`).format.font.bold=true;
  sh.getRange(`A5:${end}5`).format.horizontalAlignment='center';
  sh.getRange(`A6:${end}${last}`).setNumberFormat('#,##0;[Red](#,##0);0');
  for(const i of source.percent || [])sh.getRange(`${letter(i)}6:${letter(i)}${last}`).setNumberFormat('0.00%;[Red](0.00%);0.00%');
  for(const i of source.decimal || [])sh.getRange(`${letter(i)}6:${letter(i)}${last}`).setNumberFormat('0.000;[Red](0.000);0.000');
  sh.getRange(`A5:B${last}`).format.columnWidth=18;
  if(name==='总览')sh.getRange(`C5:${end}${last}`).format.columnWidth=16;
  if(name==='逐月')sh.getRange(`B5:C${last}`).format.columnWidth=18;
  if(name==='逐笔信号')sh.getRange(`C5:C${last}`).format.columnWidth=17;
  const table=sh.tables.add(`A5:${end}${last}`,true,`B2R41Table${++counter}`);
  table.showFilterButton=true;
  if(source.rows.length>20)sh.freezePanes.freezeRows(5);
}
wb.recalculate();
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},maxChars:1500});
await fs.writeFile(`${root}/workbook_errors.ndjson`,errors.ndjson);
const check=await wb.inspect({kind:'table',range:'总览!A5:K8',include:'values',tableMaxRows:5,tableMaxCols:11,maxChars:5000});
await fs.writeFile(`${root}/workbook_inspect.ndjson`,check.ndjson);
for(const name of ['总览','逐月']){
  const view=await wb.render({sheetName:name,range:'A1:M12',scale:1.3,format:'png'});
  await fs.writeFile(`${root}/preview_${name}.png`,new Uint8Array(await view.arrayBuffer()));
}
const output=await SpreadsheetFile.exportXlsx(wb);
await output.save(file);
console.log(file);
console.log(errors.ndjson);
