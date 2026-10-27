"""End-to-end data-quality and reporting-integrity checks.

Run after every pipeline execution. A failed check stops the weekly report from
being published (same rule as the 'Validate before notify' step in Power Automate).
"""
from __future__ import annotations

import pandas as pd

from config import MAX_OPEN_PER_EVALUATOR, RAW, STATUS_ORDER

STATUS_RANK = {s: i for i, s in enumerate(STATUS_ORDER)}


def _check(results, name, passed, detail):
    results.append({"check": name, "passed": bool(passed), "detail": detail})


def run_checks(con, out: dict) -> pd.DataFrame:
    q = lambda sql: con.execute(sql).fetchone()[0]
    res: list[dict] = []
    st = out["stats"]

    # ---------------- reconciliation: every source row is accounted for
    rows_in = sum(st["source_rows"].values())
    issues = out["data_issues"]
    quarantined = (issues.action == "row quarantined").sum()
    dropped = (issues.issue == "duplicate of another row in same source").sum()
    _check(res, "Reconciliation: source rows = loaded + quarantined + duplicates",
           rows_in == st["records_after_dedupe"] + quarantined + dropped,
           f"{rows_in} in = {st['records_after_dedupe']} kept + {quarantined} quarantined + {dropped} duplicates")

    # ---------------- uniqueness + integrity (database enforces, we verify)
    _check(res, "One golden record per contract period",
           q("SELECT COUNT(*) FROM (SELECT 1 FROM evaluations GROUP BY contract_no, period_end HAVING COUNT(*)>1)") == 0,
           "0 duplicate contract periods")
    orphans = q("SELECT COUNT(*) FROM evaluations e LEFT JOIN contracts c USING(contract_no) WHERE c.contract_no IS NULL")
    _check(res, "Every evaluation links to a contract", orphans == 0, f"{orphans} orphan evaluations")
    _check(res, "Foreign keys valid across all tables",
           len(con.execute("PRAGMA foreign_key_check").fetchall()) == 0, "PRAGMA foreign_key_check clean")
    bad_rating = q("""SELECT COUNT(*) FROM evaluations WHERE
        rating_quality NOT BETWEEN 1 AND 5 OR rating_timeliness NOT BETWEEN 1 AND 5 OR
        rating_safety NOT BETWEEN 1 AND 5 OR rating_compliance NOT BETWEEN 1 AND 5 OR
        rating_management NOT BETWEEN 1 AND 5""")
    _check(res, "All ratings on the 1-5 scale", bad_rating == 0, f"{bad_rating} out-of-range ratings")
    no_score = q("SELECT COUNT(*) FROM evaluations WHERE status IN ('Submitted','Approved') AND overall_score IS NULL")
    _check(res, "Submitted/approved evaluations have a score", no_score == 0,
           f"{no_score} completed evaluations missing scores (returned to PM)")

    # ---------------- lineage
    sel = q("SELECT COUNT(*) FROM (SELECT eval_id FROM lineage GROUP BY eval_id HAVING SUM(was_selected) <> 1)")
    _check(res, "Each golden record has exactly one winning source row", sel == 0, f"{sel} violations")

    # ---------------- status history follows the lifecycle
    hist = pd.read_sql_query("SELECT eval_id, status, changed_at FROM status_history ORDER BY eval_id, changed_at", con)
    hist["rank"] = hist.status.map(STATUS_RANK)
    backwards = (hist.groupby("eval_id")["rank"].apply(lambda s: (s.diff().dropna() < 0).any())).sum()
    _check(res, "Status history moves forward through lifecycle", backwards == 0,
           f"{backwards} evaluations with out-of-order history")

    # ---------------- workflow / automation integrity
    open_n = q("SELECT COUNT(*) FROM evaluations WHERE status IN ('Not Started','Requested','In Progress','Submitted')")
    queued = q("SELECT COUNT(*) FROM work_queue")
    _check(res, "Every open evaluation is in the work queue", open_n == queued, f"{open_n} open, {queued} queued")
    inactive = q("""SELECT COUNT(*) FROM work_queue w JOIN evaluators e ON e.evaluator_id = w.assigned_to
                    WHERE e.active = 0""")
    _check(res, "No work routed to inactive evaluators", inactive == 0, f"{inactive} items")
    unassigned = q("SELECT COUNT(*) FROM work_queue WHERE assigned_to IS NULL")
    _check(res, "Every queued evaluation has an owner", unassigned == 0, f"{unassigned} unassigned")

    wq = out["work_queue"]
    notes = out["notifications"]
    notified = {(k, e) for k, ids in zip(notes.kind, notes.eval_ids) for e in ids.split(";")}
    esc = wq[wq.escalation_tier >= 2]
    missing_esc = [e for e, t in zip(esc.eval_id, esc.escalation_tier) if (f"escalation_tier_{t}", e) not in notified]
    _check(res, "Every tier 2+ escalation produces a notification", not missing_esc, f"{len(missing_esc)} missing")
    need_pm = wq[(wq.status != "Submitted") & (wq.sla_state != "On track")]
    pm_kinds = {"evaluation_request", "pm_reminder"}
    missing_pm = [e for e in need_pm.eval_id if not any((k, e) in notified for k in pm_kinds)]
    _check(res, "Every due/overdue item reaches its evaluator", not missing_pm, f"{len(missing_pm)} missing")
    per_rcpt = notes.groupby(["recipient_id", "kind"]).size().max() if len(notes) else 0
    _check(res, "Max one message per recipient per type (no spam)", per_rcpt <= 1, f"max {per_rcpt}")
    reassigned = wq[wq.assignment_reason.str.contains("reassigned")]
    over_cap = (wq[wq.assigned_to.isin(reassigned.assigned_to)].groupby("assigned_to").size() > MAX_OPEN_PER_EVALUATOR).sum()
    _check(res, "Reassignments respect the workload cap", over_cap == 0,
           f"{len(reassigned)} reassigned, {over_cap} evaluators over cap {MAX_OPEN_PER_EVALUATOR}")

    # ---------------- accuracy vs synthetic answer key (only possible with synthetic data)
    truth = pd.read_csv(RAW / "_ground_truth" / "evaluations_truth.csv", parse_dates=["period_end"])
    truth["period_end"] = truth.period_end.dt.date
    gold = out["evaluations"]
    m = gold.merge(truth[["contract_no", "period_end", "status"]], on=["contract_no", "period_end"],
                   suffixes=("", "_true"))
    lin = out["lineage"]
    wrong = m[m.status != m.status_true]
    recoverable = lin[lin.eval_id.isin(wrong.eval_id)].merge(wrong[["eval_id", "status_true"]], on="eval_id")
    recoverable = recoverable[recoverable.source_status == recoverable.status_true].eval_id.nunique()
    _check(res, "Golden record count matches answer key", len(gold) == len(truth),
           f"{len(gold)} golden vs {len(truth)} true evaluations")
    _check(res, "Merge picks the most current status available in any source", recoverable == 0,
           f"{len(m) - len(wrong)}/{len(m)} statuses exact; the other {len(wrong)} were stale in every "
           f"system ({recoverable} picked wrong) -> only fixable by PM outreach")
    d = st["deduped"]
    d = d[d.vendor_id.notna()].merge(out["contracts"][["contract_no", "vendor_id"]], on="contract_no",
                                     suffixes=("", "_true"))
    matched = (d.vendor_id == d.vendor_id_true).mean()
    _check(res, "Vendor name resolution accuracy >= 99%", matched >= 0.99,
           f"{matched:.2%} of {len(d)} vendor names resolved to the right vendor ID")
    return pd.DataFrame(res)
