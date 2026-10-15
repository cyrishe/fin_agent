import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const wb=Workbook.create();
const dates=new Set(['日期','月份','预测月','训练起','训练止','验证起','验证止']);
const rates=new Set(['平衡准确率','原大跌率','标记比例','标记大跌率','大跌检出率','非大跌误杀率','上涨误杀率',
 '保留大跌率','原三日均涨','保留三日均涨','名义标记比例','实际标记比例','三日大跌率','D1-3再板率',
 'D0至D3均涨','D2均涨','D3均涨','D4均涨','策略均涨','三日涨幅','三日最低收涨','D2收益','D3收益','D4收益','策略收益',
 '换手率','样本比例']);
const scores=new Set(['两极AUC','两极AP','训练CVAUC','验证AUC','风险门槛','风险评分']);
function letter(i){let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
let nt=0;
for(const [name,source] of Object.entries(data)){
 const rows=[...source];
 if(name==='总体效果'||name==='逐月效果')rows.sort((a,b)=>({测试:0,验证:1,训练交叉验证:2,训练拟合内:3}[a.集合])-({测试:0,验证:1,训练交叉验证:2,训练拟合内:3}[b.集合]));
 const cols=Object.keys(rows[0]),end=letter(cols.length-1),sh=wb.worksheets.add(name),n=rows.length+1;
 sh.showGridLines=false;
 sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>dates.has(c)&&r[c]
  ? (Date.parse(r[c]+(r[c].length===7?'-01':'')+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000 :r[c]??null));
 sh.tables.add(`A1:${end}${n}`,true,`R17Table${++nt}`).showFilterButton=true;
 sh.getRange(`A1:${end}${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:24,verticalAlignment:'center'};
 sh.getRange(`A1:${end}1`).format={fill:'#253D56',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,
  horizontalAlignment:'center',verticalAlignment:'center',rowHeight:38};
 cols.forEach((c,i)=>{
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=c==='组合'?26:c==='采样'?22:c==='范围'||c==='集合'?20:c==='日期'?15:15;
  const r=sh.getRangeByIndexes(1,i,rows.length,1);
  if(dates.has(c)){r.format.numberFormat=c==='日期'?'yyyy-mm-dd':'yyyy-mm';r.format.horizontalAlignment='center';}
  else if(rates.has(c))r.format.numberFormat='0.00%;[Red](0.00%);0.00%';
  else if(scores.has(c))r.format.numberFormat='0.0000';
  else if(c==='学习率')r.format.numberFormat='0.000';
  else if(c==='流值估算亿'||c==='成交额亿')r.format.numberFormat='#,##0.00';
  else if(c.endsWith('数'))r.format.numberFormat=rows.some(x=>typeof x[c]==='number'&&!Number.isInteger(x[c]))?'#,##0.0':'#,##0';
  if(['原入选','风险标记','是否ST','D1-3再板','种子'].includes(c))r.format.horizontalAlignment='center';
 });
 if(rows.length>15)sh.freezePanes.freezeRows(1);
}
// Keep interpretation beside the small summary, outside its analytic table.
const notes=wb.worksheets.getItem('总体效果');
notes.getRange('A8:P8').merge();notes.getRange('A8').values=[['主标签：D3收盘 / D0收盘 − 1；跌超5%为大跌，跌幅≤2%含上涨为非大跌，[-5%,-2%)不参与二分训练。']];
notes.getRange('A9:P9').merge();notes.getRange('A9').values=[['两极AUC/AP/平衡准确率只评价两极；过滤指标覆盖全部可标注样本（包括中间区间）。风险评分不是全样本大跌概率。']];
notes.getRange('A10:P10').merge();notes.getRange('A10').values=[['主过滤门槛取训练交叉验证评分80%分位；测试实际标记比例可变化。训练/验证为14窗口中位；测试为去重合计。']];
notes.getRange('A11:P11').merge();notes.getRange('A11').values=[['原模型核验：给原名单追加独立风险标记，标记后空仓、不递补；交易收益沿用原09:41成交代理，未扣成本。']];
notes.getRange('A12:P12').merge();notes.getRange('A12').values=[['三日均涨是D0至D3的价格变化，不代表可执行交易收益；三日最低收涨仅检查D1—D3收盘价，不是盘中最低。']];
notes.getRange('A8:P12').format={font:{name:'Arial',size:10,color:'#465263'},rowHeight:25,verticalAlignment:'center'};
wb.recalculate();
const checks=[];
for(const [name,rows] of Object.entries(data)){
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F5`,include:'values,formulas',tableMaxRows:5,tableMaxCols:6,maxChars:1400})).ndjson);
 const n=Object.keys(rows[0]).length;
 for(let i=0;i<n;i+=8){
  const blob=await wb.render({sheetName:name,range:`${letter(i)}1:${letter(Math.min(i+7,n-1))}7`,scale:1.2,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${i}.png`),new Uint8Array(await blob.arrayBuffer()));
 }
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const filename='涨停后三日大跌_独立两极模型_验证与前五核验_R17_20261015.xlsx';
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,filename));
console.log(path.join(dir,filename));
