import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const wb=Workbook.create();
const order=['交易效果','搜索对比','条件组合','逐月收益','第一阶段','风险过滤','候选比较','前五明细'];
const rateNames=['D2-4再板率','命中率','同日基率','提升','坏样本率','保留坏例率','D2均涨','D3均涨','D4均涨','策略均涨','保留策略均涨',
 '每笔均涨','账户总收益','账户月均收益','账户最大回撤','正收益月占比','扣20基点均涨','账户月收益','月内最大回撤',
 'D2涨幅','D3涨幅','D4涨幅','策略涨幅','换手率','D1开涨','早盘涨幅','早盘回吐','初抽比例','实用比例','特征比例',
 '训练Top3','训练Top5','验证Top3','验证Top5','测试Top3','测试Top5','测试月波动','测试最差月','月度波动','最差月命中','种子差异'];
const rates=new Set(rateNames),dates=new Set(['日期','预测月','训练起','训练止','验证起','验证止','月份']);
function letter(i){let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
let ntable=0;
for(const name of order){
 let rows=data[name];
 if(['交易效果','前五明细','风险过滤','第一阶段'].includes(name)){
  rows=[...rows].sort((a,b)=>({测试:0,验证:1,训练:2,训练折外:2,训练拟合内:3}[a.集合]??9)-({测试:0,验证:1,训练:2,训练折外:2,训练拟合内:3}[b.集合]??9));
 }
 if(name==='交易效果'){
  const key=r=>[({测试:0,验证:1,训练:2}[r.集合]??9),r.范围==='测试全期'?'0':r.范围,
   ({D2收盘:0,D3收盘:1,D4收盘:2,策略卖出:3}[r.卖出]??9),r.方案==='过滤后'?0:1,r.实验==='R15原方案'?0:1];
  rows.sort((a,b)=>{const x=key(a),y=key(b);for(let i=0;i<x.length;i++){if(x[i]<y[i])return -1;if(x[i]>y[i])return 1;}return 0;});
 }
 const cols=Object.keys(rows[0]),sh=wb.worksheets.add(name),end=letter(cols.length-1),n=rows.length+1;
 sh.showGridLines=false;
 sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>{
  if(dates.has(c)&&r[c])return (Date.parse(r[c]+(r[c].length===7?'-01':'')+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000;
  return r[c]??null;
 }));
 sh.tables.add(`A1:${end}${n}`,true,`R16Table${++ntable}`).showFilterButton=true;
 sh.getRange(`A1:${end}${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:23,verticalAlignment:'center'};
 sh.getRange(`A1:${end}1`).format={fill:'#203754',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,
  horizontalAlignment:'center',verticalAlignment:'center',rowHeight:36};
 cols.forEach((c,i)=>{
  const width=c==='组合'||c==='模型'?22:c==='范围'||c==='实验'||c==='算法'?18:c==='日期'?15:14;
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=width;
  const rng=sh.getRangeByIndexes(1,i,rows.length,1);
  if(dates.has(c))rng.format.numberFormat=c==='日期'?'yyyy-mm-dd':'yyyy-mm';
  else if(rates.has(c))rng.format.numberFormat='0.00%;[Red](0.00%);0.00%';
  else if(c.includes('评分')||c==='过滤门槛'||c==='选模分')rng.format.numberFormat='0.0000';
  else if(c==='学习率')rng.format.numberFormat='0.000';
  else if(c==='流值估算亿'||c==='早盘量比')rng.format.numberFormat='0.00';
  if(rates.has(c)&&c.includes('涨'))rng.conditionalFormats.add('cellIs',{operator:'lessThan',formula:0,format:{font:{color:'#B42318'}}});
 });
 sh.freezePanes.freezeRows(1);
}
wb.recalculate();
const checks=[];
for(const name of order){
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F5`,include:'values,formulas',tableMaxRows:5,tableMaxCols:6,maxChars:1500})).ndjson);
 const n=Object.keys(data[name][0]).length;
 for(let start=0;start<n;start+=8){
  const preview=await wb.render({sheetName:name,range:`${letter(start)}1:${letter(Math.min(start+7,n-1))}7`,scale:1.2,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${start}.png`),new Uint8Array(await preview.arrayBuffer()));
 }
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const output=path.join(dir,'D0涨停_三算法72模型搜索_条件组合与前五_R16_20261015.xlsx');
await(await SpreadsheetFile.exportXlsx(wb)).save(output);
console.log(output);
