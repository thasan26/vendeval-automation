"""VENDEVAL workflow engine: routing, SLA tracking, stall detection, escalation.

This is the same decision logic the Power Automate flows implement
(see powerautomate/flow_specs.md). Writing it in code first means every rule is
unit-tested and every decision is written to an audit log before it is
automated in Microsoft 365.

Routing rules
  * Review status decides the lane:
      Not Started / Requested / In Progress -> project-manager outreach
      Submitted                             -> Supplier Relations review queue
  * Evaluator assignment:
      contract PM if active  ->  else the active PM in the same department with
      the fewest open evaluations (under the workload cap)  ->  else the PM's supervisor
  * Contract year sets the priority weight (oldest backlog first).
  * Escalation tier is driven by days overdue (config.ESCALATION_TIERS).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, timedelta

import pandas as pd

from config import (ESCALATION_TIERS, EVAL_DUE_DAYS_AFTER_PERIOD_END, MAX_OPEN_PER_EVALUATOR,
                    OPEN_STATUSES, REMINDER_DAYS_BEFORE_DUE, STALL_DAYS)

SR_DIRECTOR = "E900"
SR_REVIEWERS = ["E901", "E902"]
YEAR_WEIGHT = {2022: 1.0, 2023: 0.66, 2024: 0.33}


# ------------------------------------------------------------- pure rules
def due_date_for(period_end: date) -> date:
    return period_end + timedelta(days=EVAL_DUE_DAYS_AFTER_PERIOD_END)


def sla_state(days_overdue: int) -> str:
    if days_overdue > 0:
        return "Overdue"
    if days_overdue >= -REMINDER_DAYS_BEFORE_DUE:
        return "Due soon"
    return "On track"


def escalation_tier(days_overdue: int) -> tuple[int, str | None]:
    tier, label = 0, None
    for min_days, t, who in ESCALATION_TIERS:
        if days_overdue >= min_days:
            tier, label = t, who
    return tier, label


def is_stalled(status: str, days_since_update: int | None) -> bool:
    limit = STALL_DAYS.get(status)
    return limit is not None and days_since_update is not None and days_since_update > limit


def priority_score(days_overdue: int, award_value: float, contract_year: int,
                   vendor_bidding: bool, is_final: bool) -> float:
    """0-100. Weighted so an old, overdue, high-value evaluation for a vendor that
    is bidding right now goes to the top of the outreach list."""
    overdue = min(max(days_overdue, 0), 365) / 365
    value = min(max(math.log10(max(award_value, 1)) - 4, 0) / 3, 1)     # $10k..$10M -> 0..1
    score = (0.40 * overdue + 0.15 * value + 0.15 * YEAR_WEIGHT.get(contract_year, 0)
             + 0.25 * vendor_bidding + 0.05 * is_final)
    return round(100 * score, 1)


# ------------------------------------------------------------- engine
class WorkflowEngine:
    def __init__(self, evaluations, contracts, evaluators, vendors, solicitations, status_history,
                 as_of: date):
        self.as_of = as_of
        self.ev = evaluators.set_index("evaluator_id")
        self.vendors = vendors.set_index("vendor_id")
        self.evals = evaluations
        self.contracts = contracts.set_index("contract_no")
        hist = status_history.copy()
        hist["changed_at"] = pd.to_datetime(hist.changed_at)
        self.last_change = hist.groupby("eval_id").changed_at.max()
        bids = solicitations.copy()
        bids["bid_due_date"] = pd.to_datetime(bids.bid_due_date).dt.date
        window = as_of + timedelta(days=60)
        self.bidding = set(bids[(bids.bid_due_date >= as_of) & (bids.bid_due_date <= window)].vendor_id)
        self.audit: list[dict] = []
        self.load: Counter = Counter()

    def log(self, eval_id, action, detail):
        self.audit.append({"as_of": self.as_of.isoformat(), "eval_id": eval_id,
                           "action": action, "detail": detail})

    def _director(self, dept):
        d = self.ev[(self.ev.department == dept) & (self.ev.role == "Director")]
        return d.index[0] if len(d) else SR_DIRECTOR

    def _supervisor_of(self, eid, dept):
        sup = self.ev.at[eid, "supervisor_id"]
        return sup if isinstance(sup, str) else self._director(dept)

    def _assign(self, eval_id, status, contract) -> tuple[str, str]:
        if status == "Submitted":
            reviewer = min(SR_REVIEWERS, key=lambda r: self.load[r])
            return reviewer, "Supplier Relations review queue"
        pm = contract.pm_id if isinstance(contract.pm_id, str) else None
        if pm and bool(self.ev.at[pm, "active"]):
            return pm, "contract PM"
        why = "PM inactive" if pm else "no PM on contract"
        pool = self.ev[(self.ev.department == contract.department) &
                       (self.ev.role == "Project Manager") & (self.ev.active.astype(bool))]
        pool = [p for p in pool.index if self.load[p] < MAX_OPEN_PER_EVALUATOR]
        if pool:
            pick = min(pool, key=lambda p: (self.load[p], p))
            return pick, f"{why} -> reassigned to least-loaded active PM in {contract.department}"
        sup = self.ev.at[pm, "supervisor_id"] if pm else self._director(contract.department)
        return sup, f"{why}; all department PMs at cap -> routed to supervisor"

    def build_work_queue(self) -> pd.DataFrame:
        open_evals = self.evals[self.evals.status.isin(OPEN_STATUSES + ["Submitted"])].copy()
        rows = []
        for e in open_evals.itertuples():
            c = self.contracts.loc[e.contract_no]
            due = due_date_for(pd.Timestamp(e.period_end).date())
            days_overdue = (self.as_of - due).days
            last = self.last_change.get(e.eval_id)
            if last is None and pd.notna(e.last_updated):
                last = pd.Timestamp(e.last_updated)
            since = (self.as_of - last.date()).days if last is not None else None
            bidding = c.vendor_id in self.bidding
            rows.append({
                "eval_id": e.eval_id, "status": e.status, "contract_no": e.contract_no,
                "contract_year": int(c.contract_year), "department": c.department,
                "vendor_id": c.vendor_id, "pm_id": c.pm_id, "due_date": due,
                "days_overdue": days_overdue, "sla_state": sla_state(days_overdue),
                "stalled": is_stalled(e.status, since), "days_since_update": since,
                "vendor_bidding": bidding,
                "priority_score": priority_score(days_overdue, c.award_value, int(c.contract_year),
                                                 bidding, e.eval_type == "Final"),
            })
        q = pd.DataFrame(rows).sort_values(["priority_score", "days_overdue"], ascending=False)
        q["priority_rank"] = range(1, len(q) + 1)

        # Seed workloads with items whose owner is certain, then place reassignments
        for r in q.itertuples():
            pm = r.pm_id if isinstance(r.pm_id, str) else None
            if r.status != "Submitted" and pm and bool(self.ev.at[pm, "active"]):
                self.load[pm] += 1
        assigned, reasons, tiers, esc_to = [], [], [], []
        for r in q.itertuples():
            owner, reason = self._assign(r.eval_id, r.status, self.contracts.loc[r.contract_no])
            if reason != "contract PM":
                self.load[owner] += 1
                self.log(r.eval_id, "assign", f"{owner}: {reason}")
            tier, who = (escalation_tier(r.days_overdue) if r.status != "Submitted" else (0, None))
            if r.status == "Submitted" and r.stalled:
                tier, who = 2, "Supplier Relations director (approval stalled)"
            esc = {0: None, 1: owner,
                   2: SR_DIRECTOR if r.status == "Submitted" else self._supervisor_of(owner, r.department),
                   3: self._director(r.department), 4: SR_DIRECTOR}[tier]
            if tier >= 2:
                self.log(r.eval_id, f"escalate_tier_{tier}", f"{r.days_overdue} days overdue -> {who} ({esc})")
            if r.stalled:
                self.log(r.eval_id, "stalled", f"'{r.status}' with no update for {r.days_since_update} days")
            assigned.append(owner); reasons.append(reason); tiers.append(tier); esc_to.append(esc)
        q["assigned_to"], q["assignment_reason"] = assigned, reasons
        q["escalation_tier"], q["escalate_to"] = tiers, esc_to
        q["as_of"] = self.as_of.isoformat()
        return q

    # --------------------------------------------------------- notifications
    def _name(self, eid):
        return f"{self.ev.at[eid, 'first_name']} {self.ev.at[eid, 'last_name']}"

    def build_notifications(self, q: pd.DataFrame) -> pd.DataFrame:
        """One consolidated message per recipient per kind - never one email per evaluation."""
        groups = defaultdict(list)
        for r in q.itertuples():
            if r.status == "Not Started" and r.days_overdue >= -REMINDER_DAYS_BEFORE_DUE:
                groups[(r.assigned_to, "evaluation_request")].append(r)
            elif r.status in ("Requested", "In Progress") and r.sla_state != "On track":
                groups[(r.assigned_to, "pm_reminder")].append(r)
            elif r.status == "Submitted" and r.stalled:
                groups[(r.assigned_to, "review_reminder")].append(r)
            if r.escalation_tier >= 2 and isinstance(r.escalate_to, str):
                groups[(r.escalate_to, f"escalation_tier_{r.escalation_tier}")].append(r)

        subjects = {
            "evaluation_request": "Action needed: {n} vendor evaluation(s) to complete",
            "pm_reminder": "Reminder: {n} vendor evaluation(s) due or overdue",
            "review_reminder": "{n} submitted evaluation(s) waiting on your approval",
            "escalation_tier_2": "Escalation: {n} overdue evaluation(s) on your team",
            "escalation_tier_3": "Director escalation: {n} evaluation(s) 90+ days overdue",
            "escalation_tier_4": "Leadership summary: {n} evaluation(s) 180+ days overdue",
        }
        out = []
        for i, ((rcpt, kind), items) in enumerate(sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])), 1):
            items = sorted(items, key=lambda r: r.priority_rank)
            lines = [f"- {r.eval_id} | {self.vendors.at[r.vendor_id, 'vendor_name']} | due {r.due_date} | "
                     f"{'%d days overdue' % r.days_overdue if r.days_overdue > 0 else r.sla_state}"
                     + (" | VENDOR BIDDING NOW" if r.vendor_bidding else "")
                     for r in items[:15]]
            if len(items) > 15:
                lines.append(f"...and {len(items) - 15} more (see the dashboard)")
            body = (f"Hi {self.ev.at[rcpt, 'first_name']},\n\n"
                    f"{subjects[kind].format(n=len(items))} as of {self.as_of:%b %d, %Y}, highest priority first:\n\n"
                    + "\n".join(lines)
                    + "\n\nPlease complete them in the VENDEVAL form, or reply if one should be reassigned.\n"
                      "- Supplier Relations, Vendor Performance")
            out.append({"notification_id": f"N{self.as_of:%Y%m%d}-{i:04d}", "as_of": self.as_of.isoformat(),
                        "recipient_id": rcpt, "recipient_email": self.ev.at[rcpt, "email"], "kind": kind,
                        "eval_count": len(items), "eval_ids": ";".join(r.eval_id for r in items),
                        "subject": subjects[kind].format(n=len(items)), "body": body})
        return pd.DataFrame(out)

    def run(self):
        q = self.build_work_queue()
        notes = self.build_notifications(q)
        return q, notes, pd.DataFrame(self.audit)
