const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../src/web/templates/strategy_executions.html'),'utf8');
const helper=html.slice(html.indexOf('function refreshHistoricalCache('),html.indexOf('async function refresh(force='));
const stock=code=>({code,bars:[{close:10}],daily_bars:[{close:10}]});
const response=codes=>({requested_minutes:240,stocks:codes.map(stock)});
function cache(){const context={historicalMarkets:new Map(),minuteMarkets:new Map()};vm.createContext(context);vm.runInContext(helper,context);return context;}
test('backfill adds stocks to an already viewed date; next poll invalidates only the stale date',()=>{
 const c=cache(),old=response(['002343']),complete=response(['002812']);
 c.historicalMarkets.set('2026-10-13',old);c.historicalMarkets.set('2026-10-14',complete);
 c.refreshHistoricalCache([{signal_date:'2026-10-13',stock_code:'002343'},{signal_date:'2026-10-13',stock_code:'603629'},{signal_date:'2026-10-13',stock_code:'688498'},{signal_date:'2026-10-14',stock_code:'002812'}]);
 assert.equal(c.historicalMarkets.has('2026-10-13'),false);assert.equal(c.historicalMarkets.get('2026-10-14'),complete);
});
test('complete cached history is reused across unchanged execution polls and model filters',()=>{
 const c=cache(),data=response(['603629','688498']);c.historicalMarkets.set('2026-10-13',data);
 for(let i=0;i<2;i++)c.refreshHistoricalCache([{signal_date:'2026-10-13',stock_code:'603629'}]);
 assert.equal(c.historicalMarkets.get('2026-10-13'),data);
});
test('empty data or failed requests retry on the next poll, including browsed minute dates',()=>{
 const c=cache();c.historicalMarkets.set('2026-10-13',{stocks:[]});
 c.minuteMarkets.set('2026-10-13:1:603629',{error:'temporary unavailable'});
 c.minuteMarkets.set('2026-10-13:-1:688498',{requested_minutes:240,stocks:[{code:'688498',bars:[],daily_bars:[{}]}]});
 c.refreshHistoricalCache([{signal_date:'2026-10-13',stock_code:'603629'}]);
 assert.equal(c.historicalMarkets.size,0);assert.equal(c.minuteMarkets.size,0);
});
