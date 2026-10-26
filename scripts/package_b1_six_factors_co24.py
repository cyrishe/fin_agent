"""Freeze CO24 research locally; cloud upload remains deferred by the user."""
from pathlib import Path
import argparse,json,shutil,tempfile,subprocess
from scripts.research_b1_six_factors_co24 import ROOT,OUT,REPORT,SOURCE
from scripts.archive_automl_training import LOCAL_ROOT,CLOUD_ROOT,sha256_file,verify_package,record_local_package

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source-commit',required=True);args=ap.parse_args()
 assert subprocess.check_output(['git','rev-parse',args.source_commit],cwd=ROOT,text=True).strip()==args.source_commit
 category='03_因子筛选_7至14';folder='20261026_CO24_原七加六_条件因子滚动对照';dest=LOCAL_ROOT/category/folder
 if dest.exists():raise ValueError('Immutable package already exists')
 src=Path(tempfile.mkdtemp(prefix='fin_agent_co24_'))
 sources=[p for p in OUT.rglob('*') if p.is_file() and not any(x.startswith('._') for x in p.parts)]
 sources += [p for p in REPORT.iterdir() if p.is_file() and not p.name.startswith('._') and p.name!='archive_receipt.json']
 sources += [ROOT/'scripts'/n for n in ['research_b1_six_factors_co24.py','analyze_b1_six_factors_co24.py','verify_b1_six_factors_co24.py','plot_b1_six_factors_co24.py','package_b1_six_factors_co24.py']]
 sources += [ROOT/'tests/experiments/test_b1_six_factors_co24.py',ROOT/'docs/stock_automl_runs/20261026_b1_douyin_factors_co23/SIX_FACTOR_DESIGN.md']
 for p in sources:
  target=src/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
 meta={'experiment_id':'CO24_20261026_B1_six_condition_factors','source_commit':args.source_commit,'parent_experiment':'CO7_20261015_B1_conditional_risk','parent_zip_sha256':'274f5b41c7e7a1dfe53868cb4de6105e06fe1b155feef3b640777fa5f2b004ae','parent_source':str(SOURCE),'parent_candidates_sha256':sha256_file(SOURCE/'b1_candidates.csv.gz'),'sklearn':'1.7.1','pandas':'2.3.2','numpy':'2.3.2','completed_on':'2026-10-27'}
 (src/'package_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
 (src/'README.md').write_text('''# CO24 六个条件因子与原七因子滚动对照\n\n包含完整实际训练面板（137,833行）、382日可复算因子的分钟路径/历史同钟点量均值/压力与波幅输入、362折成员名单、五组共1,810个模型、全候选概率、原成交额轮空后的逐日/逐月结果、代码和核验。具体口径、因子定义及限制见包内docs/stock_automl_runs/20261026_b1_six_factors_co24。没有因新因子缺失删原候选。\n\n训练每折只读取panel.csv.gz中membership.csv.gz指定的行，label为stage1_second_high/signal_price的原四类，特征列表见complete.json。模型和预测均已落盘回读。原模型验证及成交额使用父包CO7，身份和哈希见package_manifest.json。全部原始市场分钟/日线大档不重复复制，来源和逐文件哈希见build_manifest.json；feature_inputs内保存实际计算六因子所用的候选路径及历史基准，足以复算本轮因子。\n\n复现需恢复对应Git提交及依赖，再把本包目录覆盖到该检出；父包CO7按manifest位置恢复（换机器时配置脚本SOURCE/OLD路径）。运行python -m scripts.verify_b1_six_factors_co24复算输入；训练在新的OUT目录运行research_b1_six_factors_co24 train，原输出目录只用于已完成折回读，不覆盖冻结包。分析脚本依赖父包原成交额表。脚本默认使用本次记录的本地路径。\n\n模型只是研究对照，无生产替换。用户要求先保留本地，云端待同步，不能称云端已核验。\n''')
 all_files=sorted(p for p in src.rglob('*') if p.is_file())
 (src/'SHA256SUMS').write_text(''.join(f'{sha256_file(p)}  {p.relative_to(src)}\n' for p in all_files))
 expected=verify_package(src)
 dest.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.co24-stage-',dir=dest.parent) as t:
  stage=Path(t)/folder;shutil.copytree(src,stage)
  # Only remove automatically synthesized AppleDouble files in the new copy.
  for p in stage.rglob('._*'):
   if p.is_file():p.unlink()
  assert verify_package(stage)==expected
  stage.rename(dest)
 assert verify_package(dest)==expected
 record={'user_id':'imac','experiment_id':meta['experiment_id'],'experiment_date':'2026-10-26','title':'CO24 原七加六条件因子滚动对照','category_path':category,'local_path':str(dest),'cloud_path':'/'.join((CLOUD_ROOT,category,folder)),'source_commit':args.source_commit,'manifest_sha256':expected[0],'file_count':expected[1]}
 record_local_package(ROOT/'.env',record)
 save={**record,'local_check':'ok','db_recorded':True,'cloud_verified_at':None,'cloud_status':'待同步；用户要求先保留本地包','models':1810,'training_panel_rows':137833}
 (REPORT/'archive_receipt.json').write_text(json.dumps(save,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(save,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
