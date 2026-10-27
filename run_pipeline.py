"""Run the full weekly VENDEVAL pipeline.

    python run_pipeline.py                     # report as of config.AS_OF
    python run_pipeline.py --as-of 2026-11-06  # any other Friday
    python run_pipeline.py --regenerate        # rebuild the synthetic source data first
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import config  # noqa: E402
import database  # noqa: E402
import report  # noqa: E402
import standardize  # noqa: E402
import validate  # noqa: E402
from workflow import WorkflowEngine  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=config.AS_OF)
    ap.add_argument("--regenerate", action="store_true")
    args = ap.parse_args()

    if args.regenerate or not (config.RAW / "access_evaluations.csv").exists():
        import generate_data
        generate_data.main()

    print("[1/5] Standardizing and merging Access + Excel + Archer ...")
    out = standardize.run()
    st = out["stats"]
    print(f"      {sum(st['source_rows'].values())} source rows -> {st['golden_records']} golden records, "
          f"{len(out['data_issues'])} issues logged")

    print("[2/5] Routing, SLA and escalation ...")
    engine = WorkflowEngine(out["evaluations"], out["contracts"], out["evaluators"], out["vendors"],
                            out["solicitations"], out["status_history"], args.as_of)
    out["work_queue"], out["notifications"], out["audit_log"] = engine.run()
    print(f"      {len(out['work_queue'])} open items, {len(out['notifications'])} consolidated messages")

    print("[3/5] Loading relational database ...")
    con = database.build_database(out)

    print("[4/5] Validating ...")
    checks = validate.run_checks(con, out)
    print(f"      {checks.passed.sum()}/{len(checks)} checks passed")

    print("[5/5] Writing dashboard and reports ...")
    report.write_all(con, out, checks, args.as_of)
    for name in ["evaluations", "work_queue", "notifications", "audit_log", "data_issues", "lineage"]:
        out[name].to_csv(config.PROCESSED / f"{name}.csv", index=False)

    if not checks.passed.all():
        print("VALIDATION FAILED - report marked as not publishable:")
        print(checks[~checks.passed].to_string(index=False))
        sys.exit(1)
    print(f"Done. Open reports/weekly_dashboard.html")


if __name__ == "__main__":
    main()
