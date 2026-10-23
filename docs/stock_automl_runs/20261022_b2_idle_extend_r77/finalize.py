from pathlib import Path
import hashlib,json
r=Path('/data/cyris/work/b2_idle_extend_r77_20261022')
payload=dict(experiment_id='B2_IDLE_D3_EXTEND_R77_20261022',parent_strategy_owner='imac',parent_strategy_id='B2_V2',parent_definition_sha256='54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00',record_kind='read_only_counterfactual_research',cohort='frozen_306_v2_buys_D0_2022-05_to_2026-09',intervention='on D2 no new buy in frozen v2 cohort, delay old holding from D2 close to D3 close',production_db_status='NOT_WRITTEN_REQUIRES_HUMAN_GATE',cloud_readback_status='PENDING',training='no_new_model')
(r/'registration_payload.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
skip={'manifest.json','quick.py','d3_bar_count.py'}
manifest={}
for p in sorted(r.rglob('*')):
    if not p.is_file() or p.name in skip:continue
    manifest[str(p.relative_to(r))]=dict(size=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
(r/'manifest.json').write_text(json.dumps({'experiment_id':payload['experiment_id'],'files':manifest},ensure_ascii=False,indent=2)+'\n')
print('manifest files',len(manifest))
