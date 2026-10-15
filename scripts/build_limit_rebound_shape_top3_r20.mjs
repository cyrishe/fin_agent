import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]),data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const wb=Workbook.create(),checks=[];
const pct=new Set(['大涨率','大跌率','平均收益','三日再板率','D2均值','D3均值','D4均值','策略均值','D2大涨率','D2大跌率',
 '五分最高','五分最低','五分实体','高点回落','收价位置','D2收益','D3收益','D4收益','策略收益']);
const notes=[
 '大涨=对应退出收盘相对模拟买价累计涨幅≥7%；大跌=累计跌幅>5%；中间样本仍在分母。分别看D2、D3、D4、策略，不是盘中最高/最低收益。',
 '涨跌区分：各D0排名独立可买样本的通过/未通过对照，未施加每日只能买一只的竞争；独立组别笔数不能当每日组合交易数。',
 '顺延效果：每天最多一笔，按当时可买性顺延至D0前三。第一阶段分数及R19逐月形态规则全部复用，本轮未重训、调阈值或重新选模。',
 '交易：先完整观察D1前五分钟；之后至09:40逐分钟选形态通过、有成交且开盘低于涨停价的候选，同一分钟按D0排名选第一只。',
 '09:36标签代表09:35—09:36，该分钟开价是买价代理。若先买Top2/3，后来Top1才开板，也不回头改选。未模拟真实下单延迟和成交。',
 '训练/验证数据为14个重叠窗口各自指标的中位数，笔数可有小数；只对有可计算结果的窗口求比例中位数，窗口不能相加为独立样本。',
 '6月训练＋2月验证＋下一月测试，训练第一阶段分数为历史折外；形态规则沿用R19训练/验证已冻结结果，Top2/3不再拟合。',
 '本轮按每个期限独立保留已知收益；旧R19选阈值要求D4完整，因此训练/验证描述性分母可与旧选阈值样本稍有不同。测试Top1复现不变。',
 '测试：2025-09至2026-10-13，267个日期、801条前三候选，10月不完整。该历史区间此前已研究，非全新外部盲测。',
 '前三加形态156笔：Top1 64、Top2 59、Top3 33。顺延92笔中89个新增交易日，3笔先买低排名候选，Top1后来才开板。',
 '156笔中再板标签有效154笔；D2/D3/D4收益有效155/154/152笔。未齐或价格不连续留空，不能填0。三日再板包含D1当天。',
 '策略卖出=D2封板收盘卖，否则D3封板收盘卖，否则D4收盘卖；没再板也纳入。均为每笔毛收益，未扣费，不是账户收益。',
 '明细日期为D0，D1日期为分钟发生日。筛前三入选=1得156笔；其他行的收益为同一入场方法的候选对照，不代表组合同时买入。',
 '形态通过并不代表可买。测试排除7条历史特殊状态和6条普通股无成交缺分钟；不递补到第4名。历史状态按当时区间连接。',
 '五分最高/最低相对D1前收盘；实体=09:35收价/09:30开价−1；高点回落=(最高价−09:35收价)/前收盘；收价位置在高低区间内，无振幅记50%。',
 '触板分钟=有量且高价触及涨停；整分板下=有量且高价低于涨停。买分全板下是事后执行诊断，不能证明每秒都有足够卖盘。',
 '来源：R18保存分数及日线、R19冻结规则，本轮原始分钟与历史状态快照。1609个分钟日、2410条旧Top1、9656个逐时序选股和21702项收益复核通过。',
 '结论：大涨分层能迁移，但Top2/3的D2大跌率没有同步降低；扩容增加机会，原策略收益仍近零，尚未证明整体更优。'
];
function col(i){let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
let ti=0;
for(const [name,rows] of Object.entries(data)){
 const sh=wb.worksheets.add(name),cols=Object.keys(rows[0]),n=rows.length+1;
 sh.showGridLines=false;
 sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>['日期','D1日期'].includes(c)&&r[c]?(Date.parse(r[c]+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000:r[c]??null));
 sh.tables.add(`A1:${col(cols.length-1)}${n}`,true,`R20Table${++ti}`).showFilterButton=true;
 sh.getRange(`A1:${col(cols.length-1)}${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:25,verticalAlignment:'center'};
 sh.getRange(`A1:${col(cols.length-1)}1`).format={fill:'#234D46',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',rowHeight:40};
 cols.forEach((c,i)=>{
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=['集合','方案','核对'].includes(c)?29:['日期','D1日期','再板有效笔','策略有效笔'].includes(c)?17:14;
  const r=sh.getRangeByIndexes(1,i,rows.length,1);
  if(['日期','D1日期'].includes(c))r.format.numberFormat='yyyy-mm-dd';
  else if(pct.has(c))r.format.numberFormat='0.00%;[Red](0.00%);0.00%';
  else if(c==='评分')r.format.numberFormat='0.0000';
  else if(c==='买入价')r.format.numberFormat='0.00';
  else if(['有效笔','成交笔数','再板有效笔','策略有效笔'].includes(c))r.format.numberFormat='0.0';
  else if(rows.some(x=>typeof x[c]==='number'))r.format.numberFormat='0';
 });
 sh.freezePanes.freezeRows(1);
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F6`,include:'values,formulas',tableMaxRows:6,tableMaxCols:6,maxChars:1600})).ndjson);
}
const summary=wb.worksheets.getItem('顺延效果'),first=data['顺延效果'].length+4;
notes.forEach((text,i)=>{const row=summary.getRange(`A${first+i}:L${first+i}`);row.merge();summary.getRange(`A${first+i}`).values=[[text]];
 row.format={font:{name:'Arial',size:10,color:'#465263'},wrapText:true,rowHeight:32,verticalAlignment:'center'};});
wb.recalculate();
for(const [name,rows] of Object.entries(data)){
 const n=Object.keys(rows[0]).length;
 for(let i=0;i<n;i+=7){
  const range=`${col(i)}1:${col(Math.min(i+6,n-1))}${name==='顺延效果'?16:6}`;
  const png=await wb.render({sheetName:name,range,scale:1.2,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${i}.png`),new Uint8Array(await png.arrayBuffer()));
 }
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const name='前五分形态_涨跌区分与前三顺延_R20_20261015.xlsx';
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,name));
console.log(JSON.stringify({xlsx:path.join(dir,name),sheets:Object.keys(data)}));
