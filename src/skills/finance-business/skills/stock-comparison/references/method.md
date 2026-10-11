# 个股与可比公司分析方法

## 目录

1. 选择比较对象
2. 选择指标
3. 保证可比
4. 形成结论
5. 方法来源

## 选择比较对象

优先顺序：

1. 用户明确指定的公司；
2. 业务模式、客户和收入来源相近的公司；
3. 成长阶段、规模或竞争位置相近的公司；
4. 行业或板块成分仅作为候选池。

多元化集团或独特商业模式需要拆分业务后比较，不能依赖一个总量倍数。

## 选择指标

围绕用户的问题控制在少量指标：

- 规模：收入、利润、资产或市值；
- 成长：收入和利润变化；
- 质量：利润率、ROE、现金流、负债和周转；
- 估值：适合行业的主要倍数；
- 市场表现：同一时间窗口的涨跌、波动和成交；
- 行业特有指标：只有平台具备可比数据时加入。

## 保证可比

- 使用相同报告期或明确标注差异；
- 统一币种、单位、复权和指标定义；
- 将规模指标和效率指标分开；
- 缺失值不填零；
- 不对负分母或极端倍数做无意义平均；
- 统计中位数通常比简单均值更能抵抗极端值。

## 形成结论

好的比较回答：

- 谁在哪些维度领先；
- 领先来自什么业务原因；
- 更高估值换来了什么；
- 各自最重要的风险；
- 哪家公司更适合用户当前关注点。

## 方法来源

主要参考：

- [Anthropic Comparable Company Analysis](https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/financial-analysis/skills/comps-analysis)：可比对象、指标口径、统计基准和质量检查。
- [Anthropic Competitive Analysis](https://github.com/anthropics/financial-services/tree/main/plugins/vertical-plugins/financial-analysis/skills/competitive-analysis)：行业关键指标、竞争定位和差异解释。

未采用 Excel、PPT 或固定审批流程。
