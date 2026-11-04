"""Real-model authoring/revision and progressive method use, with synthetic facts.

Uses the actual candidate store, active registry, runtime context and read tools.
No production writes or financial data queries. The model adapter is isolated
from the production CC/DSH transport; this is not a full chat acceptance test.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.eval_skill_selection_only import replay_request, save


class ModelAuthor:
    provider_name = "isolated-configured-model"

    def __init__(self, output, thinking):
        self.output = output
        self.calls = 0
        self.model = os.environ["LLM_DEFAULT_MODEL"]
        self.thinking = thinking

    def available(self):
        return True

    def run_turn(self, **kwargs):
        self.calls += 1
        request = {"model": self.model, "stream": False, "max_tokens": 6000,
            "thinking": {"type": self.thinking}, "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": kwargs["developer_instructions"]
                 + "\nReturn JSON matching this schema:\n" + json.dumps(kwargs["output_schema"])},
                {"role": "user", "content": kwargs["prompt"]}]}
        save(self.output / f"author-{self.calls}-request.json", request)
        _, response = replay_request(request)
        save(self.output / f"author-{self.calls}-response.json", response)
        choice = response["choices"][0]
        return {"ok": choice.get("finish_reason") == "stop",
                "final": json.loads(choice["message"]["content"]),
                "llm_usage": response.get("usage", {})}


def run_method_case(out, hub, skill_ids, question, thinking):
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    from src.services.session_variable_store_service import SessionVariableStoreService
    agent = FinancialQaCcService(enabled=True, skill_hub_catalog_service=hub,
                                session_service=object(), dsh_session_service=object())
    context = agent._runtime_context(application_context={}, owner_id="synthetic-owner",
                                     explicit_skill_ids=skill_ids)
    service = FinanceDataQueryCcTools(result_store=SessionVariableStoreService(data_root=out / "results"))
    definitions, _, tracker = service.build_tools(owner_ids=["synthetic-owner"], tool_context=context)
    allowed = {d.name: d for d in definitions if d.name in {
        "read_finance_skill", "read_finance_skill_reference", "read_finance_catalog"}}
    messages = [
        {"role": "system", "content": (ROOT / "src/scenarios/financial_qa/dsh_system.md").read_text()},
        {"role": "system", "content": "隔离合成证据测试。只有目录和方法读取工具，没有行情查询或模型图像工具。"
         "给定事实可直接使用；缺失事实如实说明，不声称取数或计算。\n"
         + context.get("_finance_skill_catalog_prompt", "") + "\n已加载的方法：\n"
         + context.get("_finance_explicit_skill_prompt", "")},
        {"role": "user", "content": question}]
    schemas = [{"type": "function", "function": {"name": d.name,
               "description": d.description, "parameters": d.input_schema}} for d in allowed.values()]
    for turn in range(1, 7):
        request = {"model": os.environ["LLM_DEFAULT_MODEL"], "stream": False,
                   "max_tokens": 6000, "thinking": {"type": thinking}, "reasoning_effort": "low",
                   "messages": messages, "tools": schemas}
        save(out / f"turn-{turn}-request.json", request)
        _, response = replay_request(request)
        save(out / f"turn-{turn}-response.json", response)
        choice = response["choices"][0]
        message = choice["message"]
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            save(out / "result.json", {"finish_reason": choice.get("finish_reason"),
                 "answer": message.get("content"), "tracker": tracker})
            return
        for call in calls:
            function = call["function"]
            # Never dispatch anything outside this explicit read-only test surface.
            tool = allowed[function["name"]]
            result = asyncio.run(tool.handler(json.loads(function["arguments"])))
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
        print(f"{out.name}: completed read turn {turn}", flush=True)
    save(out / "result.json", {"error": "evaluation_turn_limit", "tracker": tracker})


def main():
    from dotenv import load_dotenv
    from src.scenarios.financial_qa.business_skills import FinanceBusinessSkillCatalog
    from src.services.skill_authoring_service import SkillAuthoringService, SkillCapabilityDiscoveryService
    from src.services.skill_candidate_store_service import InMemorySkillCandidateStoreService
    from src.services.skill_hub_catalog_service import SkillHubCatalogService
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--thinking", choices=["enabled", "disabled"], default="enabled")
    args = parser.parse_args()
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env", override=False)
    out = args.output.resolve()
    if out.exists():
        raise SystemExit("Preserve previous results; choose a new directory")
    catalog = FinanceBusinessSkillCatalog(snapshot_root=out / "snapshots")
    hub = SkillHubCatalogService(business_catalog=catalog,
        candidate_store=InMemorySkillCandidateStoreService(),
        legacy_skill_studio=SimpleNamespace(list_compiled_skills=lambda: []))
    discovery = SkillCapabilityDiscoveryService(business_catalog=catalog, catalog_provider=hub.runtime_catalog)
    author = SkillAuthoringService(store=hub.candidate_store, discovery_service=discovery,
                                  agent_harness=ModelAuthor(out, args.thinking))
    save(out / "manifest.json", {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "skill_revision": catalog.revision, "model": os.environ["LLM_DEFAULT_MODEL"],
        "thinking": args.thinking, "reasoning_effort": "low", "max_tokens": 6000,
        "scope": __doc__, "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__), ROOT / "src/services/skill_authoring_service.py",
                      ROOT / "src/scenarios/financial_qa/business_skills.py",
                      ROOT / "src/skills/skill-system/skills/skill-authoring/SKILL.md",
                      ROOT / "src/scenarios/financial_qa/skill_selection.md"]}})
    first = author.create_candidate(owner_id="synthetic-owner", requirement=
        "帮我创建持仓复盘Skill。结合技术面和相对行业表现，解释值得注意的变化与反证。"
        "具体指标由你按股票情况选择，不提供买卖指令，也不要持续监控。")
    save(out / "candidate-r1.json", first)
    print("created candidate", flush=True)
    revised = author.revise_candidate(skill_id=first["skill_id"], owner_id="synthetic-owner",
        base_revision_no=1, feedback="保留分析范围和方法，让最终回答更短，分为判断、依据、待观察三段。")
    save(out / "candidate-r2.json", revised)
    hub.activate_candidate(first["skill_id"], owner_id="synthetic-owner",
        expected_candidate_revision=2, expected_active_revision=0)
    print("revised and activated in memory", flush=True)
    facts = ("\n合成股票甲，同一截至2026-09-28的完整后复权日线事实：近20日高低点逐步抬升；"
        "收盘102，MA20为100；同区间累计收益+2%，已确认归属的行业基准同区间+5%。"
        "成交量/此前5日均量=1.02，前20日该比值范围0.90—1.20。"
        "没有其他指标序列、图像、形态扫描结果或未来收益验证。")
    run_method_case(out / "personal", hub, [first["skill_id"]], "请按我的方法复盘这只股票。" + facts, args.thinking)
    print("personal case complete", flush=True)
    run_method_case(out / "system", hub, ["technical-structure-analysis", "relative-strength-analysis"],
                    "结合技术面与相对行业表现给一份简短判断，解释相互一致或不同的地方。" + facts, args.thinking)
    print("system case complete", flush=True)


if __name__ == "__main__":
    main()
