"""Load the cleaned tables into a relational database (SQLite) with constraints."""
from __future__ import annotations

import sqlite3

import pandas as pd

from config import DB_PATH, SQL_DIR

TABLE_ORDER = ["vendors", "evaluators", "contracts", "evaluations", "status_history",
               "lineage", "solicitations", "data_issues", "work_queue", "notifications", "audit_log"]


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        elif df[col].dtype == object:
            df[col] = df[col].map(lambda v: v.isoformat() if hasattr(v, "isoformat") else v)
        if pd.api.types.is_bool_dtype(df[col]):
            df[col] = df[col].astype(int)
    return df.astype(object).where(pd.notna(df), None)


def build_database(tables: dict[str, pd.DataFrame], path=DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((SQL_DIR / "schema.sql").read_text())
    for name in TABLE_ORDER:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
        df = _prep(tables[name])[cols]               # schema decides the columns
        con.executemany(f"INSERT INTO {name} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        df.itertuples(index=False, name=None))
    con.executescript((SQL_DIR / "views.sql").read_text())
    con.commit()
    return con


def query(con, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, con)
