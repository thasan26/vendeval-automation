# Power BI build guide - VENDEVAL Weekly Status

`reports/weekly_dashboard.html` is the reference layout. This is how to build the
same report in Power BI Desktop.

## 1. Get data
**Option A (simplest):** *Get Data → Excel* → `reports/VENDEVAL_Weekly_Report.xlsx`
(sheets: Work Queue, Evaluator Workload, Vendor Performance, QA Checks) plus
`data/processed/evaluations.csv`.
**Option B (production):** *Get Data → SharePoint Online List* → `VENDEVAL Evaluations`
and `VENDEVAL Audit Log`, scheduled refresh Fridays 8:00 AM (after Flow 3 runs).

## 2. Power Query steps (Evaluations)
1. Change types: `period_end` Date, `last_updated` Date/Time, ratings Whole Number.
2. Add column `DueDate = Date.AddDays([period_end], 30)`.
3. Add column `StatusOrder` (Conditional Column: Not Started=1 ... Approved=5) - sort `status` by it so visuals show the lifecycle in order.
4. Merge with Work Queue on `eval_id` (Left Outer) to bring in `assigned_name`, `escalation_tier`, `priority_rank`.

## 3. Model
- `Evaluations` (fact) ← `Contracts` (contract_no) ← `Vendors` (vendor_id); `Evaluators` → Work Queue (assigned_to).
- A `Date` table (`CALENDAR(DATE(2022,1,1), DATE(2027,12,31))`) related to `Evaluations[DueDate]`; mark as date table.
- One-to-many, single direction everywhere.

## 4. DAX measures
```DAX
Total Evaluations = COUNTROWS ( Evaluations )

Approved = CALCULATE ( [Total Evaluations], Evaluations[status] = "Approved" )

% Complete = DIVIDE ( [Approved], [Total Evaluations] )

Open with PMs =
CALCULATE ( [Total Evaluations],
    Evaluations[status] IN { "Not Started", "Requested", "In Progress" } )

Awaiting Approval = CALCULATE ( [Total Evaluations], Evaluations[status] = "Submitted" )

Days Overdue =                                   -- calculated column
VAR d = DATEDIFF ( Evaluations[DueDate], TODAY (), DAY )
RETURN IF ( Evaluations[status] <> "Approved" && d > 0, d, 0 )

Overdue = CALCULATE ( [Open with PMs], Evaluations[Days Overdue] > 0 )

180+ Days Overdue = CALCULATE ( [Open with PMs], Evaluations[Days Overdue] >= 180 )

Avg Days Overdue = AVERAGEX ( FILTER ( Evaluations, Evaluations[Days Overdue] > 0 ), Evaluations[Days Overdue] )

Approved This Week =
CALCULATE ( [Total Evaluations],
    StatusHistory[status] = "Approved",
    DATESINPERIOD ( 'Date'[Date], TODAY (), -7, DAY ) )

Approved Last Week =
CALCULATE ( [Total Evaluations],
    StatusHistory[status] = "Approved",
    DATESINPERIOD ( 'Date'[Date], TODAY () - 7, -7, DAY ) )

WoW Change = [Approved This Week] - [Approved Last Week]

Weeks to Clear Backlog =                         -- at the last 4 weeks' pace
VAR pace = DIVIDE ( CALCULATE ( [Total Evaluations], StatusHistory[status] = "Approved",
                     DATESINPERIOD ( 'Date'[Date], TODAY (), -28, DAY ) ), 4 )
RETURN DIVIDE ( [Open with PMs] + [Awaiting Approval], pace )

QA Checks Passing = DIVIDE ( COUNTROWS ( FILTER ( QAChecks, QAChecks[passed] = TRUE () ) ), COUNTROWS ( QAChecks ) )
```

## 5. Pages
1. **Weekly Status** - KPI cards (Total, % Complete, Open with PMs, 180+ Days Overdue, Approved This Week with WoW, Weeks to Clear Backlog); 100% stacked bar of status by contract year; column chart of approvals per week; column chart of open items by escalation tier. Slicers in one row on top: Department, Contract Year, Contract Type.
2. **Outreach Queue** - table sorted by priority rank with conditional formatting on Days Overdue (30-179 amber, 180+ red), filtered to Open; drill-through to evaluation detail.
3. **Evaluator Workload** - bar chart of open items per evaluator, with a constant line at the workload cap (40).
4. **Vendor Performance** - avg score by vendor, unsatisfactory evaluations, safety rating; used for responsibility reviews.
5. **Data Quality** - QA checks table + issues by source (shows leadership the numbers can be trusted).

Colors: lifecycle stages use one blue ramp light → dark (Not Started lightest, Approved darkest)
because status is ordered; red/amber are kept only for overdue warnings.
