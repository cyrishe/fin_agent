const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../src/web/templates/strategy_executions.html'),'utf8');
const rowKey=p=>`${p.signal_date}:${p.strategy_id}:${p.stock_code}:${p.selection_rank}`;
const merge=html.slice(html.indexOf('function mergeRecords('),html.indexOf('async function loadPage('));
const refresh=html.slice(html.indexOf('async function refresh('),html.indexOf("$('strategy-filter').addEventListener"));
const pick=(day,code)=>({signal_date:day,stock_code:code,strategy_id:'收盘开盘 / v1.1',selection_rank:1});
const records=(dates,picks)=>({owners:[],trade_dates:dates,picks,strategies:[]});
test('older pages append and overlapping records do not duplicate',()=>{const c={rowKey};vm.createContext(c);vm.runInContext(merge,c);const a=pick('2026-10-15','603629'),b=pick('2026-09-10','002636');const merged=c.mergeRecords(records(['2026-10-15'],[a]),records(['2026-09-10','2026-10-15'],[b,a]));assert.equal(merged.picks.length,2);assert.equal(merged.trade_dates.length,2);assert.equal(merged.trade_dates[0],'2026-10-15');});
test('10 second poll fetches only live data and replaces only today rows',async()=>{const old=pick('2026-10-15','603629'),today=pick('2026-10-16','002636'),calls=[],patched=[];
 const c={rowKey,document:{hidden:false},records:records(['2026-10-16','2026-10-15'],[today,old]),request:null,pageEpoch:1,needLiveDaily:false,market:{stocks:[]},minuteOffsets:new Map(),historicalMarkets:new Map(),rowNodes:new Map(),pickKey:p=>rowKey(p),getData:async(p)=>{calls.push(p);return {records:records(['2026-10-16'],[today]),market:{trade_date:'2026-10-16',as_of:'2026-10-16T10:00:00',stocks:[]}}},replaceRow:p=>patched.push(p.signal_date),options:()=>{},render:()=>assert.fail('historical list should not be redrawn'),$:()=>({})};
 vm.createContext(c);vm.runInContext(merge+refresh,c);await c.refresh();assert.deepEqual(calls,['/api/strategies/live']);assert.deepEqual(patched,['2026-10-16']);assert.equal(c.records.picks.find(p=>p.signal_date==='2026-10-15'),old);
});
test('actual selections use actionable including compatible missing flags across all models',()=>{
 const source=html.slice(html.indexOf('function isActionable('),html.indexOf('function render('));const c={};vm.createContext(c);vm.runInContext(source,c);
 const rows=[{strategy_id:'B1',actionable:1,selection_rank:1},{strategy_id:'B1',actionable:0,selection_rank:2},{strategy_id:'B1',actionable:'0',selection_rank:1},{strategy_id:'B2',actionable:true,selection_rank:2},{strategy_id:'old'}];
 assert.equal(c.visiblePicks(rows,'',false).length,5);assert.equal(c.visiblePicks(rows,'',true).length,3);assert.equal(c.visiblePicks(rows,'B1',true).length,1);assert.equal(c.visiblePicks(rows,'B2',true)[0].selection_rank,2);
});
