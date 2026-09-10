"""Direct postgres access (Q-53/Q-54), replacing `ssh <host> kubectl -n
copytrade exec -i postgres-0 -- psql` in `load_news.py`, `load_vectors.py` and
`extract_fires.py` -- that shell-out only works from a laptop with a
kubeconfig, so the ingest CronJob could not run any of them in-cluster.

`conn` is an explicit first argument on `rows`/`execute`/`copy_csv` rather than
a module-level connection, so callers can be driven by a fake in tests without
a real postgres or `psycopg2` installed. Only `connect()` touches `psycopg2`,
and imports it lazily so its absence does not break every test in this module.

Params are always passed to the driver separately from the SQL text, never
interpolated (D-103): a database-derived value once reached `psql` through an
f-string.
"""

from __future__ import annotations

import io
import os

REQUIRED_ENV_VARS = [
    "LAGMATRIX_PG_HOST",
    "LAGMATRIX_PG_DATABASE",
    "LAGMATRIX_PG_USER",
    "LAGMATRIX_PG_PASSWORD",
]


def connect():
    """A real psycopg2 connection, built from `LAGMATRIX_PG_*` env vars.

    Host, database, user and password are required -- unset or blank raises
    `RuntimeError` naming the missing variable, before the driver is touched.
    `LAGMATRIX_PG_PORT` defaults to 5432.
    """
    for name in REQUIRED_ENV_VARS:
        if not os.environ.get(name):
            raise RuntimeError(f"{name} is required to connect to postgres")

    import psycopg2

    return psycopg2.connect(
        host=os.environ["LAGMATRIX_PG_HOST"],
        port=int(os.environ.get("LAGMATRIX_PG_PORT", "5432")),
        dbname=os.environ["LAGMATRIX_PG_DATABASE"],
        user=os.environ["LAGMATRIX_PG_USER"],
        password=os.environ["LAGMATRIX_PG_PASSWORD"],
    )


def rows(conn, sql, params=()) -> list[tuple]:
    """Run a SELECT and return `fetchall()`'s result."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute(conn, sql, params=()) -> None:
    """Run a statement and commit."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()


def copy_csv(conn, table, columns, csv_text) -> None:
    """`COPY <table> (<columns>) FROM STDIN` with `csv_text`, then commit."""
    column_list = ", ".join(columns)
    sql = f"COPY {table} ({column_list}) FROM STDIN WITH (FORMAT csv)"
    with conn.cursor() as cur:
        cur.copy_expert(sql, io.StringIO(csv_text))
    conn.commit()
