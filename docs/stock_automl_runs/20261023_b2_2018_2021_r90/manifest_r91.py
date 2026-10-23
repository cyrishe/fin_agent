"""Hash the read-only R91 research inputs and saved ablation models."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT=Path('/data/cyris/work/b2_2016_2020_r90_20261023')
OUT=ROOT/'audit_r91'
ASSETS=Path('/data/cyris/fin_agent_deploy/b2-20261015/assets/months')
RELEASE=Path('/data/cyris/fin_agent_deploy/b2-v2-20261021/scripts')


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):
            h.update(part)
    return h.hexdigest()


models=[]
for month in [str(m) for m in __import__('pandas').period_range('2018-01','2021-12',freq='M')]:
    base=ROOT/'daily2'/month
    model=OUT/'absolute_ablation'/month/'no_absolute.joblib'
    models.append(dict(month=month,kind='early_absolute_ablation',model_sha256=sha(model),
                       training_sha256=sha(base/'training_samples.pkl'),
                       parent_model_sha256=sha(base/'segments.joblib')))
for month in [f'2026-{m:02}' for m in range(4,10)]:
    base=ASSETS/month
    model=OUT/'absolute_ablation_late'/month/'no_absolute.joblib'
    models.append(dict(month=month,kind='late_absolute_ablation',model_sha256=sha(model),
                       training_sha256=sha(base/'training.pkl'),
                       parent_model_sha256=sha(base/'model.joblib')))
data=dict(strategy_id='imac/B2_V2',definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',
          early_panel_sha256=sha(ROOT/'panel.pkl'),later_panel_sha256=sha(OUT/'later_panel.pkl'),
          code_sha256={x:sha(ROOT/x) for x in ['audit_r91.py','independent_r91.py','prefix_audit_r91.py',
                        'live_gate_compare_r91.py','ablate_absolute_r91.py','ablate_late_r91.py',
                        'calibration_r91.py','later_model_metrics_r91.py']},
          deployed_code_sha256={x:sha(RELEASE/x) for x in ['automl_b2.py','automl_b2_data.py','run_automl_b2.py','run_b2_monthly.py','b2_monthly_pause.py']},
          output_sha256={x:sha(OUT/x) for x in ['model_months.csv','model_years.csv','later_model_months.csv',
                         'later_model_years.csv','feature_drift.csv','later_feature_drift.csv','score_calibration.csv',
                         'independent_replay.json','prefix_sensitivity.csv','live_gate_compare.csv',
                         'absolute_ablation/monthly.csv','absolute_ablation/yearly.csv',
                         'absolute_ablation_late/monthly.csv']},
          early_model_count=48,late_model_count=6,models=models,
          storage='server research directory only; production DB and cloud archive not updated')
(OUT/'manifest_r91.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print('models',len(models),'manifest_sha256',sha(OUT/'manifest_r91.json'))
