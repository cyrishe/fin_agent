"""Reproduction and source checks for the 2013-16 B2 extension."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_limit_rebound_segments_r18 import FEATURES
from scripts.replay_limit_rebound_cash_r23 import dump, sha
from scripts.research_b2_qdc_r108 import OUT, ROOT, r90
from scripts.research_b2_qdc_r108_risk import DOC

OLD = ROOT / "outputs/stock_automl/b2_daily_only_r100_20261023/r90"
KEY = ["symbol", "d0_date"]


def main() -> None:
    current = pd.read_pickle(OUT / "panel.pkl")
    prior = pd.read_pickle(OLD / "panel.pkl")
    a = current[current.d0_date.between("2016-06-01", "2016-12-31")]
    b = prior[prior.d0_date.between("2016-06-01", "2016-12-31")]
    assert set(map(tuple, a[KEY].to_numpy())) == set(map(tuple, b[KEY].to_numpy()))
    cols = KEY + ["rate", "d0_cap", "d1_cap", "d0_turnover_pct", "board_streak_days"] + FEATURES["segments"]
    cols = list(dict.fromkeys(cols))
    joined = a[cols].merge(b[cols], on=KEY, validate="one_to_one", suffixes=("_new", "_old"))
    differences = {}
    for col in cols[2:]:
        x, y = joined[f"{col}_new"], joined[f"{col}_old"]
        if pd.api.types.is_numeric_dtype(x):
            equal = np.isclose(x.to_numpy(float), y.to_numpy(float), atol=1e-8, rtol=0,
                               equal_nan=True)
        else:
            equal = x.fillna("<NA>").eq(y.fillna("<NA>")).to_numpy()
        count = int((~equal).sum())
        if count:
            differences[col] = count
    recent = pd.read_pickle(OUT / "daily2/2016-12/segments_forecast_scores.pkl")
    archived = pd.read_pickle(OLD / "daily2/2016-12/segments_forecast_scores.pkl")
    compare = recent.merge(archived, on=KEY, validate="one_to_one",
                           suffixes=("_new", "_old"))
    assert len(compare) == len(recent) == len(archived)
    score_diff = float((compare.score_new - compare.score_old).abs().max())
    rank_diff = int(compare.d0_rank_new.ne(compare.d0_rank_old).sum())
    top3_new = set(map(tuple, recent.loc[recent.d0_rank.le(3), KEY].to_numpy()))
    top3_old = set(map(tuple, archived.loc[archived.d0_rank.le(3), KEY].to_numpy()))
    top3_diff = len(top3_new.symmetric_difference(top3_old))
    calendar = json.loads((OUT / "calendar.json").read_text())
    source_files = []
    source_files.extend(sorted((OUT / "tushare_daily_pairs").glob("daily_????????_????????.csv.gz")))
    source_files.extend(sorted((OUT / "tushare_daily_pairs").glob("daily_basic_????????_????????.csv.gz")))
    source_files.extend(Path("/Volumes/ext/tushare_market_data") / day[:4] /
                        f"{day.replace('-', '')}.csv.gz" for day in calendar
                        if "2013-09-01" <= day <= "2017-01-31")
    for year in range(2013, 2018):
        source_files += [Path(f"/Volumes/ext/minite_k_data/日线/{year}.parquet"),
                         Path(f"/Volumes/ext/minite_k_data/1分钟/{year}.parquet")]
    assert all(path.exists() for path in source_files)
    hashes = pd.DataFrame([dict(path=str(path), bytes=path.stat().st_size,
                                sha256=sha(path)) for path in source_files])
    hashes.to_csv(DOC / "source_hashes.csv", index=False)
    receipt = dict(overlap_2016_jun_dec=len(joined), feature_mismatch_counts=differences,
                   december_2016_forecast_rows=len(compare), max_score_difference=score_diff,
                   rank_mismatch=rank_diff, top3_symmetric_difference=top3_diff,
                   daily_model_months=len(r90.MODELS),
                   full_test_months=len(r90.TEST), first_full_test=r90.TEST[0],
                   monthly_rows=len(pd.read_csv(DOC / "monthly.csv")),
                   first_bar_coverage={str(k): int(v) for k, v in
                     pd.read_csv(OUT / "first_bar_coverage.csv").has_0930.value_counts().items()},
                   source_files=len(hashes), source_hashes_sha256=sha(DOC / "source_hashes.csv"))
    dump(DOC / "verification.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
