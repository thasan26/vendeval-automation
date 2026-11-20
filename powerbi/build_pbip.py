"""Generate the VENDEVAL Power BI report as a Power BI Project (.pbip).

    python powerbi/build_pbip.py

Writes, next to this script:
    VENDEVAL_Weekly_Status.pbip                 <- double-click this in Power BI Desktop
    VENDEVAL_Weekly_Status.SemanticModel/       <- star schema, relationships, DAX measures (TMDL)
    VENDEVAL_Weekly_Status.Report/              <- 3 report pages (PBIR JSON)

The model reads VENDEVAL_PowerBI_Data.xlsx straight from this GitHub repo (parameter
DataFileUrl), so the project opens on any machine: open it, click Refresh, done.
Measures come from measures.dax, so that file stays the single source of truth.
"""
import json
import re
import shutil
import uuid
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
NAME = "VENDEVAL_Weekly_Status"
DATA_XLSX = HERE / "VENDEVAL_PowerBI_Data.xlsx"
DATA_URL = ("https://raw.githubusercontent.com/thasan26/vendeval-automation/main/"
            "powerbi/VENDEVAL_PowerBI_Data.xlsx")
SM = HERE / f"{NAME}.SemanticModel"
RPT = HERE / f"{NAME}.Report"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
NS = uuid.UUID("6f1d6f3e-0b7a-4d8e-9a51-6a3c2f6f0e11")  # stable ids across rebuilds

TABLES = ["Evaluations", "Contracts", "Vendors", "Evaluators", "WorkQueue", "StatusHistory",
          "Calendar", "QAChecks", "DataIssues", "ReportInfo"]

# (many side, one side) - all many-to-one, single direction
RELATIONSHIPS = [
    ("Evaluations.contract_no", "Contracts.contract_no"),
    ("Contracts.vendor_id", "Vendors.vendor_id"),
    ("WorkQueue.eval_id", "Evaluations.eval_id"),
    ("WorkQueue.assigned_to", "Evaluators.evaluator_id"),
    ("StatusHistory.eval_id", "Evaluations.eval_id"),
    ("StatusHistory.changed_date", "Calendar.date"),
]

SUMMARIZE = {"Contracts.award_value": "sum", "DataIssues.count": "sum",
             "Evaluations.overall_score": "average"}
SUMMARIZE.update({f"Evaluations.rating_{r}": "average"
                  for r in ("quality", "timeliness", "safety", "compliance", "management")})
COLUMN_FORMAT = {"Contracts.award_value": "\\$#,0"}
SORT_BY = {"Evaluations.status": "status_order"}

MEASURE_FORMAT = {"% Complete": "0%", "QA Pass Rate": "0%", "Weekly Pace (4-wk avg)": "#,0.0",
                  "Report Date": "dddd, mmmm d, yyyy"}
TEXT_MEASURES = {"WoW Label", "QA Status Text", "Report Subtitle"}

# Helper measures used by visuals in place of visual-level filters
EXTRA_MEASURES = {
    "Approvals (Last 12 Weeks)":
        "CALCULATE ( [Approvals], KEEPFILTERS ( 'Calendar'[weeks_ago] >= 0 && 'Calendar'[weeks_ago] <= 11 ) )",
    "PM Outreach Items":
        "CALCULATE ( [Open Items], WorkQueue[lane] = \"PM outreach\" )",
}


def uid(*parts):
    return str(uuid.uuid5(NS, "/".join(parts)))


def q(name):
    """Quote a TMDL object name when needed."""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path, obj):
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- semantic model
def load_measures():
    text = (HERE / "measures.dax").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("//")]
    measures = {}
    for block in re.split(r"\n\s*\n", "\n".join(lines)):
        block = block.strip()
        if not block:
            continue
        name, expr = block.split("=", 1)
        measures[name.strip()] = expr.strip()
    measures.update(EXTRA_MEASURES)
    return measures


def column_types(df):
    out = []
    for col, dtype in df.dtypes.items():
        if pd.api.types.is_datetime64_any_dtype(dtype):
            out.append((col, "dateTime", "type date"))
        elif pd.api.types.is_bool_dtype(dtype):
            out.append((col, "boolean", "type logical"))
        elif pd.api.types.is_integer_dtype(dtype):
            out.append((col, "int64", "Int64.Type"))
        elif pd.api.types.is_float_dtype(dtype):
            out.append((col, "double", "type number"))
        else:
            out.append((col, "string", "type text"))
    return out


def table_tmdl(table, cols):
    t = [f"table {q(table)}", f"\tlineageTag: {uid('table', table)}"]
    if table == "Calendar":
        t.append("\tdataCategory: Time")
    t.append("")
    for col, dtype, _ in cols:
        key = f"{table}.{col}"
        t.append(f"\tcolumn {q(col)}")
        t.append(f"\t\tdataType: {dtype}")
        if table == "Calendar" and col == "date":
            t.append("\t\tisKey")
        if key in COLUMN_FORMAT:
            t.append(f"\t\tformatString: {COLUMN_FORMAT[key]}")
        elif dtype == "dateTime":
            t.append("\t\tformatString: mm/dd/yyyy")
        elif dtype == "int64":
            t.append("\t\tformatString: 0")
        t.append(f"\t\tlineageTag: {uid('column', key)}")
        t.append(f"\t\tsummarizeBy: {SUMMARIZE.get(key, 'none')}")
        t.append(f"\t\tsourceColumn: {col}")
        if key in SORT_BY:
            t.append(f"\t\tsortByColumn: {q(SORT_BY[key])}")
        t.append("")
        t.append("\t\tannotation SummarizationSetBy = User")
        t.append("")
    types = ", ".join(f'{{"{c}", {m}}}' for c, _, m in cols)
    t += [f"\tpartition {q(table)} = m",
          "\t\tmode: import",
          "\t\tsource =",
          "\t\t\t\tlet",
          "\t\t\t\t    Source = Excel.Workbook(Web.Contents(DataFileUrl), null, true),",
          f'\t\t\t\t    Sheet = Source{{[Item="{table}",Kind="Sheet"]}}[Data],',
          "\t\t\t\t    Promoted = Table.PromoteHeaders(Sheet, [PromoteAllScalars=true]),",
          f"\t\t\t\t    Typed = Table.TransformColumnTypes(Promoted, {{{types}}})",
          "\t\t\t\tin",
          "\t\t\t\t    Typed",
          "",
          "\tannotation PBI_ResultType = Table",
          ""]
    return "\n".join(t)


def measures_tmdl(measures):
    t = ["table _Measures", f"\tlineageTag: {uid('table', '_Measures')}", ""]
    for name, expr in measures.items():
        expr_lines = expr.splitlines()
        if len(expr_lines) == 1:
            t.append(f"\tmeasure {q(name)} = {expr_lines[0]}")
        else:
            t.append(f"\tmeasure {q(name)} =")
            t += [f"\t\t\t{ln.strip()}" for ln in expr_lines]
        if name in MEASURE_FORMAT:
            t.append(f"\t\tformatString: {MEASURE_FORMAT[name]}")
        elif name not in TEXT_MEASURES:
            t.append("\t\tformatString: #,0")
        t.append(f"\t\tlineageTag: {uid('measure', name)}")
        t.append("")
    t += ["\tcolumn Placeholder",
          "\t\tdataType: string",
          "\t\tisHidden",
          f"\t\tlineageTag: {uid('column', '_Measures.Placeholder')}",
          "\t\tsummarizeBy: none",
          "\t\tsourceColumn: Placeholder",
          "",
          "\t\tannotation SummarizationSetBy = Automatic",
          "",
          "\tpartition _Measures = m",
          "\t\tmode: import",
          "\t\tsource =",
          "\t\t\t\tlet",
          "\t\t\t\t    Source = #table(type table [Placeholder = text], {})",
          "\t\t\t\tin",
          "\t\t\t\t    Source",
          "",
          "\tannotation PBI_ResultType = Table",
          ""]
    return "\n".join(t)


def build_model():
    if SM.exists():
        shutil.rmtree(SM)
    d = SM / "definition"
    sheets = pd.read_excel(DATA_XLSX, sheet_name=None)
    for table in TABLES:
        write(d / "tables" / f"{table}.tmdl", table_tmdl(table, column_types(sheets[table])))
    write(d / "tables" / "_Measures.tmdl", measures_tmdl(load_measures()))

    rel = []
    for frm, to in RELATIONSHIPS:
        rel += [f"relationship {uid('rel', frm, to)}", f"\tfromColumn: {frm}", f"\ttoColumn: {to}", ""]
    write(d / "relationships.tmdl", "\n".join(rel))

    write(d / "expressions.tmdl",
          f'expression DataFileUrl = "{DATA_URL}" meta [IsParameterQuery=true, Type="Text", '
          f'IsParameterQueryRequired=true]\n'
          f"\tlineageTag: {uid('expression', 'DataFileUrl')}\n\n"
          "\tannotation PBI_ResultType = Text\n")

    order = json.dumps(["DataFileUrl"] + TABLES + ["_Measures"])
    write(d / "model.tmdl", "\n".join([
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        f"annotation PBI_QueryOrder = {order}",
        "",
        *[f"ref table {q(t)}" for t in ["_Measures"] + TABLES],
        "",
        "ref cultureInfo en-US",
        ""]))
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1567\n")
    (d / "cultures").mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "_template" / "en-US.tmdl", d / "cultures" / "en-US.tmdl")
    write_json(SM / "definition.pbism", {"version": "4.0", "settings": {}})
    write_json(SM / ".platform", {
        "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": uid("logical", "model")}})
    write_json(SM / "diagramLayout.json", {
        "version": "1.1.0", "diagrams": [{"ordinal": 0, "scrollPosition": {"x": 0, "y": 0}, "nodes": [],
                                          "name": "All tables", "zoomValue": 100}],
        "selectedDiagram": "All tables", "defaultDiagram": "All tables"})


# --------------------------------------------------------------------------- report helpers
def lit(v):
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": f"{v}D"}}}
    return {"expr": {"Literal": {"Value": "'" + str(v).replace("'", "''") + "'"}}}


def color(hex_):
    return {"solid": {"color": lit(hex_)}}


def col(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def mea(prop):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "_Measures"}}, "Property": prop}}


def sum_(entity, prop):
    return {"Aggregation": {"Expression": col(entity, prop), "Function": 0}}


def proj(field):
    if "Measure" in field:
        p = field["Measure"]["Property"]
        return {"field": field, "queryRef": f"_Measures.{p}", "nativeQueryRef": p}
    if "Aggregation" in field:
        c = field["Aggregation"]["Expression"]["Column"]
        e, p = c["Expression"]["SourceRef"]["Entity"], c["Property"]
        return {"field": field, "queryRef": f"Sum({e}.{p})", "nativeQueryRef": f"Sum of {p}"}
    c = field["Column"]
    e, p = c["Expression"]["SourceRef"]["Entity"], c["Property"]
    return {"field": field, "queryRef": f"{e}.{p}", "nativeQueryRef": p}


class Page:
    def __init__(self, pid, display):
        self.pid, self.display, self.visuals, self.z = pid, display, [], 0

    def add(self, vid, vtype, x, y, w, h, title=None, query=None, objects=None, sort=None):
        self.z += 1000
        container = {"background": [{"properties": {"show": lit(vtype not in ("textbox",))}}]}
        if title:
            container["title"] = [{"properties": {"show": lit(True), "text": lit(title)}}]
        elif vtype in ("card", "textbox"):
            container["title"] = [{"properties": {"show": lit(False)}}]
        visual = {"visualType": vtype}
        if query:
            visual["query"] = {"queryState": {role: {"projections": [proj(f) for f in fields]}
                                              for role, fields in query.items()}}
            if sort:
                field, direction = sort
                visual["query"]["sortDefinition"] = {"sort": [{"field": field, "direction": direction}],
                                                     "isDefaultSort": False}
        visual["objects"] = objects or {}
        visual["visualContainerObjects"] = container
        visual["drillFilterOtherVisuals"] = True
        self.visuals.append({
            "$schema": f"{SCHEMA}/item/report/definition/visualContainer/1.3.0/schema.json",
            "name": vid,
            "position": {"x": x, "y": y, "z": self.z, "height": h, "width": w, "tabOrder": self.z},
            "visual": visual})

    # convenience builders
    def text(self, vid, x, y, w, h, text, size=20, bold=True, colour="#0b0b0b"):
        run = {"value": text, "textStyle": {"fontSize": f"{size}pt", "color": colour}}
        if bold:
            run["textStyle"]["fontWeight"] = "bold"
        self.add(vid, "textbox", x, y, w, h,
                 objects={"general": [{"properties": {"paragraphs": [{"textRuns": [run]}]}}]})

    def card(self, vid, x, y, w, h, measure, size=28, colour="#0b0b0b", label=True):
        self.add(vid, "card", x, y, w, h, query={"Values": [mea(measure)]}, objects={
            "labels": [{"properties": {"fontSize": lit(size), "color": color(colour),
                                       "labelDisplayUnits": lit(1)}}],
            "categoryLabels": [{"properties": {"show": lit(label)}}]})

    def slicer(self, vid, x, y, w, h, entity, prop):
        self.add(vid, "slicer", x, y, w, h, query={"Values": [col(entity, prop)]},
                 objects={"data": [{"properties": {"mode": lit("Dropdown")}}]})

    def chart(self, vid, vtype, x, y, w, h, title, category, values, series=None, sort=None,
              labels=True, categorical=False):
        query = {"Category": [category], "Y": values}
        if series:
            query["Series"] = [series]
        objects = {"labels": [{"properties": {"show": lit(labels)}}]}
        if categorical:
            objects["categoryAxis"] = [{"properties": {"axisType": lit("Categorical")}}]
        self.add(vid, vtype, x, y, w, h, title=title, query=query, objects=objects, sort=sort)


def build_report():
    if RPT.exists():
        shutil.rmtree(RPT)
    d = RPT / "definition"
    BLUE, GREY, GREEN = "#1c5cab", "#605e5c", "#006300"

    # Page 1 - Weekly Status ------------------------------------------------------------
    p1 = Page("weekly_status", "Weekly Status")
    p1.text("title", 20, 10, 700, 40, "VENDEVAL Weekly Status — Supplier Relations")
    p1.card("subtitle", 20, 48, 700, 32, "Report Subtitle", size=11, colour=GREY, label=False)
    p1.slicer("slicer_dept", 760, 10, 165, 62, "Contracts", "department")
    p1.slicer("slicer_year", 935, 10, 160, 62, "Contracts", "contract_year")
    p1.slicer("slicer_type", 1105, 10, 155, 62, "Contracts", "contract_type")
    p1.card("qa_banner", 20, 85, 1240, 40, "QA Status Text", size=12, colour=GREEN, label=False)
    kpis = ["Total Evaluations", "% Complete", "Open with PMs", "180+ Days Overdue",
            "Approved This Week", "Vendors Bidding with Open Evals"]
    for i, m in enumerate(kpis):
        p1.card(f"kpi_{i + 1}", 20 + i * 208, 135, 200, 90, m, colour=BLUE)
    p1.card("kpi_wow", 20 + 4 * 208, 222, 200, 22, "WoW Label", size=9, colour=GREY, label=False)
    p1.chart("status_by_year", "hundredPercentStackedBarChart", 20, 250, 1240, 215,
             "Evaluation status by contract year",
             col("Contracts", "contract_year"), [mea("Total Evaluations")],
             series=col("Evaluations", "status"),
             sort=(col("Contracts", "contract_year"), "Ascending"))
    p1.chart("approvals_per_week", "clusteredColumnChart", 20, 475, 615, 235,
             "Evaluations approved per week (last 12 weeks)",
             col("Calendar", "week_ending"), [mea("Approvals (Last 12 Weeks)")],
             sort=(col("Calendar", "week_ending"), "Ascending"), categorical=True)
    p1.chart("days_overdue", "clusteredColumnChart", 645, 475, 615, 235,
             "Outstanding evaluations by days overdue (waiting on PMs)",
             col("WorkQueue", "tier_label"), [mea("PM Outreach Items")],
             sort=(col("WorkQueue", "tier_label"), "Ascending"))

    # Page 2 - Outreach Queue -----------------------------------------------------------
    p2 = Page("outreach_queue", "Outreach Queue")
    p2.text("title", 20, 10, 800, 40, "Outreach Queue — who to chase first")
    queue_cols = [col("WorkQueue", "priority_rank"), col("WorkQueue", "eval_id"),
                  col("Vendors", "vendor_name"), col("WorkQueue", "status"),
                  col("WorkQueue", "days_overdue"), col("Evaluators", "evaluator_name"),
                  col("WorkQueue", "tier_label"), col("WorkQueue", "vendor_bidding"),
                  col("WorkQueue", "stalled"), col("WorkQueue", "reassigned")]
    p2.add("queue_table", "tableEx", 20, 60, 830, 650, title="Priority work queue",
           query={"Values": queue_cols}, sort=(col("WorkQueue", "priority_rank"), "Ascending"))
    p2.card("open_items", 865, 60, 130, 90, "Open Items", colour=BLUE)
    p2.card("stalled", 1000, 60, 130, 90, "Stalled Items", colour=BLUE)
    p2.card("weeks_to_clear", 1135, 60, 125, 90, "Weeks to Clear Backlog", colour="#d03b3b")
    p2.chart("evaluator_load", "clusteredBarChart", 865, 160, 395, 550,
             "Open items by assignee", col("Evaluators", "evaluator_name"), [mea("Open Items")],
             sort=(mea("Open Items"), "Descending"))

    # Page 3 - Data Quality -------------------------------------------------------------
    p3 = Page("data_quality", "Data Quality")
    p3.text("title", 20, 10, 800, 40, "Data Quality — can this report be trusted?")
    p3.card("qa_rate", 20, 60, 200, 90, "QA Pass Rate", colour=GREEN)
    p3.card("qa_text", 230, 60, 1030, 90, "QA Status Text", size=14, colour=GREEN, label=False)
    p3.add("qa_table", "tableEx", 20, 160, 760, 550, title="Automated checks run before publishing",
           query={"Values": [col("QAChecks", "result"), col("QAChecks", "check"), col("QAChecks", "detail")]})
    p3.chart("issues", "clusteredBarChart", 790, 160, 470, 550, "Source data issues caught and fixed",
             col("DataIssues", "issue"), [sum_("DataIssues", "count")], series=col("DataIssues", "source"),
             sort=(sum_("DataIssues", "count"), "Descending"))

    pages = [p1, p2, p3]
    for p in pages:
        write_json(d / "pages" / p.pid / "page.json", {
            "$schema": f"{SCHEMA}/item/report/definition/page/1.2.0/schema.json",
            "name": p.pid, "displayName": p.display, "displayOption": "FitToPage",
            "height": 720, "width": 1280})
        for v in p.visuals:
            write_json(d / "pages" / p.pid / "visuals" / v["name"] / "visual.json", v)
    write_json(d / "pages" / "pages.json", {
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.pid for p in pages], "activePageName": pages[0].pid})
    write_json(d / "version.json", {
        "$schema": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0"})

    theme_file = "VENDEVAL_theme.json"
    write_json(d / "report.json", {
        "$schema": f"{SCHEMA}/item/report/definition/report/1.2.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.59", "type": "SharedResources"},
            "customTheme": {"name": theme_file, "reportVersionAtImport": "5.59", "type": "RegisteredResources"}},
        "layoutOptimization": "None",
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": theme_file, "path": theme_file, "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                     "allowChangeFilterTypes": True, "useDefaultAggregateDisplayName": True}})

    shutil.copytree(HERE / "_template" / "StaticResources", RPT / "StaticResources")
    (RPT / "StaticResources" / "RegisteredResources").mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "vendeval_theme.json", RPT / "StaticResources" / "RegisteredResources" / theme_file)
    write_json(RPT / "definition.pbir", {
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    write_json(RPT / ".platform", {
        "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": uid("logical", "report")}})


def main():
    build_model()
    build_report()
    write_json(HERE / f"{NAME}.pbip", {
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True}})
    print(f"Wrote {NAME}.pbip, {NAME}.SemanticModel/ and {NAME}.Report/ in {HERE}")


if __name__ == "__main__":
    main()
