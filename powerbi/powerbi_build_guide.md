# Build the VENDEVAL Power BI report (about 60 minutes)

Everything is prepared in this folder:

| File | What it is |
|---|---|
| `VENDEVAL_PowerBI_Data.xlsx` | The data model: 10 clean tables, one per sheet (regenerate with `python powerbi/build_powerbi_model.py`) |
| `vendeval_theme.json` | Colors: status ramp light → dark blue, so the lifecycle reads in order |
| `measures.dax` | All 23 DAX measures, ready to paste |
| `../reports/weekly_dashboard.html` | What the finished page should look like |

You need **Power BI Desktop** (free, Windows only: Microsoft Store → "Power BI Desktop").
On a Mac, use a CUNY lab PC or a Windows virtual machine.

---

## Step 1 - Load the data (5 min)
1. Open Power BI Desktop → **Blank report**.
2. **Home → Get data → Excel workbook** → pick `VENDEVAL_PowerBI_Data.xlsx`.
3. Tick **all 10 sheets** → **Load** (not Transform; the data is already clean).
4. **View → Themes → Browse for themes** → `vendeval_theme.json`.

## Step 2 - Check data types (5 min)
Go to **Table view** (grid icon on the left). For each column below, select it and set
**Column tools → Data type**:

| Table | Column | Type |
|---|---|---|
| Evaluations | period_end, due_date, last_updated | Date |
| StatusHistory | changed_date | Date |
| Calendar | date, week_ending | Date |
| ReportInfo | as_of | Date |
| Contracts | award_value | Fixed decimal (Format: Currency) |

**Sort the status in lifecycle order (important):** select `Evaluations[status]` →
**Column tools → Sort by column → status_order**. Without this, charts sort alphabetically
and "Approved" comes first.

## Step 3 - Relationships (10 min)
Go to **Model view** (third icon on the left). Delete any relationships Power BI guessed
(right-click → Delete), then drag to create exactly these. All are **Many-to-one (*:1),
Single** cross-filter direction:

| From (many side) | To (one side) |
|---|---|
| Evaluations[contract_no] | Contracts[contract_no] |
| Contracts[vendor_id] | Vendors[vendor_id] |
| WorkQueue[eval_id] | Evaluations[eval_id] |
| WorkQueue[assigned_to] | Evaluators[evaluator_id] |
| StatusHistory[eval_id] | Evaluations[eval_id] |
| StatusHistory[changed_date] | Calendar[date] |

Leave QAChecks, DataIssues and ReportInfo unconnected. They are standalone.
Then select the `Calendar` table → **Table tools → Mark as date table** → column `date`.

> Interview point: there is deliberately **no** relationship from Contracts to Evaluators.
> WorkQueue already links Evaluators to Evaluations, so a second path would make the model
> ambiguous. That's why `pm_name` is stored as a text column on Contracts.

## Step 4 - Measures (10 min)
1. Make a home for the measures: **Home → Enter data** → name the table `_Measures` → **Load**.
2. Select `_Measures`, then **Home → New measure**. Paste one measure from `measures.dax`,
   press Enter, and repeat for all of them.
3. Format: `% Complete` and `QA Pass Rate` → **Measure tools → Percentage, 0 decimals**.
   `Weeks to Clear Backlog` → Decimal, 0 places.

**Check your numbers.** Make a quick Card for each and compare:

| Measure | Should show |
|---|---|
| Total Evaluations | 2,252 |
| Approved / % Complete | 1,159 / 51% |
| Open with PMs | 870 |
| Awaiting Approval | 223 |
| Overdue with PMs | 771 |
| 180+ Days Overdue | 683 |
| Avg Days Overdue | ~673 |
| Open Items / Stalled / Reassigned | 1,093 / 834 / 134 |
| Approved This Week / Last Week | 4 / 7 |
| Vendors Bidding with Open Evals | 20 |
| Weeks to Clear Backlog | ~182 |
| QA Pass Rate | 100% |

If one is off, the usual cause is a missing relationship or a wrong data type in Step 2/3.

## Step 5 - Page 1 "Weekly Status" (20 min)
Canvas: **Format page → Canvas settings → 16:9**. Build top to bottom:

1. **Title.** Text box: "VENDEVAL Weekly Status — Supplier Relations", 20pt bold.
   Below it, a **Card (new)** with `Report Subtitle`, no category label, 11pt grey.
2. **QA banner.** Card with `QA Status Text`. **Format → Callout value → Color → fx →
   Rules**: based on `QA Pass Rate`; if = 1 then green `#006300`, else red `#d03b3b`.
3. **KPI row.** Six cards side by side, same size (select all → Format → Align → Distribute horizontally):
   `Total Evaluations` · `% Complete` · `Open with PMs` · `180+ Days Overdue` ·
   `Approved This Week` · `Vendors Bidding with Open Evals`.
   For `Approved This Week`, add `WoW Label` as the reference label (Card new → Reference labels).
4. **Status by contract year.** Visual: **100% stacked bar chart**.
   Y-axis `Contracts[contract_year]` · Legend `Evaluations[status]` · X-axis `Total Evaluations`.
   Data labels on. Title "Evaluation status by contract year".
   Check the legend runs Not Started → Approved, light to dark. If not, redo the sort in Step 2.
5. **Approvals per week.** Visual: **Clustered column chart**.
   X-axis `Calendar[week_ending]` (right-click the field in the well → choose **week_ending**,
   not Date Hierarchy) · Y-axis `Approvals`.
   **Filters pane → this visual → Calendar[weeks_ago] → Advanced → is greater than or equal to 0
   AND is less than or equal to 11** (last 12 weeks).
6. **Days overdue.** Clustered column chart: X-axis `WorkQueue[tier_label]`, Y-axis `Open Items`,
   visual filter `WorkQueue[lane] = PM outreach`. Title "Outstanding evaluations by days overdue".
7. **Slicers.** One row across the top: `Contracts[department]`, `Contracts[contract_year]`,
   `Contracts[contract_type]` (Slicer → Format → Style: Dropdown).

## Step 6 - Page 2 "Outreach Queue" (10 min)
1. **Table** visual: `WorkQueue[priority_rank]`, `WorkQueue[eval_id]`, `Vendors[vendor_name]`,
   `WorkQueue[status]`, `WorkQueue[days_overdue]`, `Evaluators[evaluator_name]`,
   `WorkQueue[escalation_tier]`, `WorkQueue[vendor_bidding]`, `WorkQueue[stalled]`, `WorkQueue[reassigned]`.
   Set every numeric column to **Don't summarize** (click the field's dropdown in the well).
   Sort by priority_rank ascending.
2. **Conditional formatting** on days_overdue: field dropdown → **Conditional formatting →
   Background color → Rules**: ≥ 180 → `#F4C7C7`; ≥ 30 and < 180 → `#FDE7B0`.
3. Add a **Bar chart** of `Evaluators[evaluator_name]` by `Open Items` (top 15: Filters → Top N),
   and put a constant line at 40 (Analytics pane → Constant line). That line is the workload cap.

## Step 7 - Page 3 "Data Quality" (5 min)
- **Table** of `QAChecks` (result, check, detail), with conditional font color on result.
- **Bar chart**: `DataIssues[issue]` by Sum of `count`, legend `DataIssues[source]`.
- Card: `QA Pass Rate`.

## Step 8 - Save and screenshot
1. **File → Save as** → `powerbi/VENDEVAL_Weekly_Status.pbix`.
2. Screenshot each page (Windows: `Win + Shift + S`) into `docs/`, named
   `powerbi_page1.png`, `powerbi_page2.png`, `powerbi_page3.png`.
3. Push:
   ```
   git add .
   git commit -m "Add Power BI report: weekly status, outreach queue, data quality"
   git push
   ```

## What to say about it in the interview
- **Model:** a star schema. Evaluations is the fact table, and Contracts, Vendors and Evaluators
  are dimensions. Relationships are single-direction, and the Calendar uses Friday-ending weeks
  to match the weekly report.
- **Report date:** "this week" is based on a report date, not `TODAY()`, so last Friday's
  report shows the same numbers if leadership reopens it on Monday.
- **The headline insight:** at the current pace of about 6 approvals a week, the open backlog
  takes about **182 weeks** to clear. That's the case for automating outreach and escalation
  rather than chasing evaluations by hand.
