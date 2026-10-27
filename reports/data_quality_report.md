# Data Quality Report — 2026-10-23

## Record flow

| Stage | Records |
|---|---:|
| Source rows: access | 1,997 |
| Source rows: excel | 1,331 |
| Source rows: archer | 854 |
| After validation (quarantined rows removed) | 4,170 |
| After within-source de-duplication | 4,083 |
| **Golden evaluation records** | **2,252** |

## Vendor name matching

| Method | Records |
|---|---:|
| exact_key | 1,305 |
| vendor_id | 615 |
| loose_key | 58 |
| contract_assisted | 45 |
| fuzzy | 37 |
| fuzzy_contract_assisted | 26 |

## Issues found and fixed

| Source | Issue | Count |
|---|---|---:|
| excel | non-standard status text | 1,000 |
| merge | sources disagree on status | 322 |
| excel | duplicate of another row in same source | 87 |
| excel | vendor name contract_assisted | 42 |
| access | contract has no PM | 39 |
| excel | vendor name fuzzy | 30 |
| excel | vendor name fuzzy_contract_assisted | 22 |
| access | rating outside 1-5 scale | 20 |
| excel | missing or unparseable key | 12 |

## Checks

| Result | Check | Detail |
|---|---|---|
| PASS | Reconciliation: source rows = loaded + quarantined + duplicates | 4182 in = 4083 kept + 12 quarantined + 87 duplicates |
| PASS | One golden record per contract period | 0 duplicate contract periods |
| PASS | Every evaluation links to a contract | 0 orphan evaluations |
| PASS | Foreign keys valid across all tables | PRAGMA foreign_key_check clean |
| PASS | All ratings on the 1-5 scale | 0 out-of-range ratings |
| PASS | Submitted/approved evaluations have a score | 0 completed evaluations missing scores (returned to PM) |
| PASS | Each golden record has exactly one winning source row | 0 violations |
| PASS | Status history moves forward through lifecycle | 0 evaluations with out-of-order history |
| PASS | Every open evaluation is in the work queue | 1093 open, 1093 queued |
| PASS | No work routed to inactive evaluators | 0 items |
| PASS | Every queued evaluation has an owner | 0 unassigned |
| PASS | Every tier 2+ escalation produces a notification | 0 missing |
| PASS | Every due/overdue item reaches its evaluator | 0 missing |
| PASS | Max one message per recipient per type (no spam) | max 1 |
| PASS | Reassignments respect the workload cap | 108 reassigned, 0 evaluators over cap 40 |
| PASS | Golden record count matches answer key | 2252 golden vs 2252 true evaluations |
| PASS | Merge picks the most current status available in any source | 2167/2252 statuses exact; the other 85 were stale in every system (0 picked wrong) -> only fixable by PM outreach |
| PASS | Vendor name resolution accuracy >= 99% | 100.00% of 2086 vendor names resolved to the right vendor ID |
