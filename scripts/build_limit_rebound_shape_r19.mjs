import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const wb=Workbook.create(),checks=[];
const pct=new Set(['三日再板率','D2均值','D3均值','D4均值','策略均值','原策略均值','策略亏超5%',
  '训原命中','训后命中','验原命中','验后命中','测原命中','测后命中','开盘涨幅','五分最高','五分最低',
  '五分实体','高点回落','收价位置','D2收益','D3收益','D4收益','策略收益']);
const notes=[
 '主规则：前五分钟最高涨幅≥6%且最低涨幅≥−3%，在14个月中入选8个月。全期成绩属于每月所选规则，不能等同于这一条固定规则。',
 '范围：已保存R18完整分段模型的原D0 Top1。测试2025-09至2026-10-13，10月不完整；历史区间曾研究，非全新外部盲测。',
 '切分：近6个月训练、接下来2个月验证、下1个月测试。训练用旧模型历史折外分数选规则；验证参与选优；测试不参与规则选择。',
 '训练和验证：表中各项为14个窗口各自指标的中位数，故成交笔数可能有小数；窗口重叠，不能相加作独立样本。',
 '判定：D1 09:35完成后，仅使用09:31—09:35五根分钟K。最多2条件；无合格规则的月份全部保留。规则详情见月度规则。',
 '买价代理：判定后至09:40，首根有成交且开盘低于涨停价的分钟，其开盘作买价；无合格分钟不买、不递补。未验证真实委托成交。',
 '分钟采用结束标记：09:36指09:35—09:36，其开盘为代理买点，并非09:36:00实际成交。没有使用判定之前的窗口最高价买入。',
 '三日再板=D1—D3任一天收盘封板，包含买入日D1。D2—D4收益均相对买价、未扣费；不是账户复利收益。',
 '策略卖出：D2收盘封板则卖，否则D3封板收盘卖，否则D4收盘卖。D4前收盘参考价不连续的一笔收益留空，非0收益。',
 '测试Top1日期首列为D0，第二列为D1。执行入选=规则通过×可买代理；筛执行入选=1得67笔。未入选的收益保留作同口径对照。',
 '五分最高/最低相对D1前收盘；实体=09:35收价/09:30开价−1；高点回落=(五分最高价−09:35收价)/D1前收盘。',
 '收价位置=(09:35收价−最低价)/(最高价−最低价)，无振幅记50%。形态由前2分钟与后3分钟两段收价方向划分。',
 '板下分钟=有量且最低价低于涨停；整分板下=有量且最高价也低于涨停。分钟根数不是连续秒数。买入分全板下是事后执行诊断。',
 '测试267条中排除5条历史L特殊状态、2条全天无成交且分钟缺失。历史状态按当日区间连接；原森林旧训练池本轮未重训。',
 '67笔入选：33笔前五分钟未触板；55笔买入分钟全程价格在涨停价下。仍有119条通过规则但后续窗口没有买点。',
 '风险：2026-09留下3笔，策略每笔均值−11.11%。再板识别改善不等于收益稳定，不应只看命中率。',
 '来源：R18保存的逐月分数、日线快照；本轮保存的原始分钟与历史状态区间。601个分钟日、2410条分数、7230项收益独立复核通过。',
 '可复现文件位于同目录：specification.json、all_rules_frozen.json、enriched_top1.pkl、minute_days、verification.json。'
];
const col=i=>{let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
let t=0;
for(const [name,rows] of Object.entries(data)){
 const sh=wb.worksheets.add(name),cols=Object.keys(rows[0]),n=rows.length+1;
 sh.showGridLines=false;
 sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>
  ['日期','D1日期'].includes(c)&&r[c]?(Date.parse(r[c]+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000:r[c]??null));
 sh.tables.add(`A1:${col(cols.length-1)}${n}`,true,`R19Table${++t}`).showFilterButton=true;
 sh.getRange(`A1:${col(cols.length-1)}${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:25,verticalAlignment:'center'};
 sh.getRange(`A1:${col(cols.length-1)}1`).format={fill:'#234D46',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',rowHeight:40};
 cols.forEach((c,i)=>{
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=c==='条件'?62:['集合','核对','训练区间','验证区间'].includes(c)?28:['日期','D1日期','买入分全板下','策略有效笔'].includes(c)?17:14;
  const r=sh.getRangeByIndexes(1,i,rows.length,1);
  if(['日期','D1日期'].includes(c))r.format.numberFormat='yyyy-mm-dd';
  else if(pct.has(c))r.format.numberFormat='0.00%;[Red](0.00%);0.00%';
  else if(c==='评分')r.format.numberFormat='0.0000';
  else if(c==='买入价')r.format.numberFormat='0.00';
  else if(['成交笔数','策略有效笔'].includes(c))r.format.numberFormat='0.0';
  else if(rows.some(x=>typeof x[c]==='number'))r.format.numberFormat='0';
 });
 sh.freezePanes.freezeRows(1);
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F6`,include:'values,formulas',tableMaxRows:6,tableMaxCols:6,maxChars:1600})).ndjson);
}
const summary=wb.worksheets.getItem('效果对照'),first=data['效果对照'].length+4;
notes.forEach((text,i)=>{
 const row=summary.getRange(`A${first+i}:J${first+i}`);row.merge();
 summary.getRange(`A${first+i}`).values=[[text]];
 row.format={font:{name:'Arial',size:10,color:'#465263'},wrapText:true,rowHeight:32,verticalAlignment:'center'};
});
wb.recalculate();
for(const [name,rows] of Object.entries(data)){
 const n=Object.keys(rows[0]).length;
 for(let i=0;i<n;i+=7){
  const png=await wb.render({sheetName:name,range:`${col(i)}1:${col(Math.min(i+6,n-1))}${name==='效果对照'?7:6}`,scale:1.2,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${i}.png`),new Uint8Array(await png.arrayBuffer()));
 }
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const filename='前五分钟形态_Top1简单规则_R19_20261015.xlsx';
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,filename));
console.log(JSON.stringify({xlsx:path.join(dir,filename),sheets:Object.keys(data)}));
