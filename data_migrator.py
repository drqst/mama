# mama — Oracle to PostgreSQL migration tool
# data_migrator.py  —  bulk data copy with progress and parallelism

from __future__ import annotations
import logging
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Any
import oracledb
import psycopg2
import psycopg2.extras
from tqdm import tqdm

log = logging.getLogger("mama.data")


def _adapt_value(v: Any) -> Any:
    """Coerce Oracle-specific types to Python types psycopg2 can handle."""
    if v is None:
        return None
    # Oracle LOBs
    if hasattr(v, "read"):
        data = v.read()
        return data
    # cx_Oracle / oracledb CLOB string wrapper
    if isinstance(v, str):
        return v
    # Oracle DATE — already datetime in python-oracledb
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v
    return v


def migrate_table(
    oracle_conn: oracledb.Connection,
    pg_conn: psycopg2.extensions.connection,
    schema_o: str,
    schema_p: str,
    table: str,
    batch_size: int = 10_000,
    dry_run: bool = False,
) -> dict:
    """
    Migrate all rows of one table from Oracle to PostgreSQL.

    Returns a dict with table name, rows copied, and any error.
    """
    result = {"table": table, "rows": 0, "error": None}

    try:
        # Fetch column names from Oracle
        cur_o = oracle_conn.cursor()
        cur_o.execute(f'SELECT * FROM "{schema_o}"."{table}" WHERE 1=0')
        col_names = [d[0].lower() for d in cur_o.description]
        quoted_cols = ", ".join(f'"{c}"' for c in col_names)
        placeholders = "%s" + ", %s" * (len(col_names) - 1)
        insert_sql = (
            f'INSERT INTO "{schema_p}"."{table.lower()}" ({quoted_cols})\n'
            f'VALUES ({placeholders})\n'
            f'ON CONFLICT DO NOTHING'
        )

        # Count for progress bar
        cur_o.execute(f'SELECT COUNT(*) FROM "{schema_o}"."{table}"')
        total = cur_o.fetchone()[0]

        # Stream rows in batches
        cur_o.arraysize = batch_size
        cur_o.execute(f'SELECT * FROM "{schema_o}"."{table}"')

        with tqdm(total=total, desc=f"  {table}", unit="rows", leave=False) as pbar:
            while True:
                rows = cur_o.fetchmany(batch_size)
                if not rows:
                    break
                adapted = [tuple(_adapt_value(v) for v in row) for row in rows]
                if not dry_run:
                    with pg_conn.cursor() as cur_p:
                        psycopg2.extras.execute_values(cur_p, insert_sql, adapted, page_size=batch_size)
                    pg_conn.commit()
                result["rows"] += len(adapted)
                pbar.update(len(adapted))

    except Exception as exc:
        result["error"] = str(exc)
        log.error("Table %s failed: %s", table, exc)
        try:
            pg_conn.rollback()
        except Exception:
            pass

    return result


def migrate_all_tables(
    oracle_dsn: str,
    oracle_schema: str,
    pg_dsn: str,
    pg_schema: str,
    tables: List[str],
    batch_size: int = 10_000,
    workers: int = 4,
    dry_run: bool = False,
    thick_mode: bool = False,
) -> List[dict]:
    """
    Migrate data for all given tables in parallel using a thread pool.
    Each thread gets its own Oracle and PostgreSQL connection.
    """
    results = []

    def _worker(table: str) -> dict:
        # Per-thread connections
        if thick_mode:
            oracledb.init_oracle_client()
        o_conn = oracledb.connect(oracle_dsn)
        p_conn = psycopg2.connect(pg_dsn)
        p_conn.autocommit = False
        try:
            return migrate_table(
                o_conn, p_conn,
                oracle_schema, pg_schema,
                table, batch_size, dry_run
            )
        finally:
            o_conn.close()
            p_conn.close()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_worker, t): t for t in tables}
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            status = "✅" if not res["error"] else "❌"
            log.info("%s %s: %d rows", status, res["table"], res["rows"])

    return results
