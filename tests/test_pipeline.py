"""Unit tests for the cleanup rules and workflow rules, plus one end-to-end run.

    python -m pytest -q
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from standardize import (EXCEL_STATUS, VendorResolver, normalize_contract_no, parse_dt,  # noqa: E402
                         person_key, vendor_full_key, vendor_key)
from workflow import escalation_tier, is_stalled, priority_score, sla_state  # noqa: E402


# ------------------------------------------------------------ normalization
@pytest.mark.parametrize("raw", ["C-2022-10004", "C2022-10004", "C 2022 10004", " c-2022-10004 ", "C2022 10004"])
def test_contract_numbers_normalize(raw):
    assert normalize_contract_no(raw) == "C-2022-10004"


@pytest.mark.parametrize("raw", [None, "", "2022-10004", "C-22-10004", float("nan")])
def test_bad_contract_numbers_rejected(raw):
    assert normalize_contract_no(raw) is None


@pytest.mark.parametrize("raw", ["03/04/2025", "2025-03-04", "Mar 04, 2025", "2025-03-04T00:00:00",
                                 datetime(2025, 3, 4), date(2025, 3, 4), 45720])
def test_dates_parse(raw):
    assert parse_dt(raw).date() == date(2025, 3, 4)


def test_people_names_match_both_formats():
    assert person_key("Rodriguez, Maria") == person_key("Maria  Rodriguez") == "MARIA RODRIGUEZ"


def test_every_tracker_status_maps_to_lifecycle():
    assert set(EXCEL_STATUS.values()) <= {"Not Started", "Requested", "In Progress", "Submitted", "Approved"}


# ------------------------------------------------------------ vendor matching
VENDORS = pd.DataFrame({"vendor_id": ["V1", "V2", "V3"],
                        "vendor_name": ["Atlas Electric Inc.", "Atlas Electric LLC", "Harbor Supply & Sons Inc."]})


def test_suffix_spellings_are_same_entity():
    assert vendor_full_key("ATLAS ELECTRIC, INCORPORATED") == vendor_full_key("Atlas Electric Inc.")
    assert vendor_full_key("Atlas Electric LLC") != vendor_full_key("Atlas Electric Inc.")


def test_resolver_exact_and_loose():
    r = VendorResolver(VENDORS)
    assert r.resolve("ATLAS ELECTRIC INCORPORATED  ")[:2] == ("V1", "exact_key")
    assert r.resolve("Harbor Supply and Sons Inc")[:2] == ("V3", "exact_key")
    assert r.resolve("Harbor Supply")[:2] == ("V3", "loose_key")


def test_resolver_never_guesses_between_two_real_vendors():
    r = VendorResolver(VENDORS)
    assert r.resolve("Atlas Electric")[0] is None                       # Inc. or LLC?
    assert r.resolve("Atlas Electric", contract_vendor_id="V2")[:2] == ("V2", "contract_assisted")


def test_resolver_fixes_typos():
    r = VendorResolver(VENDORS)
    vid, method, score = r.resolve("Harbor Supyl & Sons Inc.")
    assert vid == "V3" and method.startswith("fuzzy")


# ------------------------------------------------------------ workflow rules
@pytest.mark.parametrize("days,tier", [(-10, 0), (0, 0), (1, 1), (29, 1), (30, 2), (89, 2), (90, 3), (180, 4), (900, 4)])
def test_escalation_ladder(days, tier):
    assert escalation_tier(days)[0] == tier


def test_sla_states():
    assert sla_state(5) == "Overdue"
    assert sla_state(-3) == "Due soon"
    assert sla_state(-30) == "On track"


def test_stall_rules():
    assert is_stalled("Requested", 15) and not is_stalled("Requested", 14)
    assert not is_stalled("Not Started", 500)        # nothing has been asked yet


def test_priority_prefers_old_overdue_bidding_vendors():
    base = dict(award_value=1_000_000, contract_year=2023, is_final=False)
    assert priority_score(300, vendor_bidding=True, **base) > priority_score(300, vendor_bidding=False, **base)
    assert priority_score(300, vendor_bidding=False, **base) > priority_score(10, vendor_bidding=False, **base)
    assert 0 <= priority_score(5000, 1e12, 2022, True, True) <= 100


# ------------------------------------------------------------ end to end
def test_full_pipeline_passes_all_checks(tmp_path, monkeypatch):
    import config
    import database
    import standardize
    import validate
    from workflow import WorkflowEngine

    out = standardize.run()
    eng = WorkflowEngine(out["evaluations"], out["contracts"], out["evaluators"], out["vendors"],
                         out["solicitations"], out["status_history"], config.AS_OF)
    out["work_queue"], out["notifications"], out["audit_log"] = eng.run()
    con = database.build_database(out, path=tmp_path / "test.db")
    checks = validate.run_checks(con, out)
    assert checks.passed.all(), checks[~checks.passed].to_string()
