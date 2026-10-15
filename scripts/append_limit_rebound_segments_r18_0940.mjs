import fs from 'node:fs/promises';
import path from 'node:path';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';

const base=path.resolve(process.argv[2]),out=path.join(base,'entry0940_r18a_20261015');
const source=path.join(base,'涨停前与连板段_特征对照及前五_R18_20261015.xlsx');
const rows=JSON.parse(await fs.readFile(path.join(out,'append_rows.json'),'utf8'));
const endpoint=JSON.parse(await fs.readFile(path.join(out,'endpoint_cell_patches.json'),'utf8'));
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const sh=wb.worksheets.getItem('分段模型前五'),n=rows.length+1;
const cols=Object.keys(rows[0]);
const before=(await wb.inspect({kind:'table',range:"'分段模型前五'!A1:AA5",include:'values,formulas',tableMaxRows:5,tableMaxCols:27,maxChars:3500})).ndjson;
for(const r of endpoint){sh.getRange(`E${r.excel_row}`).values=[[r.open_activated]];sh.getRange(`G${r.excel_row}`).values=[[r.d1_sealed]];}
sh.getRange('V1:AA1').values=[cols];
sh.getRange(`V2:AA${n}`).values=rows.map(r=>cols.map(c=>r[c]??null));
sh.getRange(`V1:AA${n}`).format={font:{name:'Arial',size:10,color:'#202936'},rowHeight:24,verticalAlignment:'center',columnWidth:15};
sh.getRange('V1:AA1').format={fill:'#234D46',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,
  horizontalAlignment:'center',verticalAlignment:'center',rowHeight:38};
sh.getRange(`W2:W${n}`).format.numberFormat='#,##0.00';
sh.getRange(`Y2:AA${n}`).format.numberFormat='0.00%;[Red](0.00%);0.00%';
for(const col of ['V','X']){sh.getRange(`${col}2:${col}${n}`).format.numberFormat='0';sh.getRange(`${col}2:${col}${n}`).format.horizontalAlignment='center';}
// The public API has no documented resize. Replace only this table definition,
// preserving its name/style and all existing cells; all other tables stay intact.
const old=sh.tables.getItemAt(0),name=old.name,style=old.style,filters=old.showFilterButton;
old.delete();const table=sh.tables.add(`A1:AA${n}`,true,name);table.style=style;table.showFilterButton=filters;
const note=wb.worksheets.getItem('效果对照');
note.getRange('A26').values=[['新增09:40前最高价买入的D2—D4收盘收益测算，分类评价保持原口径；未重训模型或重新排名。']];
const explanations=[
 '940前可买：09:31—09:40结束的10根分钟K内，曾有成交量且价格低于涨停价，沿用D1非ST条件；1可买、0不可买、空白待核验。',
 '买入价取上述窗口有成交量分钟的最高价；买价触板=1表示此最高价已触及涨停，仍按该价假设买入，不代表保证成交。',
 'D2/D3/D4收益=相应日收盘价/买入价−1，未扣费；缺分钟、价格不连续或未到对应日期留空。已补齐末端5条D1开盘及封板状态。'
];
explanations.forEach((text,i)=>{const r=28+i;note.getRange(`A${r}:Q${r}`).merge();note.getRange(`A${r}`).values=[[text]];});
note.getRange('A28:Q30').format={font:{name:'Arial',size:10,color:'#465263'},rowHeight:26,verticalAlignment:'center'};
wb.recalculate();
const checks=[before,(await wb.inspect({kind:'table',range:"'分段模型前五'!V1:AA8",include:'values,formulas',tableMaxRows:8,tableMaxCols:6,maxChars:3500})).ndjson];
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'errors'})).ndjson);
await fs.writeFile(path.join(out,'workbook_checks.txt'),checks.join('\n'));
for(const [tag,range] of [['head','V1:AA12'],['tail',`V${n-8}:AA${n}`],['notes','A26:Q30']]){
  const img=await wb.render({sheetName:tag==='notes'?'效果对照':'分段模型前五',range,scale:1.3,format:'png'});
  await fs.writeFile(path.join(out,`preview_${tag}.png`),new Uint8Array(await img.arrayBuffer()));
}
const dest=path.join(out,'涨停分段模型_前五_940前最高价买入_D2D4收益_R18A_20261015.xlsx');
await(await SpreadsheetFile.exportXlsx(wb)).save(dest);console.log(dest);
