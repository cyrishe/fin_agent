import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const dir=path.resolve(process.argv[2]);
const require=createRequire(path.join(dir,'package.json'));
const {Workbook,SpreadsheetFile}=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const data=JSON.parse(await fs.readFile(path.join(dir,'workbook_data.json'),'utf8'));
const notes=JSON.parse(await fs.readFile(path.join(dir,'workbook_notes.json'),'utf8'));
const wb=Workbook.create(),checks=[],ranges={};
const col=i=>{let s='';for(let n=i+1;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
const serial=d=>(Date.parse(d+'T00:00:00Z')-Date.UTC(1899,11,30))/86400000;
const percent=new Set(['保留率','准确率','命中率','召回率','F1','平衡准确']);
const header={fill:'#263D53',font:{name:'Arial',size:11,bold:true,color:'#FFFFFF'},horizontalAlignment:'center',verticalAlignment:'center',rowHeight:32};
let tested=0;
for(const name of ['分类对照','逐月对照']){
 const rows=data[name],cols=Object.keys(rows[0]),sh=wb.worksheets.add(name),start=6,last=5+rows.length;
 sh.showGridLines=false;sh.tabColor='#263D53';
 sh.getRange(`A2:${col(cols.length-1)}${last}`).format={font:{name:'Arial',size:11,color:'#202936'},rowHeight:26,verticalAlignment:'center'};
 sh.getRange('A2').values=[[name==='分类对照'?'四K与五K分类对照':'逐月分类与混淆矩阵']];sh.getRange('A2').format.font={name:'Arial',size:15,bold:true,color:'#263D53'};
 sh.getRange('A3').values=[[name==='分类对照'?'2026年1—10月目标模型。标签为D1—D3收盘再板，主表不要求开板或成交。':'目标月按D0模型月份。可筛全部候选或共同可买；四方案使用同一组样本。']];
 sh.getRange('A3').format.font={name:'Arial',size:10,color:'#5A6673'};
 sh.getRangeByIndexes(4,0,1,cols.length).values=[cols];
 sh.getRangeByIndexes(5,0,rows.length,cols.length).values=rows.map(r=>cols.map(c=>c==='目标月'?serial(r[c]):r[c]));
 sh.tables.add(`A5:${col(cols.length-1)}${last}`,true,name==='分类对照'?'R24BClass':'R24BMonths').showFilterButton=true;
 sh.getRange(`A5:${col(cols.length-1)}5`).format=header;
 const at=(c,r)=>col(cols.indexOf(c))+r;
 const forms={
  '评估数':r=>`=SUM(${at('选中再板',r)}:${at('排除未板',r)})`,
  '保留数':r=>`=${at('选中再板',r)}+${at('选中未板',r)}`,
  '保留率':r=>`=IF(${at('评估数',r)}=0,"",${at('保留数',r)}/${at('评估数',r)})`,
  '准确率':r=>`=IF(${at('评估数',r)}=0,"",(${at('选中再板',r)}+${at('排除未板',r)})/${at('评估数',r)})`,
  '命中率':r=>`=IF(${at('保留数',r)}=0,"",${at('选中再板',r)}/${at('保留数',r)})`,
  '召回率':r=>`=IF(${at('选中再板',r)}+${at('漏掉再板',r)}=0,"",${at('选中再板',r)}/(${at('选中再板',r)}+${at('漏掉再板',r)}))`,
  'F1':r=>`=IF(2*${at('选中再板',r)}+${at('选中未板',r)}+${at('漏掉再板',r)}=0,"",2*${at('选中再板',r)}/(2*${at('选中再板',r)}+${at('选中未板',r)}+${at('漏掉再板',r)}))`,
  '平衡准确':r=>`=IF(OR(${at('选中再板',r)}+${at('漏掉再板',r)}=0,${at('排除未板',r)}+${at('选中未板',r)}=0),"",(${at('召回率',r)}+${at('排除未板',r)}/(${at('排除未板',r)}+${at('选中未板',r)}))/2)`
 };
 for(const [c,make] of Object.entries(forms))sh.getRangeByIndexes(5,cols.indexOf(c),rows.length,1).formulas=rows.map((_,i)=>[make(start+i)]);
 cols.forEach((c,i)=>{
  const g=sh.getRangeByIndexes(0,i,last,1),v=sh.getRangeByIndexes(5,i,rows.length,1);
  g.format.columnWidth=c==='方案'?22:c==='样本口径'?16:c==='集合'?15:c==='目标月'?14:c==='范围'?10:13;
  v.format.horizontalAlignment=['集合','范围','方案','样本口径'].includes(c)?'left':'right';
  v.format.numberFormat=percent.has(c)?'0.00%;[Red](0.00%)':c==='目标月'?'yyyy-mm':'#,##0';
 });
 sh.freezePanes.freezeRows(5);sh.freezePanes.freezeColumns(name==='分类对照'?3:5);
 ranges[name]={start,cols,rows,last};
 // Probe a confusion count and restore it; verifies live dependency, not just cached values.
 const cell=sh.getRange(at('选中再板',start)),original=cell.values[0][0];
 cell.values=[[original+1]];
 const predicted=sh.getRange(at('命中率',start)).values[0][0];
 const expected=(original+1)/(rows[0]['保留数']+1);
 if(Math.abs(predicted-expected)>1e-12)throw new Error('Live formula dependency failed');
 cell.values=[[original]];tested++;
}
const sh=wb.worksheets.add('规则与口径');sh.showGridLines=false;
sh.getRange('A2:B45').format={font:{name:'Arial',size:11,color:'#202936'},rowHeight:28,verticalAlignment:'center'};
sh.getRange('A1:A45').format.columnWidth=18;sh.getRange('B1:B45').format.columnWidth=128;
sh.getRange('A2').values=[['分类定义与冻结规则']];sh.getRange('A2').format.font={name:'Arial',size:15,bold:true,color:'#263D53'};
sh.getRange('A5:B5').values=[['项目','说明']];sh.getRange('A5:B5').format=header;
sh.getRange(`A6:B${5+notes.length}`).values=notes.map((n,i)=>[i+1,n]);
sh.getRange(`B6:B${5+notes.length}`).format={wrapText:true,rowHeight:42};
const ruleStart=notes.length+9;sh.getRange(`A${ruleStart}:B${ruleStart}`).values=[['目标月','训练、验证与实际规则']];sh.getRange(`A${ruleStart}:B${ruleStart}`).format=header;
sh.getRangeByIndexes(ruleStart,0,data['规则与口径'].length,2).values=data['规则与口径'].map(r=>[serial(r['目标月']),`训练 ${r['训练段']}；验证 ${r['验证段']}。五K：${r['五K规则']}。四K重选：${r['四K重选']}。`]);
sh.getRange(`A${ruleStart+1}:A${ruleStart+data['规则与口径'].length}`).format.numberFormat='yyyy-mm';
sh.getRange(`B${ruleStart+1}:B${ruleStart+data['规则与口径'].length}`).format={wrapText:true,rowHeight:60};
wb.recalculate();
let valuesChecked=0;
for(const [name,{start,cols,rows,last}] of Object.entries(ranges)){
 const s=wb.worksheets.getItem(name),v=s.getRangeByIndexes(start-1,0,rows.length,cols.length).values;
 for(let i=0;i<rows.length;i++)for(let j=0;j<cols.length;j++){
  const expect=cols[j]==='目标月'?serial(rows[i][cols[j]]):rows[i][cols[j]],actual=v[i][j];
  if(expect==null ? actual!=null&&actual!=='' : typeof expect==='number'?Math.abs(actual-expect)>1e-11:actual!==expect)throw new Error(`${name} ${i} ${cols[j]} ${actual} != ${expect}`);
  valuesChecked++;
 }
 checks.push((await wb.inspect({kind:'table',range:`'${name}'!A5:${col(cols.length-1)}9`,include:'values,formulas',tableMaxRows:5,tableMaxCols:18,maxChars:2500})).ndjson);
 for(const [label,range] of [['left',`A2:${col(Math.min(11,cols.length-1))}13`],['right',`${col(cols.length-4)}5:${col(cols.length-1)}13`]]){
  const png=await wb.render({sheetName:name,range,scale:1.2,format:'png'});await fs.writeFile(path.join(dir,`preview_${name}_${label}.png`),new Uint8Array(await png.arrayBuffer()));
 }
}
for(const [label,range] of [['notes','A2:B14'],['rules',`A${ruleStart}:B${ruleStart+4}`]]){
 const png=await wb.render({sheetName:'规则与口径',range,scale:1.1,format:'png'});await fs.writeFile(path.join(dir,`preview_${label}.png`),new Uint8Array(await png.arrayBuffer()));
}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'formula error scan'})).ndjson);
await fs.writeFile(path.join(dir,'workbook_checks.txt'),checks.join('\n'));
const dest=path.join(dir,'四K五K_分类指标与混淆矩阵_R24B_20261015.xlsx');
await(await SpreadsheetFile.exportXlsx(wb)).save(dest);
await fs.writeFile(path.join(dir,'workbook_verification.json'),JSON.stringify({valuesChecked,liveFormulaProbes:tested,sheets:3,export:dest},null,2));
console.log(JSON.stringify({export:dest,valuesChecked,liveFormulaProbes:tested}));
