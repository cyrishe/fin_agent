# 2026 年 1—7 月分钟 K 本地压缩存储

原 `mk/none` 逐日 CSV 已整理为 `outputs/stock_automl/mk_1m_compact/YYYY-MM-DD.csv.zst`。覆盖 **2026-01-05 至 2026-07-31 的 139 个交易日**，保留原始 CSV 全部八列和每一行，按交易日独立无损压缩；pandas 可直接 `pd.read_csv(path)`，无需先解压整批数据。

| 项目 | 字节数 |
| --- | ---: |
| 原始 CSV 合计 | 12,204,993,302 |
| 压缩后合计 | 2,324,574,669 |
| 减少占用 | **9,880,418,633** |

转换脚本逐日读取原 [1—4 月](source_manifests/jan_apr.json)和[5—7 月](source_manifests/may_jul.json)的 SHA-256 清单，压缩后实际解压计算 SHA-256，确认与原清单一致才删除该日 CSV。原始清单现已一并提交，避免迁移后链接失效。逐日原始与压缩后的文件大小及 SHA-256 汇总在[压缩清单](compressed_manifest.json)。139/139 日全部通过，旧归档目录已无原始 `.csv`。1 月 5 日和 7 月 31 日还使用候选摘要函数直接读取 `.csv.zst`，股票行数分别为 5,171 和 5,197，与先前保存的摘要一致。

候选构建、20 日滚动训练、入场核查和分钟成交额脚本已直接支持 `.csv.zst`；默认读取新目录。已有 `minute_daily_snapshots.csv.gz` 和 `trainable_candidates.csv.gz` 等小型衍生样本继续保留，重复训练通常无需扫描原始逐分钟文件。

重跑转换：`python -m scripts.compact_mk_minute_archive`。脚本可续跑，并会重新校验已有压缩文件。2026 年这 139 个压缩文件现由 Git LFS 跟踪，位置仍是 `outputs/stock_automl/mk_1m_compact/`；同级其他运行输出继续被 Git 忽略。新机器克隆后需安装并运行 `git lfs install`、`git lfs pull`，再按本目录清单核对文件。2025 年分钟 K 未上传。
