"""One report projection for MCP evaluations; never infer semantic accuracy from HTTP 200."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def chunks(value, size=500):
    text = str(value if value is not None else "")
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def compact(value):
    if isinstance(value, str) and len(value) > 180:
        return value[:180] + "…（长字段节选）"
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def report_sheets(records, manifest, reviews=None):
    reviews = reviews or {}
    main, detail = [], []
    for record in records:
        case = record["case"]
        cid = case["case_id"]
        payload = record.get("response") or {}
        diag = payload.get("detail") or {}
        calls = diag.get("tool_calls") or []
        requests = [c.get("submitted_request") or c.get("request") for c in calls
                    if c.get("submitted_request") or c.get("request")]
        if not requests:
            for step in diag.get("steps") or []:
                if step.get("tool") == "finance_query":
                    arguments = step.get("arguments") or {}
                    requests.extend(c["request"] for c in arguments.get("steps", [arguments]) if c.get("request"))
        apis = list(dict.fromkeys(m[1] for request in requests
                    for m in re.finditer(r"(?:^|[;\n])\s*\w+\s*=\s*([a-z_]\w*(?:\.\w+)+)\s*\(", request)))
        actual_entries = {".".join(api.split(".")[:2]) for api in apis}
        expected = case.get("required_entries") or []
        coverage = len(set(expected) & actual_entries) / len(set(expected)) if expected else None
        results = (payload.get("data") or {}).get("results") or []
        counts = "\n".join(f"{r.get('result_name', f'结果{i+1}')}：{r.get('row_count', '未知')}条"
                           for i, r in enumerate(results)) or "未返回数据集"
        error = record.get("error") or record.get("mcp_error") or payload.get("error") or record.get("problems")
        status = "完成" if payload.get("ok") is True and not error else "未完成"
        review = reviews.get(cid) or {}
        if isinstance(review, str):
            review = {"verdict": review}
        seconds = record.get("elapsed_seconds")
        if seconds is None and record.get("client_elapsed_ms") is not None:
            seconds = record["client_elapsed_ms"] / 1000
        llm_steps = [step for step in diag.get("steps") or [] if step.get("kind") == "llm"]
        llm_seconds = (sum(step["duration_ms"] for step in llm_steps) / 1000
                       if llm_steps and all(step.get("duration_ms") is not None for step in llm_steps) else None)
        queries = [call for call in calls if call.get("tool") == "finance_query"]
        api_seconds = (sum(call["api_execution_ms"] for call in queries) / 1000
                       if queries and all(call.get("api_execution_ms") is not None for call in queries) else None)
        main.append([cid, compact(case["question"]), "\n".join(apis) or "无调用明细", seconds, counts,
                     coverage, status, diag.get("turns"), diag.get("total_tokens"),
                     llm_seconds, api_seconds, review.get("verdict", "未人工评审")])
        evidence = "\n".join(f"{r.get('result_name')}：总{r.get('row_count', '未知')}条，返回{len(r.get('rows') or [])}条\n"
                    + str(r.get("goal") or "") + "\nschema=" + json.dumps(r.get("schema") or {}, ensure_ascii=False)
                    for r in results)
        previews = "\n\n".join(str(r.get("result_name")) + "\n" +
                    "\n".join(json.dumps(compact(row), ensure_ascii=False) for row in (r.get("rows") or [])[:2])
                    for r in results)
        errors = [c.get("validation_errors") or c.get("error") or c.get("execution_error") for c in calls]
        request_text = "\n\n".join(requests)
        if error or any(errors):
            request_text += "\n错误：" + json.dumps({"request": error, "tools": [e for e in errors if e]}, ensure_ascii=False)
        columns = [chunks(case["question"]), chunks(request_text), chunks(evidence), chunks(previews),
                   chunks(payload.get("summary")), chunks(review.get("sql_row_count", "未核验")),
                   chunks(review.get("evidence", "未人工评审；接口完成与入口覆盖不代表业务正确。"))]
        for i in range(max(map(len, columns))):
            detail.append([cid if i == 0 else cid + " 续"] + [col[i] if i < len(col) else "" for col in columns])
    context = f"DSH；{manifest.get('response_mode', '未知')}；并发{manifest.get('concurrency', '未知')}；{manifest.get('created_at', '')}"
    return [
        {"name": "评测结果", "title": "MCP 金融查询评测", "context": context,
         "note": "入口覆盖≠业务正确率。执行时间为客户端耗时；模型/API时间为各调用累计，不相加。Token含缓存；缺失指标留空，0条不视为错误。",
         "headers": ["编号", "问题", "所选工具", "执行时间(秒)", "各数据集条数", "入口覆盖", "接口状态", "LLM轮次", "总Token", "模型时间(秒)", "API时间(秒)", "人工结论"],
         "widths": [15, 48, 34, 17, 26, 14, 14, 12, 16, 18, 18, 22],
         "formats": {3: "0.00", 5: "0%", 7: "#,##0", 8: "#,##0", 9: "0.00", 10: "0.000"}, "rows": main},
        {"name": "结果明细", "title": "调用、数据与回答", "context": "来源：本批MCP tools/call返回；未另行调用模型评审或查询数据库。",
         "note": "各数据集展示前2条，长字段明确节选；summary原文分行续排。完整API返回保存在results.json。SQL证据仅由评审文件导入。",
         "headers": ["编号", "问题", "实际API及错误", "数据集及schema", "数据前2条", "summary原文", "SQL核验条数", "人工分析证据"],
         "widths": [15, 48, 80, 75, 80, 80, 20, 65], "formats": {}, "rows": detail},
    ]


def _artifact_runtime():
    """Use an installed Artifact Tool when available, otherwise the Python fallback."""
    node = os.environ.get("FINANCE_EVAL_NODE") or shutil.which("node")
    modules = os.environ.get("FINANCE_EVAL_ARTIFACT_MODULES")
    if not node:
        return None
    if modules:
        if not (Path(modules) / "@oai/artifact-tool").is_dir():
            raise RuntimeError("FINANCE_EVAL_ARTIFACT_MODULES does not contain @oai/artifact-tool")
        return node, Path(modules)
    check = subprocess.run([node, "--input-type=module", "-e",
        "try { console.log(import.meta.resolve('@oai/artifact-tool')) } catch { process.exit(3) }"],
        capture_output=True, text=True)
    if check.returncode == 0:
        return node, None
    return None


def export_report(records, manifest, folder, *, reviews=None):
    folder = Path(folder).resolve()
    sheets = report_sheets(records, manifest, reviews)
    grid = folder / "report_grid.json"
    grid.write_text(json.dumps(sheets, ensure_ascii=False, indent=2))
    output = folder / "MCP评测结果.xlsx"
    runtime = _artifact_runtime()
    if runtime:
        node, modules = runtime
        if modules:
            link = folder / "node_modules"
            if not link.exists():
                link.symlink_to(modules, target_is_directory=True)
        builder = folder / "export_excel.mjs"
        shutil.copyfile(Path(__file__).with_name("finance_mcp_report.mjs"), builder)
        completed = subprocess.run([node, str(builder), str(grid), str(output)], capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError("Artifact Tool export failed; JSON retained. Check exporter dependencies.")
    else:
        # Standalone server environments do not ship Codex's Artifact Tool.
        # Keep the exact same report projection using the existing Python dependency.
        _export_python(sheets, output)
    return output


def _export_python(sheets, output):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo
    wb = Workbook()
    wb.remove(wb.active)
    for si, spec in enumerate(sheets):
        ws = wb.create_sheet(spec["name"])
        ws.sheet_view.showGridLines = False
        for row, value in [(2, spec["title"]), (3, spec["context"]), (4, spec["note"])]:
            ws.cell(row, 1, value).font = Font(name="Arial", size=16 if row == 2 else 11, bold=row == 2)
        for ri, values in enumerate([spec["headers"], *spec["rows"]], 6):
            for ci, value in enumerate(values, 1):
                cell = ws.cell(ri, ci, value)
                if isinstance(value, str):
                    cell.data_type = "s"  # Untrusted query/summary must never become an Excel formula.
                cell.font = Font(name="Arial", size=11, color="FFFFFF" if ri == 6 else "253449", bold=ri == 6)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if ri == 6:
                    cell.fill = PatternFill("solid", fgColor="24476B")
                if ri > 6 and ci - 1 in spec["formats"]:
                    cell.number_format = spec["formats"][ci - 1]
            ws.row_dimensions[ri].height = 32 if ri == 6 else min(409, max(48, max(
                sum(max(1, (sum(2 if ord(c) > 255 else 1 for c in line) + width - 4) // (width - 3))
                    for line in str(v if v is not None else "").split("\n"))
                for v, width in zip(values, spec["widths"])) * 15 + 12))
        for ci, width in enumerate(spec["widths"], 1):
            ws.column_dimensions[get_column_letter(ci)].width = width
        ws.freeze_panes = "C7" if si == 0 else "B7"
        if spec["rows"]:
            table = Table(displayName=f"McpResults{si}", ref=f"A6:{get_column_letter(len(spec['headers']))}{len(spec['rows']) + 6}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            ws.add_table(table)
    wb.save(output)
