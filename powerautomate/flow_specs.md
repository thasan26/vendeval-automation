# Power Automate build specs

These four cloud flows run the same rules as `src/workflow.py`. The Python version
exists so every rule is tested and audited before it is automated; the flows are how
the process runs day to day inside Microsoft 365 with no code to maintain.

**Where the data lives in M365**

| SharePoint list / file | Purpose | Key columns |
|---|---|---|
| `VENDEVAL Evaluations` (list) | Golden record, one item per evaluation | EvalID (unique), ContractNo, ContractYear, Department, VendorName, Status (choice), PeriodEnd, DueDate, AssignedTo (person), EscalationTier (number), LastStatusChange, LastReminderSent, NeedsReview (yes/no) |
| `VENDEVAL Config` (list) | Business rules, editable without touching flows | Key, Value - e.g. `StallDays_Requested=14`, `Tier2Days=30`, `Tier3Days=90`, `Tier4Days=180`, `MaxOpenPerEvaluator=40`, `SRDirectorEmail` |
| `VENDEVAL Audit Log` (list) | One row per automated decision | EvalID, Action, Detail, RunTime, FlowName |
| `Evaluations` (document library) | Signed evaluation PDFs | Foldered `/<ContractYear>/<Department>/<ContractNo>/` with EvalID metadata |

Status choice values must exactly match: `Not Started, Requested, In Progress, Submitted, Approved`.

---

## Flow 1 - Intake & standardize (automated)

**Trigger:** *When an item is created or modified* - `VENDEVAL Evaluations`
(trigger condition so it doesn't loop on its own updates):
```
@not(equals(triggerOutputs()?['body/Editor/Email'], 'svc-vendeval@yourdomain'))
```
1. **Compose - NormalizedContract**
   `toUpper(replace(replace(triggerOutputs()?['body/ContractNo'],' ',''),'-',''))`
   then rebuild as `C-YYYY-NNNNN` with `concat('C-', substring(outputs('Compose'),1,4), '-', substring(outputs('Compose'),5,5))`.
2. **Condition - valid format?** `length(outputs('Compose_-_Compact')) equals 10` and starts with `C`.
   *No* → set `NeedsReview = Yes`, log `invalid contract number`, terminate (Succeeded).
3. **Get items** - duplicate check: filter query
   `ContractNo eq '<normalized>' and PeriodEnd eq '<PeriodEnd>' and ID ne <current ID>`.
   If `length(body('Get_items')?['value']) > 0` → keep the item with the latest `LastStatusChange`,
   mark the other `Duplicate = Yes`, log `duplicate merged`.
4. **Compose - DueDate** `addDays(triggerOutputs()?['body/PeriodEnd'], 30, 'yyyy-MM-dd')`
5. **Update item** - write normalized ContractNo + DueDate.
6. **Create item** in Audit Log.

## Flow 2 - Routing & assignment (automated)

**Trigger:** item created, or `Status` changed (trigger condition on Status column change).
1. **Switch on Status**
   - `Submitted` → assign the Supplier Relations analyst with the fewest open `Submitted` items
     (*Get items* filtered `Status eq 'Submitted'`, then *Select* AssignedTo and count with
     `length(filter(...))` inside an *Apply to each* over the two analysts) → start **Flow 4**.
   - `Approved` → set `EscalationTier = 0`, move the PDF to the year/department folder, log.
   - otherwise → step 2.
2. **Get user profile (V2)** for the contract PM → *Condition* `accountEnabled is true`.
   - **True** → `AssignedTo = PM`, reason `contract PM`.
   - **False / PM empty** → *Get items* active PMs in the same Department; for each, count open
     items (`Status ne 'Approved'`), filter those under `MaxOpenPerEvaluator`, pick the minimum
     (*Select* → *Filter array* where count equals `min(...)` → `first()`). If none, assign the
     PM's manager (*Get manager (V2)*). Reason text goes to the Audit Log.
3. **Update item** AssignedTo, **Create item** Audit Log.

## Flow 3 - Weekly reminders, delay detection & escalation (scheduled)

**Trigger:** *Recurrence* - weekly, Friday 7:00 AM Eastern.
1. **Get items** `Status ne 'Approved'` (turn on pagination, threshold 5000).
2. **Select** - add calculated fields to every row:
   - `DaysOverdue`: `div(sub(ticks(utcNow()), ticks(item()?['DueDate'])), 864000000000)`
   - `DaysSinceUpdate`: same formula on `LastStatusChange`
   - `Tier`: `if(greaterOrEquals(DaysOverdue,180),4,if(greaterOrEquals(DaysOverdue,90),3,if(greaterOrEquals(DaysOverdue,30),2,if(greaterOrEquals(DaysOverdue,1),1,0))))`
   - `Stalled`: compare `DaysSinceUpdate` to the Config value for that status.
3. **Validate before notify (Scope)** - stop the run if the data looks wrong:
   row count is 0, any row has no AssignedTo, or any DueDate is empty →
   send one alert to the process owner and **Terminate (Failed)**. Bad data never reaches PMs.
4. **Consolidate per person** - *Select* the distinct `AssignedTo/Email` values
   (`union(body('Select_emails'), body('Select_emails'))` removes duplicates), then
   *Apply to each* person:
   - *Filter array* their rows, sort by priority, **Create HTML table** (EvalID, Vendor, Due, Days overdue).
   - **Send an email (V2)** - one email per person per week, never one per evaluation.
   - Skip anyone whose `LastReminderSent` is within 6 days.
5. **Escalations** - *Filter array* `Tier ge 2`; group by tier exactly like step 4:
   tier 2 → PM's manager, tier 3 → department director, tier 4 → `SRDirectorEmail` (one summary).
6. **Update item** `EscalationTier`, `LastReminderSent` on every row touched; Audit Log rows.
7. **Error handling** - steps 4-6 sit in a *Scope - Try*; a *Scope - Catch* configured to run
   after "has failed / has timed out" emails the process owner the run URL:
   `concat('https://make.powerautomate.com/environments/', workflow()?['tags']?['environmentName'], '/flows/', workflow()?['name'], '/runs/', workflow()?['run']?['name'])`

## Flow 4 - Supplier Relations approval (automated)

**Trigger:** called from Flow 2 when Status becomes `Submitted`.
1. **Start and wait for an approval** (Approve/Reject) assigned to the analyst, with the
   evaluation link and scores.
2. **Approve** → Status `Approved`, stamp approver + date. **Reject** → Status `In Progress`,
   email the PM the reviewer's comments (the only allowed backward transition).
3. Audit Log row either way.

---

### How to test the flows (end-to-end reliability)

| Test | Expected |
|---|---|
| Enter `c 2023 10450` as a contract number | Saved as `C-2023-10450` |
| Create the same contract + period twice | One item kept, other flagged Duplicate |
| Set a PM account to disabled, change an item's Status | Item reassigned to least-loaded active PM, Audit Log row written |
| Set DueDate to 35 days ago, run Flow 3 manually | PM reminder + supervisor escalation (tier 2), one email each |
| Blank out one AssignedTo, run Flow 3 | Run fails at validation, owner alerted, **no** PM emails sent |
| Reject an approval | Status back to In Progress, PM gets the comments |

The Python test suite (`tests/`) covers the same cases, so the expected results are
known before the flows are built.
