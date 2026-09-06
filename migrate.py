#!/usr/bin/env python3
"""
mama — Oracle to PostgreSQL migration tool
==========================================

Usage examples
--------------
# Migrate full schema (schema + data + functions):
  python migrate.py \
    --oracle-user hr --oracle-password secret \
    --oracle-host db.example.com --oracle-port 1521 \
    --oracle-service ORCL \
    --oracle-schema HR \
    --pg-dsn "postgresql://pguser:pgpass@localhost:5432/mydb" \
    --pg-schema public

# Schema only (no data):
  python migrate.py ... --skip-data

# Specific tables only:
  python migrate.py ... --tables EMPLOYEES,DEPARTMENTS

# Dry-run (no writes):
  python migrate.py ... --dry-run
"""

from __future__ import annotations
import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

import oracledb
import psycopg2

from config import Config
from oracle_reader import OracleReader
from pg_writer import PgWriter
from data_migrator import migrate_all_tables
from sql_converter import clear_warnings

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("migration.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("mama")

BANNER = r"""
  __  __   ___    __  __   ___
 |  \/  | / _ \  |  \/  | / _ \
 | |\/| || |_| | | |\/| || |_| |
 |_|  |_| \__,_| |_|  |_| \__,_|

  Oracle → PostgreSQL migration tool
"""


# ── CLI ───────────────────────────────────────────────────────────────────────
def parse_args() -> Config:
    p = argparse.ArgumentParser(
        prog="mama",
        description="Migrate an Oracle schema to PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Oracle connection
    og = p.add_argument_group("Oracle connection")
    og.add_argument("--oracle-user",     required=True)
    og.add_argument("--oracle-password", required=True)
    og.add_argument("--oracle-host",     default="localhost")
    og.add_argument("--oracle-port",     type=int, default=1521)
    og.add_argument("--oracle-service",  default="", help="Service name (preferred)")
    og.add_argument("--oracle-sid",      default="", help="SID (fallback)")
    og.add_argument("--oracle-schema",   default="",
                    help="Schema to migrate (defaults to oracle-user)")
    og.add_argument("--oracle-thick",    action="store_true",
                    help="Use thick mode (requires Oracle Instant Client)")

    # PostgreSQL connection
    pg = p.add_argument_group("PostgreSQL connection")
    pg.add_argument("--pg-dsn",    required=True,
                    help='e.g. postgresql://user:pass@host:5432/dbname')
    pg.add_argument("--pg-schema", default="public",
                    help="Target schema in PostgreSQL (default: public)")

    # Scope
    sc = p.add_argument_group("Scope")
    sc.add_argument("--tables",          default="",
                    help="Comma-separated list of tables to migrate (default: all)")
    sc.add_argument("--skip-data",       action="store_true")
    sc.add_argument("--skip-functions",  action="store_true")
    sc.add_argument("--skip-triggers",   action="store_true")
    sc.add_argument("--skip-views",      action="store_true")
    sc.add_argument("--dry-run",         action="store_true",
                    help="Print DDL/DML without executing")

    # Tuning
    tn = p.add_argument_group("Tuning")
    tn.add_argument("--batch-size",  type=int, default=10_000)
    tn.add_argument("--workers",     type=int, default=4,
                    help="Parallel threads for data migration")
    tn.add_argument("--use-identity", action="store_true",
                    help="Use IDENTITY columns instead of sequences")

    args = p.parse_args()

    cfg = Config(
        oracle_user=args.oracle_user,
        oracle_password=args.oracle_password,
        oracle_host=args.oracle_host,
        oracle_port=args.oracle_port,
        oracle_service=args.oracle_service,
        oracle_sid=args.oracle_sid,
        oracle_schema=args.oracle_schema or args.oracle_user,
        oracle_thick_mode=args.oracle_thick,
        pg_dsn=args.pg_dsn,
        tables=[t.strip() for t in args.tables.split(",") if t.strip()],
        skip_data=args.skip_data,
        skip_functions=args.skip_functions,
        skip_triggers=args.skip_triggers,
        skip_views=args.skip_views,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        workers=args.workers,
        use_identity=args.use_identity,
    )
    return cfg


# ── Report builder ────────────────────────────────────────────────────────────
class Report:
    def __init__(self):
        self.entries: list[dict] = []
        self.started = datetime.datetime.now()

    def add(self, obj_type: str, name: str, status: str, warnings: list[str] = None, error: str = ""):
        self.entries.append({
            "type": obj_type,
            "name": name,
            "status": status,
            "warnings": warnings or [],
            "error": error,
        })

    def save(self, path: str = "conversion_report.md"):
        ok     = [e for e in self.entries if e["status"] == "ok"]
        review = [e for e in self.entries if e["status"] == "review"]
        failed = [e for e in self.entries if e["status"] == "failed"]

        lines = [
            "# mama — Conversion Report",
            f"Generated: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
            "",
            f"**Total objects:** {len(self.entries)}  |  "
            f"✅ {len(ok)}  |  ⚠️ {len(review)}  |  ❌ {len(failed)}",
            "",
        ]

        for section, emoji, items in [
            ("Objects needing review", "⚠️", review),
            ("Failed objects",         "❌", failed),
            ("Successfully migrated",  "✅", ok),
        ]:
            if not items:
                continue
            lines.append(f"## {emoji} {section}\n")
            lines.append("| Type | Name | Warnings / Error |")
            lines.append("|------|------|-----------------|")
            for e in items:
                detail = "; ".join(e["warnings"]) if e["warnings"] else e.get("error", "")
                lines.append(f"| {e['type']} | `{e['name']}` | {detail} |")
            lines.append("")

        Path(path).write_text("\n".join(lines), encoding="utf-8")
        log.info("Conversion report saved to %s", path)


# ── Main migration orchestrator ───────────────────────────────────────────────
def run(cfg: Config):
    print(BANNER)
    report = Report()

    # ── Connect to Oracle ─────────────────────────────────────────────────────
    log.info("Connecting to Oracle %s:%d …", cfg.oracle_host, cfg.oracle_port)
    if cfg.oracle_thick_mode:
        oracledb.init_oracle_client()

    if cfg.oracle_service:
        dsn = oracledb.makedsn(cfg.oracle_host, cfg.oracle_port, service_name=cfg.oracle_service)
    elif cfg.oracle_sid:
        dsn = oracledb.makedsn(cfg.oracle_host, cfg.oracle_port, sid=cfg.oracle_sid)
    else:
        log.error("Provide --oracle-service or --oracle-sid")
        sys.exit(1)

    oracle_conn = oracledb.connect(user=cfg.oracle_user, password=cfg.oracle_password, dsn=dsn)
    reader = OracleReader(oracle_conn, cfg.oracle_schema)

    # ── Connect to PostgreSQL ─────────────────────────────────────────────────
    log.info("Connecting to PostgreSQL …")
    pg_conn = psycopg2.connect(cfg.pg_dsn)
    pg_conn.autocommit = False
    writer = PgWriter(pg_conn,
                      target_schema=cfg.pg_schema or "public",
                      use_identity=cfg.use_identity,
                      dry_run=cfg.dry_run)
    writer.ensure_schema()

    # ─────────────────────────────────────────────────────────────────────────
    #  1. Sequences
    # ─────────────────────────────────────────────────────────────────────────
    log.info("── Sequences ──────────────────────────────────────────────────")
    sequences = reader.sequences()
    log.info("Found %d sequences", len(sequences))
    for seq in sequences:
        try:
            writer.create_sequence(seq)
            log.info("  ✅ SEQ %s", seq.name)
            report.add("SEQUENCE", seq.name, "ok")
        except Exception as exc:
            log.error("  ❌ SEQ %s: %s", seq.name, exc)
            report.add("SEQUENCE", seq.name, "failed", error=str(exc))

    # ─────────────────────────────────────────────────────────────────────────
    #  2. Tables (structure only)
    # ─────────────────────────────────────────────────────────────────────────
    log.info("── Tables ─────────────────────────────────────────────────────")
    table_names = reader.tables(cfg.tables or None)
    log.info("Found %d tables", len(table_names))

    tables_meta = []
    for tname in table_names:
        from oracle_reader import OracleTable
        t = OracleTable(
            name=tname,
            columns=reader.columns(tname),
            constraints=reader.constraints(tname),
            indexes=reader.indexes(tname),
        )
        tables_meta.append(t)

        try:
            writer.create_table(t)
            warns = writer.warnings[-len(writer.warnings):]
            status = "review" if warns else "ok"
            log.info("  %s TABLE %s", "⚠️" if warns else "✅", tname)
            report.add("TABLE", tname, status, warns)
        except Exception as exc:
            log.error("  ❌ TABLE %s: %s", tname, exc)
            report.add("TABLE", tname, "failed", error=str(exc))

    # ─────────────────────────────────────────────────────────────────────────
    #  3. Constraints and Indexes
    # ─────────────────────────────────────────────────────────────────────────
    log.info("── Constraints & Indexes ──────────────────────────────────────")
    for t in tables_meta:
        before = len(writer.warnings)
        writer.add_constraints(t)
        for idx in t.indexes:
            writer.create_index(idx)
        added = writer.warnings[before:]
        if added:
            log.warning("  ⚠️ %s: %d constraint/index warnings", t.name, len(added))

    # ─────────────────────────────────────────────────────────────────────────
    #  4. Views
    # ─────────────────────────────────────────────────────────────────────────
    if not cfg.skip_views:
        log.info("── Views ──────────────────────────────────────────────────────")
        views = reader.views()
        log.info("Found %d views", len(views))
        for view in views:
            before = len(writer.warnings)
            writer.create_view(view)
            new_warns = writer.warnings[before:]
            status = "review" if new_warns else "ok"
            log.info("  %s VIEW %s", "⚠️" if new_warns else "✅", view.name)
            report.add("VIEW", view.name, status, new_warns)

    # ─────────────────────────────────────────────────────────────────────────
    #  5. PL/SQL functions, procedures, triggers
    # ─────────────────────────────────────────────────────────────────────────
    obj_types = []
    if not cfg.skip_functions:
        obj_types += ["FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE BODY", "TYPE"]
    if not cfg.skip_triggers:
        obj_types += ["TRIGGER"]

    if obj_types:
        log.info("── PL/SQL objects ─────────────────────────────────────────────")
        src_objects = reader.source_objects(obj_types)
        log.info("Found %d PL/SQL objects", len(src_objects))
        for obj in src_objects:
            before = len(writer.warnings)
            writer.create_source_object(obj)
            new_warns = writer.warnings[before:]
            has_error = any("failed to create" in w for w in new_warns)
            status = "failed" if has_error else ("review" if new_warns else "ok")
            icon = "❌" if has_error else ("⚠️" if new_warns else "✅")
            log.info("  %s %s %s", icon, obj.type, obj.name)
            report.add(obj.type, obj.name, status, new_warns)

    # ─────────────────────────────────────────────────────────────────────────
    #  6. Data migration
    # ─────────────────────────────────────────────────────────────────────────
    if not cfg.skip_data and table_names:
        log.info("── Data ───────────────────────────────────────────────────────")
        data_results = migrate_all_tables(
            oracle_dsn=f"{cfg.oracle_user}/{cfg.oracle_password}@{dsn}",
            oracle_schema=cfg.oracle_schema,
            pg_dsn=cfg.pg_dsn,
            pg_schema=writer.schema,
            tables=table_names,
            batch_size=cfg.batch_size,
            workers=cfg.workers,
            dry_run=cfg.dry_run,
            thick_mode=cfg.oracle_thick_mode,
        )
        total_rows = sum(r["rows"] for r in data_results)
        failed_tables = [r for r in data_results if r["error"]]
        log.info("Data migration complete: %d rows across %d tables (%d failed)",
                 total_rows, len(data_results), len(failed_tables))

    # ─────────────────────────────────────────────────────────────────────────
    #  7. Verification: row count comparison
    # ─────────────────────────────────────────────────────────────────────────
    if not cfg.skip_data and not cfg.dry_run:
        log.info("── Row count verification ──────────────────────────────────────")
        mismatch = 0
        for tname in table_names:
            o_count = reader.row_count(tname)
            with pg_conn.cursor() as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{writer.schema}"."{tname.lower()}"')
                p_count = cur.fetchone()[0]
            match = "✅" if o_count == p_count else "⚠️"
            if o_count != p_count:
                mismatch += 1
                log.warning("  ⚠️ %s: Oracle=%d  PG=%d", tname, o_count, p_count)
            else:
                log.info("  ✅ %s: %d rows", tname, o_count)
        if mismatch:
            log.warning("%d table(s) have row count mismatches — see migration.log", mismatch)

    # ─────────────────────────────────────────────────────────────────────────
    #  8. Save report
    # ─────────────────────────────────────────────────────────────────────────
    report.save("conversion_report.md")

    oracle_conn.close()
    pg_conn.close()

    log.info("Done! Check migration.log and conversion_report.md for details.")


if __name__ == "__main__":
    cfg = parse_args()
    run(cfg)
