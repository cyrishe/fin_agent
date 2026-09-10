#!/usr/bin/env bash
# 用法：修改下面的配置，随后执行 sh run_eval.sh（也支持bash）。
# macOS / Linux 可直接运行；Windows 请在 Git Bash 或 WSL 中运行。
# 即使用 sh 启动，也先切换到 Bash 再解析下方数组配置。
if [ -z "${BASH_VERSION:-}" ] || [ "${BASH##*/}" = "sh" ]; then
  exec bash "$0" "$@"
fi
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
# 本评测客户端自动标记is_test=true，计入统计页“测试”栏及系统总量。
RESPONSE_MODE="both"                     # both=回答+数据；summary=仅回答；data=仅数据
RESEARCH_MODE="auto"                     # auto / fast / deep：答复深度
EXECUTION_MODE="standard"                # standard=正常执行；fast=服务端快速取数路径
CONCURRENCY=2                            # 同时执行的问题数
TIMEOUT=360                              # 单次HTTP超时，单位秒；不会自动重试
MAX_ROWS=100                             # 每个结果集返回的样本行数，1～100
LIMIT=""                                 # 空=全部；例如3表示只测前3题
OUTPUT_DIR=""                            # 空=自动创建独立outputs目录；相对路径以本脚本目录为基准
PYTHON_BIN=""                            # 空=自动找Python≥3.10；指定路径用于首次创建.eval-venv
# ==================== 以下为执行逻辑，无需修改 ====================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

# 依赖只装在本目录，避开系统/Homebrew Python的安装限制。
EVAL_PYTHON="$SCRIPT_DIR/.eval-venv/bin/python"
if [[ ! -x "$EVAL_PYTHON" ]]; then
  if [[ -z "$PYTHON_BIN" ]]; then
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
      if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
      fi
    done
  fi
  if ! "${PYTHON_BIN:-python3}" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo '需要Python≥3.10；请安装或在顶部PYTHON_BIN填写新版Python路径。' >&2
    exit 2
  fi
  echo '首次运行：创建独立环境 .eval-venv …'
  "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.eval-venv" || { echo '创建失败，请确认该Python已安装venv模块。' >&2; exit 2; }
fi
if ! "$EVAL_PYTHON" -c 'import httpx, openpyxl' 2>/dev/null; then
  echo '首次安装评测依赖，需要联网 …'
  "$EVAL_PYTHON" -m pip install --disable-pip-version-check -q -r requirements-eval.txt || exit 2
fi
[[ "${1:-}" != --setup-only ]] || { echo '评测环境已就绪。'; exit 0; }

args=(--url "$MCP_URL" --token-file "$TOKEN_FILE" --cases-file "$CASES_FILE"
      --tool "${TOOL:-finance_task}" --runtime dsh --response-mode "$RESPONSE_MODE"
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
printf '开始评测：%s；MCP入口：%s\n' "$CASES_FILE" "${TOOL:-finance_task}"
exec "$EVAL_PYTHON" scripts/eval_finance_mcp.py "${args[@]}"
