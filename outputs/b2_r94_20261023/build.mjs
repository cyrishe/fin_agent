import fs from 'node:fs/promises';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const base='/Volumes/ext/fin_agent/docs/stock_automl_runs/20261023_b2_2018_2021_r90/audit_r94';
const out='/Volumes/ext/fin_agent/outputs/b2_r94_20261023';
const d=JSON.parse(await fs.readFile(base+'/workbook_data.json','utf8'));
const wb=Workbook.create();
const pct='0.00%;(0.00%);"—"';
const num='#,##0;(#,##0);"—"';
function col(n){let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
function val(v,type){if(v==null)return null;if(type==='date'||type==='month')return new Date(v+(type==='month'?'-01T00:00:00Z':'T00:00:00Z'));if(type==='code')return Number(v);return v;}
async function sheet(name,title,note,fields,rows){
 const s=wb.worksheets.add(name),end=col(fields.length-1),n=rows.length;
 s.showGridLines=false;s.tabColor='#174F67';
 s.getRange('A1:J1').merge();s.getRange('A1').values=[[title]];
 s.getRange('A1:J1').format={fill:'#174F67',font:{bold:true,color:'#FFFFFF',size:19},rowHeight:34};
 s.getRange('A2:J2').merge();s.getRange('A2').values=[[note]];s.getRange('A2:J2').format={font:{color:'#526572',size:10},rowHeight:45,wrapText:true};
 s.getRange(`A4:${end}4`).values=[fields.map(x=>x[1])];s.getRange(`A4:${end}4`).format={fill:'#DDEBF0',font:{bold:true,color:'#174F67'},rowHeight:38,wrapText:true};
 const values=rows.map(r=>fields.map(([key,label,type])=>val(r[key],type)));
 if(n)s.getRange(`A5:${end}${n+4}`).values=values;
 s.getRange(`A4:${end}${n+4}`).format.font.name='Arial';s.getRange(`A5:${end}${n+4}`).format.rowHeight=23;
 fields.forEach(([key,label,type],i)=>{
  const a=col(i);s.getRange(`${a}4:${a}${n+4}`).format.columnWidth=(type==='text'?35:type==='date'?13:14);
  const range=s.getRange(`${a}5:${a}${n+4}`);
  range.setNumberFormat(type==='pct'?pct:type==='money'?num:type==='int'?num:type==='price'?'0.00':type==='month'?'yyyy-mm':type==='date'?'yyyy-mm-dd':type==='code'?'000000':'General');
  if(type==='text'){range.format.wrapText=true;if(key==='worst3'){range.format.columnWidth=80;range.format.rowHeight=64;}}
 });
 for(let i=0;i<n;i++){if(i%2===1)s.getRange(`A${i+5}:${end}${i+5}`).format.fill='#F3F7F9';if(rows[i].paused)s.getRange(`A${i+5}:B${i+5}`).format.fill='#FFF1D6';}
 s.freezePanes.freezeRows(4);s.freezePanes.freezeColumns(2);
 const csvCell=v=>{const t=v==null?'':String(v);return /[",\n]/.test(t)?'"'+t.replaceAll('"','""')+'"':t;};
 const csv='\uFEFF'+[fields.map(x=>x[1]),...rows.map(r=>fields.map(([k])=>r[k]))].map(row=>row.map(csvCell).join(',')).join('\n')+'\n';
 await fs.writeFile(`${base}/${name}.csv`,csv);
 return s;
}
const months=d.monthly.map(r=>({...r,state:r.paused?'停摆':'放行'}));
await sheet('月度明细','2018—2021｜含停摆月影子结果','D0 模型月；每月独立 100 万元、按原仓位与双边各 0.1% 费用回放，结清跨月 D2。停摆仅令本月新买为零；影子=假设照常交易。收益为小数底值。',[
 ['month','月份','month'],['state','状态'],['allowed_trades','主策略笔数','int'],['actual_7pct_return','主策略 7% 卖法','pct'],['actual_close_return','主策略纯收盘','pct'],['shadow_trades','影子笔数','int'],['shadow_7pct_return','影子 7% 卖法','pct'],['7pct_win','影子胜率','pct'],['shadow_close_return','影子纯收盘','pct'],['close_win','收盘胜率','pct'],['csi300','沪深300','pct'],['csi1000','中证1000','pct'],['previous_r5','停摆依据 5 日','pct'],['previous_r10','停摆依据 10 日','pct'],['d2_close_floor','D2 收盘跌停','int'],['d1_extrema_gaps','D1 极值缺口','int'],['early_sells','上午早卖','int'],['d2_missing','D2 分钟缺档','int']
],months);
await sheet('大亏月份','大亏月份｜策略、市场与损失来源','影子 7% 卖法月亏 ≥15% 的全部 7 月（含实际放行 5 月）；按亏损排序。损益按各月独立 100 万元账户，非逐笔涨跌幅之和。',[
 ['month','月份','month'],['state','状态'],['shadow_7pct_return','策略收益','pct'],['shadow_trades','笔数','int'],['7pct_win','胜率','pct'],['csi300','沪深300','pct'],['csi1000','中证1000','pct'],['d2_close_floor','D2 收盘跌停','int'],['loss_pnl','亏损笔损益(元)','money'],['gain_pnl','盈利笔损益(元)','money'],['worst3_pnl','最差3笔损益(元)','money'],['d1_extrema_gaps','D1 极值缺口','int'],['worst3','最差3笔｜代码、D0、费后收益、损益','text']
],d.losses.map(r=>({...r,state:r.paused?'停摆':'放行'})));
const tradefields=[['month','月份','month'],['state','状态'],['symbol','股票','code'],['d0_date','D0','date'],['d1_date','D1','date'],['d2_date','D2','date'],['entry','买价','price'],['sell','7% 卖法卖价','price'],['return_','7% 费后收益','pct'],['close_net_return','收盘费后收益','pct'],['pnl','月内账户损益(元)','money'],['cost','投入(含费,元)','money'],['csi300_d0_d2','同期沪深300','pct'],['csi1000_d0_d2','同期中证1000','pct'],['early','上午早卖'],['floor','D2 收盘跌停'],['oneprice','D2 全天一价跌停'],['extrema_proxy_gap_pct','D1 极值缺口','pct'],['d2_quality','D2 质量','text'],['rule_id','规则']];
const ts=d.trades.map(r=>({...r,state:r.paused?'停摆影子':'放行',early:r.early_sell?'是':'否',floor:r.d2_close_floor?'是':'否',oneprice:r.d2_oneprice_floor?'是':'否'}));
await sheet('全部逐笔','642 笔｜实际与停摆影子','每笔损益来自对应 D0 月独立账户。指数为 D0 收盘至 D2 收盘；并非精确买入时点基准。跌停收盘仍假设可卖，见报告执行限制。',tradefields,ts);
await sheet('大亏逐笔','大亏月份｜全部 100 笔交易','包含停摆月份的假设交易；请按“状态”区分实际放行与影子。全部 7 个月，不只列亏损交易。',tradefields,ts.filter(r=>d.losses.some(m=>m.month===r.month)));
const trfields=[['month','模型月','month'],['rf_fit','日线训练样本','int'],['rf_historical_growth','旧创业板样本','int'],['rf_historical_growth_fraction','旧创业板占比','pct'],['train_n','四K训练样本','int'],['train_extrema_gap','训练极值缺口','int'],['train_extrema_fraction','训练涉及占比','pct'],['valid_n','四K验证样本','int'],['valid_extrema_gap','验证极值缺口','int'],['valid_extrema_fraction','验证涉及占比','pct'],['rule','宽松规则'],['strict_rule','严格规则']];
const trainSheet=await sheet('训练影响','训练数据｜逐月分母与问题暴露','旧创业板样本已按历史 10% 修正后训练；极值缺口是股票日暴露，不能断言每根开盘 K 都错误。滚动窗口会重复使用同一样本。',trfields,d.training);
for(let i=5;i<53;i++)for(const [a,f] of [['D',`=C${i}/B${i}`],['G',`=F${i}/E${i}`],['J',`=I${i}/H${i}`]])trainSheet.getRange(a+i).formulas=[[f]];
await sheet('大盘月份','市场背景｜五个指数逐月指标','来源：服务器 kcrp_index_price 只读快照；逐日 close/preclose 连乘，包含每月首日。回撤包含月初 1。原始日线与哈希见同目录 index_daily.csv / manifest.json。',[
 ['month','月份','month'],['index_code','指数代码'],['index_name','指数名称','text'],['days','交易日','int'],['return_','月收益','pct'],['drawdown','月内最大回撤','pct'],['worst_day','最差单日','pct'],['worst_day_date','最差日','date'],['up_days_fraction','上涨日占比','pct'],['daily_volatility','日收益标准差','pct']
],d.market.filter(r=>r.month<='2021-12'));
wb.recalculate();
await fs.writeFile(out+'/inspect.ndjson',(await wb.inspect({kind:'region',sheetId:'月度明细',range:'A4:L10',maxChars:5000,tableMaxRows:7,tableMaxCols:12})).ndjson);
const errs=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:30},maxChars:2000});await fs.writeFile(out+'/errors.ndjson',errs.ndjson);
for(const name of ['月度明细','大亏月份','全部逐笔','大亏逐笔','训练影响','大盘月份']){
 const image=await wb.render({sheetName:name,range:'A1:J12',scale:1.5,format:'png'});await fs.writeFile(`${out}/${name}.png`,new Uint8Array(await image.arrayBuffer()));
}
const x=await SpreadsheetFile.exportXlsx(wb);await x.save(out+'/B2_R94_训练影响与停摆月复盘.xlsx');
console.log('DONE',out,errs.ndjson);
