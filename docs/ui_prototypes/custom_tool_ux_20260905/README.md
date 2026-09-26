# 2026-09-05 自定义金融工具交互原型

## 当天结论

推荐 B「共同编辑的逻辑文档」作为主工作空间，融合 C「样例教学」的核对与纠错；A 是轻量聊天入口，D 用于深入调参与效果评测。不是建设四套业务流程，也不是增加必须逐项确认的状态机。

- [完整交互调研与逐环节方案](../custom_tool_ux_directions_20260905.md)
- [代码进展、正确性问题与规划初稿](../../development_tasks/custom_tool_strategy_review_and_plan_20260905.md)

## 打开原型

以下为完整 HTML 页面：克隆仓库后直接用现代浏览器打开即可。GitHub / Codeup 的文件预览一般只显示源码，需要下载或在本地打开。

| 原型 | 页面 | 主要交互 |
| --- | --- | --- |
| A：对话卡片 | [fin-tool-dialogue.html](fin-tool-dialogue.html) | 六个环节切换、计算差异与微调 |
| B：共同编辑的逻辑文档 | [fin-tool-logic-document.html](fin-tool-logic-document.html) | 规则定位、独立推导、候选修订对比 |
| C：样例教学台 | [fin-tool-example-studio.html](fin-tool-example-studio.html) | 正反例判断、文字意见、核对表 |
| D：实验对比工作台 | [fin-tool-experiment-lab.html](fin-tool-experiment-lab.html) | 参数试算、命中差异、效果研究方案 |

所有数据与实现结果均为构造演示。没有连接模型、金融 API、账户或回测。自由文本只在当前页面显示，刷新后演示状态不保留。原型中的“保存”“修订”不会写入真实工具资产。

## 可编辑源文件

`sources/` 保留四份原始 HTML 片段；上一级同名 HTML 为包含样式和预览环境的独立导出版本。普通交互无需 Codex。可选的 `Tweak` 外观调整只在支持该功能的宿主中出现，缺少宿主时不会影响核心原型。

如需重新导出，可使用 Codex visualize skill 自带的 `scripts/render.py`：

```sh
python3 /path/to/visualize/scripts/render.py sources/fin-tool-dialogue.html fin-tool-dialogue.html --force
```

导出页面随仓库保存，阅读者无需安装该 skill。后续修改源文件时应同步更新导出页面。

## 验证范围

已做四份原型脚本语法检查、关键交互抽查和暗色桌面/窄屏布局抽查；没有因此验证生产工具、真实模型或投资有效性。具体记录见完整交互调研文档末尾。
