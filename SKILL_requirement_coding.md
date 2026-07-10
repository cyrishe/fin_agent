---
name: custom-tool-requirement-coding
description: 用于自定义工具设计稿已确认后的代码生成阶段，将明确的工具设计稿转成受控、可测试、可注册的 Python 工具实现。适合生成代码、函数模块、测试样例、执行结果摘要和需要回到设计阶段的问题反馈；本技能不重新解释需求。
---

# 自定义工具代码生成

## 使用边界

当自定义工具的设计稿已经确认，并需要生成或更新工具代码时使用本技能。目标是把已确认的设计稿变成清晰、可执行、可测试的代码产物。

本阶段只做代码设计、代码生成、最小测试和实现反馈，不重新定义工具目标，不擅自改变输入输出协议，不正式提交或注册工具。

如果设计稿不足以继续编码，返回 `need_design_fix`，并指出需要回到设计阶段确认的问题。

## 输入上下文

优先使用以下信息：

- 已确认的工具设计稿
- 当前自定义工具会话状态
- 工具英文名、中文名和描述
- 输入 schema
- 输出 schema
- 计算逻辑说明
- 用户提供的样例、期望结果或反例
- 可用运行环境、依赖限制和沙箱规则
- 现有自定义工具注册规范
- 上一轮代码、测试结果和用户反馈

如果上下文不足以安全生成代码，优先返回 `need_design_fix`，不要自行补出会改变工具行为的关键假设。

## 工作流程

1. 校验设计稿是否足够实现。
2. 确认输入输出协议，不改变字段名和字段含义。
3. 梳理实现计划：核心函数、辅助函数、入口函数、测试样例。
4. 生成工具代码，优先保持简单、显式、可读。
5. 生成最小测试样例；如果当前环境允许，执行小测试并输出结果。
6. 如果发现设计问题、权限问题或执行边界问题，停止继续扩写代码，返回 `need_design_fix`。
7. 形成最终代码产物、测试结果和实现说明，等待外层执行、用户确认或 commit。

## 代码约束

- Python 工具入口建议统一为 `run(inputs: dict) -> dict`。
- 返回值必须是普通 JSON 可序列化对象。
- 保留设计稿中的输入输出字段名和字段含义。
- 对输入做必要校验，错误返回结构化信息。
- 计算逻辑应显式、可读、可测试。
- 可以拆出小函数，但不要为了抽象而抽象。
- 不在工具代码中直接读取密钥。
- 不执行不可控 shell 命令。
- 不直接访问网络，除非设计稿明确允许且系统策略允许。
- 不修改全局状态，除非外层注册流程明确要求。
- 不把用户样例写死成固定输出。
- 不把个人工具并入标准金融数据工具体系。

## 流式输出格式

输出必须使用 NDJSON，一行一个完整 JSON 对象。前面的事件用于前端展示和过程追踪，最后一行必须是 `final`，且只有 `final` 作为外层状态机依据。

推荐事件类型：

- `codex/agent_update`：将你的准备、观察、编码过程、测试过程都打包在内，注意你的过程说明和正式产物可能也是分阶段的，比如 分析--计划--代码--测试--修正--最终结束。
- `model/analysis`：输出阶段性实现分析，可用于前端实时展示，同样也是多段，和codex/agent_update可以交替。
- `model/code_plan`：输出代码实现计划，包括模块、函数、文件、测试边界等。
- `model/artifact`：输出代码、函数、文件、schema、测试或说明片段，作为可展示和可落盘的阶段性产物。
- `model/test_result`：输出测试过程和结果摘要；如果没有实际执行测试，也要说明原因。
- `model/final`：最终代码交付结果，一般在`model/artifact` 和 `model/test_result`之后，作为自定义工具代码卡片和状态机的正式输出来源,包含多个必要字段,如下所示。

```jsonl
{"source":"codex","type":"agent_update","content":"<此为准备、观察、编码过程、测试过程，且过程说明和正式产物支持分段交叉进行的,根据你实际的执行过程来区分>"}
{"source":"model","type":"analysis","content":"<用MD格式输出阶段性实现分析，也可以多段输出>"}
{"source":"model","type":"code_plan","content":"<用MD格式输出代码实现计划，说明核心函数、入口函数、测试边界等>"}
{"source":"model","type":"artifact","artifact_type":"function | file | schema | test | note","name":"<产物名称>","language":"python | json | markdown","content":"<代码、测试、schema或说明内容>"}
{"source":"model","type":"test_result","name":"<测试名称>","status":"passed | failed | not_run","summary":"<测试结果摘要或未执行原因>"}
{"source":"model","type":"final","status":"code_ready | need_design_fix","message":"给用户看的简短说明","code_summary":"代码实现的简短总结","files":[{"path":"建议写入的相对路径","role":"tool | test | metadata","content":"文件内容"}],"tests":[{"name":"测试名称","status":"passed | failed | not_run","input":{},"expected":{},"actual":{},"summary":"测试意图和结果"}],"implementation_notes":["实现要点"],"need_design_fix":"若需要回到设计阶段，这里说明需要确认的问题；否则为空","risks":["仍需用户或系统确认的风险"]}
```

除 `final` 外，其他事件都不是系统状态依据，可以省略；但一旦输出，必须保证单行 JSON 可解析。

## 工具代码模板

```python
from __future__ import annotations

from typing import Any


def run(inputs: dict[str, Any]) -> dict[str, Any]:
    """Execute the custom tool with validated inputs."""
    try:
        # 1. Validate inputs.
        # 2. Compute result according to the confirmed design.
        # 3. Return JSON-serializable output.
        return {
            "ok": True,
            "data": {},
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "data": None,
            "error": str(exc),
        }
```

## 自检清单

生成代码后检查：

- 是否只实现已确认设计。
- 是否保留输入输出字段名。
- 是否有明确入口函数。
- 是否能处理缺失参数和非法参数。
- 是否返回 JSON 可序列化对象。
- 是否有至少一个正向样例。
- 是否没有硬编码样例结果。
- 是否没有越权访问文件、网络、密钥或系统命令。
- 如果设计稿、权限或执行环境不满足实现条件，返回 `need_design_fix` 并说明需要用户确认或调整的点。

## 不做什么

- 不重新定义需求。
- 不改变已确认的 schema。
- 不引入新依赖，除非设计稿明确允许。
- 不生成需要人工隐藏修改才能运行的代码。
- 不正式提交、注册或发布工具。
- 不把个人工具并入标准金融数据工具体系。
