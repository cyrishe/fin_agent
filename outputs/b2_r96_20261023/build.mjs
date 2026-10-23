import fs from 'node:fs/promises';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const base='/Volumes/ext/fin_agent/docs/stock_automl_runs/20261023_b2_minute_review_r96';
const out='/Volumes/ext/fin_agent/outputs/b2_r96_20261023';
const d=JSON.parse(await fs.readFile(base+'/review_data.json','utf8'));
const wb=Workbook.create(), names=[];
// Numeric Excel dates avoid the exporter rounding exact minute Dates down by 1 ms.
// Guard is < 0.1 ms; source timestamps and verification retain exact whole seconds.
const dt=v=>Date.parse(v.replace(' ','T')+(v.length===10?'T00:00:00Z':'Z'))/86400000+25569+(v.length===10?0:1e-9);
function sheet(name,title,note,headers,rows,types){
 const s=wb.worksheets.add(name);names.push(name);const end=String.fromCharCode(64+headers.length),last=rows.length+5;
 s.showGridLines=false;s.tabColor='#205B70';
 s.getRange(`A1:${end}1`).merge();s.getRange('A1').values=[[title]];
 s.getRange(`A1:${end}1`).format={fill:'#205B70',font:{color:'#FFFFFF',bold:true,size:18},rowHeight:36};
 s.getRange(`A2:${end}3`).merge();s.getRange('A2').values=[[note]];
 s.getRange(`A2:${end}3`).format={font:{color:'#526572',size:10},wrapText:true,rowHeight:25,verticalAlignment:'center'};
 s.getRange(`A5:${end}5`).values=[headers];s.getRange(`A5:${end}5`).format={fill:'#DCEBF0',font:{bold:true,color:'#205B70'},rowHeight:32,wrapText:true};
 s.getRange(`A6:${end}${last}`).values=rows;
 s.getRange(`A6:${end}${last}`).format={font:{name:'Arial',size:11},rowHeight:23};
 types.forEach((t,i)=>{const a=String.fromCharCode(65+i),r=s.getRange(`${a}6:${a}${last}`);r.setNumberFormat(t==='datetime'?'yyyy-mm-dd hh:mm:ss':t==='date'?'yyyy-mm-dd':t==='price'?'0.00':t==='int'?'#,##0':t==='money'?'#,##0.00':'General');s.getRange(`${a}5:${a}${last}`).format.columnWidth=t==='datetime'?23:t==='date'?14:t==='money'?21:t==='text'?19:13;});
 for(let i=0;i<rows.length;i++)if(i%2)s.getRange(`A${i+6}:${end}${i+6}`).format.fill='#F2F7F9';
 s.freezePanes.freezeRows(5);s.freezePanes.freezeColumns(2);
 return s;
}
const overview=sheet('核对概览','分钟与日 K｜原始数据核对','R96 · 数据从服务器提取。下列原始最低价包含 09:30；300543 的零量前值需排除，修复后最低价为 47.48。所有价格均为未复权口径。',
 ['股票','日期','日K开盘','日K最低','原始最低','最低差额','原始最高','原始行数','分钟总量(股)'],
 d.cases.map(c=>[c.symbol+'.SZ',dt(c.date),c.daily.open,c.daily.low,null,null,null,null,null]),
 ['text','date','price','price','price','price','price','int','int']);
for(const c of d.cases){
 const s=sheet(c.symbol+'原始241',`${c.symbol}｜${c.date}｜完整原始 241 行`,
 '按 trade_time 升序展示；“接口顺序”保留原返回位置（原接口倒序）。字段按名称读取；价格未修补。vol 为股，amount 为元。来源：'+c.source,
 ['接口顺序','股票','trade_time','open','high','low','close','vol(股)','amount(元)'],
 c.raw.map(r=>[r.api_order,r.ts_code,dt(r.trade_time),r.open,r.high,r.low,r.close,r.vol,r.amount]),
 ['int','text','datetime','price','price','price','price','int','money']);
 s.getRange('A6:I6').format.fill='#FFF0CD';
}
sheet('300313标准240','300313｜标准 240 根｜2019-05-24',
 '当前转换口径：09:30 有量记录并入 09:31；零量 09:30 不参与价格合并。此表用于展示转换，原始表未改动。第一根包含开盘汇总，不是纯连续竞价一分钟。',
 ['时间序号','股票','time','open','high','low','close','volume(股)','turnover(元)'],
 d.cases[0].standard.map(r=>[r.label_index,r.symbol,dt(r.time),r.open,r.high,r.low,r.close,r.volume,r.turnover]),
 ['int','text','datetime','price','price','price','price','int','money']);
const daily=sheet('日K对照','日 K 对照｜目标日与前后各 3 个交易日',
 '来源：服务器 R90 raw_price_months 未复权日线研究快照。第 0 日为目标日；用于核对日期是否整体错位。相邻日指该股日线记录，非自然日。',
 ['股票','日期','相对日','open','high','low','close','volume(股)','amount(元)'],
 d.neighbors.map(r=>[r.stk_code,dt(r.trade_date),r.day_offset,r.open,r.high,r.low,r.close,r.volume,r.amount]),
 ['text','date','int','price','price','price','price','int','money']);
d.neighbors.forEach((r,i)=>{if(r.day_offset===0)daily.getRange(`A${i+6}:I${i+6}`).format.fill='#FFF0CD';});
for(let i=0;i<d.cases.length;i++){
 const c=d.cases[i],r=i+6,n=c.symbol+'原始241';
 overview.getRange(`E${r}:I${r}`).formulas=[[`=MIN('${n}'!F6:F246)`,`=E${r}-D${r}`,`=MAX('${n}'!E6:E246)`,`=COUNT('${n}'!A6:A246)`,`=SUM('${n}'!H6:H246)`]];
}
overview.getRange('A12:I14').merge();overview.getRange('A12').values=[['定位依据：ts_code + trade_time（另固定频率 1min）；没有独立分钟 ID。先校验股票、日期、时间戳唯一性及完整时间集合，再按时间排序。原始顺序不是分钟身份。']];overview.getRange('A12:I14').format={wrapText:true,rowHeight:25,font:{color:'#526572',size:11}};
overview.getRange('A16:I18').merge();overview.getRange('A16').values=[['300313、002457：同日开盘、最高、收盘及总成交量一致，仅最低价明显不符；前后 3 个交易日没有完整 OHLC 匹配。更符合盘中极值缺失，不支持简单整体错日，但底层报价来源仍待核实。']];overview.getRange('A16:I18').format={wrapText:true,rowHeight:25,font:{color:'#526572',size:11}};
wb.recalculate();
await fs.writeFile(out+'/inspect.ndjson',(await wb.inspect({kind:'region',sheetId:'核对概览',range:'A5:I9',maxChars:4000,tableMaxRows:5,tableMaxCols:9})).ndjson);
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:20},maxChars:2000});
await fs.writeFile(out+'/errors.ndjson',errors.ndjson);
for(const name of names){const p=await wb.render({sheetName:name,range:name==='核对概览'?'A1:I18':'A1:I13',scale:1.4,format:'png'});await fs.writeFile(`${out}/${name}.png`,new Uint8Array(await p.arrayBuffer()));}
await (await SpreadsheetFile.exportXlsx(wb)).save(out+'/B2_R96_分钟原始数据核对.xlsx');
console.log('DONE',names,errors.ndjson);
