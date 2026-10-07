-- 只读查看首批样本。需连接 kingdomai；批次 notice_pilot_20261002_v1。

-- 1. 每类公告数量。65份为有意分层样本，不用于推算全库分布。
SELECT business_category, COUNT(*) AS documents
FROM notice_documents WHERE batch_id='notice_pilot_20261002_v1'
GROUP BY business_category ORDER BY business_category;

-- 2. 比亚迪、江淮8月产销量：保留产品范围，不将分项和总量相加。
SELECT d.issuer_name, f.metric_name, f.scope, f.value_num, f.unit_raw,
       f.period_start, f.period_end, f.basis
FROM notice_documents d JOIN notice_metric_facts f USING(document_version_id)
WHERE d.batch_id='notice_pilot_20261002_v1'
AND d.source_record_id IN ('96262453','96437179')
AND f.period_start='2026-08-01' AND f.period_end='2026-08-31'
ORDER BY d.source_record_id, f.fact_no;

-- 3. 中芯晶圆销量、ASP：保留8吋等效与美元口径。
SELECT f.metric_name, f.value_num, f.unit_raw, f.currency,
       f.scope, f.period_start, f.period_end, f.basis
FROM notice_documents d JOIN notice_metric_facts f USING(document_version_id)
WHERE d.batch_id='notice_pilot_20261002_v1' AND d.source_record_id='96314940'
AND f.metric_name IN ('晶圆销量','晶圆平均售价');

-- 4. 振华重工A/B股关键日期；日期来自已核对PDF的命名日期集合。
SELECT d.issuer_name, j.date_name, j.date_value, j.date_scope, j.note
FROM notice_documents d JOIN notice_events e USING(document_version_id)
JOIN JSON_TABLE(e.key_dates, '$[*]' COLUMNS(
 date_name VARCHAR(128) PATH '$.name', date_value DATE PATH '$.date',
 date_scope VARCHAR(255) PATH '$.scope', note TEXT PATH '$.note'
)) AS j
WHERE d.batch_id='notice_pilot_20261002_v1' AND d.source_record_id='96882306';

-- 5. 担保合同金额、实际余额与包含未使用额度的总额分别查看。
SELECT e.event_title, f.metric_name, f.subject_name, f.scope,
       f.value_num, f.unit_raw, f.value_type, f.basis
FROM notice_documents d JOIN notice_metric_facts f USING(document_version_id)
LEFT JOIN notice_events e ON e.event_id=f.event_id
WHERE d.batch_id='notice_pilot_20261002_v1' AND d.source_record_id='96882593'
ORDER BY f.fact_no;

-- 6. 天顺扣非更正前后：不能SUM，同一期间旧值和新值共存。
SELECT f.metric_name, f.value_num, f.unit_raw, f.period_start, f.period_end, f.basis
FROM notice_documents d JOIN notice_metric_facts f USING(document_version_id)
WHERE d.batch_id='notice_pilot_20261002_v1' AND d.source_record_id='96539728'
AND f.metric_name='扣非归母净利润' ORDER BY f.fact_no;

-- 7. 通过事项参与方角色辨认奥尼GPU合同的交易方向。
SELECT d.issuer_name, e.event_title, p.name_raw, p.role, p.role_description
FROM notice_documents d JOIN notice_events e USING(document_version_id)
JOIN notice_participants p USING(event_id)
WHERE d.batch_id='notice_pilot_20261002_v1' AND d.source_record_id='96882234'
ORDER BY p.participant_no;

-- 8. 补助获得金额与预计收益分开；依据原称，不强制都映射归母净利润。
SELECT d.issuer_name, f.subject_name, f.metric_name, f.value_num,
       f.unit_raw, f.value_type, f.period_label, f.basis
FROM notice_documents d JOIN notice_metric_facts f USING(document_version_id)
WHERE d.batch_id='notice_pilot_20261002_v1'
AND d.source_record_id IN ('96882594','96881651','96882618')
ORDER BY d.source_record_id,f.fact_no;

-- 9. 同一公告多个诉讼各自存储，保留程序进展和责任主体。
SELECT e.event_no, e.event_title, e.event_summary,
       f.metric_name, f.subject_name, f.value_num, f.unit_raw, f.basis
FROM notice_documents d JOIN notice_events e USING(document_version_id)
LEFT JOIN notice_metric_facts f ON f.event_id=e.event_id
WHERE d.batch_id='notice_pilot_20261002_v1' AND d.source_record_id='96877545'
ORDER BY e.event_no,f.fact_no;

-- 10. 投关活动中的产能利用率指引，区间不取中点冒充实际。
SELECT d.issuer_name, f.subject_name, f.metric_name, f.value_raw,
       f.value_num, f.value_lower, f.value_upper, f.unit_raw, f.value_type,
       f.period_start, f.period_end, f.basis
FROM notice_documents d JOIN notice_metric_facts f USING(document_version_id)
WHERE d.batch_id='notice_pilot_20261002_v1' AND d.source_record_id='96851555'
AND f.metric_name='预计产能利用率';
