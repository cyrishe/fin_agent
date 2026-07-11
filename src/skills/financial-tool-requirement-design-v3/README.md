# financial-tool-requirement-design v3.1

金融工具设计阶段的应用内 Skill 包。

```text
SKILL.md                  业务指令
schema.json               Codex 最终业务结果 Schema
references/               按需读取的业务参考
assets/sample-*.json      联调与回归样例
```

模型输出只包含设计业务结果。运行事件、版本、diff、确认记录和前端动作由 Harness 与业务后端维护。

设计状态只有：

- `clarification`
- `review`

用户确认由外层状态机处理，不重新生成设计。
