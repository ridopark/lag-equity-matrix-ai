"""RED for Q-53/Q-54: `lagmatrix.pg`, a direct postgres connection.

`load_news.py:37-55`, `load_vectors.py:32-33,97` and `extract_fires.py:54-57`
all reach postgres by shelling out through `ssh <host> kubectl -n copytrade
exec -i postgres-0 -- psql`. That only works from a laptop with a kubeconfig,
so the ingest CronJob cannot run any of them in-cluster (Q-54). Q-53 measured
`postgres.copytrade:5432` reachable from the `lagmatrix` namespace, so a direct
connection is viable and removes a cluster-admin-shaped dependency from a batch
job that only ever needed a database connection.

`src/lagmatrix/pg.py` does not exist yet, so every test below fails on
`ModuleNotFoundError: No module named 'lagmatrix.pg'` at collection. That is
the RED this file exists to produce.

Surface, sized to what the three call sites actually need:
  - `connect()` -- builds a real connection from `LAGMATRIX_PG_HOST`,
    `LAGMATRIX_PG_PORT` (default 5432), `LAGMATRIX_PG_DATABASE`,
    `LAGMATRIX_PG_USER`, `LAGMATRIX_PG_PASSWORD`.
  - `rows(conn, sql, params=())` -- runs a SELECT, returns `list[tuple]`.
  - `execute(conn, sql, params=())` -- runs a statement, commits.
  - `copy_csv(conn, table, columns, csv_text)` -- `COPY ... FROM STDIN`.

`conn` is an explicit first argument on `rows`/`execute`/`copy_csv` rather than
a module-level connection or a factory the functions reach for internally --
that is the injection seam: unit tests below drive them with `FakeConnection`,
a plain recorder, so none of this file needs a real postgres or `psycopg2`
installed. Only `connect()` and `test_select_1_round_trip_against_a_live_postgres`
touch `psycopg2`, and only the latter touches a real socket.

D-103 is why `test_rows_sends_params_separately_never_interpolated_into_sql`
and its `execute` counterpart exist: a value read out of ArangoDB reached
`psql` through an f-string there. The point of this module is that the same
mistake becomes structurally hard to make, so these tests fail if `rows()` or
`execute()` were implemented as `sql % params` or an f-string instead of
handing the driver a parameterised statement.
"""

from __future__ import annotations

import pytest

from conftest import pg_conn_or_skip
from lagmatrix import pg

MALICIOUS = "x'); DROP TABLE lagmatrix.news_article; --"

REQUIRED_PG_ENV = [
    "LAGMATRIX_PG_HOST",
    "LAGMATRIX_PG_DATABASE",
    "LAGMATRIX_PG_USER",
    "LAGMATRIX_PG_PASSWORD",
]


def _set_all_pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAGMATRIX_PG_HOST", "postgres.copytrade")
    monkeypatch.setenv("LAGMATRIX_PG_DATABASE", "orchestrator")
    monkeypatch.setenv("LAGMATRIX_PG_USER", "temporal")
    monkeypatch.setenv("LAGMATRIX_PG_PASSWORD", "s3cret")


class FakeCursor:
    """Records what it was asked to do; never touches a real database."""

    def __init__(self, fetch_rows: list[tuple] | None = None):
        self.executed: list[tuple[str, tuple | None]] = []
        self.copy_calls: list[tuple[str, str]] = []
        self._fetch = fetch_rows or []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._fetch

    def copy_expert(self, sql, file):
        self.copy_calls.append((sql, file.read()))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, fetch_rows: list[tuple] | None = None):
        self.cursor_obj = FakeCursor(fetch_rows)
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


# ---------------------------------------------------------------------------
# rows()
# ---------------------------------------------------------------------------

def test_rows_returns_fetchall_result_as_list_of_tuples():
    """Falsifiable: fails if `rows()` returns the cursor, `None`, or anything
    other than exactly what `fetchall()` produced."""
    conn = FakeConnection(fetch_rows=[(1, "AAPL"), (2, "MSFT")])
    result = pg.rows(conn, "SELECT id, symbol FROM t")
    assert result == [(1, "AAPL"), (2, "MSFT")]


def test_rows_sends_params_separately_never_interpolated_into_sql():
    """D-103 pin. Falsifiable: fails if `rows()` were implemented with
    `sql % params` or an f-string -- then `MALICIOUS` would appear inside the
    SQL text actually sent to the cursor, instead of arriving as a parameter."""
    conn = FakeConnection()
    pg.rows(conn, "SELECT * FROM t WHERE symbol = %s", (MALICIOUS,))
    sql_sent, params_sent = conn.cursor_obj.executed[0]
    assert MALICIOUS not in sql_sent
    assert params_sent == (MALICIOUS,)


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------

def test_execute_sends_params_separately_never_interpolated_into_sql():
    """D-103 pin, `execute`'s counterpart to the `rows` test above. Falsifiable:
    fails if `execute()` interpolates `params` into `sql` instead of passing
    both to the cursor as separate arguments."""
    conn = FakeConnection()
    pg.execute(conn, "DELETE FROM t WHERE symbol = %s", (MALICIOUS,))
    sql_sent, params_sent = conn.cursor_obj.executed[0]
    assert MALICIOUS not in sql_sent
    assert params_sent == (MALICIOUS,)


def test_execute_commits():
    """Falsifiable: fails if `execute()` runs the statement but never commits,
    which would silently drop every write on a non-autocommit connection."""
    conn = FakeConnection()
    pg.execute(conn, "DELETE FROM t WHERE symbol = %s", ("AAPL",))
    assert conn.committed is True


# ---------------------------------------------------------------------------
# copy_csv()
# ---------------------------------------------------------------------------

def test_copy_csv_uses_copy_from_stdin_with_the_given_table_and_columns():
    """Falsifiable: fails if `copy_csv()` builds row-at-a-time INSERTs instead
    of a COPY, or omits the table/column list from the statement -- this is
    the primitive `load_news.py`'s docstring says row-at-a-time INSERT is
    unusable for."""
    conn = FakeConnection()
    pg.copy_csv(conn, "lagmatrix.stage", ["id", "symbol"], "1,AAPL\n2,MSFT\n")
    sql_sent, body_sent = conn.cursor_obj.copy_calls[0]
    assert "COPY" in sql_sent
    assert "lagmatrix.stage" in sql_sent
    assert "id" in sql_sent and "symbol" in sql_sent
    assert body_sent == "1,AAPL\n2,MSFT\n"


def test_copy_csv_commits():
    """Falsifiable: fails if `copy_csv()` never commits, which would silently
    drop the load on a non-autocommit connection."""
    conn = FakeConnection()
    pg.copy_csv(conn, "lagmatrix.stage", ["id"], "1\n")
    assert conn.committed is True


# ---------------------------------------------------------------------------
# connect() -- credentials come from the environment, never a literal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_var", REQUIRED_PG_ENV)
def test_connect_fails_loudly_when_required_env_var_is_unset(monkeypatch, missing_var):
    """Falsifiable: fails if `connect()` instead attempts a default connection
    (e.g. treats a missing password as an empty one) instead of raising before
    ever reaching the driver."""
    _set_all_pg_env(monkeypatch)
    monkeypatch.delenv(missing_var, raising=False)
    with pytest.raises(RuntimeError, match=missing_var):
        pg.connect()


@pytest.mark.parametrize("blank_var", REQUIRED_PG_ENV)
def test_connect_fails_loudly_when_required_env_var_is_blank(monkeypatch, blank_var):
    """Falsifiable: fails if `connect()` treats an empty string as a present
    value and attempts to connect with it rather than rejecting it up front."""
    _set_all_pg_env(monkeypatch)
    monkeypatch.setenv(blank_var, "")
    with pytest.raises(RuntimeError, match=blank_var):
        pg.connect()


# ---------------------------------------------------------------------------
# Live round trip -- gated per tests/conftest.py's REQUIRE_LIVE convention.
# Skips on this machine unless a reachable postgres and psycopg2 are present;
# fails instead of skipping when LAGMATRIX_REQUIRE_LIVE=1.
# ---------------------------------------------------------------------------

def test_select_1_round_trip_against_a_live_postgres():
    """Falsifiable: fails if `connect()`+`rows()` cannot complete a real
    `SELECT 1` against an actual postgres instance -- the one thing none of
    the fake-backed tests above can prove. Read-only; writes nothing."""
    conn = pg_conn_or_skip()
    assert pg.rows(conn, "SELECT 1") == [(1,)]
