"""Compare native notice extraction with reviewed samples; no writes to business tables.

Number/unit coverage is a diagnostic, not semantic accuracy. Optional business questions
exercise only the extracted material. Store real answers for human review.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path

from notice_pipeline import LLM, settings, save_json


def compact(extraction):
    if isinstance(extraction, dict):
        return {k: compact(v) for k, v in extraction.items()
                if k not in ('source_quote', 'source_locator')}
    if isinstance(extraction, list):
        return [compact(v) for v in extraction]
    return extraction


def comparable(f):
    unit = (f.get('unit_raw') or '').replace('人民币', '').replace(' ', '')
    aliases = {'人民币元': '元', '美元': '美元', '万元人民币': '万元', '元人民币': '元'}
    unit = aliases.get(unit, unit)
    # Deterministic same-unit check. No inferred conversions or semantic name matching.
    values = tuple(Decimal(str(f[k])) if f.get(k) is not None else None
                   for k in ('value_num', 'value_lower', 'value_upper'))
    return unit, values


def compare(run, references):
    manifest = json.loads((run/'manifest.json').read_text())
    if not manifest.get('completed_at'):
        raise ValueError('Extraction is still running; evaluate a completed manifest')
    rows, calls = [], []
    for item in manifest['results']:
        result = json.loads(Path(item['result_file']).read_text())
        calls += result['calls'] + result['prepared'].get('ocr_calls', [])
        path = references/(str(item['source_id'])+'.json')
        if not path.exists():
            continue
        ref = json.loads(path.read_text()); got = result['extraction']
        missing, matched = [], []
        for fact in ref['facts']:
            candidates = [g for g in got['facts'] if comparable(g) == comparable(fact)]
            info = {k:fact.get(k) for k in ('metric_name','value_num','value_lower','value_upper','unit_raw','scope','basis')}
            (matched if candidates else missing).append(info)
        rows.append(dict(source_id=item['source_id'], category=ref['business_category'],
            extracted_category=got['business_category'], ignored=bool(got['ignore_reason']),
            reference_facts=len(ref['facts']), generated_facts=len(got['facts']),
            matched_number_unit=len(matched), missing=missing))
    count = sum(r['reference_facts'] for r in rows)
    covered = sum(r['matched_number_unit'] for r in rows)
    usage = {k:sum((c.get('usage') or {}).get(k,0) or 0 for c in calls)
             for k in ('prompt_tokens','completion_tokens','total_tokens')}
    return dict(documents=len(rows), errors=manifest['errors'], reference_facts=count,
        matched_number_unit=covered, number_unit_coverage=covered/count if count else None,
        note='Only same unit and exact scalar/bounds matching; does not prove metric, subject, period, or business correctness.',
        llm_calls=len(calls), usage=usage, rows=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True);p.add_argument('--reference',required=True)
    p.add_argument('--questions');p.add_argument('--env-file');a=p.parse_args()
    run=Path(a.run);report=compare(run,Path(a.reference));save_json(run/'comparison.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False))
    if a.questions:
        questions=json.loads(Path(a.questions).read_text());manifest=json.loads((run/'manifest.json').read_text())
        by_id={str(r['source_id']):json.loads(Path(r['result_file']).read_text()) for r in manifest['results']}
        missing={str(i) for q in questions for i in q['source_ids']} - set(by_id)
        if missing:
            raise ValueError('Question sources are missing: '+', '.join(sorted(missing)))
        llm=LLM(settings(a.env_file)); answers=[]
        for q in questions:
            data=[compact(by_id[str(i)]['extraction']) for i in q['source_ids']]
            answer,trace=llm.chat('仅依据给定的公告抽取结果回答问题，清楚区分事实、计划、预测及缺失。不补造信息。输出JSON：{"answer":"回答"}。',
                                json.dumps({'question':q['question'],'documents':data},ensure_ascii=False),max_tokens=4000)
            answers.append(dict(q,answer=json.loads(answer)['answer'],trace=trace))
            save_json(run/'business_answers.json',answers)
            print(q['id'],flush=True)


if __name__=='__main__':main()
