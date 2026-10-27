"""Standardize, de-duplicate and merge VENDEVAL records from Access, Excel and Archer.

Pipeline stages
  1. Extract   - read each source export as-is
  2. Normalize - contract numbers, dates, statuses, people, vendor names
  3. Validate  - quarantine unusable rows, null out-of-range ratings, log every fix
  4. Dedupe    - collapse duplicates inside each source
  5. Merge     - one golden record per evaluation across sources, with lineage
Every change is written to `data_issues` so the cleanup is auditable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher, get_close_matches

import pandas as pd

from config import RATING_CATEGORIES, RAW, SOURCE_PRECEDENCE, STATUS_ORDER

STATUS_RANK = {s: i for i, s in enumerate(STATUS_ORDER)}

ACCESS_STATUS = {"NS": "Not Started", "REQ": "Requested", "INPR": "In Progress",
                 "SUBM": "Submitted", "APPR": "Approved"}
ARCHER_STATUS = {"draft": "Not Started", "assigned": "Requested", "in review": "In Progress",
                 "pending approval": "Submitted", "approved": "Approved"}
# Free-text tracker values -> controlled vocabulary (checked case-insensitively)
EXCEL_STATUS = {
    "": "Not Started", "not started": "Not Started", "n/a - not sent": "Not Started",
    "requested": "Requested", "sent to pm": "Requested", "pending pm": "Requested",
    "in progress": "In Progress", "in-progress": "In Progress", "pm working": "In Progress", "wip": "In Progress",
    "submitted": "Submitted", "submitted - pending review": "Submitted", "recv'd": "Submitted",
    "approved": "Approved", "complete": "Approved", "done": "Approved", "final - approved": "Approved",
}

LEGAL_SUFFIXES = {"INC", "INCORPORATED", "LLC", "CORP", "CORPORATION", "CO", "COMPANY",
                  "LTD", "LIMITED", "GROUP", "ASSOCIATES", "THE", "SONS", "AND"}
# Suffix spellings that mean the same legal entity type
SUFFIX_CANON = {"INCORPORATED": "INC", "CORPORATION": "CORP", "COMPANY": "CO", "LIMITED": "LTD"}


# =========================================================== issue logging
@dataclass
class IssueLog:
    rows: list = field(default_factory=list)

    def add(self, source, record_ref, field_name, issue, action, value=None):
        self.rows.append({"source": source, "record_ref": str(record_ref), "field": field_name,
                          "issue": issue, "action": action, "original_value": None if value is None else str(value)})

    def frame(self):
        return pd.DataFrame(self.rows, columns=["source", "record_ref", "field", "issue", "action", "original_value"])


# =========================================================== normalizers
CONTRACT_RE = re.compile(r"^C(\d{4})(\d{5})$")


def normalize_contract_no(value) -> str | None:
    """'C2022-10004', 'C 2022 10004', 'c-2022-10004 ' -> 'C-2022-10004'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    compact = re.sub(r"[\s\-_]", "", str(value).upper())
    m = CONTRACT_RE.match(compact)
    return f"C-{m.group(1)}-{m.group(2)}" if m else None


DATE_FORMATS = ["%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%b %d, %Y"]


def parse_dt(value) -> datetime | None:
    """Parse every date style found in the three systems (incl. Excel serials)."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)) and 20000 < value < 80000:          # Excel serial
        return datetime(1899, 12, 30) + timedelta(days=float(value))
    s = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _tokens(name) -> list[str]:
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return []
    s = str(name).upper().replace("&", " AND ")
    return re.sub(r"[^A-Z0-9 ]", " ", s).split()


def vendor_full_key(name) -> str:
    """Strict key: case/punctuation/'&' normalized, suffix spelled one way, 'The' dropped.
    'Atlas Electric Incorporated' == 'ATLAS ELECTRIC, INC.'  but  != 'Atlas Electric LLC'."""
    toks = [SUFFIX_CANON.get(t, t) for t in _tokens(name)]
    if toks and toks[0] == "THE":
        toks = toks[1:]
    return " ".join(toks)


def vendor_key(name) -> str:
    """Loose key: all legal suffixes removed. Different legal entities can collide here."""
    return " ".join(t for t in _tokens(name) if t not in LEGAL_SUFFIXES)


def person_key(name) -> str:
    """'Rodriguez, Maria' and 'Maria Rodriguez' -> 'MARIA RODRIGUEZ'."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    s = str(name).strip()
    if "," in s:
        last, first = [p.strip() for p in s.split(",", 1)]
        s = f"{first} {last}"
    return re.sub(r"\s+", " ", s).upper()


class VendorResolver:
    """Resolve messy vendor names to a vendor_id.

    Match ladder (stops at first hit):
      1. vendor_id supplied by the source system
      2. strict key  (same name + same legal suffix)
      3. loose key   (suffix dropped/misspelled) - if several vendors share it,
                     use the contract's vendor as a tie-breaker ("contract_assisted")
      4. fuzzy match on loose key (typos), refused if the runner-up is too close
    """

    def __init__(self, vendors: pd.DataFrame, fuzzy_cutoff: float = 0.88):
        self.by_full = {vendor_full_key(r.vendor_name): r.vendor_id for r in vendors.itertuples()}
        self.by_key: dict[str, list[str]] = {}
        for r in vendors.itertuples():
            self.by_key.setdefault(vendor_key(r.vendor_name), []).append(r.vendor_id)
        self.keys = list(self.by_key)
        self.cutoff = fuzzy_cutoff
        self.cache: dict[str, tuple] = {}

    def resolve(self, name, vendor_id=None, contract_vendor_id=None):
        if isinstance(vendor_id, str) and vendor_id.strip():
            return vendor_id.strip(), "vendor_id", 1.0
        full = vendor_full_key(name)
        if full in self.by_full:
            return self.by_full[full], "exact_key", 1.0
        k = vendor_key(name)
        ids = self.by_key.get(k)
        if ids and len(ids) > 1:
            if contract_vendor_id in ids:
                return contract_vendor_id, "contract_assisted", 1.0
            return None, "ambiguous", 1.0
        if ids:
            return ids[0], "loose_key", 1.0
        if k not in self.cache:
            self.cache[k] = [(c, round(SequenceMatcher(None, k, c).ratio(), 3))
                             for c in get_close_matches(k, self.keys, n=3, cutoff=self.cutoff)]
        cands = self.cache[k]
        if not cands:
            return None, "unresolved", 0.0
        best_key, best = cands[0]
        # refuse to guess when the runner-up is nearly as good
        if len(cands) > 1 and best - cands[1][1] < 0.03:
            pool = [v for c, _ in cands for v in self.by_key[c]]
            if contract_vendor_id in pool:
                return contract_vendor_id, "fuzzy_contract_assisted", best
            return None, "ambiguous", best
        ids = self.by_key[best_key]
        if len(ids) == 1:
            return ids[0], "fuzzy", best
        if contract_vendor_id in ids:
            return contract_vendor_id, "fuzzy_contract_assisted", best
        return None, "ambiguous", best


# =========================================================== extract + map
def load_reference():
    vendors = pd.read_csv(RAW / "access_vendors.csv")
    vendors["vendor_name"] = vendors.vendor_name.str.strip()
    # Pick a display name: Title Case for names that were stored in all caps
    vendors["vendor_name"] = vendors.vendor_name.where(~vendors.vendor_name.str.isupper(),
                                                       vendors.vendor_name.str.title())
    evaluators = pd.read_csv(RAW / "access_evaluators.csv")
    contracts = pd.read_csv(RAW / "access_contracts.csv")
    for col in ("start_date", "end_date"):
        contracts[col] = contracts[col].map(lambda v: parse_dt(v).date())
    solicitations = pd.read_csv(RAW / "procurement_solicitations.csv", parse_dates=["bid_due_date"])
    return vendors, evaluators, contracts, solicitations


def ratings_from(row, columns, issues, source, ref):
    out = {}
    for cat, col in zip(RATING_CATEGORIES, columns):
        v = row.get(col)
        if v is None or pd.isna(v):
            out[f"rating_{cat}"] = None
        elif 1 <= float(v) <= 5:
            out[f"rating_{cat}"] = int(v)
        else:
            out[f"rating_{cat}"] = None
            issues.add(source, ref, f"rating_{cat}", "rating outside 1-5 scale", "set to null; flagged for PM", v)
    return out


def extract_access(issues):
    df = pd.read_csv(RAW / "access_evaluations.csv")
    recs = []
    for r in df.to_dict("records"):
        ref = r["eval_id"]
        status = ACCESS_STATUS.get(str(r["status_code"]).strip().upper())
        if status is None:
            issues.add("access", ref, "status_code", "unknown status code", "row quarantined", r["status_code"])
            continue
        recs.append({
            "source": "access", "source_ref": ref, "eval_id": r["eval_id"],
            "contract_no": normalize_contract_no(r["contract_no"]),
            "eval_type": str(r["eval_type"]).title(),
            "period_end": parse_dt(r["period_end"]).date(),
            "status": status, "last_updated": parse_dt(r["last_updated"]),
            "vendor_id": None, "evaluator_id": None, "vendor_match": None,
            **ratings_from(r, [f"rating_{c}" for c in RATING_CATEGORIES], issues, "access", ref),
        })
    return pd.DataFrame(recs)


def extract_excel(issues, resolver, evaluators, contract_vendor):
    df = pd.read_excel(RAW / "excel_tracker.xlsx", dtype=object)
    people = {person_key(f"{r.first_name} {r.last_name}"): r.evaluator_id for r in evaluators.itertuples()}
    recs = []
    for i, r in enumerate(df.to_dict("records"), start=2):        # row 1 = header
        ref = f"row {i}"
        cn = normalize_contract_no(r["Contract #"])
        pend = parse_dt(r["Period End"])
        if cn is None or pend is None:
            issues.add("excel", ref, "Contract # / Period End", "missing or unparseable key",
                       "row quarantined", r["Contract #"])
            continue
        raw_status = "" if pd.isna(r["Status"]) else str(r["Status"])
        status = EXCEL_STATUS.get(raw_status.strip().lower())
        if status is None:
            issues.add("excel", ref, "Status", "unmapped status text", "row quarantined", raw_status)
            continue
        if raw_status != status and raw_status.strip():
            issues.add("excel", ref, "Status", "non-standard status text", f"mapped to '{status}'", raw_status)
        vid, method, score = resolver.resolve(r["Vendor"], contract_vendor_id=contract_vendor.get(cn))
        if method not in ("vendor_id", "exact_key", "loose_key"):
            issues.add("excel", ref, "Vendor", f"vendor name {method} (score {score})",
                       "matched" if vid else "needs manual match", r["Vendor"])
        pm = people.get(person_key(r["Project Manager"]))
        recs.append({
            "source": "excel", "source_ref": ref, "eval_id": None, "contract_no": cn,
            "eval_type": str(r["Eval Type"]).strip().title(), "period_end": pend.date(),
            "status": status, "last_updated": parse_dt(r["Last Touched"]),
            "vendor_id": vid, "vendor_match": method, "evaluator_id": pm,
            "tracker_overall_score": float(r["Overall Score"]) if pd.notna(r["Overall Score"]) else None,
            **{f"rating_{c}": None for c in RATING_CATEGORIES},
        })
    return pd.DataFrame(recs)


def extract_archer(issues, resolver, evaluators, contract_vendor):
    df = pd.read_csv(RAW / "archer_export.csv")
    emails = {e.lower(): i for e, i in zip(evaluators.email, evaluators.evaluator_id)}
    recs = []
    for r in df.to_dict("records"):
        ref = r["Record ID"]
        status = ARCHER_STATUS.get(str(r["Workflow Status"]).strip().lower())
        cn = normalize_contract_no(r["Contract Number"])
        if status is None or cn is None:
            issues.add("archer", ref, "Workflow Status / Contract Number", "unmappable value",
                       "row quarantined", r["Workflow Status"])
            continue
        vid, method, _ = resolver.resolve(r["Vendor Name"], r["Vendor ID"] if pd.notna(r["Vendor ID"]) else None,
                                          contract_vendor.get(cn))
        recs.append({
            "source": "archer", "source_ref": ref, "eval_id": None, "contract_no": cn,
            "eval_type": str(r["Evaluation Type"]).title(),
            "period_end": parse_dt(r["Period End Date"]).date(),
            "status": status, "last_updated": parse_dt(r["Last Modified"]),
            "vendor_id": vid, "vendor_match": method,
            "evaluator_id": emails.get(str(r["Evaluator Email"]).strip().lower()),
            **ratings_from(r, [f"{c.title()} Score" for c in RATING_CATEGORIES], issues, "archer", ref),
        })
    return pd.DataFrame(recs)


# =========================================================== dedupe + merge
def dedupe_within_source(df: pd.DataFrame, issues: IssueLog) -> pd.DataFrame:
    """Same evaluation entered twice in one system -> keep the most recent row."""
    df = df.copy()
    df["status_rank"] = df.status.map(STATUS_RANK)
    df = df.sort_values(["last_updated", "status_rank"], ascending=False, na_position="last")
    dup_mask = df.duplicated(["source", "contract_no", "period_end"], keep="first")
    for r in df[dup_mask].itertuples():
        issues.add(r.source, r.source_ref, "*", "duplicate of another row in same source",
                   "dropped (kept most recent)")
    return df[~dup_mask]


def merge_sources(records: pd.DataFrame, issues: IssueLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build one golden record per (contract_no, period_end)."""
    records = records.copy()
    records["precedence"] = records.source.map(SOURCE_PRECEDENCE)
    records = records.sort_values(["last_updated", "status_rank", "precedence"],
                                  ascending=False, na_position="last")
    golden, lineage = [], []
    for (cn, pend), g in records.groupby(["contract_no", "period_end"], sort=False):
        win = g.iloc[0]
        statuses = set(g.status)
        conflict = False
        if len(statuses) > 1:
            same_day = g[g.last_updated.dt.date == win.last_updated.date()]
            conflict = same_day.status.nunique() > 1
            issues.add("merge", f"{cn}|{pend}", "status",
                       "sources disagree: " + ", ".join(f"{s}={st}" for s, st in zip(g.source, g.status)),
                       f"kept '{win.status}' from {win.source} (most recent update)"
                       + ("; same-day conflict -> manual review" if conflict else ""))
        rec = {
            "eval_id": next((e for e in g.eval_id if isinstance(e, str)), None)
                       or f"EV-{cn[2:]}-P{pend:%Y%m%d}",
            "contract_no": cn, "period_end": pend, "eval_type": win.eval_type,
            "status": win.status, "last_updated": win.last_updated, "status_source": win.source,
            "needs_manual_review": conflict,
            "source_systems": "+".join(sorted(set(g.source))),
            "excel_evaluator_id": next((e for e in g.evaluator_id if isinstance(e, str)), None),
        }
        for cat in RATING_CATEGORIES:   # first non-null rating in precedence order
            vals = g.sort_values("precedence", ascending=False)[f"rating_{cat}"].dropna()
            rec[f"rating_{cat}"] = int(vals.iloc[0]) if len(vals) else None
        cats = [rec[f"rating_{c}"] for c in RATING_CATEGORIES if rec[f"rating_{c}"] is not None]
        tracker = g.tracker_overall_score.dropna() if "tracker_overall_score" in g else []
        rec["overall_score"] = (round(sum(cats) / len(cats), 2) if cats
                                else float(tracker.iloc[0]) if len(tracker) else None)
        golden.append(rec)
        for r in g.itertuples():
            lineage.append({"eval_id": rec["eval_id"], "source": r.source, "source_ref": r.source_ref,
                            "source_status": r.status, "source_last_updated": r.last_updated,
                            "was_selected": r.source_ref == win.source_ref and r.source == win.source})
    return pd.DataFrame(golden), pd.DataFrame(lineage)


def run():
    issues = IssueLog()
    vendors, evaluators, contracts, solicitations = load_reference()
    resolver = VendorResolver(vendors)
    contract_vendor = dict(zip(contracts.contract_no, contracts.vendor_id))

    raw = {
        "access": extract_access(issues),
        "excel": extract_excel(issues, resolver, evaluators, contract_vendor),
        "archer": extract_archer(issues, resolver, evaluators, contract_vendor),
    }
    records = pd.concat(raw.values(), ignore_index=True)
    records["last_updated"] = pd.to_datetime(records.last_updated)
    deduped = dedupe_within_source(records, issues)
    golden, lineage = merge_sources(deduped, issues)
    # Records that never reached Access have no eval_id: number them the way Access
    # does (period sequence within the contract) so IDs look the same everywhere.
    golden = golden.sort_values(["contract_no", "period_end"])
    seq = golden.groupby("contract_no").cumcount() + 1
    temp = golden.eval_id.str.contains("-P")
    new_ids = "EV-" + golden.contract_no.str[2:] + "-" + seq.map("{:02d}".format)
    remap = dict(zip(golden.eval_id[temp], new_ids[temp]))
    golden["eval_id"] = golden.eval_id.replace(remap)
    lineage["eval_id"] = lineage.eval_id.replace(remap)

    # Enrich with contract master data, check tracker vendor matches the contract
    golden = golden.merge(contracts, on="contract_no", how="left", validate="many_to_one")
    vend_check = deduped[deduped.vendor_id.notna()][["contract_no", "vendor_id", "source", "source_ref"]] \
        .merge(contracts[["contract_no", "vendor_id"]], on="contract_no", suffixes=("", "_contract"))
    for r in vend_check[vend_check.vendor_id != vend_check.vendor_id_contract].itertuples():
        issues.add(r.source, r.source_ref, "Vendor", "vendor on record does not match contract vendor",
                   "contract vendor kept; flagged", r.vendor_id)
    # Fill missing PM on the contract from the tracker if it names one
    missing_pm = golden.pm_id.isna() & golden.excel_evaluator_id.notna()
    for r in golden[missing_pm].itertuples():
        issues.add("access", r.contract_no, "pm_id", "contract has no PM", "filled from Excel tracker",
                   r.excel_evaluator_id)
    golden.loc[missing_pm, "pm_id"] = golden.loc[missing_pm, "excel_evaluator_id"]
    golden = golden.drop(columns=["excel_evaluator_id"])

    # Status history: Access log + every status observed in any source
    hist = pd.read_csv(RAW / "access_status_history.csv")
    hist["changed_at"] = hist.changed_at.map(parse_dt)
    key = golden.set_index("eval_id")
    observed = lineage.rename(columns={"source_status": "status", "source_last_updated": "changed_at"})
    history = pd.concat([hist[hist.eval_id.isin(key.index)][["eval_id", "status", "changed_at"]],
                         observed[["eval_id", "status", "changed_at"]]])
    history = history[history.status != "Not Started"].dropna()
    history["changed_at"] = pd.to_datetime(history.changed_at)
    # Access's audited log wins; other sources only fill statuses it never recorded
    history = history.drop_duplicates(["eval_id", "status"], keep="first")
    # Excel/Archer often carry a date with no time. Keep the lifecycle order intact:
    # a later status can't be timestamped before the status that precedes it.
    history["rank"] = history.status.map(STATUS_RANK)
    history = history.sort_values(["eval_id", "rank"])
    fixed = []
    for _, g in history.groupby("eval_id", sort=False):
        prev = None
        for t in g.changed_at:
            if prev is not None and t <= prev:
                t = prev + pd.Timedelta(minutes=1)
            fixed.append(t)
            prev = t
    history["changed_at"] = fixed
    history = history.drop(columns="rank")

    stats = {
        "source_rows": {"access": len(pd.read_csv(RAW / "access_evaluations.csv")),
                        "excel": len(pd.read_excel(RAW / "excel_tracker.xlsx")),
                        "archer": len(pd.read_csv(RAW / "archer_export.csv"))},
        "records_after_validation": len(records),
        "records_after_dedupe": len(deduped),
        "golden_records": len(golden),
        "vendor_match_methods": deduped.vendor_match.value_counts().to_dict(),
        "resolver": resolver,
        "deduped": deduped,
    }
    return {"vendors": vendors, "evaluators": evaluators, "contracts": contracts,
            "solicitations": solicitations, "evaluations": golden, "lineage": lineage,
            "status_history": history, "data_issues": issues.frame(), "stats": stats}
