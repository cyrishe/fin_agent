import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const wb=Workbook.create();
const order=['交易效果','逐月收益','第一阶段','风险过滤','月度模型','前五明细'];
const rates=new Set(['三日再板率','命中率','同日基率','提升','坏样本率','保留坏例率','D2均涨','D3均涨','D4均涨','策略均涨','保留策略均涨',
 '每笔均涨','账户总收益','账户月均收益','账户最大回撤','正收益月占比','扣20基点均涨','账户月收益','月内最大回撤',
 'D2涨幅','D3涨幅','D4涨幅','策略涨幅','换手率','D1开涨','早盘涨幅','早盘回吐']);
const dateCols=new Set(['日期','预测月','训练起','训练止','验证起','验证止','月份']);
const notes={
 '交易效果':'测试为 2025-09 至 2026-10（10月未完）；每笔最多25%仓位；账户为毛收益。训练、验证按每轮分别统计。',
 '第一阶段':'Top固定于D0；开盘激活口径只统计其中D1开盘未封板者。训练拟合内用于诊断；选模使用训练折外和验证。',
 '风险过滤':'坏样本：D2—D4未封板且D4较09:41买价跌超5%；拦盈利数按策略卖出收益>0统计。训练为二段拟合内诊断。',
 '前五明细':'按集合筛选；各轮训练/验证有重叠，不可混加。D0日期列首；风险因子截至D1 09:40，买价代理为09:41开盘。'
};
function letter(i){let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
let number=0;
for(const name of order){
 let rows=data[name];
 if(name==='交易效果'||name==='前五明细'||name==='风险过滤'||name==='第一阶段'){
  rows=[...rows].sort((a,b)=>({测试:0,验证:1,训练:2,训练折外:2,训练拟合内:3}[a.集合]??9)-({测试:0,验证:1,训练:2,训练折外:2,训练拟合内:3}[b.集合]??9));
 }
 const columns=Object.keys(rows[0]);const sh=wb.worksheets.add(name);sh.showGridLines=false;
 const end=letter(columns.length-1),n=rows.length+1;
 sh.getRangeByIndexes(0,0,1,columns.length).values=[columns];
 const values=rows.map(r=>columns.map(c=>{
  if(dateCols.has(c)&&r[c])return (Date.parse(r[c]+(r[c].length===7?'-01':'')+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000;
  return r[c]??null;
 }));
 sh.getRangeByIndexes(1,0,rows.length,columns.length).values=values;
 sh.tables.add(`A1:${end}${n}`,true,`R15Table${++number}`).showFilterButton=true;
 sh.getRange(`A1:${end}${n}`).format.font={name:'Arial',size:10,color:'#202936'};
 sh.getRange(`A1:${end}${n}`).format.rowHeight=22;
 sh.getRange(`A1:${end}1`).format={fill:'#203754',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',verticalAlignment:'center',rowHeight:36};
 columns.forEach((c,i)=>{
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=c==='范围'?17:c==='模型'||c.endsWith('模型')?19:c==='日期'?15:14;
  const rng=sh.getRangeByIndexes(1,i,rows.length,1);
  if(dateCols.has(c))rng.format.numberFormat=c==='日期'?'yyyy-mm-dd':'yyyy-mm';
  else if(rates.has(c))rng.format.numberFormat='0.00%;[Red](0.00%);0.00%';
  else if(c.includes('评分')||c==='过滤门槛')rng.format.numberFormat='0.0000';
  else if(c==='流值估算亿'||c==='早盘量比')rng.format.numberFormat='0.00';
  if(rates.has(c)&&c.includes('涨'))rng.conditionalFormats.add('cellIs',{operator:'lessThan',formula:0,format:{font:{color:'#B42318'}}});
 });
 if(notes[name])sh.getRangeByIndexes(1,columns.length+1,1,1).values=[[notes[name]]];
 sh.freezePanes.freezeRows(1);
}
wb.recalculate();
const checks=[];
for(const name of order){
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F5`,include:'values,formulas',tableMaxRows:5,tableMaxCols:6,maxChars:1800})).ndjson);
 const count=Object.keys(data[name][0]).length;
 for(let start=0;start<count;start+=9){
  const range=`${letter(start)}1:${letter(Math.min(start+8,count-1))}7`;
  const preview=await wb.render({sheetName:name,range,scale:1.2,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${start}.png`),new Uint8Array(await preview.arrayBuffer()));
 }
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'final error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const output=path.join(dir,'D0初选_D1早盘避险_6月训练2月验证_R15_20261015.xlsx');
await(await SpreadsheetFile.exportXlsx(wb)).save(output);
console.log(output);
