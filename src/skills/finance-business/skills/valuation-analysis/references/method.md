# 个股估值分析方法

## 目录

1. 选择方法
2. 历史与同业比较
3. 形成结论
4. 方法来源

## 选择方法

先问估值要回答什么：

- 当前价格相对自身历史是否偏高或偏低；
- 相对业务相近公司是否存在溢价或折价；
- 市场为增长和盈利质量支付了什么价格；
- 哪些假设变化会明显改变估值。

常用视角：

- 盈利相对稳定：PE 及其一致口径；
- 资产驱动或金融类业务：PB 与资本回报；
- 收入可比但利润阶段不同：PS 仅作辅助；
- 现金流具有代表性：现金流倍数；
- 预测和假设充分：才考虑现金流折现或价值区间。

不要为了完整而同时展示所有倍数。

## 历史与同业比较

- 历史比较必须使用一致的盈利和价格口径；
- 周期高点的低 PE 可能来自高盈利，不能直接解释为便宜；
- 可比公司先看业务模式、收入来源和成长阶段，再看行业标签；
- 估值差异需要用增长、回报率、现金流、风险或治理差异解释；
- 极端值、亏损公司和口径不一致的数据不参与机械平均。

## 形成结论

结论至少说明：

1. 当前估值状态；
2. 主要比较基准；
3. 哪些经营事实支持当前估值；
4. 市场可能隐含什么预期；
5. 哪些变化会推翻判断。

## 方法来源

主要参考：

- [Anthropic Comparable Company Analysis](https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/financial-analysis/skills/comps-analysis)：指标选择、可比性、统计基准和异常值处理。
- [Anthropic DCF Model](https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/financial-analysis/skills/dcf-model)：现金流估值的假设、敏感性和可追溯性。

未采用 Excel 交付格式、固定公式区块和机构数据源绑定。
