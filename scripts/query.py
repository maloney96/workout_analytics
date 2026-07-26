#!/usr/bin/env python3
"""Run SQL against the warehouse and print it as a table.

    python scripts/query.py                          # list tables
    python scripts/query.py "select * from bronze.workouts"
    python scripts/query.py -f some_query.sql
    echo "select 1" | python scripts/query.py -     # '-' reads stdin

Opens read-only, so it is safe to run while the pipeline is writing. Wide values
are truncated to keep the output readable — bronze rows hold whole JSON documents.
Pass --wide to see them in full, or --csv to pipe elsewhere.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import config  # noqa: E402

TABLES_SQL = """
select table_name, estimated_size as rows, column_count as cols
from duckdb_tables()
where schema_name = 'bronze'
order by table_name
"""


def render(columns: list[str], rows: list[tuple], max_width: int) -> str:
    def cell(value: object) -> str:
        if value is None:
            text = ""
        elif isinstance(value, (dict, list)):
            # JSON columns come back as Python objects; show them as JSON, not reprs.
            text = json.dumps(value, default=str, ensure_ascii=False)
        else:
            text = str(value)
        text = text.replace("\n", " ")
        return text if len(text) <= max_width else text[: max_width - 1] + "…"

    body = [[cell(v) for v in row] for row in rows]
    widths = [
        max(len(columns[i]), *(len(r[i]) for r in body)) if body else len(columns[i])
        for i in range(len(columns))
    ]

    out = ["  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns))]
    out.append("  " + "  ".join("-" * w for w in widths))
    out += ["  " + "  ".join(v.ljust(widths[i]) for i, v in enumerate(r)) for r in body]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the DuckDB warehouse")
    parser.add_argument(
        "sql", nargs="?", help="SQL to run; '-' reads stdin; omit to list bronze tables"
    )
    parser.add_argument("-f", "--file", type=Path, help="read SQL from a file")
    parser.add_argument("-l", "--limit", type=int, default=20, help="max rows shown (default 20)")
    parser.add_argument("--wide", action="store_true", help="do not truncate wide values")
    parser.add_argument("--csv", action="store_true", help="emit CSV instead of a table")
    args = parser.parse_args()

    # stdin is only read when asked for with '-'. Sniffing isatty() instead would
    # hang whenever stdin is an open pipe that nobody is writing to.
    if args.file:
        sql = args.file.read_text()
    elif args.sql == "-":
        sql = sys.stdin.read()
    else:
        sql = args.sql or ""
    sql = sql.strip() or TABLES_SQL

    path = config.duckdb_path()
    if not path.exists():
        print(f"No warehouse at {path}. Run: python ingest/run_ingest.py --full-refresh",
              file=sys.stderr)
        return 1

    con = duckdb.connect(str(path), read_only=True)
    try:
        result = con.execute(sql)
        columns = [d[0] for d in result.description]
        rows = result.fetchall()
    except duckdb.Error as exc:
        print(f"Query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()

    if args.csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(columns)
        writer.writerows(rows)
        return 0

    shown = rows if args.limit <= 0 else rows[: args.limit]
    print(render(columns, shown, 10_000 if args.wide else 40))
    if len(rows) > len(shown):
        print(f"\n  ({len(shown)} of {len(rows)} rows — use --limit 0 for all)")
    else:
        print(f"\n  ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
