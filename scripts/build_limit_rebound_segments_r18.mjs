import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const wb=Workbook.create();
const dates=new Set(['日期','月份','预测月','训练起','训练止','验证起','验证止']);
const rates=new Set(['开盘样本再板率','Top1命中率','Top3命中率','Top5命中率','全池Top1','全池Top3','全池Top5',
  '板前涨幅','板前换手','D0换手','板段总换手','板段一字比']);
const ratios=new Set(['D0量比','板段量比','成交额亿','流值估算亿']);
function letter(i){let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
const checks=[];let nt=0;
for(const [name,rows] of Object.entries(data)){
  const cols=Object.keys(rows[0]);const sh=wb.worksheets.add(name),n=rows.length+1,end=letter(cols.length-1);
  sh.showGridLines=false;
  sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
  sh.getRangeByIndexes(1,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>dates.has(c)&&r[c]
   ?(Date.parse(r[c]+(r[c].length===7?'-01':'')+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000:r[c]??null));
  sh.tables.add(`A1:${end}${n}`,true,`R18Table${++nt}`).showFilterButton=true;
  sh.getRange(`A1:${end}${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:24,verticalAlignment:'center'};
  sh.getRange(`A1:${end}1`).format={fill:'#234D46',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,
    horizontalAlignment:'center',verticalAlignment:'center',rowHeight:38};
  cols.forEach((c,i)=>{
    sh.getRangeByIndexes(0,i,n,1).format.columnWidth=c==='含义'?90:c==='字段'?21:c==='方案'||c==='集合'?18:15;
    const r=sh.getRangeByIndexes(1,i,rows.length,1);
    if(dates.has(c)){r.format.numberFormat=c==='日期'?'yyyy-mm-dd':'yyyy-mm';r.format.horizontalAlignment='center';}
    else if(rates.has(c))r.format.numberFormat='0.00%;[Red](0.00%);0.00%';
    else if(c==='评分'||c==='开盘AP')r.format.numberFormat='0.0000';
    else if(ratios.has(c))r.format.numberFormat='#,##0.00';
    else if(rows.some(x=>typeof x[c]==='number'))r.format.numberFormat=rows.some(x=>typeof x[c]==='number'&&!Number.isInteger(x[c]))?'#,##0.0':'#,##0';
  });
  sh.freezePanes.freezeRows(1);
  checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F5`,include:'values,formulas',tableMaxRows:5,tableMaxCols:6,maxChars:1200})).ndjson);
}
const sh=wb.worksheets.getItem('效果对照');
const notes=[
 '标签：D1—D3任一天收盘封板为1，三天均不封板为0；未来行情不足时留空。',
 'Top1/3/5：先固定D0全池排名，再统计其中D1开盘低于涨停价的股票；不向后递补。全池TopK不要求开盘激活。',
 '训练拟合内、训练折外、验证：14个窗口中位数，不能相加；测试：2025-09至2026-10-13去重合计。逐月效果以模型预测月列示。',
 '每月6个月训练、2个月验证。按月选优只看历史折外及验证的Top3/5；测试未用于选择。训练使用历史开盘激活样本。',
 '三组均固定随机森林：240树，深7，叶最少50，特征采样0.8，种子20261013，类别均衡权重；只改变特征。',
 '旧特征35项；板前对齐35项；完整分段51项。明细只列完整分段模型的每日前五；该方案每月重训，未按测试成绩改选。',
 '本表评价再封板分类；09:40成交与收益策略需另行回放。历史区间曾用于研究，尚不是全新市场验证。',
 '当前候选沿用R15历史非ST池。流值估算由成交额/换手率推算，不是实际总市值。详细模型、数据来源和口径见同目录研究记录。'
];
notes.forEach((s,i)=>{const r=20+i;sh.getRange(`A${r}:Q${r}`).merge();sh.getRange(`A${r}`).values=[[s]];});
sh.getRange('A20:Q27').format={font:{name:'Arial',size:10,color:'#465263'},rowHeight:26,verticalAlignment:'center'};
wb.recalculate();
for(const [name,rows] of Object.entries(data)){
  const n=Object.keys(rows[0]).length;
  for(let i=0;i<n;i+=8){
    const blob=await wb.render({sheetName:name,range:`${letter(i)}1:${letter(Math.min(i+7,n-1))}7`,scale:1.2,format:'png'});
    await fs.writeFile(path.join(dir,`preview_${name}_${i}.png`),new Uint8Array(await blob.arrayBuffer()));
  }
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const filename='涨停前与连板段_特征对照及前五_R18_20261015.xlsx';
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,filename));
console.log(path.join(dir,filename));
