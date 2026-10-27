"""Export the pipeline results as a Power BI-ready star schema.

    python powerbi/build_powerbi_model.py

Writes powerbi/VENDEVAL_PowerBI_Data.xlsx - one sheet per table, already typed and
shaped so Power BI needs no Power Query cleanup. Run run_pipeline.py first.
"""
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from config import DB_PATH, STATUS_ORDER  # noqa: E402

OUT = ROOT / "powerbi" / "VENDEVAL_PowerBI_Data.xlsx"
TIER_LABEL = {0: "0 · Not overdue", 1: "1 · 1-29 days", 2: "2 · 30-89 days",
              3: "3 · 90-179 days", 4: "4 · 180+ days"}


def main():
    con = sqlite3.connect(DB_PATH)
    q = lambda sql: pd.read_sql_query(sql, con)
    as_of = pd.Timestamp(q("SELECT MAX(as_of) AS d FROM work_queue").d.iloc[0])

    ev = q("SELECT * FROM evaluations")
    ev["period_end"] = pd.to_datetime(ev.period_end)
    ev["due_date"] = ev.period_end + pd.Timedelta(days=30)
    ev["status_order"] = ev.status.map({s: i + 1 for i, s in enumerate(STATUS_ORDER)})
    ev["last_updated"] = pd.to_datetime(ev.last_updated).dt.normalize()
    evaluations = ev[["eval_id", "contract_no", "eval_type", "period_end", "due_date", "status",
                      "status_order", "last_updated", "overall_score", "rating_quality", "rating_timeliness",
                      "rating_safety", "rating_compliance", "rating_management", "source_systems"]]

    contracts = q("""SELECT c.contract_no, c.vendor_id, c.department, c.contract_type, c.contract_year,
                            c.award_value, c.start_date, c.end_date,
                            COALESCE(e.first_name || ' ' || e.last_name, '(no PM)') AS pm_name
                     FROM contracts c LEFT JOIN evaluators e ON e.evaluator_id = c.pm_id""")
    for c in ("start_date", "end_date"):
        contracts[c] = pd.to_datetime(contracts[c])

    vendors = q("SELECT * FROM vendors")
    evaluators = q("""SELECT evaluator_id, first_name || ' ' || last_name AS evaluator_name, department, role,
                             CASE active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS status
                      FROM evaluators""")

    wq = q("SELECT * FROM work_queue")
    wq = wq.merge(ev[["eval_id", "status"]], on="eval_id").merge(contracts[["contract_no", "vendor_id"]],
               left_on=wq.eval_id.map(dict(zip(ev.eval_id, ev.contract_no))), right_on="contract_no")
    wq["tier_label"] = wq.escalation_tier.map(TIER_LABEL)
    wq["lane"] = wq.status.map(lambda s: "Supplier Relations approval" if s == "Submitted" else "PM outreach")
    wq["stalled"] = wq.stalled.map({1: "Yes", 0: "No"})
    wq["vendor_bidding"] = wq.vendor_bidding.map({1: "Yes", 0: "No"})
    wq["reassigned"] = wq.assignment_reason.map(lambda r: "Yes" if ("reassigned" in r or "supervisor" in r) else "No")
    work_queue = wq[["eval_id", "status", "lane", "vendor_id", "assigned_to", "assignment_reason", "reassigned",
                     "days_overdue", "sla_state", "escalation_tier", "tier_label", "stalled",
                     "days_since_update", "vendor_bidding", "priority_score", "priority_rank"]]

    hist = q("SELECT eval_id, status, changed_at FROM status_history")
    hist["changed_date"] = pd.to_datetime(hist.changed_at).dt.normalize()
    status_history = hist[["eval_id", "status", "changed_date"]]

    # Calendar: Friday-ending report weeks, so a "week" matches the weekly report
    days = pd.date_range("2022-01-01", "2027-12-31", freq="D")
    cal = pd.DataFrame({"date": days})
    cal["week_ending"] = cal.date + pd.to_timedelta((4 - cal.date.dt.weekday) % 7, unit="D")
    cal["week_label"] = cal.week_ending.dt.strftime("%m/%d")
    cal["weeks_ago"] = ((as_of - cal.week_ending).dt.days // 7).astype(int)
    cal["year"] = cal.date.dt.year
    cal["month"] = cal.date.dt.strftime("%Y-%m")

    qa_path = ROOT / "reports" / "VENDEVAL_Weekly_Report.xlsx"
    checks = pd.read_excel(qa_path, sheet_name="QA Checks")
    checks["result"] = checks.passed.map({True: "Pass", False: "Fail"})

    issues = q("SELECT source, issue, action FROM data_issues")
    issues["issue"] = (issues.issue.str.replace(r"\s*\(score [0-9.]+\)", "", regex=True)
                       .str.replace(r"sources disagree: .*", "sources disagree on status", regex=True))
    issue_summary = issues.groupby(["source", "issue"]).size().reset_index(name="count")

    report_info = pd.DataFrame({"as_of": [as_of], "report_title": ["VENDEVAL Weekly Status"]})

    sheets = {"Evaluations": evaluations, "Contracts": contracts, "Vendors": vendors,
              "Evaluators": evaluators, "WorkQueue": work_queue, "StatusHistory": status_history,
              "Calendar": cal, "QAChecks": checks[["result", "check", "detail"]],
              "DataIssues": issue_summary, "ReportInfo": report_info}
    with pd.ExcelWriter(OUT, engine="openpyxl", datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
    print(f"Wrote {OUT.relative_to(ROOT)}: " + ", ".join(f"{k} ({len(v):,})" for k, v in sheets.items()))


if __name__ == "__main__":
    main()
