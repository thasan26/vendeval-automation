-- VENDEVAL relational model (SQLite here; the same tables are built in MS Access,
-- see docs/access_build_guide.md for the Access data types and relationships).
-- Grain of each table is stated so nobody has to guess what one row means.

PRAGMA foreign_keys = ON;

-- One row per vendor (master data from Access)
CREATE TABLE vendors (
    vendor_id        TEXT PRIMARY KEY,
    vendor_name      TEXT NOT NULL,
    vendor_category  TEXT
);

-- One row per MTA employee who evaluates or supervises evaluations
CREATE TABLE evaluators (
    evaluator_id   TEXT PRIMARY KEY,
    first_name     TEXT NOT NULL,
    last_name      TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    department     TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('Project Manager','Supervisor','Director','Analyst')),
    active         INTEGER NOT NULL CHECK (active IN (0,1)),
    supervisor_id  TEXT REFERENCES evaluators(evaluator_id)
);

-- One row per contract
CREATE TABLE contracts (
    contract_no    TEXT PRIMARY KEY CHECK (contract_no GLOB 'C-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9]'),
    vendor_id      TEXT NOT NULL REFERENCES vendors(vendor_id),
    department     TEXT NOT NULL,
    pm_id          TEXT REFERENCES evaluators(evaluator_id),
    contract_type  TEXT NOT NULL,
    contract_year  INTEGER NOT NULL CHECK (contract_year BETWEEN 2000 AND 2100),
    award_value    REAL NOT NULL CHECK (award_value > 0),
    start_date     TEXT NOT NULL,
    end_date       TEXT NOT NULL,
    CHECK (end_date > start_date)
);

-- One row per evaluation period of a contract (the "golden record")
CREATE TABLE evaluations (
    eval_id              TEXT PRIMARY KEY,
    contract_no          TEXT NOT NULL REFERENCES contracts(contract_no),
    period_end           TEXT NOT NULL,
    eval_type            TEXT NOT NULL CHECK (eval_type IN ('Interim','Final')),
    status               TEXT NOT NULL CHECK (status IN ('Not Started','Requested','In Progress','Submitted','Approved')),
    last_updated         TEXT,
    status_source        TEXT NOT NULL,
    source_systems       TEXT NOT NULL,
    needs_manual_review  INTEGER NOT NULL DEFAULT 0,
    rating_quality       INTEGER CHECK (rating_quality BETWEEN 1 AND 5),
    rating_timeliness    INTEGER CHECK (rating_timeliness BETWEEN 1 AND 5),
    rating_safety        INTEGER CHECK (rating_safety BETWEEN 1 AND 5),
    rating_compliance    INTEGER CHECK (rating_compliance BETWEEN 1 AND 5),
    rating_management    INTEGER CHECK (rating_management BETWEEN 1 AND 5),
    overall_score        REAL,
    UNIQUE (contract_no, period_end)
);

-- One row per status change of an evaluation
CREATE TABLE status_history (
    eval_id     TEXT NOT NULL REFERENCES evaluations(eval_id),
    status      TEXT NOT NULL,
    changed_at  TEXT NOT NULL,
    PRIMARY KEY (eval_id, status)
);

-- One row per source record that fed a golden record (data lineage)
CREATE TABLE lineage (
    eval_id              TEXT NOT NULL REFERENCES evaluations(eval_id),
    source               TEXT NOT NULL,
    source_ref           TEXT NOT NULL,
    source_status        TEXT,
    source_last_updated  TEXT,
    was_selected         INTEGER NOT NULL
);

-- One row per open procurement where a vendor is bidding (drives priority)
CREATE TABLE solicitations (
    solicitation_no  TEXT PRIMARY KEY,
    vendor_id        TEXT NOT NULL REFERENCES vendors(vendor_id),
    bid_due_date     TEXT NOT NULL
);

-- One row per data problem found and fixed during cleanup (audit trail)
CREATE TABLE data_issues (
    source          TEXT,
    record_ref      TEXT,
    field           TEXT,
    issue           TEXT,
    action          TEXT,
    original_value  TEXT
);

-- Workflow engine outputs ---------------------------------------------------
-- One row per open evaluation for this weekly run
CREATE TABLE work_queue (
    eval_id            TEXT PRIMARY KEY REFERENCES evaluations(eval_id),
    as_of              TEXT NOT NULL,
    assigned_to        TEXT REFERENCES evaluators(evaluator_id),
    assignment_reason  TEXT NOT NULL,
    due_date           TEXT NOT NULL,
    days_overdue       INTEGER NOT NULL,
    sla_state          TEXT NOT NULL,
    escalation_tier    INTEGER NOT NULL,
    escalate_to        TEXT,
    stalled            INTEGER NOT NULL,
    days_since_update  INTEGER,
    vendor_bidding     INTEGER NOT NULL,
    priority_score     REAL NOT NULL,
    priority_rank      INTEGER NOT NULL
);

-- One row per message the automation will send (consolidated per recipient)
CREATE TABLE notifications (
    notification_id  TEXT PRIMARY KEY,
    as_of            TEXT NOT NULL,
    recipient_id     TEXT NOT NULL REFERENCES evaluators(evaluator_id),
    recipient_email  TEXT NOT NULL,
    kind             TEXT NOT NULL,
    eval_count       INTEGER NOT NULL,
    eval_ids         TEXT NOT NULL,
    subject          TEXT NOT NULL,
    body             TEXT NOT NULL
);

-- One row per automated decision (who was assigned/escalated and why)
CREATE TABLE audit_log (
    as_of     TEXT NOT NULL,
    eval_id   TEXT NOT NULL,
    action    TEXT NOT NULL,
    detail    TEXT NOT NULL
);

CREATE INDEX ix_eval_status   ON evaluations(status);
CREATE INDEX ix_eval_contract ON evaluations(contract_no);
CREATE INDEX ix_contract_pm   ON contracts(pm_id);
CREATE INDEX ix_hist_time     ON status_history(changed_at);
