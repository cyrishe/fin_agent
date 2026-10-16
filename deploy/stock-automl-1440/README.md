# 14:40 rolling seven-factor deployment package

This package runs the registered rolling-20, seven-factor classifier with
scikit-learn 1.7.1. It is a new daily fit. Historical B1 was reproduced for
February–July; recent live inputs have not yet been proven identical to B1.

The package is built from the exact script dependency graph and SQL schema.
It contains no `.env` or credentials. Use a Python 3.12 Linux x86_64 venv,
install `requirements-linux-py312.txt`, and check the four runtime versions
before scheduling. The wrapper reads the existing server `.env` via
`AUTOML1440_ENV_FILE` and uses `AUTOML1440_USER_ID=imac` by default.
`./install_runtime.sh` performs this installation as the current user;
its installed `python/` and `.venv/` directories can be tarred as the
machine-specific runtime bundle.
When `wheelhouse/` is present, the dependency step installs entirely from
those bundled Linux wheels.

`automl1440.sh prepare --date YYYY-MM-DD` first audits/backfills the prior
trading day's minute bars, then fits and caches today's model. `run` captures
the quote within 14:40:00–14:40:59. With `--write-db`, it writes the legacy
selection rows, one `automl_strategy_run` row (including zero-pick days), and
the shared `automl_strategy_execution` rows in one
transaction, and fills prior actionable rows from the next trading day's
finalized 09:40 minute close. Missing or ambiguous price bars remain pending
for a later run. The execution row labels both prices as market proxies; it
does not assert a broker fill, fees, or slippage. Strategies that omit
`actionable` use the shared default `1`; this strategy explicitly marks its
Top2 and turnover-gated Top1 as `0`.
The wrapper uses `flock` and needs no sudo. It never places orders.

Suggested user crontab entries (server timezone Asia/Shanghai):

```
50 13 * * 1-5 /home/che/cyris/fin_agent_deploy/automl1440-171-20261015/automl1440.sh prepare >> /home/che/cyris/fin_agent_deploy/automl1440-171-20261015/logs/prepare.log 2>&1
40 14 * * 1-5 /home/che/cyris/fin_agent_deploy/automl1440-171-20261015/automl1440.sh run --write-db >> /home/che/cyris/fin_agent_deploy/automl1440-171-20261015/logs/run.log 2>&1
```

Check `outputs/stock_automl/live_1440/YYYY-MM-DD_decision.json`, the job
logs, and `kingdomai.automl_strategy_selection` after each run. Do not claim
a B1 continuation until the current data lineage and daily Top1 have been
reproduced against the frozen baseline.

The reviewed rollout adds `automl_strategy_run` and
`automl_strategy_execution` with `init-db` before
deploying this version of the runner. `backfill-execution --write-db` imports
the existing active strategy's selection rows and fills matured price proxies
without changing historical selections. Both commands change the production
database and require the repository's human Gate. Keep the current release and
cron entries until migration, package verification, and rollback preparation
are approved. The new table is keyed by user, strategy, signal date, and rank;
each future strategy supplies its own model environment, actionable decision,
entry observation, and exit rule through the shared execution writer.
