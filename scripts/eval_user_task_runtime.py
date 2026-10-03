"""Exercise real task APIs, persisted SQLite, spawned workers and research engines.

The optional loopback viewer uses an explicit evaluation identity, not production
authentication. No system database is touched. --kingdomai reads market data only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def demo_draft():
    return {"requirement_brief": "合成数据训练、阶段回测和报告流程验证", "trigger": {},
            "budget": {"max_runtime_seconds": 300}, "execution_plan": {"steps": [{
                "step_id": "research", "type": "tool", "target_ref": {"kind": "tool", "name": "stock_automl_research"},
                "inputs": {"requirement_brief": "显式配置的合成流程验证", "source": "demo", "llm_review": False,
                           "spec": {"start": "2023-01-02", "end": "2023-12-29", "max_symbols": 6,
                                    "max_trials": 4, "rounds": 1, "folds": 2, "models": ["linear"],
                                    "tasks": ["classification", "regression"], "horizons": [1],
                                    "samplers": ["all"], "feature_sets": ["technical"]}}, "depends_on": []}]}}


def document_draft():
    return {"requirement_brief": "保存一份研究说明，再读取核对", "trigger": {},
            "budget": {"max_runtime_seconds": 60}, "execution_plan": {"steps": [
                {"step_id": "write", "type": "tool", "target_ref": {"kind": "tool", "name": "file_io"},
                 "inputs": {"action": "write", "file_type": "text", "file_name": "研究说明.md",
                            "content": "# 研究说明\n\n任务系统支持多步执行，并保存每步证据。"}, "depends_on": []},
                {"step_id": "read", "type": "tool", "target_ref": {"kind": "tool", "name": "file_io"},
                 "inputs": {"action": "read", "file_id": {"$from": "write.result.data.artifact_ref"}}, "depends_on": ["write"]}]}}


def create_app(service):
    from flask import Flask, jsonify
    from src.web.scheduled_task_routes import create_scheduled_task_blueprint
    app = Flask(__name__)
    identity = {"user_id": "task-eval-owner", "user_type": "member", "display_name": "任务链路验证"}
    app.register_blueprint(create_scheduled_task_blueprint(service=service, identity_resolver=lambda: identity))
    # Only the evaluation viewer uses these fixture navigation endpoints.
    app.add_url_rule("/api/auth/session", endpoint="eval_identity", view_func=lambda: jsonify(ok=True, authenticated=True, user=identity))
    app.add_url_rule("/api/assistant/threads", endpoint="eval_threads", view_func=lambda: jsonify(ok=True, threads=[], total=0))
    return app


def execute_case(client, worker, name, payload):
    started = time.monotonic()
    response = client.post("/api/task-definitions", json=payload, headers={"Idempotency-Key": name})
    data = response.get_json()
    if response.status_code >= 400:
        raise RuntimeError(f"{name}: submission failed: {data}")
    task = data["task"]
    task_id = task["task_id"]
    duplicate = client.post("/api/task-definitions", json=payload, headers={"Idempotency-Key": name}).get_json()["task"]
    assert duplicate["task_id"] == task_id
    worker.run_once()
    runs = client.get(f"/api/task-definitions/{task_id}/runs").get_json()["runs"]
    assert len(runs) == 1, runs
    run = runs[0]
    if run["status"] != "completed":
        raise RuntimeError(f"{name}: {run['status']}: {run.get('error_text')}")
    checks = []
    evidence = {}
    for artifact in run.get("artifacts") or []:
        download = client.get(artifact["url"])
        assert download.status_code == 200, artifact
        checks.append({"name": artifact["name"], "bytes": len(download.data)})
        if artifact["name"].endswith(".json"):
            evidence[artifact["name"]] = json.loads(download.data)
    outputs = [step["result"] for step in run["result"]["steps"]]
    expected_budgets = {"document-two-steps": 60, "automl-structured-demo": 300,
                        "automl-natural-language-demo": 600, "automl-natural-language-kingdomai": 900}
    constraint_checks = {"runtime_budget_preserved": task["budget"]["max_runtime_seconds"] == expected_budgets[name]}
    if name.startswith("automl-"):
        spec = evidence["spec.json"]
        is_demo = "demo" in name
        constraint_checks.update(
            classification_and_regression_preserved=set(spec["tasks"]) == {"classification", "regression"},
            algorithm_families_preserved=set(spec["models"]) == ({"linear"} if is_demo else {"linear", "tree"}),
            symbols_budget_preserved=spec["max_symbols"] == (6 if is_demo else 16),
            trial_budget_preserved=spec["max_trials"] == (4 if is_demo else 8),
            horizons_preserved=set(spec["horizons"]) == ({1} if is_demo else {1, 3, 7}),
            folds_preserved=spec["folds"] == 2,
            dates_preserved=(spec["start"], spec["end"]) == (("2023-01-02", "2023-12-29") if is_demo else ("2024-01-01", "2025-12-31")),
            trials_within_budget=evidence["report.json"]["attempted_trials"] <= spec["max_trials"],
        )
        if is_demo:
            constraint_checks["both_prediction_tasks_trained"] = {row["candidate"]["task"] for row in evidence["development.json"]["results"]} == {"classification", "regression"}
    assert all(constraint_checks.values()), {"case": name, "constraint_checks": constraint_checks}
    return {"case": name, "task_id": task_id, "run_id": run["run_id"], "status": run["status"],
            "duration_seconds": round(time.monotonic() - started, 2), "step_count": len(outputs),
            "artifact_downloads": checks, "constraint_checks": constraint_checks, "summary": run.get("summary"),
            "research": [output["domain_result"] for output in outputs if "domain_result" in output]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/task_runtime_eval")
    parser.add_argument("--llm", action="store_true", help="also use real configured LLM for natural-language demo planning/review")
    parser.add_argument("--kingdomai", action="store_true", help="also run bounded natural-language research with SELECT-only market data")
    parser.add_argument("--serve", type=int, help="keep loopback evaluation API/worker running on this port for UI verification")
    parser.add_argument("--ui-origin", help="explicit local frontend origin allowed by the evaluation viewer")
    parser.add_argument("--serve-existing", type=Path, help="serve an existing evaluation directory without re-running cases")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    destination = args.serve_existing.resolve() if args.serve_existing else args.output.resolve() / stamp
    if args.serve_existing:
        if not args.serve or not (destination / "tasks.sqlite3").is_file():
            parser.error("--serve-existing requires --serve and an existing tasks.sqlite3")
    else:
        destination.mkdir(parents=True, exist_ok=False)
    if args.ui_origin:
        from urllib.parse import urlsplit
        parsed = urlsplit(args.ui_origin)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("--ui-origin must be a loopback HTTP origin")
        os.environ["FIN_AGENT_ALLOWED_ORIGINS"] = args.ui_origin
    os.environ["TASK_SQLITE_PATH"] = str(destination / "tasks.sqlite3")
    os.environ["TASK_ARTIFACT_ROOT"] = str(destination / "artifacts")
    # Evaluation must not create trace/session records in a configured system DB.
    os.environ["SYSTEM_DB_URL"] = ""
    from src.services.scheduled_task_service import ScheduledTaskService
    from src.services.scheduled_task_worker import ScheduledTaskWorker
    service = ScheduledTaskService()
    worker = ScheduledTaskWorker(lease_seconds=60, heartbeat_seconds=2)
    app = create_app(service)
    cases = [("document-two-steps", {"instruction": "保存研究说明并读取核对", "draft": document_draft()}),
             ("automl-structured-demo", {"instruction": "训练和回测合成数据", "draft": demo_draft()})]
    if args.llm:
        cases.append(("automl-natural-language-demo", {"instruction":
            "立即启动一次 AutoML 合成数据流程验证（source=demo），使用2023-01-02到2023-12-29，"
            "随机6家合成公司，持有1个交易日，仅比较逻辑回归和Ridge收益回归，使用技术特征和全部样本，"
            "最多4个候选实验、1轮、2个时间验证折，做公司及时间留出回测，生成大模型评审；最多运行10分钟。"}))
    if args.kingdomai:
        cases.append(("automl-natural-language-kingdomai", {"instruction":
            "立即用kingdomai库2024-01-01到2025-12-31的日K，对随机16只股票研究持有1/3/7个交易日到期收益。"
            "同时探索上涨概率分类和收益回归，仅比较逻辑回归/Ridge及决策树；样本不另筛选，特征可以使用技术及已支持的扩展特征。"
            "最多8个实验、2轮、2个时间验证折，保留其他公司与未来时间窗口，计入成本，输出设计、样本统计、各阶段回测和大模型评审。"
            "不要求保证达标，最多运行15分钟。"}))
    if args.serve_existing:
        cases = []
    report = {"git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "environment": "isolated SQLite + spawned Python workers",
              "identity": "evaluation fixture; production authentication not exercised by this script", "cases": []}
    files = sorted({*ROOT.glob("src/services/*task*.py"), *ROOT.glob("src/tools/*task*.py"),
                    *ROOT.glob("src/tools/*automl*.py"), *ROOT.glob("src/quant_research/automl/*.py"),
                    *ROOT.glob("src/quant_research/automl/prompts/*.md"),
                    *ROOT.glob("src/prompts/system/assistant.scheduled_task_compile.*"),
                    Path(__file__).resolve()})
    report["implementation_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    with app.test_client() as client:
        for name, payload in cases:
            print(f"Running {name}", flush=True)
            result = execute_case(client, worker, name, payload)
            report["cases"].append(result)
            (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"Completed {name}: {result['duration_seconds']}s, {len(result['artifact_downloads'])} artifacts", flush=True)
    print(f"Evidence: {destination / 'report.json'}", flush=True)
    if args.serve:
        def work():
            while True:
                worker.run_once()
                time.sleep(1)
        threading.Thread(target=work, daemon=True).start()
        app.run(host="127.0.0.1", port=args.serve, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
