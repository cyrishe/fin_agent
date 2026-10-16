"""Single source of research definitions: prose and executable, inspectable conditions.

Thresholds are frozen operational conventions, not universal charting truths.
No untrusted formula is executed as Python code.
"""
SOURCES={
 'candles':'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary',
 'macd':'https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd',
 'rsi':'https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI',
 'boll':'https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands',
 'ma':'https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential',
 'original':'../zhang_sufen_kline_20260928/notes.md',
}
CATALOG=[]
def add(id,name,family,origin,description,conditions,focus=3,source=None):
 CATALOG.append({'id':id,'name':name,'family':family,'origin':origin,'revision':'v1','description':description,
  'conditions':[{'label':label,'expr':expr} for label,expr in conditions], 'focus_bars':focus,
  'source':SOURCES[source or ('original' if origin=='原资料归并' else 'candles')]})
def B(i):return f'c[{i}] > o[{i}]'
def S(i):return f'c[{i}] < o[{i}]'
def UP(i):return f'c[{i}] > c[{i-5}]'
def DN(i):return f'c[{i}] < c[{i-5}]'
old='原资料归并';new='新增'
for bull in (True,False):
 side='bull' if bull else 'bear';sgn=B if bull else S;opp=S if bull else B
 add(f'engulf_{side}','阳包阴' if bull else '阴包阳','蜡烛组合',old,
  '相反颜色的后一根长实体覆盖前一根非十字实体，并带有此前五日反向趋势。v2补齐实体要求：前根实体超过其振幅10%，末根实体不短于此前20根实体均值。它描述收盘力量变化；不要求吞没影线，不能单凭两根K线推断趋势已经反转。',
  [('此前方向',DN(-2) if bull else UP(-2)),('前根颜色',opp(-1)),('当前颜色',sgn(0)),
   ('前根非十字','body[-1] > 0.1 * range[-1]'),('末根长实体','body[0] >= body_avg[0]'),
   ('实体覆盖','(o[0] <= c[-1]) & (c[0] >= o[-1])' if bull else '(o[0] >= c[-1]) & (c[0] <= o[-1])'),('实体确有扩张','body[0] > body[-1]')],2)
 CATALOG[-1]['revision']='v2'
 for context in ('down','up'):
  lower=bull
  name=('锤头/低位长下影' if context=='down' else '吊颈线/高位长下影') if lower else ('倒锤头' if context=='down' else '射击之星/高位长上影')
  add(('hammer_' if lower else 'star_')+context,name,'单根影线',old,
   '小实体位于振幅一端，另一端有长影；名称随此前五日趋势区分。影线只描述当日价格路径范围，须观察后续确认；此处“高低位”仅指局部趋势，不冒充长期顶部或底部。',
   [('此前趋势',DN(-1) if context=='down' else UP(-1)),('非零振幅','range[0] > 0'),('实体非十字','body[0] > 0.05 * range[0]'),('小实体','body[0] <= 0.35 * range[0]'),
    ('长影','lower[0] >= 2 * body[0]' if lower else 'upper[0] >= 2 * body[0]'),('另一影较短','upper[0] <= 0.15 * range[0]' if lower else 'lower[0] <= 0.15 * range[0]')],1)
 add(f'harami_{side}','看涨孕线' if bull else '看跌孕线','蜡烛组合',new,
  '大实体后出现反色小实体，后者位于前者实体内部。只说明波动和方向推进减弱，须防趋势继续；不把实体孕线与全振幅内包线混为一谈。',
  [('此前方向',DN(-2) if bull else UP(-2)),('前根颜色',opp(-1)),('后根颜色',sgn(0)),('前根长实体','body[-1] >= body_avg[-1]'),('后根收缩','body[0] <= 0.6 * body[-1]'),('实体内包','(max(o[0],c[0]) <= max(o[-1],c[-1])) & (min(o[0],c[0]) >= min(o[-1],c[-1]))')],2)
 add(f'three_{side}','红三兵/底部三连阳' if bull else '黑三兵','蜡烛组合',old,
  '三根同向实体、收盘逐步推进，后两根开盘在前根实体内。采用每根实体至少为此前20根均值的0.8倍、末端影不超过振幅25%的本地口径；已持续急涨急跌时须警惕追价。',
  [('此前趋势',DN(-3) if bull else UP(-3)),('三根同向',f'({sgn(-2)}) & ({sgn(-1)}) & ({sgn(0)})'),('连续收盘推进','c[-2] < c[-1] < c[0]' if bull else 'c[-2] > c[-1] > c[0]'),
   ('实体充分','(body[-2] >= 0.8 * body_avg[-2]) & (body[-1] >= 0.8 * body_avg[-1]) & (body[0] >= 0.8 * body_avg[0])'),
   ('开盘在前实体','(min(o[-2],c[-2]) <= o[-1] <= max(o[-2],c[-2])) & (min(o[-1],c[-1]) <= o[0] <= max(o[-1],c[-1]))'),
   ('末端短影','(upper[-2] <= 0.25 * range[-2]) & (upper[-1] <= 0.25 * range[-1]) & (upper[0] <= 0.25 * range[0])' if bull else '(lower[-2] <= 0.25 * range[-2]) & (lower[-1] <= 0.25 * range[-1]) & (lower[0] <= 0.25 * range[0])')],3)
 add(f'three_hold_{side}','三阴不破阳' if bull else '三阳不破阴','蜡烛组合',old,
  '第一根与随后三根反色；“不破”在v1明确采用盘中低点/高点，且首根实体不短于此前20根均值。这是给视频歧义选定的研究定义，不声称作者唯一原意；未包含第五根确认。',
  [('首根方向',sgn(-3)),('首根实体','body[-3] >= body_avg[-3]'),('三根反色',f'({opp(-2)}) & ({opp(-1)}) & ({opp(0)})'),('不破首根极值','min(l[-2],l[-1],l[0]) >= l[-3]' if bull else 'max(h[-2],h[-1],h[0]) <= h[-3]')],4)
 add(f'three_methods_{side}','上升三法' if bull else '下降三法','蜡烛组合',old if bull else new,
  '此前五日趋势同向，首根长实体后出现三根反色小实体，其全振幅被首根包住，第五根长实体收盘越过首根极值。v3要求中段各实体不超过首根60%，末根不短于此前20根实体均值。三根全部反色是严格本地变体；到第五根收盘才确认，后续守住突破边界才支持延续。',
  [('此前同向趋势',UP(-5) if bull else DN(-5)),('首根方向',sgn(-4)),('首根长实体','body[-4] >= body_avg[-4]'),('三根反色',f'({opp(-3)}) & ({opp(-2)}) & ({opp(-1)})'),('中段较小','max(body[-3],body[-2],body[-1]) <= 0.6 * body[-4]'),('全振幅内包','(min(l[-3],l[-2],l[-1]) >= l[-4]) & (max(h[-3],h[-2],h[-1]) <= h[-4])'),('末根长实体','body[0] >= body_avg[0]'),('第五根确认',f'({sgn(0)}) & ('+('c[0] > h[-4]' if bull else 'c[0] < l[-4]')+')')],5)
 CATALOG[-1]['revision']='v3'
 for doji in (False,True):
  add(('morning' if bull else 'evening')+('_doji' if doji else '_star'),('启明星' if bull else '黄昏星')+('（十字版本）' if doji else '（实体跳空版本）'),'蜡烛组合',old,
   '长实体、小实体、反向长实体收复构成三根组合。中间实体与首根实体跳空，第三根收盘收复首根实体一半；v2补上末根不短于此前20根实体均值。采用只要求首次实体跳空的本地变体；十字是母型的子型，不重复计作两份证据。完成组合不等于趋势已反转。',
   [('此前趋势',DN(-3) if bull else UP(-3)),('首根方向',opp(-2)),('首根长实体','body[-2] >= body_avg[-2]'),('中根小实体','(body[-1] <= 0.1 * range[-1]) & (range[-1] > 0) & (body[-1] <= 0.4 * body[-2])' if doji else 'body[-1] <= 0.4 * body[-2]'),
    ('实体跳空','max(o[-1],c[-1]) < c[-2]' if bull else 'min(o[-1],c[-1]) > c[-2]'),('末根反向',sgn(0)),('末根长实体','body[0] >= body_avg[0]'),('收复一半','c[0] > (o[-2]+c[-2])/2' if bull else 'c[0] < (o[-2]+c[-2])/2')],3)
  CATALOG[-1]['revision']='v2'
 add(f'tweezer_{side}','底部镊子（相近低点）' if bull else '顶部镊子（相近高点）','蜡烛组合',old,
  '相邻两根触及近似极值且阴阳方向反转。用前一日ATR的0.15倍作为极值接近范围；只是局部试探与回收，不等同已形成长期底部/顶部。本规则不要求长影，不能据此叫双针或双锤；长影需另有证据。',
  [('此前趋势',DN(-2) if bull else UP(-2)),('先反向',opp(-1)),('后同向',sgn(0)),('极值相近','abs(l[0]-l[-1]) <= 0.15 * atr[-1]' if bull else 'abs(h[0]-h[-1]) <= 0.15 * atr[-1]')],2)
add('doji','十字星','单根影线',old,'实体不超过振幅10%。它只表示开收接近；高位十字和低位十字共用几何定义，趋势与位置在图中另看，不能把犹豫直接变成买卖方向。',[('有效振幅','range[0] > 0'),('开收接近','body[0] <= 0.1 * range[0]')],1)
add('spinning','小实体双长影（螺旋桨类）','单根影线',old,'实体小且上下影都明显，反映日内分歧。上下影各至少占振幅30%、实体不超过20%是本地几何家族，不等于严格的长腿十字；与十字星重叠时只算一次分歧事实，不自动赋予上涨含义。',[('有效振幅','range[0] > 0'),('小实体','body[0] <= 0.2 * range[0]'),('双长影','(upper[0] >= 0.3 * range[0]) & (lower[0] >= 0.3 * range[0])')],1)
add('dark_cloud','乌云盖顶','蜡烛组合',old,'此前上行，阳线后高开收阴并跌入阳实体下半部。v1严格要求开在前高之上、收盘仍高于前阳开盘，因而与阴包阳区分。它是收盘转弱提示，不证明随后持续下跌。',[('此前上行',UP(-2)),('前阳长实体',f'({B(-1)}) & (body[-1] >= body_avg[-1])'),('本根阴线',S(0)),('高开','o[0] > h[-1]'),('深入下半实体','o[-1] < c[0] < (o[-1]+c[-1])/2')],2)
add('piercing','刺透形态','蜡烛组合',new,'此前下行，阴线后低开收阳，进入前阴实体上半部但未全部吞没。严格低于前低开盘，因此样本可能较少；形态完成前不能预判。',[('此前下行',DN(-2)),('前阴长实体',f'({S(-1)}) & (body[-1] >= body_avg[-1])'),('当前阳线',B(0)),('低开','o[0] < l[-1]'),('收回上半实体','(o[-1]+c[-1])/2 < c[0] < o[-1]')],2)
add('bull_swallow_three','下跌后一阳吞三阴','蜡烛组合',old,'三根阴线收盘逐步走低，随后长阳实体覆盖三阴实体整体区间。v2补上三阴收盘递降及末根实体不短于此前20根均值，避免把跳高收阴或细小实体组合解释成强力收复。吞的是实体，不要求全影线；量能与之后能否守住决定解读力度。',[('三阴',f'({S(-3)}) & ({S(-2)}) & ({S(-1)})'),('三阴收盘走低','c[-3] > c[-2] > c[-1]'),('末根阳',B(0)),('末根长实体','body[0] >= body_avg[0]'),('覆盖三实体','(o[0] <= min(c[-3],c[-2],c[-1])) & (c[0] >= max(o[-3],o[-2],o[-1]))')],4)
CATALOG[-1]['revision']='v2'
add('stick_sandwich','下跌后两阴夹阳（相近收盘）','蜡烛组合',old,'此前下行，阴阳阴排列且两个阴线收盘接近、中阳抬升。v2补上反转形态所需的此前下行；相近收盘只提示一个待验证的价格区域，尚未向上突破确认。它是本地三明治变体，不沿用视频“两阴裹一阳”的机械方向口令。',[('此前下行',DN(-3)),('阴阳阴',f'({S(-2)}) & ({B(-1)}) & ({S(0)})'),('中阳抬升','c[-1] > c[-2]'),('阴线收盘接近','abs(c[0]-c[-2]) <= 0.1 * atr[-1]')],3)
CATALOG[-1]['revision']='v2'
for up in (True,False):
 side='up' if up else 'down';cross='(ma5[-1] <= ma10[-1]) & (ma5[0] > ma10[0])' if up else '(ma5[-1] >= ma10[-1]) & (ma5[0] < ma10[0])'
 add('ma5_10_'+side,'5/10日均线'+('金叉' if up else '死叉'),'均线量价',old,'均线交叉是此前收盘的平滑变换，只在穿越当日记录事件。震荡市可能反复交叉；周期更改属于新参数版本。',[('本日交叉',cross)],5)
 add('ma10_20_'+side,'10/20日均线'+('金叉' if up else '死叉'),'均线量价',old,'中短期均线相对位置换向；只记录首次穿越，不将持续排列每天当成新交叉。须结合价格和成交背景。',[('本日交叉',('(ma10[-1] <= ma20[-1]) & (ma10[0] > ma20[0])' if up else '(ma10[-1] >= ma20[-1]) & (ma10[0] < ma20[0])'))],5)
 add('ma20_60_'+side,'20/60日均线'+('金叉' if up else '死叉')+'（保持3日）','均线量价',new,
  '20日SMA与60日SMA交叉后保持新方向三根完整日K，20日线相对五日前同向倾斜，确认日收盘位于两线同向一侧。用于中期趋势切换观察；确认日是交叉后的第二个交易日，不能回填交叉日。参数对应约月/季的观察尺度，不是已验证最优；没有要求60日线先同向，也不等于长期反转。',
  [('两日前交叉','(ma20[-3] <= ma60[-3]) & (ma20[-2] > ma60[-2])' if up else '(ma20[-3] >= ma60[-3]) & (ma20[-2] < ma60[-2])'),('保持新方向','(ma20[-1] > ma60[-1]) & (ma20[0] > ma60[0])' if up else '(ma20[-1] < ma60[-1]) & (ma20[0] < ma60[0])'),('短线同向倾斜','ma20[0] > ma20[-5]' if up else 'ma20[0] < ma20[-5]'),('收盘配合','c[0] > max(ma20[0],ma60[0])' if up else 'c[0] < min(ma20[0],ma60[0])')],6,'ma')
 add('double_'+('bottom' if up else 'top'),'局部W形颈线突破（双底结构）' if up else '局部M形颈线跌破（双顶结构）','摆动结构',new if up else old,
  '最近两个已确认极值间隔5–60根，两端容差0.5倍第二极值ATR，中间起伏至少1ATR，收盘越过两极值之间的颈线才触发。v2修正颈线只取间隔内部，不用端点K线的影线。右侧两根才确认极值。此前趋势和形成时间决定这是局部整理突破还是反转候选；几天形成的W/M不能叫数月级底/顶。前趋势幅度、量能和突破距离用于解释，不凭形状宣布趋势反转。',
  [('两端接近',f'abs({"lo" if up else "hi"}_p2[0]-{"lo" if up else "hi"}_p1[0]) <= 0.5 * {"lo" if up else "hi"}_atr2[0]'),('中段幅度',('lo_neck_inner[0]-max(lo_p1[0],lo_p2[0]) >= lo_atr2[0]' if up else 'min(hi_p1[0],hi_p2[0])-hi_neck_inner[0] >= hi_atr2[0]')),('颈线确认','(c[0] > lo_neck_inner[0]) & ((c[-1] <= lo_neck_inner[0]) | (lo_event[0] == 1))' if up else '(c[0] < hi_neck_inner[0]) & ((c[-1] >= hi_neck_inner[0]) | (hi_event[0] == 1))')],60)
 CATALOG[-1]['revision']='v2'
 for volume in ('expand','contract'):
  add('volume_'+side+'_'+volume,('放量' if volume=='expand' else '缩量')+('上涨' if up else '下跌'),'均线量价',old,
   '收盘相对昨收的方向，与当前成交量相对前20根均量组合。放量采用1.5倍、缩量采用0.7倍的研究阈值；这是完整日量比较，不等于行情软件的盘中同时间量比，更不能识别资金动机。',
   [('收盘方向','c[0] > c[-1]' if up else 'c[0] < c[-1]'),('量能','v[0] >= 1.5 * vbase[0]' if volume=='expand' else 'v[0] <= 0.7 * vbase[0]')],3)
add('ma20_retest','20日线上缩量回踩','均线量价',old,'20日线上行，前收盘离线超过0.3倍前日ATR；当日低点比前日更低、进入均线上下0.3ATR范围，收盘守在线上，成交量低于前五日均量。v2补上从上方靠近均线的过程，排除持续贴线；不要求阳线，也不能据此认定支撑已经可靠。',[('均线上行','ma20[0] > ma20[-5]'),('前收脱离均线','c[-1] - ma20[-1] > 0.3 * atr[-1]'),('低点下探','l[0] < l[-1]'),('低点接近线','abs(l[0]-ma20[0]) <= 0.3 * atr[-1]'),('收盘守线','c[0] >= ma20[0]'),('量缩','v[0] < v5[0]')],5)
CATALOG[-1]['revision']='v2'
add('ma20_breakdown','放量跌破20日线','均线量价',old,'收盘由20日线上方到线下、价格自身较昨收下降，同时量达到此前20日均量1.5倍。v2排除仅因均线移动而被动换位。观察跌破幅度、均线方向及后续是否收回；单次越线不证明永久转势。',[('收盘下穿','(c[-1] >= ma20[-1]) & (c[0] < ma20[0])'),('价格自身下跌','c[0] < c[-1]'),('放量','v[0] >= 1.5 * vbase[0]')],5)
CATALOG[-1]['revision']='v2'
add('ma_bull_stack','5/10/30日多头排列','均线量价',old,'三条均线从短到长排列且各比五日前高，是趋势状态而非交叉事件。视频多头排列的5/10/30口径单独保留，避免与5/20/60混用。',[('有序排列','ma5[0] > ma10[0] > ma30[0]'),('三线抬升','(ma5[0] > ma5[-5]) & (ma10[0] > ma10[-5]) & (ma30[0] > ma30[-5])')],5)
add('ma_compression','5/10/20日均线粘合','均线量价',old,'三均线最大间距不超过前日ATR的0.3倍。只定义收敛状态，方向要等价格实际突破；参数相对于波动尺度并非固定价差。',[('间距收敛','max(ma5[0],ma10[0],ma20[0])-min(ma5[0],ma10[0],ma20[0]) <= 0.3 * atr[-1]')],5)
add('kdj_high_turn','KDJ高位下拐','指标组合',old,'K此前至少80，本日K自身下降并下穿D。v2将下拐明确为K值下降，避免仅因D移动产生交叉。RSV为9期，K、D用1/3递推且起点为50，这是本地KDJ口径而非照搬所有随机指标参数。强趋势会钝化；动量降温不等于价格见顶。',[('曾在高位','k[-1] >= 80'),('K自身下拐','k[0] < k[-1]'),('交叉下行','(k[-1] >= d[-1]) & (k[0] < d[0])')],5)
CATALOG[-1]['revision']='v2'
add('upper_bear_volume','放量长上影阴线','单根影线',old,'此前上行中出现放量阴线，上影至少占振幅40%。同时呈现冲高回落和交易增强；不能从量柱确定谁在卖或为什么卖。',[('此前上行',UP(-1)),('阴线',S(0)),('上影显著','upper[0] >= 0.4 * range[0]'),('放量','v[0] >= 1.5 * vbase[0]')],1)
# New momentum applications, distinct from the source-video concepts.
for up in (True,False):
 side='bull' if up else 'bear'
 add('macd_cross_'+side,'MACD'+('金叉' if up else '死叉'),'MACD',new,
  'DIF与DEA换位，柱体随之过零。MACD使用12/26/9，柱体为DIF−DEA而非两倍；震荡环境易反复，不能把与同一交叉等价的柱体变色算作第二份证据。',
  [('交叉','(dif[-1] <= dea[-1]) & (dif[0] > dea[0])' if up else '(dif[-1] >= dea[-1]) & (dif[0] < dea[0])')],5,'macd')
 add('macd_zero_'+side,'DIF'+('上穿零轴' if up else '下穿零轴'),'MACD',new,
  '12期EMA与26期EMA发生相对趋势切换，区别于DIF与DEA交叉。过零的滞后会在震荡段增加反复；不假定零轴交叉与金叉是同一事件。',
  [('零轴穿越','(dif[-1] <= 0) & (dif[0] > 0)' if up else '(dif[-1] >= 0) & (dif[0] < 0)')],5,'macd')
 add('macd_hist_'+side,'MACD负柱连续缩短' if up else 'MACD正柱连续缩短','MACD',new,
  '柱体保持原符号但连续两次趋向零，表示动量差收敛，尚未构成交叉。它是研究派生规则；缩短后仍可能重新扩大，不能提前宣布反转。',
  [('三柱向零','hist[-2] < hist[-1] < hist[0] < 0' if up else 'hist[-2] > hist[-1] > hist[0] > 0')],3,'macd')
 add('rsi_reentry_'+side,'RSI脱离超卖区' if up else 'RSI退出超买区','RSI',new,
  'Wilder RSI14从30下方回到30上方，或从70上方回到70下方。标记离开极端区而不是一进入极端就操作；强趋势可长期停留极端区。',
  [('阈值回穿','(rsi[-1] <= 30) & (rsi[0] > 30)' if up else '(rsi[-1] >= 70) & (rsi[0] < 70)')],5,'rsi')
 add('rsi_mid_'+side,'RSI上穿50' if up else 'RSI下穿50','RSI',new,
  'RSI越过中线描述近期涨跌力量相对变化。50为明确中线约定，不是稳定的买卖分界；需要结合趋势和波动。',
  [('中线交叉','(rsi[-1] <= 50) & (rsi[0] > 50)' if up else '(rsi[-1] >= 50) & (rsi[0] < 50)')],5,'rsi')
 for indicator in ('dif','rsi'):
  p='lo' if up else 'hi';op='<' if up else '>';rev='>' if up else '<'
  add(('macd' if indicator=='dif' else 'rsi')+'_div_'+side,('MACD/DIF' if indicator=='dif' else 'RSI')+('底背离' if up else '顶背离'),'MACD' if indicator=='dif' else 'RSI',new,
   '在两次已确认的价格极值上比较指标：价格创新极值而指标未跟随。取价格的同一组峰谷而非另挑指标峰谷；两峰谷相隔5–60根，第二个极值后两根才确认。背离可延续，未包含后续突破确认。',
   [('本日确认第二极值',f'{p}_event[0] == 1'),('价格新极值',f'{p}_p2[0] {op} {p}_p1[0]'),('指标反向',f'{p}_{indicator}2[0] {rev} {p}_{indicator}1[0]')],60,'macd' if indicator=='dif' else 'rsi')
 p='rlo' if up else 'rhi'
 add('rsi_failure_'+side,'RSI底部失败摆动' if up else 'RSI顶部失败摆动','RSI',new,
  ('先在30下方形成RSI低点，反弹后第二低点保持30上方，随后向上突破两低点间的反弹高点。' if up else '先在70上方形成RSI高点，回落后第二高点保持70下方，随后向下跌破两高点间的回落低点。')+'中间摆动水平由实际峰谷决定，不是固定50。使用已确认的RSI峰谷，信号在实际穿越当日。',
  [('首极值进入极端',f'{p}_p1[0] < 30' if up else f'{p}_p1[0] > 70'),('第二极值离开极端',f'{p}_p2[0] > 30' if up else f'{p}_p2[0] < 70'),('突破中间摆动',f'(rsi[-1] <= {p}_neck[0]) & (rsi[0] > {p}_neck[0])' if up else f'(rsi[-1] >= {p}_neck[0]) & (rsi[0] < {p}_neck[0])')],60,'rsi')
 add('boll_squeeze_'+side,'布林持续收缩后'+('向上越带' if up else '向下越带'),'波动突破',new,
  '前三根带宽均处各自此前120根的低20%区域，当前带宽扩大且收盘首次越过布林边界。v2把持续收缩明确为3根；20期、2倍总体标准差。越带说明波动开始向一侧扩展；是否同时突破价格区间、成交是否增强另作力度事实，不把尚未越过20日前高/低说成形态错误，也不把越带等同价格趋势已经确认。3根是本地观察口径，会遗漏仅短暂收缩的启动。',
  [('持续收缩','(bw[-1] <= bw_q20[-1]) & (bw[-2] <= bw_q20[-2]) & (bw[-3] <= bw_q20[-3])'),('带宽扩大','bw[0] > bw[-1]'),('越带','(c[-1] <= bu[-1]) & (c[0] > bu[0])' if up else '(c[-1] >= bl[-1]) & (c[0] < bl[0])')],4,'boll')
 CATALOG[-1]['revision']='v2'
 add('inside_break_'+side,'内包线后'+('向上突破' if up else '向下突破'),'蜡烛组合',new,
  '中间一根全振幅被母线包住，下一根收盘突破母线高点/低点。母线边界固定，不随突破日变化；属于范围压缩后的方向确认，区别于只含实体的孕线。',
  [('振幅内包','(h[-1] < h[-2]) & (l[-1] > l[-2])'),('收盘突破','c[0] > h[-2]' if up else 'c[0] < l[-2]')],3)
for p in CATALOG:
 p['formula']=' & '.join('('+c['expr']+')' for c in p['conditions'])
assert len({p['id'] for p in CATALOG})==len(CATALOG)

# Business selection, not a validity or prediction score. These definitions remain
# available for explicit questions and historical replay, but do not occupy the
# default special-pattern shortlist. Evidence: kline_frequency_100_20260929/report.md.
BASIC_PATTERN_IDS=frozenset({
 'doji','spinning',
 'volume_up_expand','volume_down_expand','volume_up_contract','volume_down_contract',
 'ma5_10_up','ma5_10_down','ma10_20_up','ma10_20_down',
 'ma_bull_stack','ma_compression',
 'macd_cross_bull','macd_cross_bear','macd_hist_bull','macd_hist_bear',
 'rsi_mid_bull','rsi_mid_bear',
 'tweezer_bull','tweezer_bear',
})
DEFAULT_CATALOG=[p for p in CATALOG if p['id'] not in BASIC_PATTERN_IDS]
