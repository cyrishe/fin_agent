#!/usr/bin/env bash
# 用法：修改下面的配置，随后执行 bash run_eval.sh。
# macOS / Linux 可直接运行；Windows 请在 Git Bash 或 WSL 中运行。
set -eo pipefail

# ==================== 用户配置：通常只改这一段 ====================
MCP_URL="https://ai-agent.kingdomai.com/fin_agent/mcp"
TOKEN_FILE="token.json"                   # 管理员给你的临时Token文件，不是服务端.env
CASES_FILE="tests/evals/report_mcp_skill_smoke_v1.json"  # 自带6题；可改为你的 cases.json

# Skill选择：空数组 () 表示自动路由，忽略样本里预设的skill_ids。
# 单个Skill：SKILLS=("equity-report-analysis")
# 多个Skill：SKILLS=("earnings-analysis" "valuation-analysis")，按填写顺序采用方法。
# 常用ID：equity-report-analysis 个股研报解读；earnings-analysis 财报分析；valuation-analysis 估值分析。
# Skill是否可用以服务器当前账号的已发布目录为准，脚本会在评测前核对。
SKILLS=()

# MCP工具入口：空字符串 "" 或 "finance_task" = 通用分析入口，可自动选择或指定Skill。
# "finance_data_query" = 金融数据查询入口，此时SKILLS必须为空。
# stock.report.query等底层数据方法不是MCP工具名，不能填在这里。
TOOL=""

DETAIL=true                              # true默认返回轮次、Token、耗时、步骤和工具证据；false关闭
# 当前API不返回内部思考原文；推理Token与执行步骤按API实际返回记录。
RESPONSE_MODE="both"                     # both=回答+数据；summary=仅回答；data=仅数据
RESEARCH_MODE="auto"                     # auto / fast / deep：答复深度
EXECUTION_MODE="standard"                # standard=正常执行；fast=服务端快速取数路径
CONCURRENCY=2                            # 同时执行的问题数
TIMEOUT=360                              # 单次HTTP超时，单位秒；不会自动重试
MAX_ROWS=100                             # 每个结果集返回的样本行数，1～100
LIMIT=""                                 # 空=全部；例如3表示只测前3题
OUTPUT_DIR=""                            # 空=自动创建独立outputs目录；相对路径以本脚本目录为基准
PYTHON_BIN=""                            # 空=自动找Python；也可填写虚拟环境Python的完整路径
# ==================== 以下为执行逻辑，无需修改 ====================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "未找到Python，请先安装Python 3.10或更新版本。" >&2
    exit 2
  fi
fi
if ! "$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 10); import httpx, openpyxl' >/dev/null 2>&1; then
  echo "请确认Python版本至少为3.10，并首次安装依赖：" >&2
  printf '  "%s" -m pip install -r requirements-eval.txt\n' "$PYTHON_BIN" >&2
  exit 2
fi
for file in "$TOKEN_FILE" "$CASES_FILE"; do
  if [[ ! -f "$file" ]]; then
    printf '找不到文件：%s\n请检查顶部配置；相对路径以run_eval.sh所在目录为基准。\n' "$file" >&2
    exit 2
  fi
done
case "$TOOL" in
  ""|finance_task) SELECTED_TOOL="finance_task" ;;
  finance_data_query) SELECTED_TOOL="finance_data_query" ;;
  *) echo 'TOOL只能为空、finance_task或finance_data_query。' >&2; exit 2 ;;
esac
if [[ "$SELECTED_TOOL" == finance_data_query && ${#SKILLS[@]} -gt 0 ]]; then
  echo '金融数据查询入口不接受Skill，请将SKILLS设为()，或将TOOL设为finance_task。' >&2
  exit 2
fi
args=(--url "$MCP_URL" --token-file "$TOKEN_FILE" --cases-file "$CASES_FILE"
      --tool "$SELECTED_TOOL" --runtime dsh --response-mode "$RESPONSE_MODE"
      --research-mode "$RESEARCH_MODE" --execution-mode "$EXECUTION_MODE"
      --concurrency "$CONCURRENCY" --timeout "$TIMEOUT" --max-rows "$MAX_ROWS")
if [[ ${#SKILLS[@]} -eq 0 ]]; then
  args+=(--auto)
else
  for skill in "${SKILLS[@]}"; do args+=(--skill "$skill"); done
fi
case "$DETAIL" in
  true) args+=(--detail) ;;
  false) args+=(--no-detail) ;;
  *) echo 'DETAIL请填写true或false。' >&2; exit 2 ;;
esac
[[ -z "$LIMIT" ]] || args+=(--limit "$LIMIT")
[[ -z "$OUTPUT_DIR" ]] || args+=(--output-dir "$OUTPUT_DIR")
printf '开始评测：%s\nMCP入口：%s；详细指标：%s\n' "$CASES_FILE" "$SELECTED_TOOL" "$DETAIL"
"$PYTHON_BIN" scripts/eval_finance_mcp.py "${args[@]}" && status=0 || status=$?
case "$status" in
  0) echo '评测已完成。Excel、完整阅读页和原始JSON已生成，路径见上方。' ;;
  1) echo '报告已生成，但部分问题失败或未完成，请查看报告中的异常。' >&2 ;;
  *) echo '评测未完成，请查看上方错误。已有JSON会保留，不会自动重跑。' >&2 ;;
esac
exit "$status"
