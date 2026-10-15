import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const notes=JSON.parse(await fs.readFile(path.join(dir,'workbook_notes.json'),'utf8'));
const wb=Workbook.create(),checks=[];
const pct=new Set(['再板率','单笔均值','账户收益','最大回撤','训练原命中','训练命中','验证原命中','验证命中','月均差','差值下限','差值上限',...['D2','D3','D4','策略'].flatMap(x=>[x+'均值',x+'月收益',x+'回撤'])]);
function col(i){let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
let ti=0;
for(const [name,rows] of Object.entries(data)){
 const sh=wb.worksheets.add(name),cols=Object.keys(rows[0]),n=rows.length+1;
 sh.showGridLines=false;
 sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>['日期','D1日期'].includes(c)&&r[c]?(Date.parse(r[c]+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000:r[c]??null));
 sh.tables.add(`A1:${col(cols.length-1)}${n}`,true,`R22Table${++ti}`).showFilterButton=true;
 sh.getRange(`A1:${col(cols.length-1)}${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:25,verticalAlignment:'center'};
 sh.getRange(`A1:${col(cols.length-1)}1`).format={fill:'#234D46',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',rowHeight:38};
 cols.forEach((c,i)=>{
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=c==='条件'?58:c==='验证月'?24:['区间','方案','窗口','滚动方案','对照方案'].includes(c)?21:14;
  const r=sh.getRangeByIndexes(1,i,rows.length,1);
  if(rows.some(x=>typeof x[c]==='number'))r.format.horizontalAlignment='center';
  if(c==='条件')r.format.wrapText=true;
  if(['日期','D1日期'].includes(c))r.format.numberFormat='yyyy-mm-dd';
  else if(pct.has(c))r.format.numberFormat='0.00%;[Red](0.00%);0.00%';
  else if(['评分','月末净值'].includes(c))r.format.numberFormat='0.0000';
  else if(['买入价','36最高价','36最低价'].includes(c))r.format.numberFormat='0.00';
  else if(rows.some(x=>typeof x[c]==='number'))r.format.numberFormat='0';
 });
 sh.freezePanes.freezeRows(1);
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F6`,include:'values,formulas',tableMaxRows:6,tableMaxCols:6,maxChars:2000})).ndjson);
}
const summary=wb.worksheets.getItem('总体对照'),first=data['总体对照'].length+4;
notes.forEach((text,i)=>{
 const r=summary.getRange(`A${first+i}:L${first+i}`);r.merge();summary.getRange(`A${first+i}`).values=[[text]];
 r.format={font:{name:'Arial',size:10,color:'#465263'},wrapText:true,rowHeight:34,verticalAlignment:'center'};
});
wb.recalculate();
for(const [name,rows] of Object.entries(data)){
 const n=Object.keys(rows[0]).length;
 for(let i=0;i<n;i+=6){
  const range=`${col(i)}1:${col(Math.min(i+5,n-1))}${name==='总体对照'?9:7}`;
  const png=await wb.render({sheetName:name,range,scale:1.25,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${i}.png`),new Uint8Array(await png.arrayBuffer()));
 }
}
const notePng=await wb.render({sheetName:'总体对照',range:`A${first}:L${first+7}`,scale:1,format:'png'});
await fs.writeFile(path.join(dir,'preview_notes.png'),new Uint8Array(await notePng.arrayBuffer()));
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const name='形态短窗_滚动与固定规则对照_R22_20261015.xlsx';
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,name));
console.log(JSON.stringify({xlsx:path.join(dir,name),sheets:Object.keys(data)}));
