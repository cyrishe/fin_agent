import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.resolve(process.argv[2]);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const chartData=JSON.parse(await fs.readFile(path.join(dir,'charts.json'),'utf8'));
const audit=JSON.parse(await fs.readFile(path.join(dir,'audit.json'),'utf8'));
const wb=Workbook.create();
const notes=[
 '范围：R18 完整分段模型，D0每日Top1；筛选940前可买=1且买价触板=1，共116笔。日期沿用原表D0，D1日期是分钟线发生日。',
 '核对：116笔全天27840根分钟线与日线开高低收完全一致，成交量在0.1%容差内；涨停价、板前涨幅、窗口最高买价重算通过。',
 '样本问题：2笔为国华退的退市整理期，原过滤只排除ST而漏掉L状态。明细标出历史状态，对照另列剔除结果；原模型未重训。',
 '分钟标记采用结束时间：09:31代表09:30—09:31。共列1160根早盘分钟K。首触板分、首板下分也是分钟结束标记。',
 '板前涨幅：本轮首板前最多5个连续非封板交易日的累计涨幅；不包含原连板段，也不是D1的盘中跌幅。',
 '板下分钟：有成交且最低价低于涨停价。整分板下：有成交且最高价也低于涨停价；计数不等于连续可交易时长。',
 '触板后整分板下：首触板分钟之后出现的整分板下根数。同一分钟同时触板、板下时，分钟K不能确定先后或持续秒数。',
 '940在板：09:40分钟收价等于涨停价。它不证明当时的委托封单。买价取整个早盘窗口最高价，本组全部等于涨停价。',
 '收益：D2/D3/D4收盘价÷上述买价−1，未扣费。末尾1笔后续数据不足，留空；对照组各期限的有效笔数分别列出。',
 '分组是已有测试区间的事后观察，未用于重新训练或选模。若等到940才确认走势，需按确认之后的可成交价格另做回放。',
 '来源：R18A保存的分钟原始行情与日线快照、R18板前原始日线及模型预测；原始记录、哈希及核对证据另存研究目录。'
];
const pct=new Set(['板前涨幅','开盘涨幅','940涨幅','窗口最低涨幅','D2收益','D3收益','D4收益','收盘涨幅','三日再板率','D1封板率','D2均值','D3均值','D4均值','D2胜率','D3胜率','D4胜率']);
const prices=new Set(['涨停价','买入价','开盘价','最高价','最低价','收盘价']);
function col(i){let x='';for(let n=i+1;n;n=Math.floor((n-1)/26))x=String.fromCharCode(65+(n-1)%26)+x;return x;}
const checks=[];let ti=0;
for(const [name,rows] of Object.entries(data)){
 const sh=wb.worksheets.add(name),cols=Object.keys(rows[0]),n=rows.length+1;
 sh.showGridLines=false;
 const values=rows.map(r=>cols.map(c=>['日期','D1日期'].includes(c)?(Date.parse(r[c]+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000:r[c]??null));
 sh.getRangeByIndexes(0,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(1,0,rows.length,cols.length).values=values;
 sh.tables.add(`A1:${col(cols.length-1)}${n}`,true,`R18BTable${++ti}`).showFilterButton=true;
 sh.getRange(`A1:${col(cols.length-1)}${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:24,verticalAlignment:'center'};
 sh.getRange(`A1:${col(cols.length-1)}1`).format={fill:'#234D46',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',rowHeight:40};
 cols.forEach((c,i)=>{
  sh.getRangeByIndexes(0,i,n,1).format.columnWidth=c==='分组'||c==='走势'?36:['日期','D1日期','时段'].includes(c)?17:c==='触板后整分板下'||c==='整分板下量万股'?19:14;
  const r=sh.getRangeByIndexes(1,i,rows.length,1);
  if(['日期','D1日期'].includes(c))r.format.numberFormat='yyyy-mm-dd';
  else if(pct.has(c))r.format.numberFormat='0.00%;[Red](0.00%);0.00%';
  else if(prices.has(c)||c.includes('万股'))r.format.numberFormat='#,##0.00';
  else if(rows.some(x=>typeof x[c]==='number'))r.format.numberFormat='#,##0';
 });
 sh.freezePanes.freezeRows(1);
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A1:F6`,include:'values,formulas',tableMaxRows:6,tableMaxCols:6,maxChars:1400})).ndjson);
}
const summary=wb.worksheets.getItem('核对与对照');
const firstNote=data['核对与对照'].length+4;
notes.forEach((t,i)=>summary.getRange(`A${firstNote+i}`).values=[[t]]);
summary.getRange(`A${firstNote}:N${firstNote+notes.length-1}`).format={font:{name:'Arial',size:10,color:'#465263'},rowHeight:26};
wb.recalculate();
for(const [name,rows] of Object.entries(data)){
 const n=Object.keys(rows[0]).length;
 for(let i=0;i<n;i+=8){
  const png=await wb.render({sheetName:name,range:`${col(i)}1:${col(Math.min(i+7,n-1))}${name==='早盘分钟'?12:7}`,scale:1.2,format:'png'});
  await fs.writeFile(path.join(dir,`preview_${name}_${i}.png`),new Uint8Array(await png.arrayBuffer()));
 }
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const xlsx='Top1触板_分钟核对与收益_R18B_20261015.xlsx';
await(await SpreadsheetFile.exportXlsx(wb)).save(path.join(dir,xlsx));

const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Top1触板 · 早盘分钟K · R18B</title>
<style>
:root{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;color:#202936;background:#f4f6f7}body{margin:0}main{max-width:1320px;margin:auto;padding:28px 24px}h1{font-size:25px;margin:0 0 12px}p{line-height:1.7;color:#52616d;font-size:14px;margin:8px 0}.controls{display:flex;flex-wrap:wrap;gap:14px;align-items:center;padding:15px 0;position:sticky;top:0;background:#f4f6f7f5;z-index:2;border-bottom:1px solid #d3dce0}select,input,button{font:inherit;font-size:14px;padding:8px;border:1px solid #b6c7cc;border-radius:5px;background:white}label{font-size:14px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-top:18px}.card{background:#fff;border:1px solid #d9e2e5;border-radius:9px;padding:18px;overflow:hidden}.card h2{font-size:17px;margin:0 0 5px}.meta{font-size:13px;color:#52616d;line-height:1.8}.returns{display:flex;gap:18px;font-size:14px;margin:10px 0}.up{color:#b83139}.down{color:#168366}.tag{font-size:12px;background:#edf3f1;padding:3px 6px;border-radius:4px;color:#34564d}.chart{width:100%;height:auto}details{margin-top:8px;font-size:13px}summary{cursor:pointer;color:#34564d}table{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}td,th{padding:6px 3px;text-align:right;border-bottom:1px solid #e6ecef}th:first-child,td:first-child{text-align:left}th{color:#465b64;font-weight:500}.count{font-weight:600;color:#234d46}.legend{font-size:13px}.empty{padding:60px;text-align:center}.notes{background:white;border-left:3px solid #718e9a;padding:12px 16px;margin:16px 0}.notes p{margin:4px 0}@media(max-width:850px){.grid{grid-template-columns:1fr}main{padding:18px 12px}}@media print{.controls{position:static}.card{break-inside:avoid}.grid{display:block}.card{margin-bottom:18px}}
</style></head><body><main><h1>Top1触板：09:30—09:40 分钟K</h1>
<p>116笔全部列出。日期为D0评分日，分钟K发生在D1。红涨绿跌，虚线为涨停价；浅绿色底纹表示该分钟所有成交价均在涨停价下方。</p>
<div class="notes"><p>核对通过：全天分钟汇总与日线开高低收完全一致，成交量在0.1%容差内，涨停价与板前涨幅已重算。收益沿用早盘窗口最高价，本组全部为涨停价。</p><p>其中2笔为退市整理期，旧ST过滤遗漏，已在对应图和Excel标出。“板前涨幅”是本轮首板之前最多5天的累计涨幅。</p><p>09:31对应09:30—09:31。分钟K不能还原高低点顺序、打开的秒数或排队成交。等到09:40才确认的条件，需要用确认之后的价格回放。</p></div>
<div class="controls"><label>开盘 <select id="opening"><option value="all">全部116笔</option><option value="1">开盘激活</option><option value="0">板上开盘</option></select></label><label>09:40 <select id="at940"><option value="all">全部</option><option value="1">收价在涨停</option><option value="0">收价在板下</option></select></label><label>整分板下 <select id="whole"><option value="all">全部</option><option value="yes">至少1根</option><option value="no">没有</option></select></label><input id="date" aria-label="按D0或D1日期查找" placeholder="日期，如2025-09"><button id="reset">重置</button><span id="count" class="count"></span></div>
<div id="grid" class="grid"></div></main>
<script type="application/json" id="payload">${JSON.stringify(chartData).replaceAll('<','\\u003c')}</script>
<script>
const rows=JSON.parse(document.querySelector('#payload').textContent);
const pct=v=>v==null?'未齐':(v*100).toFixed(2)+'%';
const num=v=>Number(v).toFixed(2);
const color=v=>v==null?'':v>=0?'up':'down';
function chart(r){
 const bs=r.bars,cap=r.case['涨停价'],pc=r.preclose,W=580,H=280,L=51,R=45,T=27,B=182,volB=243;
 const lows=bs.map(x=>x['最低价']),highs=bs.map(x=>x['最高价']);
 let mn=Math.min(...lows),mx=Math.max(cap,...highs),pad=Math.max((mx-mn)*.13,cap*.002);mn-=pad;mx+=pad;
 const y=v=>T+(mx-v)/(mx-mn)*(B-T),dx=(W-L-R)/10,x=i=>L+dx*(i+.5),bw=22,vm=Math.max(...bs.map(x=>x['成交万股']));
 let a=['<svg class="chart" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+r.case['D1日期']+'早盘十分钟蜡烛图">'];
 for(let i=0;i<5;i++){let v=mn+(mx-mn)*i/4,yy=y(v);a.push('<line x1="'+L+'" x2="'+(W-R)+'" y1="'+yy+'" y2="'+yy+'" stroke="#e7edef"/><text x="'+(L-7)+'" y="'+(yy+4)+'" text-anchor="end" fill="#6a7a84" font-size="11">'+pct(v/pc-1)+'</text>');}
 bs.forEach((b,i)=>{if(b['整分板下'])a.push('<rect x="'+(x(i)-dx/2+1)+'" y="'+T+'" width="'+(dx-2)+'" height="'+(B-T)+'" fill="#e9f5ef"/>');});
 a.push('<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y(cap)+'" y2="'+y(cap)+'" stroke="#b17c28" stroke-dasharray="5 4"/><text x="'+(W-R+3)+'" y="'+(y(cap)+4)+'" fill="#90651f" font-size="11">涨停</text>');
 bs.forEach((b,i)=>{const o=b['开盘价'],h=b['最高价'],l=b['最低价'],c=b['收盘价'],co=c>=o?'#bd4147':'#21856e',yy=Math.min(y(o),y(c)),hh=Math.max(1.5,Math.abs(y(o)-y(c))),vv=b['成交万股']/vm*40;
 a.push('<g><title>'+b['时段']+' 开'+num(o)+' 高'+num(h)+' 低'+num(l)+' 收'+num(c)+' 量'+num(b['成交万股'])+'万股</title><line x1="'+x(i)+'" x2="'+x(i)+'" y1="'+y(h)+'" y2="'+y(l)+'" stroke="'+co+'" stroke-width="1.4"/><rect x="'+(x(i)-bw/2)+'" y="'+yy+'" width="'+bw+'" height="'+hh+'" fill="'+co+'"/><rect x="'+(x(i)-bw/2)+'" y="'+(volB-vv)+'" width="'+bw+'" height="'+vv+'" fill="'+co+'" opacity=".62"/></g><text x="'+x(i)+'" y="263" text-anchor="middle" fill="#6a7a84" font-size="11">'+(31+i)+'</text>');});
 a.push('<text x="'+L+'" y="16" fill="#52616d" font-size="11">涨幅相对D0收盘</text><text x="'+(W-R)+'" y="16" text-anchor="end" fill="#90651f" font-size="11">涨停价 '+num(cap)+'</text><text x="8" y="225" fill="#6a7a84" font-size="10">量</text><text x="'+(W-R)+'" y="278" text-anchor="end" fill="#6a7a84" font-size="10">09点 · 分钟结束标记</text></svg>');return a.join('');
}
function card(r){const c=r.case;return '<article class="card"><h2>D0 '+c['日期']+' <span class="tag">D1 '+c['D1日期']+'</span></h2>'+(c['历史状态']==='普通'?'':'<p class="up">历史状态：'+c['历史状态']+'，请单独分析</p>')+'<div class="meta">'+c['走势']+' · 原连板 '+c['原连板']+'天 · 板前 '+pct(c['板前涨幅'])+'<br>首触板 '+c['首触板分']+' · 首板下 '+c['首板下分']+' · 整分板下 '+c['整分板下']+'根（触板后 '+c['触板后整分板下']+'根）</div><div class="returns">'+[2,3,4].map(k=>'<span>D'+k+' <strong class="'+color(c['D'+k+'收益'])+'">'+pct(c['D'+k+'收益'])+'</strong></span>').join('')+'</div>'+chart(r)+'<details><summary>查看10根分钟数据</summary><table><thead><tr><th>时段</th><th>开</th><th>高</th><th>低</th><th>收</th><th>万股</th></tr></thead><tbody>'+r.bars.map(b=>'<tr><td>'+b['时段']+'</td>'+['开盘价','最高价','最低价','收盘价','成交万股'].map(k=>'<td>'+num(b[k])+'</td>').join('')+'</tr>').join('')+'</tbody></table></details></article>';}
function render(){const op=document.querySelector('#opening').value,at=document.querySelector('#at940').value,wh=document.querySelector('#whole').value,date=document.querySelector('#date').value.trim();const f=rows.filter(r=>{const c=r.case;return(op==='all'||String(c['开盘激活'])===op)&&(at==='all'||String(c['940在板'])===at)&&(wh==='all'||(wh==='yes'?c['整分板下']>0:c['整分板下']===0))&&(!date||c['日期'].includes(date)||c['D1日期'].includes(date));});document.querySelector('#count').textContent=f.length+' / 116笔';document.querySelector('#grid').innerHTML=f.length?f.map(card).join(''):'<div class="empty">没有符合条件的记录</div>';}
for(const id of ['opening','at940','whole','date'])document.getElementById(id).addEventListener('input',render);
document.querySelector('#reset').addEventListener('click',()=>{['opening','at940','whole'].forEach(id=>document.getElementById(id).value='all');document.querySelector('#date').value='';render();});render();
</script></body></html>`;
await fs.writeFile(path.join(dir,'Top1触板_全部分钟K_R18B_20261015.html'),html);
console.log(JSON.stringify({xlsx:path.join(dir,xlsx),charts:chartData.length,verified:audit.status}));
