import fs from 'node:fs/promises';
import path from 'node:path';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]),mode=process.argv[3]??'edit';
const source=path.resolve('outputs/stock_automl/limit_rebound_fee001_r23c_20261015/百万资金轮换_单边千一费后_R23C_20261015.xlsx');
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const sh=wb.worksheets.getItem('买卖明细');
if(mode==='inspect'){
 console.log((await wb.inspect({kind:'workbook,sheet,table',maxChars:3500,tableMaxRows:2,tableMaxCols:4})).ndjson);
 console.log(wb.help('table.resize',{include:'index,examples,notes',maxChars:2200}).ndjson);
 const p=await wb.render({sheetName:'买卖明细',range:'L1:P7',scale:1.1,format:'png'});
 await fs.writeFile(path.join(dir,'preview_before.png'),new Uint8Array(await p.arrayBuffer()));
}else{
 const rows=JSON.parse(await fs.readFile(path.join(dir,'added_columns.json'),'utf8'));
 const cols=Object.keys(rows[0]),n=rows.length+1,start=16;
 const col=i=>{let s='';for(let j=i+1;j;j=Math.floor((j-1)/26))s=String.fromCharCode(65+(j-1)%26)+s;return s;};
 sh.getRangeByIndexes(0,start,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,start,rows.length,cols.length).values=rows.map(r=>cols.map(c=>r[c]??null));
 // Resize is not exposed by the documented API. Recreate only this native table,
 // retaining its name/style/flags and all existing worksheet cells.
 const prior=sh.tables.items[0];
 const options={name:prior.name,style:prior.style,showHeaders:prior.showHeaders,showTotals:prior.showTotals,
   showBandedColumns:prior.showBandedColumns,showFilterButton:prior.showFilterButton};
 prior.delete();
 const table=sh.tables.add(`A1:${col(start+cols.length-1)}${n}`,true,options.name);
 for(const [k,v] of Object.entries(options))if(k!=='name')table[k]=v;
 const range=sh.getRangeByIndexes(0,start,n,cols.length);
 range.format={font:{name:'Arial',size:11,color:'#202936'},rowHeight:28,verticalAlignment:'center'};
 sh.getRangeByIndexes(0,start,1,cols.length).format={fill:'#234D46',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',rowHeight:36};
 const pct=new Set(['五分最高','五分最低','五分实体','五分位置','买量占分量']);
 cols.forEach((c,i)=>{
   const full=sh.getRangeByIndexes(0,start+i,n,1),r=sh.getRangeByIndexes(1,start+i,rows.length,1);
   full.format.columnWidth=c==='触发条件'?49:c==='六至十分'?29:['买点时段','买后首触'].includes(c)?23:18;
   r.format.horizontalAlignment=c==='触发条件'?'left':'center';
   if(pct.has(c))r.format.numberFormat='0.00%;[Red](0.00%)';
   else if(c==='评分')r.format.numberFormat='0.0000';
   else if(typeof rows[0][c]==='number')r.format.numberFormat='0';
   if(c==='买量占分量')rows.forEach((row,j)=>{if(row[c]>1)sh.getRangeByIndexes(j+1,start+i,1,1).format.font.color='#C62828';});
 });
 const notes=JSON.parse(await fs.readFile(path.join(dir,'added_notes.json'),'utf8'));
 const overview=wb.worksheets.getItem('资金总览');
 notes.forEach((text,i)=>{
   const r=overview.getRange(`A${36+i}:C${36+i}`);r.merge();overview.getRange(`A${36+i}`).values=[[text]];
   r.format={font:{name:'Arial',size:10,color:'#465263'},wrapText:true,rowHeight:40,verticalAlignment:'center'};
 });
 wb.recalculate();
 for(let i=0;i<cols.length;i+=4){
   const png=await wb.render({sheetName:'买卖明细',range:`${col(start+i)}1:${col(start+Math.min(i+3,cols.length-1))}7`,scale:1.15,format:'png'});
   await fs.writeFile(path.join(dir,`preview_added_${i}.png`),new Uint8Array(await png.arrayBuffer()));
 }
 const png=await wb.render({sheetName:'资金总览',range:`A36:C${35+notes.length}`,scale:1,format:'png'});
 await fs.writeFile(path.join(dir,'preview_added_notes.png'),new Uint8Array(await png.arrayBuffer()));
 const checks=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},summary:'error scan'});
 await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.ndjson);
 const dest=path.join(dir,'百万资金轮换_信号与交易窗口_R23D_20261015.xlsx');
 await(await SpreadsheetFile.exportXlsx(wb)).save(dest);
 console.log(JSON.stringify({xlsx:dest,addedColumns:cols}));
}
