from pathlib import Path
import json,re,pandas as pd
R=Path('/data/cyris/work/b2_news_factor_r74_20261022')
# Manually read the primary as-of financial statements; numbers are year-to-date, not mixed with Q3 standalone.
data={
'000779':('2022Q1',40359354.26,32679394.78,-342080340.14,-28.49,'盈利但同比下降，经营现金净流出；短窗口主要是例行异动公告。'),
'603166':('2022Q1',30107816.22,24174744.44,2903850.35,-51.58,'盈利下滑；5月24日更正公告称不涉及主营业务和主要财务指标，不能将“更正”直接当财务造假。'),
'000595':('2022Q1',-13171422.22,-16741790.38,-29793405.70,20.14,'仍在亏损，亏损同比收窄；选股前两次异动公告。6月6日“不涉及汽车前轴”的报道晚于选股，不能回填。'),
'000678':('2022Q1',-27965149.64,-29034322.58,48767786.69,-671.24,'由盈转亏；前几日两次例行异动提示，不能单凭后续连跌认定此前已有突发利空。'),
'600692':('2023Q1-Q3',-19200857.66,-26226085.17,-405529582.39,None,'累计亏损、经营现金净流出；近5交易日未检出公司公告，10日窗口有财务总监辞职，不推断辞职动机。'),
'002868':('2023Q1-Q3',-86079986.84,-103963096.09,-122560703.12,-27.01,'累计亏损；1月13日部分监事、高管减持计划已披露，计划为公告15交易日后执行，不能说选股时已经兑现减持。'),
'600053':('2023Q1-Q3',18604059.89,14502318.01,-32831241.12,-83.37,'累计盈利但大幅下滑；1月25/26日公告提示估值高于行业及业绩下降。使用当时名称九鼎投资，不倒填如今的ST状态。'),
'600088':('2023Q1-Q3',308207282.75,17799481.84,-171832144.95,None,'归母盈利主要受非经常性项目拉动；1月31日年度预盈公告仍预计扣非盈利2350—2650万元，不能误写成亏损。多次交易风险提示。'),
'600550':('2024H1',22785666.93,18022188.31,-521226576.51,None,'上半年扭亏但经营现金净流出；9月20日公告提示市净率35.21，同行业2.18。不能把扭亏视为高位股价的安全保证。'),
'002765':('2024Q1-Q3',103619357.91,61521255.85,138004171.82,66.83,'盈利增长且经营现金流为正；短期公告以异动和经营安排为主，属于“公司经营改善仍可连续跌停”的反例。'),
'002354':('2024Q1-Q3',-8581740.56,-71114801.29,-19251940.08,-163.25,'累计归母、扣非均亏损；近窗口有异动公告，市场热度不能替代盈利改善。'),
'002181':('2024Q1-Q3',-38033033.88,-47156875.54,-66479993.77,None,'累计亏损、经营现金净流出；12月13/18日异动公告。12月19日券商重点监控提示仅有日期，D0盘中是否已知不确定，D1前可参考。'),
'600778':('2024Q1-Q3',11190184.90,3559823.82,84086868.60,None,'累计盈利但金额较薄；12月17日风险提示在10交易日窗口内、超出5交易日窗口，窗口差异已保留。'),
'000626':('2025H1',29066188.74,-125282783.30,-732068010.47,172.02,'归母盈利、扣非亏损；公司解释期现结合业务中衍生品收益计入非经常性，不能仅据扣非为负断言主营崩坏。经营现金净流出。'),
'600693':('2025Q1-Q3',88047895.54,77396250.23,414743091.15,3.04,'盈利和经营现金流均为正；公告提示高估值并明确无免税品经营资质、不涉及免税商品经营，是题材与实际业务不匹配的例子。'),
'601212':('2025Q1-Q3',-214872843.70,-1898228.64,6059521887.76,None,'归母亏损；1月24日已公告全年预计亏损4.5—6.75亿元；风险提示说明白银收入占比仅4.54%、外购原料占比高、控股股东高比例质押及估值风险。'),
'603137':('2026Q1',36206126.04,36002880.97,112759563.14,-23.87,'2025年度亏损3502.43万元，2026Q1盈利但营收下降42.56%；选股前反复提示重组推进周期长、审批及并表不确定。两笔信号属于同一行情事件。'),
'002172':('2026Q1',5236055.46,5486451.20,31835078.04,-71.70,'仍盈利但同比大幅下降；8月14日异动公告为主，不能由连续跌停倒推出已知未披露利空。'),
'002295':('2026H1',3953321.51,2010427.89,-248793333.02,-63.59,'盈利薄且下滑、经营现金净流出；9月1日重大经营合同是可见催化，合同规模不等于最终利润或回款。')
}
f=pd.read_pickle(R/'features709.pkl');cases=f[f.v2_allowed&f.D2D3连续收盘跌停.eq(1)]
rows=[];checks=[]
for r in cases.to_dict('records'):
 code=r['stk_code'][:6];period,profit,deduct,cash,yoy,note=data[code]
 report=json.loads((R/'company_reports'/(r['id'].replace('|','_')+'.json')).read_text())['selected'];aid=report['announcementId']
 text=re.sub(r'\s+','',(R/'documents'/(aid+'.txt')).read_text()).replace(',','')
 for label,value in [('net',profit),('deduct',deduct),('cash',cash)]:
  assert f'{value:.2f}' in text,(code,label,value)
 checks.append({'id':r['id'],'report_id':aid,'three_numbers_in_primary_pdf':True})
 meta=json.loads((R/'cninfo_windows'/(r['id'].replace('|','_')+'.json')).read_text())
 news=[d for d in meta['items'] if d['adjunctUrl'].split('/')[1]<=r['d0_date'] and re.search('异常波动|风险|减持|辞职|重大合同|业绩预',d['announcementTitle'])]
 news.sort(key=lambda d:d['adjunctUrl'],reverse=True)
 rows.append({'股票':r['stk_code'],'当时简称':r['historical_name'],'选中日':r['d0_date'],'D2':r['d2_date'],'D3':r['d3_date'],'财报期间':period,'财报公告日':report['adjunctUrl'].split('/')[1],'归母净利润_万元':profit/1e4,'扣非净利润_万元':deduct/1e4,'经营现金净额_万元':cash/1e4,'归母同比_pct':yoy,'近5日风险提示天数_D1前':r['d1_5_risk_warning_days'],'近10日风险提示天数_D1前':r['d1_10_risk_warning_days'],'近5日异动公告数_D1前':r['d1_5_abnormal'],'近5日减持计划数_D1前':r['d1_5_reduction_plan'],'当时公司与消息判断':note,'财报来源':'https://static.cninfo.com.cn/'+report['adjunctUrl'],'近窗公告':' | '.join(d['adjunctUrl'].split('/')[1]+' '+d['announcementTitle'] for d in news),'近窗公告链接':' | '.join('https://static.cninfo.com.cn/'+d['adjunctUrl'] for d in news)})
out=pd.DataFrame(rows);out.to_csv(R/'连续跌停20笔_公司与事前新闻.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
(R/'company_numeric_verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
print('profiles',len(out),'companies',out['股票'].nunique(),'latest-period net losses companies',out.drop_duplicates('股票')['归母净利润_万元'].lt(0).sum())

