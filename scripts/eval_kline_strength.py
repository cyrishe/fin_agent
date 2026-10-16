"""Replay conservative strength on frozen hits; no external model calls."""
from pathlib import Path
import hashlib
import json
import platform
import shutil
import subprocess
import time

from scripts.eval_kline_frequency import ROOT, OUT as INPUT, frames, dump
from src.experiments.kline_patterns.analysis import subject_interval
from src.experiments.kline_patterns.catalog import CATALOG, BASIC_PATTERN_IDS
from src.experiments.kline_patterns.strength import profile, REVISION
from src.experiments.kline_patterns.render import render

OUT = ROOT/'docs/development_tasks/evidence/kline_strength_20260929'
OLD = ROOT/'docs/development_tasks/evidence/kline_definition_audit_20260929'


def main(output=OUT):
    OUT = Path(output)
    OUT.mkdir(exist_ok=True)
    (OUT/'images').mkdir(exist_ok=True)
    assert CATALOG == json.loads((OLD/'definitions.json').read_text()), 'Replay inputs require identical definitions'
    hits = json.loads((OLD/'hits.json').read_text())
    patterns = {p['id']: p for p in CATALOG}
    rows = [];specs = {};drawn = 0;started = time.perf_counter()
    for n, (code, f) in enumerate(frames(), 1):
        for p in CATALOG:
            if p['id'] not in specs:
                at = len(f)-1
                # Specification export only; this fabricated interval is not a hit.
                spec = profile(p, {'at': at, 'start': at-4, 'end': at}, f)
                specs[p['id']] = {'id': p['id'], 'name': p['name'], 'priority_hint': spec['priority_hint'],
                    'weak_all': [{k: m[k] for k in ('label', 'weak_below', 'unit')} for m in spec['measurements']]}
        for hit in [h for h in hits if h['code'] == code]:
            p = patterns[hit['id']];at = f.index.get_loc(hit['date'])
            start, end = subject_interval(p, f, at)
            candidate = {'at': at, 'start': start, 'end': end}
            before = time.perf_counter();current = profile(p, candidate, f);elapsed = time.perf_counter()-before
            historical = profile(p, candidate, f.iloc[:at+1])
            assert current['measurements'] == historical['measurements']
            row = {**hit, 'current': current, 'signal_day': historical,
                   'profile_ms': 1000*elapsed, 'basic': p['id'] in BASIC_PATTERN_IDS}
            # Review all weak default-special instances; a bounded set of ordinary observations.
            if current['skip_visual'] and drawn < 18 and (not row['basic'] or drawn < 6):
                path = OUT/'images'/f"{code}_{p['id']}_{hit['date']}.png"
                row['image'] = str(path.relative_to(OUT))
                render(p, f, at, code, path, display_bars=10, target_start=start, target_end=end,
                       annotate_values=True, compact=True)
                drawn += 1
            rows.append(row)
        if n % 20 == 0:
            print('reviewed', n, 'stocks', flush=True)
    stats = []
    for pid in patterns:
        group = [r for r in rows if r['id'] == pid]
        stats.append({'id': pid, 'hits': len(group),
            'weak_at_signal': sum(r['signal_day']['skip_visual'] for r in group),
            'weak_at_analysis': sum(r['current']['skip_visual'] for r in group),
            'retained_due_to_later_evolution': sum(r['signal_day']['skip_visual'] and not r['current']['skip_visual'] for r in group)})
    dump(OUT/'instances.json', rows);dump(OUT/'strength_definitions.json', list(specs.values()));dump(OUT/'per_pattern.json', stats)
    summary = {'stocks': 100, 'definitions': len(specs), 'hits': len(rows),
        'weak_at_signal': sum(r['signal_day']['skip_visual'] for r in rows),
        'weak_at_analysis': sum(r['current']['skip_visual'] for r in rows),
        'weak_special_at_analysis': sum(r['current']['skip_visual'] and not r['basic'] for r in rows),
        'retained_due_to_later_evolution': sum(r['signal_day']['skip_visual'] and not r['current']['skip_visual'] for r in rows),
        'profile_mean_ms_on_hits': sum(r['profile_ms'] for r in rows)/len(rows), 'images': drawn,
        'seconds': time.perf_counter()-started, 'model_calls': 0}
    dump(OUT/'summary.json', summary)
    files = list((ROOT/'src/experiments/kline_patterns').glob('*.py'))
    files += list((ROOT/'src/skills/finance-business/skills/kline-analysis').rglob('*.md'))
    files += [Path(__file__), ROOT/'scripts/eval_kline_analysis.py', ROOT/'tests/test_kline_strength.py', ROOT/'tests/test_kline_analysis.py']
    for path in files:
        target = OUT/'source_snapshot'/path.relative_to(ROOT);target.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(path, target)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    dump(OUT/'manifest.json', {'revision': REVISION, 'commit': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        'python': platform.python_version(), 'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in files},
        'input_sha256': sha(INPUT/'daily_inputs.jsonl.gz'), 'hits_sha256': sha(OLD/'hits.json'),
        'period': ['2026-08-29','2026-09-28'], 'scope': 'Strength/exclusion and as-of evolution replay, not an independent human recall or profitability measurement.'})
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(OUT))
    main(ROOT / parser.parse_args().output)
