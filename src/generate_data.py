"""Generate realistic, intentionally messy VENDEVAL source exports.

All data is synthetic. Nothing here is real MTA data.

The goal is to reproduce the problems a real evaluation backlog has when the
same records live in three systems that were never reconciled:

  * access_*.csv      - legacy Access database export (system of record, IDs,
                        status codes, US dates, some missing PMs, lagging status)
  * excel_tracker.xlsx - hand-maintained tracker (no vendor IDs, free-text
                        status, "Last, First" names, copy-paste duplicates,
                        mixed date formats, typos in vendor names)
  * archer_export.csv  - GRC/Archer export (emails instead of names, ISO
                        timestamps, contract numbers with spaces)

A ground-truth answer key is written to data/raw/_ground_truth/ so the pipeline
can measure how accurately it de-duplicates and resolves the records.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from config import AS_OF, RAW, RATING_CATEGORIES, SEED, STATUS_ORDER

rng = np.random.default_rng(SEED)
rnd = random.Random(SEED)

DEPARTMENTS = [
    "Construction & Development", "NYCT Infrastructure", "LIRR Engineering",
    "Metro-North Engineering", "Bridges & Tunnels", "MTA IT", "Facilities & Real Estate",
]
CONTRACT_TYPES = ["Construction", "Professional Services", "IT Services", "Goods & Supply"]
FIRST = ["Maria", "James", "Aisha", "Daniel", "Priya", "Kevin", "Sofia", "Michael", "Fatima",
         "Robert", "Jasmine", "Luis", "Grace", "Anthony", "Nadia", "Samuel", "Elena", "Omar",
         "Rachel", "Victor", "Hannah", "Marcus", "Leila", "Thomas", "Yuki", "Andre", "Carmen"]
LAST = ["Rodriguez", "Chen", "Patel", "Johnson", "Okafor", "Kim", "Rahman", "Murphy", "Santos",
        "Goldberg", "Nguyen", "Williams", "Haddad", "Brown", "Rossi", "Ali", "Cohen", "Diaz",
        "Thompson", "Singh", "Lopez", "Walker", "Hussain", "Martinez", "Greene", "Park"]
V_PREFIX = ["Atlas", "Hudson", "Empire", "Liberty", "Gotham", "Harbor", "Keystone", "Summit",
            "Bridgeway", "Pinnacle", "Northeast", "Tri-State", "Union", "Ironline", "Queensboro",
            "Statewide", "Interboro", "Meridian", "Granite", "Beacon", "Sterling", "Crescent",
            "Riverside", "Continental", "Allied", "Eastern", "Paragon", "Vanguard", "Cornerstone",
            "Patriot", "Horizon", "Lexington"]
V_CORE = ["Electric", "Construction", "Signal Systems", "Rail Services", "Engineering",
          "Contracting", "Technologies", "Builders", "Mechanical", "Environmental",
          "Consulting", "Supply", "Infrastructure", "Track Works", "Data Systems"]
V_SUFFIX = ["Inc.", "LLC", "Corp.", "Co.", "Group LLC", "& Sons Inc.", "Ltd.", "Associates"]

ACCESS_CODES = {"Not Started": "NS", "Requested": "REQ", "In Progress": "INPR",
                "Submitted": "SUBM", "Approved": "APPR"}
EXCEL_STATUS = {
    "Not Started": ["not started", "", "Not started", "N/A - not sent"],
    "Requested": ["Requested", "sent to PM", "Pending PM", "requested "],
    "In Progress": ["In progress", "PM working", "in-progress", "WIP"],
    "Submitted": ["Submitted", "submitted - pending review", "Recv'd"],
    "Approved": ["Approved", "complete", "DONE", "Final - approved"],
}
ARCHER_STATUS = {"Not Started": "Draft", "Requested": "Assigned", "In Progress": "In Review",
                 "Submitted": "Pending Approval", "Approved": "Approved"}


# --------------------------------------------------------------------- helpers
def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=int(rng.integers(0, max((end - start).days, 1))))


def typo(word: str) -> str:
    if len(word) < 5:
        return word
    i = rnd.randint(1, len(word) - 3)
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


def vendor_variant(name: str) -> str:
    """Return a realistic way a human might have typed this vendor name."""
    v = name
    r = rnd.random()
    if r < 0.18:
        v = v.upper()
    elif r < 0.30:
        v = v.replace("Inc.", "Incorporated").replace("Corp.", "Corporation").replace("Co.", "Company")
    elif r < 0.40:
        v = v.replace(".", "").replace(",", "")
    elif r < 0.48:
        v = v.replace("&", "and")
    elif r < 0.55:
        parts = v.split(" ")
        v = " ".join(parts[:-1]) if len(parts) > 2 else v          # dropped suffix
    elif r < 0.61:
        parts = v.split(" ")
        parts[1] = typo(parts[1])                                     # keyboard typo
        v = " ".join(parts)
    if rnd.random() < 0.15:
        v = v + "  "                                                  # trailing spaces
    if rnd.random() < 0.05:
        v = "The " + v
    return v


def fmt_date_excel(d: date | None):
    if d is None:
        return None
    r = rnd.random()
    if r < 0.45:
        return d                                     # real Excel date cell
    if r < 0.75:
        return d.strftime("%m/%d/%Y")
    if r < 0.90:
        return d.strftime("%Y-%m-%d")
    return d.strftime("%b %d, %Y")


# ---------------------------------------------------------------- master data
def build_master():
    # Evaluators (project managers) with a supervisor chain per department
    evaluators, used = [], set()
    eid = 100
    for dept in DEPARTMENTS:
        director = None
        for k in range(8):
            while True:
                fn, ln = rnd.choice(FIRST), rnd.choice(LAST)
                if (fn, ln) not in used:
                    used.add((fn, ln))
                    break
            eid += 1
            role = "Director" if k == 0 else ("Supervisor" if k in (1, 2) else "Project Manager")
            evaluators.append({
                "evaluator_id": f"E{eid}", "first_name": fn, "last_name": ln,
                "email": f"{fn[0].lower()}{ln.lower()}@mta-example.org",
                "department": dept, "role": role,
                "active": not (role == "Project Manager" and rnd.random() < 0.12),
            })
    # Supplier Relations team: reviews submitted evaluations, top of escalation ladder
    for k, (fn, ln) in enumerate([("Denise", "Carter"), ("Rafael", "Ortiz"), ("Mei", "Lin")]):
        evaluators.append({
            "evaluator_id": f"E{900 + k}", "first_name": fn, "last_name": ln,
            "email": f"{fn[0].lower()}{ln.lower()}@mta-example.org", "department": "Supplier Relations",
            "role": "Director" if k == 0 else "Analyst", "active": True,
        })
    ev = pd.DataFrame(evaluators)
    for dept, grp in ev.groupby("department"):
        if dept == "Supplier Relations":
            ev.loc[grp.index, "supervisor_id"] = [None, "E900", "E900"]
            continue
        director = grp[grp.role == "Director"].evaluator_id.iloc[0]
        sups = grp[grp.role == "Supervisor"].evaluator_id.tolist()
        for idx in grp.index:
            role = ev.at[idx, "role"]
            ev.at[idx, "supervisor_id"] = (None if role == "Director" else
                                           director if role == "Supervisor" else rnd.choice(sups))

    # Vendors
    names = set()
    while len(names) < 340:
        names.add(f"{rnd.choice(V_PREFIX)} {rnd.choice(V_CORE)} {rnd.choice(V_SUFFIX)}")
    vendors = pd.DataFrame({
        "vendor_id": [f"V{20000 + i}" for i in range(len(names))],
        "vendor_name": sorted(names),
    })
    vendors["vendor_category"] = [rnd.choice(CONTRACT_TYPES) for _ in range(len(vendors))]

    # Contracts awarded 2022-2024
    pms = ev[ev.role == "Project Manager"]
    rows = []
    for i in range(1300):
        year = int(rng.choice([2022, 2023, 2024], p=[0.32, 0.34, 0.34]))
        start = rand_date(date(year, 1, 1), date(year, 12, 31))
        months = int(rng.choice([6, 12, 18, 24, 36, 48], p=[.15, .3, .15, .2, .12, .08]))
        end = start + timedelta(days=int(months * 30.4))
        pm = pms.sample(1, random_state=int(rng.integers(1e9))).iloc[0]
        rows.append({
            "contract_no": f"C-{year}-{10000 + i:05d}",
            "vendor_id": vendors.vendor_id.iloc[int(rng.integers(len(vendors)))],
            "department": pm.department,
            "pm_id": pm.evaluator_id,
            "contract_type": rnd.choice(CONTRACT_TYPES),
            "contract_year": year,
            "award_value": round(float(rng.lognormal(13.2, 1.3)), 2),
            "start_date": start, "end_date": end,
        })
    contracts = pd.DataFrame(rows)

    # Vendors currently bidding on new work need an up-to-date responsibility file
    pipeline_vendors = vendors.sample(frac=0.08, random_state=SEED)
    solicitations = pd.DataFrame({
        "solicitation_no": [f"RFP-{26000 + i}" for i in range(len(pipeline_vendors))],
        "vendor_id": pipeline_vendors.vendor_id.values,
        "bid_due_date": [rand_date(AS_OF, AS_OF + timedelta(days=75)) for _ in range(len(pipeline_vendors))],
    })
    return ev, vendors, contracts, solicitations


def build_evaluations(contracts: pd.DataFrame):
    """One interim evaluation per contract-year plus a final evaluation."""
    evals, history = [], []
    horizon = AS_OF + timedelta(days=120)
    for c in contracts.itertuples():
        periods, p = [], c.start_date
        while True:
            nxt = date(p.year + 1, p.month, min(p.day, 28))
            if nxt >= c.end_date:
                periods.append(("Final", c.end_date))
                break
            periods.append(("Interim", nxt))
            p = nxt
        for seq, (etype, pend) in enumerate(periods, 1):
            if pend > horizon:
                continue
            due = pend + timedelta(days=30)
            overdue_days = (AS_OF - due).days
            if overdue_days > 365:
                probs = [0.08, 0.14, 0.10, 0.08, 0.60]
            elif overdue_days > 90:
                probs = [0.10, 0.20, 0.17, 0.10, 0.43]
            elif overdue_days > 0:
                probs = [0.15, 0.30, 0.25, 0.12, 0.18]
            elif overdue_days > -45:
                probs = [0.30, 0.45, 0.17, 0.06, 0.02]
            else:
                probs = [0.85, 0.15, 0.0, 0.0, 0.0]
            status = STATUS_ORDER[int(rng.choice(5, p=probs))]
            eval_id = f"EV-{c.contract_no[2:]}-{seq:02d}"

            # Build the status history (timestamps of each step reached), never after AS_OF
            steps = STATUS_ORDER[1:STATUS_ORDER.index(status) + 1]
            t = datetime.combine(pend, datetime.min.time()) + timedelta(days=int(rng.integers(1, 20)), hours=9)
            times = []
            for _ in steps:
                t = t + timedelta(days=int(rng.integers(2, 60)), hours=int(rng.integers(0, 8)))
                times.append(t)
            cutoff = datetime.combine(AS_OF, datetime.min.time()) - timedelta(days=int(rng.integers(1, 30)))
            if times and times[-1] > cutoff:
                shift = times[-1] - cutoff
                times = [x - shift for x in times]
            for s_, t_ in zip(steps, times):
                history.append({"eval_id": eval_id, "status": s_, "changed_at": t_})
            last = times[-1] if times else None
            ratings = {}
            if status in ("Submitted", "Approved"):
                base = rng.normal(3.6, 0.7)
                for cat in RATING_CATEGORIES:
                    ratings[cat] = int(np.clip(round(base + rng.normal(0, 0.6)), 1, 5))
            evals.append({
                "eval_id": eval_id, "contract_no": c.contract_no, "eval_type": etype,
                "period_end": pend, "due_date": due, "status": status,
                "last_updated": last if last else datetime.combine(min(pend, AS_OF), datetime.min.time()),
                **{f"rating_{k}": ratings.get(k) for k in RATING_CATEGORIES},
            })
    return pd.DataFrame(evals), pd.DataFrame(history)


# ---------------------------------------------------------------- source views
def lag_status(row, hist_by_eval):
    """Return an older (stale) status/timestamp, as a system that missed updates would."""
    h = hist_by_eval.get(row.eval_id)
    if h is None or len(h) < 2:
        return row.status, row.last_updated
    prev = h.iloc[-2]
    return prev.status, prev.changed_at


def build_sources(ev, vendors, contracts, evals, history):
    vmap = vendors.set_index("vendor_id").vendor_name
    ev_by_id = ev.set_index("evaluator_id")
    cmap = contracts.set_index("contract_no")
    hist_by_eval = {k: g.sort_values("changed_at") for k, g in history.groupby("eval_id")}

    in_access = rng.random(len(evals)) < 0.82
    in_excel = rng.random(len(evals)) < 0.55
    in_archer = (rng.random(len(evals)) < 0.50) & evals.status.isin(["In Progress", "Submitted", "Approved"]).values
    orphan = ~(in_access | in_excel | in_archer)
    in_access = in_access | orphan            # every record lives somewhere

    # ---- Access: contracts, vendors, evaluators, evaluations, status history
    acc_contracts = contracts.copy()
    acc_contracts.loc[rng.random(len(acc_contracts)) < 0.03, "pm_id"] = None
    acc_contracts["start_date"] = pd.to_datetime(acc_contracts.start_date).dt.strftime("%m/%d/%Y")
    acc_contracts["end_date"] = pd.to_datetime(acc_contracts.end_date).dt.strftime("%m/%d/%Y")
    acc_contracts.to_csv(RAW / "access_contracts.csv", index=False)
    acc_vendors = vendors.copy()
    acc_vendors["vendor_name"] = acc_vendors.vendor_name.where(rng.random(len(acc_vendors)) > 0.4,
                                                               acc_vendors.vendor_name.str.upper())
    acc_vendors.to_csv(RAW / "access_vendors.csv", index=False)
    ev.to_csv(RAW / "access_evaluators.csv", index=False)

    acc = evals[in_access].copy()
    stale = rng.random(len(acc)) < 0.18
    for idx in acc.index[stale]:
        s, t = lag_status(acc.loc[idx], hist_by_eval)
        acc.at[idx, "status"], acc.at[idx, "last_updated"] = s, t
    acc["status_code"] = acc.status.map(ACCESS_CODES)
    acc["period_end"] = pd.to_datetime(acc.period_end).dt.strftime("%m/%d/%Y")
    acc["due_date"] = pd.to_datetime(acc.due_date).dt.strftime("%m/%d/%Y")
    acc["last_updated"] = pd.to_datetime(acc.last_updated).dt.strftime("%m/%d/%Y %H:%M")
    bad = acc.sample(frac=0.01, random_state=1).index          # data-entry errors
    acc.loc[bad, "rating_safety"] = rng.choice([0, 6, 9], len(bad))
    acc.drop(columns=["status"]).to_csv(RAW / "access_evaluations.csv", index=False)
    acc_hist = history[history.eval_id.isin(acc.eval_id)].copy()
    acc_hist["changed_at"] = pd.to_datetime(acc_hist.changed_at).dt.strftime("%m/%d/%Y %H:%M")
    acc_hist.to_csv(RAW / "access_status_history.csv", index=False)

    # ---- Excel tracker
    xl = evals[in_excel].copy()
    xl = xl.merge(contracts[["contract_no", "vendor_id", "pm_id", "department"]], on="contract_no")
    stale = rng.random(len(xl)) < 0.22
    for idx in xl.index[stale]:
        s, t = lag_status(xl.loc[idx], hist_by_eval)
        xl.at[idx, "status"], xl.at[idx, "last_updated"] = s, t
    out = pd.DataFrame({
        "Contract #": [cn.replace("-", "", 1) if rnd.random() < 0.35 else cn for cn in xl.contract_no],
        "Eval Type": xl.eval_type.str.upper().where(rng.random(len(xl)) < 0.3, xl.eval_type),
        "Vendor": [vendor_variant(vmap[v]) for v in xl.vendor_id],
        "Project Manager": [f"{ev_by_id.at[p, 'last_name']}, {ev_by_id.at[p, 'first_name']}"
                            if rnd.random() < 0.6 else
                            f"{ev_by_id.at[p, 'first_name']} {ev_by_id.at[p, 'last_name']}"
                            for p in xl.pm_id],
        "Dept": xl.department,
        "Period End": [fmt_date_excel(d) for d in xl.period_end],
        "Status": [rnd.choice(EXCEL_STATUS[s]) for s in xl.status],
        "Last Touched": [fmt_date_excel(pd.Timestamp(t).date()) for t in xl.last_updated],
        "Overall Score": [round(np.nanmean([r[f"rating_{k}"] for k in RATING_CATEGORIES]), 1)
                          if r.status in ("Submitted", "Approved") else None
                          for _, r in xl.iterrows()],
        "Notes": [rnd.choice(["", "", "", "left VM", "emailed twice", "PM on leave", "see email 3/2"])
                  for _ in range(len(xl))],
    })
    dups = out.sample(frac=0.05, random_state=2)                       # copy-paste dups
    near = out.sample(frac=0.02, random_state=3).copy()
    near["Vendor"] = near.Vendor.str.strip() + " "
    near["Status"] = near.Status.str.upper()
    missing = out.sample(frac=0.01, random_state=4).copy()
    missing["Contract #"] = None                                       # unusable rows
    out = pd.concat([out, dups, near, missing]).sample(frac=1, random_state=5)
    out.to_excel(RAW / "excel_tracker.xlsx", index=False, sheet_name="VENDEVAL Tracker")

    # ---- Archer export
    ar = evals[in_archer].copy()
    ar = ar.merge(contracts[["contract_no", "vendor_id", "pm_id"]], on="contract_no")
    pd.DataFrame({
        "Record ID": [f"ARC-{700000 + i}" for i in range(len(ar))],
        "Contract Number": [cn.replace("-", " ") if rnd.random() < 0.5 else cn for cn in ar.contract_no],
        "Evaluation Type": ar.eval_type,
        "Vendor Name": [vendor_variant(vmap[v]) for v in ar.vendor_id],
        "Vendor ID": ar.vendor_id.where(rng.random(len(ar)) < 0.7, None),
        "Evaluator Email": [ev_by_id.at[p, "email"].upper() if rnd.random() < 0.2 else ev_by_id.at[p, "email"]
                            for p in ar.pm_id],
        "Workflow Status": ar.status.map(ARCHER_STATUS),
        "Period End Date": pd.to_datetime(ar.period_end).dt.strftime("%Y-%m-%dT00:00:00"),
        "Last Modified": pd.to_datetime(ar.last_updated).dt.strftime("%Y-%m-%dT%H:%M:%S"),
        **{f"{k.title()} Score": ar[f"rating_{k}"] for k in RATING_CATEGORIES},
    }).to_csv(RAW / "archer_export.csv", index=False)

    # ---- Ground truth (answer key, used only to score the pipeline)
    gt = RAW / "_ground_truth"
    gt.mkdir(exist_ok=True)
    evals.to_csv(gt / "evaluations_truth.csv", index=False)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    ev, vendors, contracts, solicitations = build_master()
    evals, history = build_evaluations(contracts)
    solicitations.to_csv(RAW / "procurement_solicitations.csv", index=False)
    build_sources(ev, vendors, contracts, evals, history)
    print(f"Generated {len(vendors)} vendors, {len(contracts)} contracts, "
          f"{len(evals)} evaluations, {len(history)} status events")


if __name__ == "__main__":
    main()
