"""Create and read back the immutable local CO15 research package."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from scripts.archive_automl_training import sha256_file, verify_package
from scripts.research_b1_frozen_rsi_timing_co15 import (
    EOD, EARLY, LATE, MARKET, OUT, REPLAY, REPORT, ROOT,
)

FOLDER = '20261017_CO15_最高收益B1冻结名单_RSI时机'
# The external volume creates AppleDouble sidecars when SHA256SUMS is written.
# Keep the immutable package on the local filesystem and preserve research outputs on ext.
LOCAL_PACKAGE_OUT = Path('/Users/cyrcyrisis/work/fin_agent/outputs/stock_automl/b1_frozen_rsi_timing_co15_20261017')
PACKAGE = LOCAL_PACKAGE_OUT / 'package' / FOLDER
ZIP = LOCAL_PACKAGE_OUT / 'CO15_B1冻结名单_RSI时机_20261017.zip'
FILES = [
    REPORT / name for name in ('DESIGN.md', 'README.md', 'summary.csv', 'monthly.csv',
                               'matched_random.csv', 'stress.csv', 'manifest.json', 'verification.json')
] + [
    OUT / 'frozen_decisions.csv', OUT / 'daily_replays.csv.gz',
    ROOT / 'scripts/research_b1_frozen_rsi_timing_co15.py',
    ROOT / 'scripts/verify_b1_frozen_rsi_timing_co15.py',
    ROOT / 'scripts/package_b1_frozen_rsi_timing_co15.py',
    ROOT / 'scripts/research_b1_market_gate_co14.py',
    REPLAY, EARLY, LATE, MARKET, EOD,
]


def main() -> None:
    if PACKAGE.exists() or ZIP.exists():
        raise FileExistsError('CO15 package is immutable; create a new experiment revision')
    PACKAGE.mkdir(parents=True)
    try:
        for source in FILES:
            assert source.is_file(), source
            target = PACKAGE / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        entries = sorted(p for p in PACKAGE.rglob('*') if p.is_file())
        (PACKAGE / 'SHA256SUMS').write_text(''.join(
            f'{sha256_file(p)}  {p.relative_to(PACKAGE)}\n' for p in entries))
        manifest_sha, count = verify_package(PACKAGE)
        with zipfile.ZipFile(ZIP, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(PACKAGE.rglob('*')):
                if p.is_file():
                    archive.write(p, arcname=str(p.relative_to(PACKAGE)))
        with zipfile.ZipFile(ZIP) as archive:
            assert len(archive.namelist()) == count + 1
            for member in archive.infolist():
                assert sha256_file(PACKAGE / member.filename) == __import__('hashlib').sha256(archive.read(member)).hexdigest()
        receipt = {'experiment_id': 'CO15_20261017_B1_frozen_RSI_timing',
                   'folder': FOLDER, 'local_package': str(PACKAGE), 'zip_path': str(ZIP),
                   'zip_bytes': ZIP.stat().st_size, 'zip_sha256': sha256_file(ZIP),
                   'manifest_sha256': manifest_sha, 'files': count,
                   'all_zip_members_readback': True, 'new_models': 0,
                   'cloud_path': f'策略训练/收盘开盘_20261014/02_联合目标与尾部风险/{FOLDER}',
                   'cloud_verified_at': None}
        (REPORT / 'archive_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    except Exception:
        if PACKAGE.exists():
            shutil.rmtree(PACKAGE)
        if ZIP.exists():
            ZIP.unlink()
        raise


if __name__ == '__main__':
    main()
