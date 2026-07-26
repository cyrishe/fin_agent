# Coding output contract

Coding 阶段稳定保存和展示：

- `code`：外层系统从隔离工作区回收的实际源码。
- `implementation_summary`：结合需求说明实现内容和核心函数，并给出需求、Design、代码一致性的核心证据与结论。
- `execution_examples`：少量真实运行的 input 与完整 output。

`tool_contract` 只供系统注册工具。合法空结果和零命中可以是正常运行，业务效果由用户判断。
