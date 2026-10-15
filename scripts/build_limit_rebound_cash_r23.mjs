import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const notes=JSON.parse(await fs.readFile(path.join(dir,'workbook_notes.json'),'utf8'));
let config={tag:'R23B',filename:'百万资金轮换_D2卖出_月度收益回撤_R23B_20261015.xlsx'};
try { config=JSON.parse(await fs.readFile(path.join(dir,'output_config.json'),'utf8')); } catch(e) {if(e.code!=='ENOENT')throw e;}
const wb=Workbook.create(),checks=[];
const pct=new Set(['总收益','最大回撤','已平胜率','月收益','月内回撤','日收益','全期回撤','收益','净收益','单边费率']);
const percent='0.00%;[Red](0.00%)',number='0.00;[Red](0.00)';
const col=i=>{let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
let ti=0;
for(const [name,rows] of Object.entries(data)){
 const sh=wb.worksheets.add(name),cols=Object.keys(rows[0]),n=rows.length+1;
 sh.showGridLines=false;
 sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>['日期','卖出日'].includes(c)&&r[c]?(Date.parse(r[c]+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000:r[c]??null));
 sh.tables.add(`A1:${col(cols.length-1)}${n}`,true,`${config.tag}Table${++ti}`).showFilterButton=true;
 sh.getRange(`A1:${col(cols.length-1)}${n}`).format={font:{name:'Arial',size:11,color:'#202936'},rowHeight:28,verticalAlignment:'center'};
 sh.getRange(`A1:${col(cols.length-1)}1`).format={fill:'#234D46',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',rowHeight:36};
 cols.forEach((c,i)=>{
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=c==='口径'?66:c==='数值'?28:c==='指标'?22:c==='用款'?17:config.tag==='R23C'?18:15;
  const r=sh.getRangeByIndexes(1,i,rows.length,1);
  r.format.horizontalAlignment=c==='口径'?'left':'center';
  if(['日期','卖出日'].includes(c))r.format.numberFormat='yyyy-mm-dd';
  else if(pct.has(c))r.format.numberFormat=percent;
  else if(c.endsWith('万')||c.endsWith('元')||['夏普','买入价','股数','卖出价','期末估价'].includes(c))r.format.numberFormat=number;
  else if(['买入笔','持仓笔','D0排名'].includes(c))r.format.numberFormat='0';
 });
 if(name==='资金总览')rows.forEach((r,i)=>{
  const cell=sh.getRangeByIndexes(i+1,1,1,1);
  if(typeof r['数值']==='number')cell.format.numberFormat=pct.has(r['指标'])?percent:r['指标'].endsWith('万')||r['指标']==='夏普'?number:'0';
 });
 sh.freezePanes.freezeRows(1);
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:${col(Math.min(cols.length-1,5))}6`,include:'values,formulas',tableMaxRows:6,tableMaxCols:6,maxChars:2200})).ndjson);
}
const overview=wb.worksheets.getItem('资金总览'),first=data['资金总览'].length+4;
notes.forEach((text,i)=>{
 const r=overview.getRange(`A${first+i}:C${first+i}`);r.merge();overview.getRange(`A${first+i}`).values=[[text]];
 r.format={font:{name:'Arial',size:10,color:'#465263'},wrapText:true,rowHeight:38,verticalAlignment:'center'};
});
wb.recalculate();
for(const [name,rows] of Object.entries(data)){
 const n=Object.keys(rows[0]).length;
 for(let i=0;i<n;i+=5){
  const png=await wb.render({sheetName:name,range:`${col(i)}1:${col(Math.min(i+4,n-1))}${name==='资金总览'?rows.length+1:7}`,scale:1.1,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${i}.png`),new Uint8Array(await png.arrayBuffer()));
 }
}
const tail=await wb.render({sheetName:'买卖明细',range:`K103:${col(Object.keys(data['买卖明细'][0]).length-1)}107`,scale:1.2,format:'png'});
await fs.writeFile(path.join(dir,'preview_pending.png'),new Uint8Array(await tail.arrayBuffer()));
const note=await wb.render({sheetName:'资金总览',range:`A${first}:C${first+notes.length-1}`,scale:1,format:'png'});
await fs.writeFile(path.join(dir,'preview_notes.png'),new Uint8Array(await note.arrayBuffer()));
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const name=config.filename;
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,name));
console.log(JSON.stringify({xlsx:path.join(dir,name),sheets:Object.keys(data)}));
