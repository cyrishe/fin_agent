import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from scripts.package_finance_eval_client import build, FILES, ROOT


@pytest.mark.parametrize('launcher', ['bash', 'sh'])
@pytest.mark.parametrize('skills,tool,detail,status', [
    ('()', '', 'true', 0),
    ('("equity-report-analysis")', '', 'false', 0),
    ('("earnings-analysis" "valuation-analysis")', 'finance_task', 'true', 1),
    ('()', 'finance_data_query', 'true', 2),
])
def test_shell_preserves_arguments_spaces_defaults_and_exit_status(tmp_path, skills, tool, detail, status, launcher):
    client=tmp_path/'client with spaces'
    client.mkdir()
    capture=tmp_path/'args.json'
    python=client/'.eval-venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text(f'#!{sys.executable}\nimport json,os,sys\n'
                      'if sys.argv[1]=="-c": sys.exit(0)\n'
                      'open(os.environ["CAPTURE"],"w").write(json.dumps(sys.argv[1:]))\n'
                      'sys.exit(int(os.environ["STATUS"]))\n')
    python.chmod(0o755)
    text=(ROOT/'run_eval.sh').read_text()
    for before,after in [
        ('PYTHON_BIN=""',f'PYTHON_BIN="{python}"'),
        ('SKILLS=()\n',f'SKILLS={skills}\n'),
        ('TOOL=""\n',f'TOOL="{tool}"\n'),
        ('DETAIL=true ',f'DETAIL={detail} '),
        ('CASES_FILE="tests/evals/report_mcp_skill_smoke_v1.json"','CASES_FILE="my cases.json"'),
        ('OUTPUT_DIR="" ', 'OUTPUT_DIR="my results" '),
    ]:text=text.replace(before,after)
    (client/'run_eval.sh').write_text(text)
    (client/'token.json').write_text('temporary-secret-for-test')
    (client/'my cases.json').write_text('["问题"]')
    result=subprocess.run([launcher,str(client/'run_eval.sh')],cwd=tmp_path,
                          env={**os.environ,'CAPTURE':str(capture),'STATUS':str(status)},capture_output=True,text=True)
    assert result.returncode==status,result.stderr
    args=json.loads(capture.read_text())
    assert args[args.index('--cases-file')+1]=='my cases.json'
    assert args[args.index('--output-dir')+1]=='my results'
    assert args[args.index('--tool')+1]==(tool or 'finance_task')
    assert ('--detail' in args)==(detail=='true')
    if skills=='()':assert '--auto' in args and '--skill' not in args
    else:
        expected=['equity-report-analysis'] if 'equity' in skills else ['earnings-analysis','valuation-analysis']
        assert [args[i+1] for i,x in enumerate(args) if x=='--skill']==expected
        assert '--auto' not in args
    assert 'temporary-secret-for-test' not in result.stdout+result.stderr
    if status:assert '评测已完成' not in result.stdout


def test_distribution_contains_only_client_files_and_runs_without_repository(tmp_path):
    package=build(tmp_path/'client.zip')
    with zipfile.ZipFile(package) as z:
        assert set(z.namelist())=={'finance_eval_client/'+p for p in FILES.values()}
        assert not any('.env' in p or 'token.json' in p or p.endswith('access_tokens.py') for p in z.namelist())
        z.extractall(tmp_path/'unpacked')
    client=tmp_path/'unpacked/finance_eval_client'
    result=subprocess.run([sys.executable,str(client/'scripts/eval_finance_mcp.py'),'--help'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0 and '--skill' in result.stdout
    result=subprocess.run(['bash','-n',str(client/'run_eval.sh')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize("install_fails", [False, True])
def test_bootstrap_skips_old_python_and_installs_only_in_venv(tmp_path, install_fails):
    client = tmp_path / "new client"
    client.mkdir()
    (client / "run_eval.sh").write_text((ROOT / "run_eval.sh").read_text())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.jsonl"
    fake = f"#!{sys.executable}\n" + r'''import json, os, pathlib, sys
with open(os.environ['CALLS'], 'a') as f:
    f.write(json.dumps([sys.argv[0], sys.argv[1:]])+'\n')
args=sys.argv[1:]
if args[0]=='-c':
    if pathlib.Path(sys.argv[0]).name=='python3': sys.exit(1)
    if 'import httpx' in args[1]: sys.exit(1)
    sys.exit(0)
if args[:2]==['-m','venv']:
    dest=pathlib.Path(args[2])/'bin/python'
    dest.parent.mkdir(parents=True)
    dest.write_text(pathlib.Path(__file__).read_text())
    dest.chmod(0o755)
    sys.exit(0)
if args[:2]==['-m','pip']: sys.exit(int(os.environ['INSTALL_FAILS']))
sys.exit(9)
'''
    for name in ('python3', 'python3.12'):
        p=bin_dir/name
        p.write_text(fake)
        p.chmod(0o755)
    # Only fake Python candidates; real shell utilities remain available.
    for name in ('bash', 'dirname', 'basename'):
        import shutil
        (bin_dir/name).symlink_to(shutil.which(name))
    env={**os.environ, 'PATH':str(bin_dir), 'CALLS':str(log), 'INSTALL_FAILS':str(int(install_fails))}
    result=subprocess.run(['/bin/sh', str(client/'run_eval.sh'), '--setup-only'], env=env, capture_output=True, text=True)
    assert result.returncode == (2 if install_fails else 0), result.stderr
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    creation=[c for c in calls if c[1][:2]==['-m','venv']]
    assert len(creation)==1 and creation[0][0].endswith('python3.12')
    installs=[c for c in calls if c[1][:2]==['-m','pip']]
    assert len(installs)==1 and installs[0][0]==str(client/'.eval-venv/bin/python')
    assert not any('eval_finance_mcp.py' in str(c) for c in calls)
