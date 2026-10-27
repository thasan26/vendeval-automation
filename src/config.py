"""Central configuration for the VENDEVAL automation pipeline.

Every business rule (SLA days, escalation tiers, workload caps) lives here so
that a reviewer can change policy without touching pipeline code. These are the
same values the Power Automate flows read from the SharePoint "Config" list.
"""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
SQL_DIR = ROOT / "sql"
DB_PATH = PROCESSED / "vendeval.db"

SEED = 17
# "Report date" for the weekly run. Fixed so outputs are reproducible;
# override with:  python run_pipeline.py --as-of 2026-11-06
AS_OF = date(2026, 10, 23)

# ---------------------------------------------------------------- SLA policy
EVAL_DUE_DAYS_AFTER_PERIOD_END = 30   # evaluation due 30 days after period ends
REMINDER_DAYS_BEFORE_DUE = 7          # first reminder 7 days before due
STALL_DAYS = {                        # no movement for N days => "stalled"
    "Requested": 14,
    "In Progress": 21,
    "Submitted": 10,                  # waiting on Supplier Relations approval
}

# Escalation ladder: (min days overdue, tier, who is notified)
ESCALATION_TIERS = [
    (1,   1, "Evaluator (PM) reminder"),
    (30,  2, "PM + PM's supervisor"),
    (90,  3, "Department director"),
    (180, 4, "Supplier Relations leadership"),
]

# ------------------------------------------------------------ routing policy
MAX_OPEN_PER_EVALUATOR = 40           # workload cap used when reassigning

# Ordered status lifecycle. Index = how far along the evaluation is.
STATUS_ORDER = ["Not Started", "Requested", "In Progress", "Submitted", "Approved"]
OPEN_STATUSES = STATUS_ORDER[:3]      # still waiting on the project manager
VALID_TRANSITIONS = {
    "Not Started": {"Requested"},
    "Requested": {"In Progress", "Submitted"},
    "In Progress": {"Submitted"},
    "Submitted": {"Approved", "In Progress"},   # can be returned for revision
    "Approved": set(),
}

RATING_CATEGORIES = ["quality", "timeliness", "safety", "compliance", "management"]

# Source-system precedence used only to break exact ties during merge.
SOURCE_PRECEDENCE = {"archer": 3, "access": 2, "excel": 1}
