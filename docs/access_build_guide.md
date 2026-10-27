# Microsoft Access build guide

How to stand up the same relational model in Access and run the cleanup there.

## 1. Tables (Design View)

| Table | Primary key | Fields (Access data type) |
|---|---|---|
| tblVendors | VendorID (Short Text 10) | VendorName (Short Text 120), VendorCategory (Short Text 40) |
| tblEvaluators | EvaluatorID (Short Text 10) | FirstName, LastName (Short Text 50), Email (Short Text 120, Indexed No Duplicates), Department (Short Text 60), Role (Short Text 30, lookup list), Active (Yes/No), SupervisorID (Short Text 10) |
| tblContracts | ContractNo (Short Text 12) | VendorID, Department, PMID, ContractType, ContractYear (Number Integer), AwardValue (Currency), StartDate, EndDate (Date/Time) |
| tblEvaluations | EvalID (Short Text 20) | ContractNo, PeriodEnd (Date/Time), EvalType (lookup Interim/Final), Status (lookup), LastUpdated (Date/Time), StatusSource, SourceSystems, NeedsManualReview (Yes/No), RatingQuality…RatingManagement (Number Byte), OverallScore (Number Double) |
| tblStatusHistory | EvalID + Status (composite) | ChangedAt (Date/Time) |
| tblDataIssues | AutoNumber | Source, RecordRef, FieldName, Issue, ActionTaken, OriginalValue |

**Validation rules** (Table Design → field property):
- `ContractNo`: `Like "C-####-#####"` with validation text "Use format C-YYYY-NNNNN".
- Each rating: `Is Null Or Between 1 And 5`.
- `tblContracts` table-level rule: `[EndDate]>[StartDate]`.
- Unique index on tblEvaluations (`ContractNo`, `PeriodEnd`) - Access then *refuses* duplicates.

**Relationships** (Database Tools → Relationships), all with *Enforce Referential Integrity*:
tblVendors 1-∞ tblContracts · tblEvaluators 1-∞ tblContracts (PMID) · tblContracts 1-∞ tblEvaluations ·
tblEvaluations 1-∞ tblStatusHistory.

## 2. Load the cleaned data
External Data → New Data Source → From File → Text File → `data/processed/*.csv`
→ *Append a copy of the records to the table*. Load order: Vendors, Evaluators, Contracts,
Evaluations, StatusHistory (parents before children, or referential integrity blocks the import).

## 3. Saved queries (Access SQL)

**qryDuplicateCheck** - must return nothing
```sql
SELECT ContractNo, PeriodEnd, Count(*) AS N
FROM tblEvaluations
GROUP BY ContractNo, PeriodEnd
HAVING Count(*) > 1;
```

**qryOpenWorkQueue** - what Flow 3 sends out
```sql
SELECT e.EvalID, c.ContractYear, c.Department, v.VendorName, e.Status,
       DateAdd("d", 30, e.PeriodEnd) AS DueDate,
       DateDiff("d", DateAdd("d", 30, e.PeriodEnd), Date()) AS DaysOverdue,
       IIf(DateDiff("d", DateAdd("d",30,e.PeriodEnd), Date()) >= 180, 4,
       IIf(DateDiff("d", DateAdd("d",30,e.PeriodEnd), Date()) >= 90, 3,
       IIf(DateDiff("d", DateAdd("d",30,e.PeriodEnd), Date()) >= 30, 2,
       IIf(DateDiff("d", DateAdd("d",30,e.PeriodEnd), Date()) >= 1, 1, 0)))) AS EscalationTier,
       ev.FirstName & " " & ev.LastName AS PM, ev.Active AS PMActive
FROM ((tblEvaluations AS e
INNER JOIN tblContracts AS c ON e.ContractNo = c.ContractNo)
INNER JOIN tblVendors AS v ON c.VendorID = v.VendorID)
LEFT JOIN tblEvaluators AS ev ON c.PMID = ev.EvaluatorID
WHERE e.Status <> "Approved"
ORDER BY DateDiff("d", DateAdd("d",30,e.PeriodEnd), Date()) DESC;
```

**qryBacklogByYear**
```sql
SELECT c.ContractYear, Count(*) AS Total,
       Sum(IIf(e.Status="Approved",1,0)) AS Approved,
       Sum(IIf(e.Status In ("Not Started","Requested","In Progress"),1,0)) AS Outstanding,
       Format(Sum(IIf(e.Status="Approved",1,0))/Count(*),"0.0%") AS PctComplete
FROM tblEvaluations AS e INNER JOIN tblContracts AS c ON e.ContractNo = c.ContractNo
GROUP BY c.ContractYear;
```

**qryInactivePMItems** - items that need reassignment
```sql
SELECT e.EvalID, c.ContractNo, c.Department, Nz(ev.FirstName & " " & ev.LastName, "(no PM)") AS PM
FROM (tblEvaluations AS e INNER JOIN tblContracts AS c ON e.ContractNo = c.ContractNo)
LEFT JOIN tblEvaluators AS ev ON c.PMID = ev.EvaluatorID
WHERE e.Status <> "Approved" AND (ev.EvaluatorID Is Null OR ev.Active = False);
```

## 4. Connect Access to Power Automate / Power BI
Access is not a cloud connector, so in production the golden table is published to the
SharePoint list (`External Data → SharePoint List → Export`), and Access links to that list.
Flows and Power BI then read one shared source, and Access stays the place analysts run ad-hoc SQL.
