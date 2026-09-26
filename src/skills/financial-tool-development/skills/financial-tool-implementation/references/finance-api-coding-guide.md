# 金融 API Catalog 索引

1. 从需求和设计识别数据主题，例如个股行情、指数、资金流、财务、估值、成分股或板块。
2. 读取 `api_catalog/index.json`，定位 subject。
3. 读取 `api_catalog/subjects/<subject>/index.json`，定位 dataview。
4. 只读取需要的 `api_catalog/subjects/<subject>/<dataview>.json`。
5. 在该文件中查看：
   - `fields`：真实可查询字段；
   - `rules`：该 dataview 的数据口径；
   - `methods`：具体 API 的用途、调用、参数、返回规则和示例；
   - `kd`：动态 K 日 API 可用的字段—方法组合。

可以先用 `rg` 搜索 API 名、字段名或业务关键词，再打开命中的 dataview 文件。不要一次读取全部 Catalog。

## 动态方法

`methods[].name` 可能是固定名称，也可能是包含 `<field>`、`<method>` 的名称模板。动态 API 的真实可用组合记录在：

```text
methods[].available_names.field_methods
```

只使用资料中明确存在的字段和方法。具体数据口径，包括 `stock.quote` 的日线、分钟线和实时模式，以对应 dataview 文件为准。
