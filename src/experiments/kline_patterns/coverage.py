"""Explicit source-video coverage, deduplication and unresolved meanings."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'docs/research/kline_pattern_library_20260928';OLD=ROOT/'docs/research/zhang_sufen_kline_20260928'
M={
'7690176915369479475':(['volume_up_expand','volume_up_contract','volume_down_expand','volume_down_contract'],'量价观察已形式化；吸筹/出货/诱多不能从日K识别。“急涨慢跌”若要执行还需固定分段窗口与速度口径。'),
'7690175813924228378':(['kdj_high_turn','hammer_up','upper_bear_volume','star_up','engulf_bear','double_top'],'原“见顶”降为局部风险特征，不给确定清仓指令。'),
'7689762603932962088':(['ma20_retest','ma20_breakdown','ma_compression'],'20日线回踩、破位与粘合补齐；突破后等待几日未从原文确定，不另造等待状态。'),
'7689418222860324123':(['volume_up_expand','volume_down_expand','volume_down_contract'],'急拉/急跌的分时速度不能由单根日K还原；洗盘与撤退的主体意图不执行。'),
'7689008686290799918':(['volume_up_expand','volume_down_expand','volume_down_contract'],'连续小涨/连续大涨仍缺长度和大小定义，原收益预测不保留。'),
'7688667972683975951':(['tweezer_bull'],'双针归并底部镊子近似几何。三探支撑可提议max(Lp1,Lp2,Lp3)-min(...)<=容差且第三极值已确认，但视频取点与确认未唯一，暂不执行。相间阴阳步名称含糊。'),
'7688281886526885155':(['tweezer_bull','tweezer_bear'],'底部/顶部镊子归并；高位并阳、下降三星无法从现摘录唯一确认根数、实体关系与所需位置，不猜公式。'),
'7687615103767743771':(['three_methods_bull'],'上升三法已补公式；绝地反击、暗度陈仓、九九艳阳天缺唯一图文映射，保留原帧供后续审阅，重仓/梭哈不是形态字段。'),
'7687544248186588425':(['tweezer_bull','star_up','hammer_up'],'双锤应在双针基础上另要求两根长下影，现归并家族并注明限制；揉搓的两影顺序和位置不明确，暂不自动判。'),
'7687169374389685555':(['star_up','doji','engulf_bear','dark_cloud','hammer_down','engulf_bull','three_bull'],'同形异名/重复条目归并。五日前趋势仅是v1上下文，不能冒充真实历史底部。'),
'7686793317413162290':(['evening_star','star_down','tweezer_bear','three_hold_bear'],'倒锤头是下行背景，此前视频离场口令不沿用。双鸦/约会线缺清晰口径；三阳不破阴保留上边界版研究定义。'),
'7686427280586951955':([], '通道可研究事前回归斜率，但需固定窗口；横盘不等于吸筹/出货，“久攻必破”不编为判定或预言。'),
'7686068282264243465':(['doji','star_up','spinning'],'长腿线和螺旋桨合并双长影几何，不重复统计证据。'),
'7685690706022714676':(['volume_up_contract','volume_down_contract','volume_down_expand'],'量价方向归并；黄金坑、假摔要求后续结果才能贴标签，不能放进当日检测。'),
'7684977565970320694':([], '换手活跃档位可写3<=turn_pct<5、5<=turn_pct<10、turn_pct>=10，但须先核对来源分母/单位；当前只留原观察，不把活跃直接等同方向。'),
'7684878476918131974':(['ma20_retest','ma_compression','ma_bull_stack'],'5/20/60可按C>=MA5、MA60[t]>MA60[t-5]等做独立背景；未把这些状态变为强制持仓条件。原多头排列另按5/10/30实现。'),
'7684508947524406582':([], '明确可用的是已确认摆动点与水平价，但视频标号规则仍未定稿；不得用双底规则冒充此特定支撑画线法。'),
'7684189874542366003':(['volume_up_expand','volume_down_expand','volume_up_contract','volume_down_contract'],'这里仅用完整日量/前期日均量；不声称复现盘中同时间量比。'),
'7683824815932853544':([], '早盘10点/14点、分时做T需要分钟与交易执行条件；日行情不能补出日内路径。'),
'7683449823806147840':([], '连涨连跌可用AND_i(C[-i]>C[-i-1])描述，但6/7/8/9天的后续涨跌承诺无证据，不作为方向检测器。'),
'7683101311063133503':(['volume_up_expand','volume_up_contract','volume_down_expand','volume_down_contract'],'量柱长短形式化且统一比较基准；单靠组合预测必然延续/反弹不保留。'),
'7682705127450821745':(['three_hold_bull','three_hold_bear','bull_swallow_three','stick_sandwich','three_bull','evening_doji','morning_star','three_bear'],'不破选盘中极值；两阴夹阳选择相近收盘研究变体，明确不是作者原意已经证实。'),
'7682316047945202609':([], '趋势线取点、延长与交点涉及未确定参数；先保留图文，不能用未来摆动点倒填历史。'),
'7681916825051698545':(['ma5_10_up','ma5_10_down','ma10_20_up','ma10_20_down','ma_bull_stack','ma_compression'],'金叉死叉合并对应均线参数，多头与粘合是状态，不重复宣称新的每日事件。'),
}
def main():
 works=[json.loads(x) for x in (OLD/'works.jsonl').read_text().splitlines()];episodes={x['id']:x for x in map(json.loads,(OLD/'episodes.jsonl').read_text().splitlines())};records=[]
 for w in works:
  ids,note=M.get(w['id'],([], '较长视频属于竞价、做T、复盘流程、行为建议或固定交易口令；不属于日K形态。当前日线能力不能验证这些执行或收益说法。'))
  records.append({**w,'pattern_ids':ids,'resolution':note,'original_frames':episodes.get(w['id'],{}).get('sample_frames',[])})
 (OUT/'original_coverage.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
 lines=['# 原资料逐条归并与补公式范围','', '所有30条原作品均在这里追踪；规则是研究v1定义，未唯一确定的内容明确保留，不伪造公式。新旧目录的匹配命中不等于原作者说法获得证明。','', '| 原作品 | 归并到可执行规则 | 未决事项/口径变化 |','|---|---|---|']
 for r in records:lines.append(f'| [{r["title"]}]({r["url"]}) | '+(', '.join('`'+x+'`' for x in r['pattern_ids']) or '未转成日K规则')+' | '+r['resolution']+' |')
 (OUT/'original_coverage.md').write_text('\n'.join(lines))
if __name__=='__main__':main()
