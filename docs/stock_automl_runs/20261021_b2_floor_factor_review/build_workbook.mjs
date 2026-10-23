import fs from 'node:fs/promises';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const root='/Volumes/ext/fin_agent';
const out=`${root}/outputs/b2_floor_factor_review_20261021`;
const original=`${root}/outputs/b2_model_month_r61_20261020/B2_严格月模型归属_R61_20261020.xlsx`;
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(original));
if(process.argv.includes('--inspect')) {
 console.log((await wb.inspect({kind:'workbook,sheet,table',maxChars:3000,tableMaxRows:2,tableMaxCols:4})).ndjson);
 const png=await wb.render({sheetName:'全部实买',range:'A2:L10',scale:1.3,format:'png'});
 await fs.writeFile(`${out}/original.png`,new Uint8Array(await png.arrayBuffer()));
 process.exit(0);
}
const data=JSON.parse(await fs.readFile(`${root}/docs/stock_automl_runs/20261021_b2_floor_factor_review/workbook_data.json`,'utf8'));
const cols=data.columns;
const letter=n=>{let s='';while(n){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26)}return s;};
const rowsByKey=new Map(data.rows.map(r=>[r.symbol+'|'+r.d0_date,r]));
const dateKey=v=>v instanceof Date?v.toISOString().slice(0,10):typeof v==='number'?new Date(Date.UTC(1899,11,30)+v*864e5).toISOString().slice(0,10):String(v).slice(0,10);
const styles={ 'D2结果':'#815336','识别':'#526476','市值行业':'#355C7D','D0量价':'#315E53','D0涨停路径':'#315E53','市场环境':'#456B56','板前及连板':'#456B56','D0及首板':'#456B56','D1买入':'#65527C','D1收盘':'#815336'};
function formatTable(sheet,headers,definitions,n,start=0){
 const head=sheet.getRangeByIndexes(4,start,1,headers.length);
 head.values=[headers];head.format={fill:'#174a57',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',verticalAlignment:'center',rowHeightPx:42};
 const body=sheet.getRangeByIndexes(5,start,n,headers.length);
 body.format.font={name:'Arial',size:10,color:'#172335'};body.format.rowHeightPx=25;body.format.verticalAlignment='center';
 for(let j=0;j<definitions.length;j++){
  const d=definitions[j];const rg=sheet.getRangeByIndexes(5,start+j,n,1);rg.setNumberFormat(d.format??'@');
  sheet.getRangeByIndexes(4,start+j,n+1,1).format.columnWidthPx=d.width??(d.format==='@'?137:118);
  if(d.group){sheet.getCell(3,start+j).values=[[j===0||definitions[j-1].group!==d.group?d.group:null]];sheet.getCell(3,start+j).format.fill=styles[d.group]??'#526476';sheet.getCell(3,start+j).format.font={name:'Arial',size:10,color:'#FFFFFF'};}
  if(['open_floor','close_floor','all_locked'].includes(d.key))rg.conditionalFormats.add('containsText',{text:'是',format:{fill:'#FBE2D5',font:{bold:true,color:'#9C2E21'}}});
  if(d.key==='mv_source')rg.conditionalFormats.add('containsText',{text:'估算',format:{fill:'#FFF2CC'}});
  if(d.key==='opened_after_touch')rg.conditionalFormats.add('containsText',{text:'不明',format:{fill:'#FFF2CC'}});
 }
 sheet.freezePanes.freezeRows(5);
 sheet.showGridLines=false;
}
// Extend the previous 306-row table; keep its original twelve columns and all other sheets.
const main=wb.worksheets.getItem('全部实买');
const old=main.getRange('A6:L311').values;
const extra=cols.filter(c=>!['entry','original_net','d2_close_saved'].includes(c.key));
const ordered=old.map(r=>{
 const key=String(r[4]).padStart(6,'0')+'|'+dateKey(r[1]);const row=rowsByKey.get(key);
 if(!row||Math.abs(row.entry-r[6])>1e-8)throw new Error('Original trade join failed: '+key);
 return row;
});
main.getRangeByIndexes(5,12,306,extra.length).values=ordered.map(r=>extra.map(c=>r[c.key]??null));
formatTable(main,extra.map(c=>c.label),extra,306,12);
for(const t of [...main.tables.items])t.delete();
const mt=main.tables.add(`A5:${letter(12+extra.length)}311`,true,'B2FloorAllTrades');mt.style='TableStyleLight9';
main.getRange('A3').values=[['306笔原交易及因子；D2收益仍是假设收盘成交。开盘/收盘跌停与因子在右侧，专项表单独列67笔。']];
main.freezePanes.freezeColumns(5);
// Also flag the cross-month detail without changing its eleven existing columns.
const cross=wb.worksheets.getItem('跨月卖出');const cr=cross.getRange('A6:K36').values;
cross.getRange('L5:M5').values=[['开盘跌停','收盘跌停']];
cross.getRange('L6:M36').values=cr.map(r=>{const x=rowsByKey.get(String(r[4]).padStart(6,'0')+'|'+dateKey(r[1]));if(!x)throw Error('cross-month join');return [x.open_floor,x.close_floor];});
formatTable(cross,['开盘跌停','收盘跌停'],cols.slice(0,2),31,11);
for(const t of [...cross.tables.items])t.delete();cross.tables.add('A5:M36',true,'B2FloorCrossMonth').style='TableStyleLight9';
const focus=wb.worksheets.add('跌停专项');focus.tabColor='#A54E32';
focus.getRange('A2').values=[['跌停股票因子对照']];focus.getRange('A2').format.font={name:'Arial',size:14,bold:true,color:'#172335'};
focus.getRange('A3').values=[['67笔：开盘34、收盘52、交集19。D0判断日涨停，D1买入，D2计划卖出。未知不记为否，打开不代表订单必成交。']];
focus.getRange('A3').format.font={name:'Arial',size:10,color:'#526476'};
const lookup=new Map(cols.map(c=>[c.key,c]));
const leading=['stock_name','open_floor','close_floor','floor_group','entry','d2_open','d2_close_saved','original_net','open_net','total_mv_yi','float_mv_yi','mv_source','d0_turnover_pct','board_streak_days','d0_amount_100m','opened_after_touch','first_touch','first_close_sealed','final_full_seal'];
const defs=[{key:'sequence',label:'原笔序',format:'0',width:68},{key:'stk_code',label:'股票',format:'@',width:110},...leading.map(k=>lookup.get(k)),...cols.filter(c=>!leading.includes(c.key)),{key:'d0_date',label:'D0索引日期',format:'yyyy-mm-dd',width:110},{key:'d1_date',label:'D1索引日期',format:'yyyy-mm-dd',width:110},{key:'卖出日',label:'D2索引日期',format:'yyyy-mm-dd',width:110}];
const special=data.rows.filter(r=>r.open_floor==='是'||r.close_floor==='是');
focus.getRangeByIndexes(5,0,special.length,defs.length).values=special.map(r=>defs.map(c=>['d0_date','d1_date','卖出日'].includes(c.key)?new Date(r[c.key]+'T00:00:00Z'):r[c.key]??null));
formatTable(focus,defs.map(c=>c.label),defs,special.length);
focus.tables.add(`A5:${letter(defs.length)}${special.length+5}`,true,'B2FloorReview').style='TableStyleLight9';
focus.freezePanes.freezeColumns(5);
const dict=wb.worksheets.add('因子说明');dict.tabColor='#7C8995';dict.showGridLines=false;
dict.getRange('A2').values=[['字段口径与可用时点']];dict.getRange('A2').format.font={name:'Arial',size:14,bold:true,color:'#172335'};
const notes=[
 ['范围','冻结V2：D0为2022-05—2026-09；306笔中，D2开盘或收盘跌停67笔，另239笔为对照。'],
 ['时点','D0收盘字段用于买前研究；D1前四K可用于买入判断。D1收盘、D2结果不能倒填为买前因子。'],
 ['市值','210笔当日历史值，96笔此前股本×D0收盘估算。来源与股本年龄逐笔列明；估算不等同真实当日市值。'],
 ['封板','分钟标签是区间结束时点；同一分钟内最高/最低先后不明会单独标注，不虚构精确炸板次数。'],
 ['成交','原收益仍假设D2收盘全额卖出；开卖收益假设开盘可成交。此次是人工因子对照，未重训或修正账户。'],
 ['来源','R60冻结信号、R61原交易账本；KingdomAI历史日K、市值、ST和行业；本地Tushare/MK及数据库D0分钟K。'],
 ['缺失','空白表示缺值或不适用。D0收盘位置在一字板时分母为0；自由流值、估值缺当日值不以前值填充。'],
 ['行业与名称','行业按D0有效期关联；名称为当前简称，仅便于识别。未用当前板块成员回填历史热点。'],
 ['模型','原51个日线模型因子全部保留。新增人工观察字段不意味着已纳入训练或已有区分能力。']
];
dict.getRange('A4:B12').values=notes;dict.getRange('A4:A12').format.columnWidthPx=150;dict.getRange('B4:B12').format.columnWidthPx=850;dict.getRange('A4:B12').format.font={name:'Arial',size:10,color:'#172335'};dict.getRange('A4:B12').format.rowHeightPx=27;
dict.getRange('A15:F15').values=[['字段','含义与分母','最早可用时点','分组','原51因子','有值笔数']];
dict.getRangeByIndexes(15,0,cols.length,6).values=cols.map(c=>[c.label,c.description,c.known,c.group,c.model_factor?'是':'否',c.nonmissing]);
dict.getRangeByIndexes(15,0,cols.length,6).format.font={name:'Arial',size:10,color:'#172335'};
dict.getRange(`A16:F${15+cols.length}`).format.rowHeightPx=27;
dict.getRange(`B16:B${15+cols.length}`).format.wrapText=true;
for(let i=0;i<cols.length;i++)if(cols[i].description.length>75)dict.getRangeByIndexes(15+i,0,1,6).format.rowHeightPx=48;
dict.getRange('A15:F15').format={fill:'#174a57',font:{name:'Arial',size:10,color:'#FFFFFF',bold:true},rowHeightPx:30};
for(const [j,w] of [[0,180],[1,850],[2,150],[3,130],[4,110],[5,100]])dict.getRangeByIndexes(14,j,cols.length+1,1).format.columnWidthPx=w;
dict.tables.add(`A15:F${15+cols.length}`,true,'B2FloorDictionary').style='TableStyleLight9';dict.freezePanes.freezeRows(15);
wb.recalculate();
for(const [name,range] of [['全部实买','L4:W12'],['跨月卖出','G4:M12'],['跌停专项','A2:L13'],['因子说明','A14:C23']]){
 const inspect=await wb.inspect({kind:'region',sheetId:name,range,maxChars:2200,tableMaxRows:5,tableMaxCols:8});await fs.writeFile(`${out}/${name}.inspect.ndjson`,inspect.ndjson);
 const png=await wb.render({sheetName:name,range,scale:1.2,format:'png'});await fs.writeFile(`${out}/${name}.png`,new Uint8Array(await png.arrayBuffer()));
}
// Preview the newly added path columns as well as the initial risk flags.
const pathCol=defs.findIndex(c=>c.key==='opened_after_touch');
const png=await wb.render({sheetName:'跌停专项',range:`${letter(pathCol+1)}4:${letter(pathCol+9)}13`,scale:1.2,format:'png'});
await fs.writeFile(`${out}/涨停路径.png`,new Uint8Array(await png.arrayBuffer()));
const scan=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:100},maxChars:1800});await fs.writeFile(`${out}/errors.ndjson`,scan.ndjson);
const target=`${out}/B2_跌停专项与全量因子.xlsx`;
await (await SpreadsheetFile.exportXlsx(wb)).save(target);
console.log(JSON.stringify({target,fullRows:ordered.length,specialRows:special.length,newColumns:extra.length,errors:scan.ndjson}));
