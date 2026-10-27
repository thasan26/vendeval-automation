-- Analyst queries used for the weekly review. Each answers one business question.

-- Q1. Which vendors are bidding on new work but have evaluations still outstanding?
--     (A responsibility determination can't be finished without them.)
SELECT s.solicitation_no, s.bid_due_date, d.vendor_name,
       COUNT(*) AS open_evals, MAX(q.days_overdue) AS oldest_overdue_days
FROM solicitations s
JOIN vw_evaluation_detail d ON d.vendor_id = s.vendor_id
JOIN work_queue q           ON q.eval_id = d.eval_id
GROUP BY s.solicitation_no
ORDER BY s.bid_due_date;

-- Q2. Which departments own the oldest backlog?
SELECT d.department,
       COUNT(*)                                  AS open_evals,
       SUM(q.days_overdue > 180)                 AS over_180_days,
       ROUND(AVG(q.days_overdue), 0)             AS avg_days_overdue
FROM work_queue q
JOIN vw_evaluation_detail d ON d.eval_id = q.eval_id
GROUP BY d.department
ORDER BY over_180_days DESC;

-- Q3. Evaluations whose PM has left: who now owns them?
SELECT q.eval_id, d.pm_name AS original_pm, q.assigned_to, q.assignment_reason
FROM work_queue q
JOIN vw_evaluation_detail d ON d.eval_id = q.eval_id
WHERE q.assignment_reason <> 'contract PM';

-- Q4. Data quality: what did cleanup fix, by source?
SELECT source, issue, COUNT(*) AS n
FROM data_issues
GROUP BY source, issue
ORDER BY n DESC;

-- Q5. Duplicate check (must return zero rows): one evaluation per contract period
SELECT contract_no, period_end, COUNT(*) AS n
FROM evaluations
GROUP BY contract_no, period_end
HAVING COUNT(*) > 1;

-- Q6. Average evaluation cycle time (Requested -> Approved), by department
SELECT d.department,
       ROUND(AVG(julianday(a.changed_at) - julianday(r.changed_at)), 1) AS avg_days_to_approve,
       COUNT(*) AS approved_evals
FROM status_history r
JOIN status_history a ON a.eval_id = r.eval_id AND a.status = 'Approved'
JOIN vw_evaluation_detail d ON d.eval_id = r.eval_id
WHERE r.status = 'Requested'
GROUP BY d.department
ORDER BY avg_days_to_approve DESC;
