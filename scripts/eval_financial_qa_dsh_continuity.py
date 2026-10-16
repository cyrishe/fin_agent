"""Replay existing raw multi-turn questions in persistent DSH sessions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.chdir(root)
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=True)
    from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService
    from src.scenarios.financial_qa.service import FinancialQaCcService
    from src.scenarios.financial_qa.tools import FinanceDataQueryCcTools
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dataset = json.loads((root / 'tests/evals/finance_data_chat_v1.json').read_text())
    tools = FinanceDataQueryCcTools()
    dsh = FinanceDeepSeekHarnessSessionService(enabled=True, system_tools=tools,
        worker_count=1, root_dir=output / 'runtime', log_path=output / 'events.jsonl')
    service = FinancialQaCcService(enabled=True, system_tools=tools,
        session_service=object(), dsh_session_service=dsh)
    try:
        for case in dataset['cases']:
            if len(case['turns']) < 2:
                continue
            thread = uuid.uuid4().hex
            for index, question in enumerate(case['turns'], 1):
                started = time.monotonic()
                response = service.answer(thread_id=thread, turn_id=uuid.uuid4().hex,
                    owner_id='continuity-regression', user_text=question,
                    dispatch_plan={'selected_agent': 'investment_analyst', 'turn_mode': 'normal_qa',
                        'entry': 'agent_route', 'semantic_turn': {'resolved_question': question}},
                    runtime='dsh', research_mode='fast', execution_mode='standard',
                    data_only=False, isolated_request=False, response_data_max_rows=5)
                record = json.loads((output / 'events.jsonl').read_text().splitlines()[-1])
                row = dict(id=case['id'], turn=index, question=question, thread=thread,
                    elapsed_ms=round((time.monotonic()-started)*1000), response=response, record=record)
                (output / f"{case['id']}-{index}.json").write_text(
                    json.dumps(row, ensure_ascii=False, indent=2, default=str))
                print(json.dumps({k: row[k] for k in ('id','turn','question','elapsed_ms')}, ensure_ascii=False), flush=True)
                if record.get('error'):
                    print('Runtime error: inspect saved evidence', flush=True)
                    break
    finally:
        dsh.close()


if __name__ == '__main__':
    main()
