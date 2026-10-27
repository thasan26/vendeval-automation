-- Reporting views. Power BI connects to these (or to the same queries in Access).

-- Every evaluation with its contract, vendor and evaluator context
CREATE VIEW vw_evaluation_detail AS
SELECT e.eval_id, e.contract_no, c.contract_year, c.department, c.contract_type, c.award_value,
       v.vendor_id, v.vendor_name, e.eval_type, e.period_end,
       date(e.period_end, '+30 days')                      AS due_date,
       e.status, e.last_updated, e.overall_score, e.source_systems, e.needs_manual_review,
       c.pm_id, ev.first_name || ' ' || ev.last_name        AS pm_name, ev.active AS pm_active
FROM evaluations e
JOIN contracts  c  ON c.contract_no = e.contract_no
JOIN vendors    v  ON v.vendor_id   = c.vendor_id
LEFT JOIN evaluators ev ON ev.evaluator_id = c.pm_id;

-- Backlog by contract year and status (the 2022-2024 outreach scope)
CREATE VIEW vw_backlog_by_year AS
SELECT contract_year,
       COUNT(*)                                                       AS total_evals,
       SUM(status = 'Approved')                                       AS approved,
       SUM(status = 'Submitted')                                      AS awaiting_approval,
       SUM(status IN ('Not Started','Requested','In Progress'))       AS outstanding,
       ROUND(100.0 * SUM(status = 'Approved') / COUNT(*), 1)          AS pct_complete
FROM vw_evaluation_detail
GROUP BY contract_year;

-- Open items per evaluator from this week's work queue
CREATE VIEW vw_evaluator_workload AS
SELECT q.assigned_to, ev.first_name || ' ' || ev.last_name AS evaluator, ev.department,
       COUNT(*)                         AS open_evals,
       SUM(q.days_overdue > 0)          AS overdue,
       SUM(q.escalation_tier >= 3)      AS at_director_level,
       MAX(q.days_overdue)              AS oldest_days_overdue
FROM work_queue q
JOIN evaluators ev ON ev.evaluator_id = q.assigned_to
GROUP BY q.assigned_to;

-- Weekly throughput: how many evaluations reached each status each week
CREATE VIEW vw_weekly_throughput AS
SELECT strftime('%Y-%W', changed_at) AS year_week,
       date(changed_at, 'weekday 5', '-6 days') AS week_start,
       SUM(status = 'Requested') AS requested,
       SUM(status = 'Submitted') AS submitted,
       SUM(status = 'Approved')  AS approved
FROM status_history
GROUP BY year_week;

-- Vendor performance from completed evaluations (feeds responsibility reviews)
CREATE VIEW vw_vendor_performance AS
SELECT v.vendor_id, v.vendor_name,
       COUNT(e.eval_id)                      AS completed_evals,
       ROUND(AVG(e.overall_score), 2)        AS avg_score,
       ROUND(AVG(e.rating_safety), 2)        AS avg_safety,
       MIN(e.overall_score)                  AS worst_score,
       SUM(e.overall_score < 2.5)            AS unsatisfactory_evals
FROM evaluations e
JOIN contracts c ON c.contract_no = e.contract_no
JOIN vendors   v ON v.vendor_id   = c.vendor_id
WHERE e.status IN ('Submitted','Approved') AND e.overall_score IS NOT NULL
GROUP BY v.vendor_id;
